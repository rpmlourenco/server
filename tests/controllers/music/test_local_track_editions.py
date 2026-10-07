"""Local edition regressions, also executable against the installed personal image."""

from __future__ import annotations

import unittest
from collections.abc import AsyncGenerator
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from music_assistant_models.enums import ExternalID
from music_assistant_models.errors import MediaNotFoundError
from music_assistant_models.media_items import Album, Artist, ProviderMapping, Track, UniqueList
from music_assistant_models.queue_item import QueueItem

from music_assistant.controllers.music import MusicController
from music_assistant.controllers.music.media.tracks import TracksController
from music_assistant.helpers.compare import compare_track
from music_assistant.helpers.tags import (
    _parse_apev2_tags,
    _parse_id3_tags,
    _parse_mp4_tags,
    _parse_vorbis_tags,
)
from music_assistant.mass import MusicAssistant

INSTANCE = "filesystem_local--editions"
RECORDING = "b4a3d031-01da-4e74-8aa3-fde9f9c8dca3"
ORIGINAL_RELEASE = "a64d99de-2c9c-4148-b190-7787575d8264"
REMASTER_RELEASE = "c5674dc2-0426-481a-917f-000a9b060412"
ORIGINAL_TRACK = "df03920a-2471-3eda-b16e-fac2f647360b"
REMASTER_TRACK = "106424fa-cd8b-4b56-81c8-ff1d5b67d38a"


def local_track(
    folder: str,
    release: str | None = None,
    release_track: str | None = None,
    instance: str = INSTANCE,
) -> Track:
    """Build a provider-native track with authoritative IDs independent of its labels."""
    artist = Artist(
        item_id="The Cure",
        provider=instance,
        name="The Cure",
        provider_mappings={
            ProviderMapping(
                item_id="The Cure",
                provider_domain="filesystem_local",
                provider_instance=instance,
                in_library=True,
            )
        },
    )
    album = Album(
        item_id=folder,
        provider=instance,
        name="The Head on the Door",
        artists=UniqueList([artist]),
        provider_mappings={
            ProviderMapping(
                item_id=folder,
                provider_domain="filesystem_local",
                provider_instance=instance,
                url=folder,
                in_library=True,
            )
        },
    )
    if release:
        album.add_external_id(ExternalID.MB_ALBUM, release)
    track = Track(
        item_id=f"{folder}/10 - Sinking.flac",
        provider=instance,
        name="Sinking",
        artists=UniqueList([artist]),
        duration=300,
        album=album,
        disc_number=1,
        track_number=10,
        external_ids={(ExternalID.MB_RECORDING, RECORDING)},
        provider_mappings={
            ProviderMapping(
                item_id=f"{folder}/10 - Sinking.flac",
                provider_domain="filesystem_local",
                provider_instance=instance,
                in_library=True,
            )
        },
    )
    if release_track:
        track.add_external_id(ExternalID.MB_TRACK, release_track)
    return track


class LocalEditionTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the actual installed controller without a running server."""

    def test_release_conflict_does_not_change_global_matching(self) -> None:
        """The official recording comparison stays intact outside the local import guard."""
        original = local_track("original", ORIGINAL_RELEASE, ORIGINAL_TRACK)
        remaster = local_track("remaster", REMASTER_RELEASE, REMASTER_TRACK)
        assert compare_track(original, remaster)
        assert not TracksController._same_local_release(original, remaster)

    def test_equal_labels_without_release_ids_do_not_merge_folders(self) -> None:
        """Identical album titles and recording IDs do not prove an identical edition."""
        assert not TracksController._same_local_release(
            local_track("original"), local_track("remaster")
        )

    def test_same_release_can_dedupe_copies(self) -> None:
        """Two copies of one identified release remain interchangeable."""
        assert TracksController._same_local_release(
            local_track("copy-a", ORIGINAL_RELEASE, ORIGINAL_TRACK),
            local_track("copy-b", ORIGINAL_RELEASE, ORIGINAL_TRACK),
        )

    def test_release_track_conflict_overrides_equal_album_release(self) -> None:
        """Distinct release-track IDs cannot be merged just because the album IDs match."""
        assert not TracksController._same_local_release(
            local_track("a", ORIGINAL_RELEASE, ORIGINAL_TRACK),
            local_track("b", ORIGINAL_RELEASE, REMASTER_TRACK),
        )

    def test_same_native_album_without_ids_can_dedupe(self) -> None:
        """A provider's exact album identity is a conservative fallback."""
        first, second = local_track("album"), local_track("album")
        second.item_id += ".copy"
        second.provider_mappings = {
            ProviderMapping(
                item_id=second.item_id,
                provider_domain="filesystem_local",
                provider_instance=INSTANCE,
            )
        }
        assert TracksController._same_local_release(first, second)

    def test_same_path_on_other_instance_is_not_the_same_album(self) -> None:
        """Provider instances are part of the native album identity."""
        assert not TracksController._same_local_release(
            local_track("album"), local_track("album", instance="filesystem_local--other")
        )

    def test_synthetic_album_identity_cannot_merge_unknown_editions(self) -> None:
        """A title-derived album ID without a physical source is not edition evidence."""
        first, second = local_track("The Cure/Album"), local_track("The Cure/Album")
        first.album.provider_mappings = second.album.provider_mappings = set()
        assert not TracksController._same_local_release(first, second)

    def test_repeated_recording_on_another_album_position_stays_separate(self) -> None:
        """Repeated recordings within one release still identify separate album tracks."""
        first, second = (
            local_track("album", ORIGINAL_RELEASE),
            local_track("album", ORIGINAL_RELEASE),
        )
        second.item_id += ".bonus"
        second.provider_mappings = set()
        second.track_number += 1
        assert not TracksController._same_local_release(first, second)

    def test_missing_album_is_not_evidence_for_merging(self) -> None:
        """Incomplete local metadata must not silently choose another edition."""
        first, second = local_track("a"), local_track("b")
        second.album = None
        assert not TracksController._same_local_release(first, second)

    def test_multidisc_and_cue_use_album_identity(self) -> None:
        """No prefix comparison or file-extension heuristic identifies the edition."""
        first, second = local_track("album"), local_track("album")
        first.item_id = "album/Disc 2/image.cue#track=2"
        second.item_id = "album/Disc 2/02.flac"
        first.provider_mappings = set()
        second.provider_mappings = set()
        first.disc_number = second.disc_number = 2
        first.track_number = second.track_number = 2
        assert TracksController._same_local_release(first, second)
        second.disc_number = 1
        assert not TracksController._same_local_release(first, second)

    async def test_native_source_overrides_aggregated_library_metadata(self) -> None:
        """An already merged library album cannot prove the local track's edition."""
        original, remaster = local_track("original"), local_track("remaster")
        stored = deepcopy(original)
        stored.provider = "library"
        stored.item_id = "1"
        stored.album = remaster.album
        mass = MagicMock()
        mass.get_provider.return_value.get_track = AsyncMock(return_value=original)
        controller = TracksController(mass)
        assert not await controller._confirm_library_candidate(stored, remaster)

    async def test_missing_native_file_cannot_confirm_a_match(self) -> None:
        """A missing source cannot justify merging a new edition into a stale row."""
        mass = MagicMock()
        mass.get_provider.return_value.get_track = AsyncMock(side_effect=MediaNotFoundError("gone"))
        assert not await TracksController(mass)._confirm_library_candidate(
            local_track("a"), local_track("b")
        )

    async def test_non_filesystem_provider_preserves_official_matching(self) -> None:
        """Other non-streaming providers are outside this local edition policy."""
        first, second = local_track("a"), local_track("b")
        second.provider_mappings = {
            ProviderMapping(item_id="b", provider_domain="plex", provider_instance="plex--test")
        }
        mass = MagicMock()
        assert await TracksController(mass)._confirm_library_candidate(first, second)
        mass.get_provider.assert_not_called()

    def test_tag_formats_keep_recording_and_release_track_distinct(self) -> None:
        """FLAC, APEv2, MP3 and M4A use their respective MusicBrainz conventions."""
        # Imports here keep the unittest entry point usable in the installed image.
        from mutagen.id3 import ID3, TXXX, UFID  # noqa: PLC0415

        id3 = ID3()
        id3.add(UFID(owner="http://musicbrainz.org", data=RECORDING.encode()))
        id3.add(TXXX(encoding=3, desc="MusicBrainz Release Track Id", text=[ORIGINAL_TRACK]))
        parsed = [
            _parse_id3_tags(id3),
            _parse_mp4_tags(
                {
                    "----:com.apple.iTunes:MusicBrainz Track Id": [RECORDING.encode()],
                    "----:com.apple.iTunes:MusicBrainz Release Track Id": [ORIGINAL_TRACK.encode()],
                }
            ),
            _parse_vorbis_tags(
                {"MUSICBRAINZ_TRACKID": [RECORDING], "MUSICBRAINZ_RELEASETRACKID": [ORIGINAL_TRACK]}
            ),
            _parse_apev2_tags(
                {"MUSICBRAINZ_TRACKID": RECORDING, "MUSICBRAINZ_RELEASETRACKID": ORIGINAL_TRACK}
            ),
        ]
        for tags in parsed:
            assert tags.get("musicbrainzrecordingid", tags.get("musicbrainztrackid")) == RECORDING
            assert tags["musicbrainzreleasetrackid"] == ORIGINAL_TRACK


@pytest.fixture
async def music(mass_minimal: MusicAssistant) -> AsyncGenerator[MusicController]:
    """Use a real SQLite library for edition identity and API serialization tests."""
    controller = MusicController(mass_minimal)
    mass_minimal.music = controller
    await controller._setup_database()
    yield controller
    if controller._database:
        await controller._database.close()


@pytest.mark.parametrize("identified", [True, False])
async def test_import_distinct_local_editions_and_playlist_identities(
    music: MusicController, identified: bool
) -> None:
    """Actual imports separate editions even when album labels are identical."""
    original = local_track("original", ORIGINAL_RELEASE if identified else None)
    remaster = local_track("remaster", REMASTER_RELEASE if identified else None)
    provider = MagicMock(
        instance_id=INSTANCE, domain="filesystem_local", is_streaming_provider=False
    )
    tracks = {original.item_id: original, remaster.item_id: remaster}
    provider.get_track = AsyncMock(side_effect=lambda item_id: tracks[item_id])
    with patch.object(music.mass, "get_provider", return_value=provider):
        first = await music.tracks.add_item_to_library(original)
        second = await music.tracks.add_item_to_library(remaster)
        again = await music.tracks.add_item_to_library(remaster)
    assert first.item_id != second.item_id
    assert again.item_id == second.item_id
    assert await music.tracks.library_count() == 2
    assert {mapping.item_id for mapping in first.provider_mappings} == {original.item_id}
    assert {mapping.item_id for mapping in second.provider_mappings} == {remaster.item_id}
    queue = [QueueItem.from_media_item("q1", track) for track in (first, second)]
    assert queue[0].uri != queue[1].uri
    assert all("personal_local_album_mappings" not in item.extra_attributes for item in queue)
    assert QueueItem.from_cache(queue[1].to_cache()).uri == second.uri


async def test_import_same_identified_release_dedupes(music: MusicController) -> None:
    """The import guard preserves deduplication for copies of one concrete release."""
    first = local_track("copy-a", ORIGINAL_RELEASE, ORIGINAL_TRACK)
    second = local_track("copy-b", ORIGINAL_RELEASE, ORIGINAL_TRACK)
    provider = MagicMock(
        instance_id=INSTANCE, domain="filesystem_local", is_streaming_provider=False
    )
    provider.get_track = AsyncMock(return_value=first)
    with patch.object(music.mass, "get_provider", return_value=provider):
        stored_first = await music.tracks.add_item_to_library(first)
        stored_second = await music.tracks.add_item_to_library(second)
    assert stored_first.item_id == stored_second.item_id
    assert len(stored_second.provider_mappings) == 2


async def test_album_version_survives_full_summary_and_queue(music: MusicController) -> None:
    """Edition metadata remains available in library lists and serialized queue items."""
    source = local_track("remaster", REMASTER_RELEASE)
    source.album.version = "2006 Remaster"
    stored = await music.tracks.add_item_to_library(source)
    summary = (await music.tracks.library_items(summary=True))[0]
    assert stored.album.version == "2006 Remaster"
    assert summary.album.version == "2006 Remaster"
    item = QueueItem.from_media_item("q1", stored)
    assert item.media_item.to_dict()["album"]["version"] == "2006 Remaster"


if __name__ == "__main__":
    unittest.main()
