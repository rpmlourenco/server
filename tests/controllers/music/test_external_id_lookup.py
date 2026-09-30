"""Tests for the indexed external id lookup of media items."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from music_assistant_models.enums import ExternalID
from music_assistant_models.media_items import Album, Artist, ProviderMapping, UniqueList

from music_assistant.constants import DB_TABLE_EXTERNAL_ID_LOOKUP
from music_assistant.controllers.music import MusicController
from music_assistant.mass import MusicAssistant

from .helpers import ISRC, create_track

MBID = "b1a9c0e9-d987-4042-ae91-78d6a3267d69"
BARCODE = "0724354283857"


def _mark_album_mappings_in_library(album: Album) -> None:
    """Mark a provider album as present in its source library for relation lookups."""
    album.provider_mappings = {
        replace(mapping, in_library=True) for mapping in album.provider_mappings
    }


@pytest.fixture
async def music(mass_minimal: MusicAssistant) -> AsyncGenerator[MusicController]:
    """Return a music controller with a real library database."""
    controller = MusicController(mass_minimal)
    mass_minimal.music = controller
    await controller._setup_database()
    yield controller
    if controller._database:
        await controller._database.close()


async def _get_lookup_rows(music: MusicController, item_id: int | str) -> set[tuple[str, str]]:
    """Return the (external_id_type, external_id) lookup rows stored for a track."""
    return {
        (row["external_id_type"], row["external_id"])
        for row in await music.database.get_rows(
            DB_TABLE_EXTERNAL_ID_LOOKUP, {"media_type": "track", "item_id": int(item_id)}
        )
    }


def _create_album(
    provider_instance: str,
    item_id: str,
    name: str,
    artist_name: str,
    year: int,
    barcode: str = BARCODE,
) -> Album:
    """
    Create an album as received from a music provider.

    :param provider_instance: Provider instance the album originates from.
    :param item_id: Provider-native album identifier.
    :param name: Album name.
    :param artist_name: Album artist name.
    :param year: Album release year.
    :param barcode: Album barcode.
    """
    provider_domain = provider_instance.rsplit("_", 1)[0]
    return Album(
        item_id=item_id,
        provider=provider_instance,
        name=name,
        year=year,
        external_ids={(ExternalID.BARCODE, barcode)},
        provider_mappings={
            ProviderMapping(
                item_id=item_id,
                provider_domain=provider_domain,
                provider_instance=provider_instance,
            )
        },
        artists=UniqueList(
            [
                Artist(
                    item_id=f"{item_id}_artist",
                    provider=provider_instance,
                    name=artist_name,
                    provider_mappings={
                        ProviderMapping(
                            item_id=f"{item_id}_artist",
                            provider_domain=provider_domain,
                            provider_instance=provider_instance,
                        )
                    },
                )
            ]
        ),
    )


async def test_same_isrc_from_two_providers_dedupes(music: MusicController) -> None:
    """Two providers exposing the same track with an identical ISRC merge into one item."""
    library_track_1 = await music.tracks.add_item_to_library(create_track("spotify_1", "track_abc"))
    library_track_2 = await music.tracks.add_item_to_library(create_track("tidal_1", "track_xyz"))

    assert library_track_1.item_id == library_track_2.item_id
    assert len(library_track_2.provider_mappings) == 2
    assert await music.tracks.library_count() == 1


async def test_same_recording_on_distinct_local_releases_stays_separate(
    music: MusicController,
) -> None:
    """A local original and remaster remain distinct playable library tracks."""
    provider_instance = "filesystem_local--test"
    provider = MagicMock(instance_id=provider_instance, is_streaming_provider=False)
    original_album = _create_album(
        provider_instance,
        "The Cure/1985 - The Head on the Door",
        "The Head on the Door",
        "The Cure",
        1985,
    )
    original_album.external_ids.add(
        (ExternalID.MB_ALBUM, "a64d99de-2c9c-4148-b190-7787575d8264")
    )
    remaster_album = _create_album(
        provider_instance,
        "The Cure/1985 - The Head on the Door (Remastered)",
        "The Head on the Door",
        "The Cure",
        1985,
    )
    remaster_album.version = "Remastered"
    remaster_album.external_ids.add(
        (ExternalID.MB_ALBUM, "c5674dc2-0426-481a-917f-000a9b060412")
    )
    _mark_album_mappings_in_library(original_album)
    _mark_album_mappings_in_library(remaster_album)
    original = create_track(provider_instance, "original/10 - Sinking.flac", name="Sinking")
    remaster = create_track(provider_instance, "remaster/10 - Sinking.flac", name="Sinking")
    recording_id = "b4a3d031-01da-4e74-8aa3-fde9f9c8dca3"
    original.external_ids.add((ExternalID.MB_RECORDING, recording_id))
    remaster.external_ids.add((ExternalID.MB_RECORDING, recording_id))
    original.external_ids.add(
        (ExternalID.MB_TRACK, "df03920a-2471-3eda-b16e-fac2f647360b")
    )
    remaster.external_ids.add(
        (ExternalID.MB_TRACK, "106424fa-cd8b-4b56-81c8-ff1d5b67d38a")
    )
    original.album = original_album
    remaster.album = remaster_album
    original.track_number = remaster.track_number = 10

    with patch.object(music.mass, "get_provider", return_value=provider):
        library_original = await music.tracks.add_item_to_library(original)
        library_remaster = await music.tracks.add_item_to_library(remaster)

    assert library_original.item_id != library_remaster.item_id
    assert await music.tracks.library_count() == 2
    assert {mapping.item_id for mapping in library_original.provider_mappings} == {
        "original/10 - Sinking.flac"
    }
    assert {mapping.item_id for mapping in library_remaster.provider_mappings} == {
        "remaster/10 - Sinking.flac"
    }


async def test_same_recording_on_same_local_release_still_dedupes(
    music: MusicController,
) -> None:
    """Duplicate local files for the same release may still share one library track."""
    provider_instance = "filesystem_local--test"
    provider = MagicMock(instance_id=provider_instance, is_streaming_provider=False)
    first_album = _create_album(
        provider_instance, "copy-a/album", "The Head on the Door", "The Cure", 1985
    )
    second_album = _create_album(
        provider_instance, "copy-b/album", "The Head on the Door", "The Cure", 1985
    )
    album_id = "a64d99de-2c9c-4148-b190-7787575d8264"
    first_album.external_ids.add((ExternalID.MB_ALBUM, album_id))
    second_album.external_ids.add((ExternalID.MB_ALBUM, album_id))
    _mark_album_mappings_in_library(first_album)
    _mark_album_mappings_in_library(second_album)
    first = create_track(provider_instance, "copy-a/10 - Sinking.flac", name="Sinking")
    second = create_track(provider_instance, "copy-b/10 - Sinking.flac", name="Sinking")
    recording_id = "b4a3d031-01da-4e74-8aa3-fde9f9c8dca3"
    first.external_ids.add((ExternalID.MB_RECORDING, recording_id))
    second.external_ids.add((ExternalID.MB_RECORDING, recording_id))
    release_track_id = "df03920a-2471-3eda-b16e-fac2f647360b"
    first.external_ids.add((ExternalID.MB_TRACK, release_track_id))
    second.external_ids.add((ExternalID.MB_TRACK, release_track_id))
    first.album = first_album
    second.album = second_album
    first.track_number = second.track_number = 10

    with patch.object(music.mass, "get_provider", return_value=provider):
        library_first = await music.tracks.add_item_to_library(first)
        stored_albums = await music.tracks.get_library_track_albums(library_first.item_id)
        assert len(stored_albums) == 1
        assert (ExternalID.MB_ALBUM, album_id) in stored_albums[0].external_ids
        library_second = await music.tracks.add_item_to_library(second)

    assert library_first.item_id == library_second.item_id
    assert await music.tracks.library_count() == 1
    assert len(library_second.provider_mappings) == 2


async def test_formatted_isrc_from_two_providers_dedupes(music: MusicController) -> None:
    """Equivalent formatted ISRC values merge into one library track."""
    first = create_track("spotify_1", "track_abc", isrc="US-RC1-76-07839")
    second = create_track("tidal_1", "track_xyz", isrc="usrc17607839")

    library_track_1 = await music.tracks.add_item_to_library(first)
    library_track_2 = await music.tracks.add_item_to_library(second)

    assert library_track_1.item_id == library_track_2.item_id
    assert library_track_2.external_ids == {(ExternalID.ISRC, ISRC)}
    untyped_match = await music.tracks.get_library_item_by_external_id("US-RC1-76-07839")
    assert untyped_match is not None
    assert untyped_match.item_id == library_track_1.item_id


async def test_matching_checks_more_than_fifty_external_id_candidates(
    music: MusicController,
) -> None:
    """Internal matching paginates every row sharing a non-unique identifier."""
    for index in range(50):
        await music.tracks.add_item_to_library(
            create_track(
                f"provider{index}_1",
                f"collision_{index}",
                name=f"Collision {index}",
                duration=100 + index * 20,
            )
        )
    expected = await music.tracks.add_item_to_library(
        create_track("expected_1", "expected", name="Expected", duration=2000)
    )

    matched = await music.tracks.add_item_to_library(
        create_track("incoming_1", "incoming", name="Different name", duration=2000)
    )

    assert matched.item_id == expected.item_id
    assert await music.tracks.library_count() == 51


async def test_non_unique_external_id_candidates_are_all_verified(
    music: MusicController,
) -> None:
    """An unrelated barcode collision does not hide the correct album candidate."""
    unrelated = await music.albums.add_item_to_library(
        _create_album("apple_music_1", "unrelated", "Keeping It All Low", "XP", 2019)
    )
    expected = await music.albums.add_item_to_library(
        _create_album(
            "apple_music_1",
            "expected",
            "#1",
            "Fischerspooner",
            2001,
            barcode="000724354283857",
        )
    )
    await music.database.execute_write(
        f"UPDATE {DB_TABLE_EXTERNAL_ID_LOOKUP} SET external_id = :external_id "
        "WHERE media_type = 'album' AND item_id = :item_id",
        {"external_id": "000724354283857", "item_id": int(expected.item_id)},
    )

    matched = await music.albums.add_item_to_library(
        _create_album("qobuz_1", "qobuz_release", "#1", "Fischerspooner", 2002)
    )

    assert matched.item_id == expected.item_id
    assert matched.item_id != unrelated.item_id
    assert await music.albums.library_count() == 2
    assert {mapping.provider_instance for mapping in matched.provider_mappings} == {
        "apple_music_1",
        "qobuz_1",
    }


async def test_get_library_item_by_external_id(music: MusicController) -> None:
    """Library items resolve by external id, both typed and untyped."""
    track = create_track("spotify_1", "track_abc")
    track.external_ids.add((ExternalID.MB_RECORDING, MBID))
    library_track = await music.tracks.add_item_to_library(track)

    # typed lookup
    match = await music.tracks.get_library_item_by_external_id(ISRC, ExternalID.ISRC)
    assert match is not None
    assert match.item_id == library_track.item_id
    match = await music.tracks.get_library_item_by_external_id(MBID, ExternalID.MB_RECORDING)
    assert match is not None
    assert match.item_id == library_track.item_id
    # untyped lookup
    match = await music.tracks.get_library_item_by_external_id(ISRC)
    assert match is not None
    assert match.item_id == library_track.item_id
    # matching is case-insensitive (as the previous LIKE based scan was)
    match = await music.tracks.get_library_item_by_external_id(ISRC.lower(), ExternalID.ISRC)
    assert match is not None
    assert match.item_id == library_track.item_id
    # no (partial) match on wrong type or unknown id
    assert await music.tracks.get_library_item_by_external_id(ISRC, ExternalID.BARCODE) is None
    assert await music.tracks.get_library_item_by_external_id("something-else") is None
    assert await music.tracks.get_library_item_by_external_id(ISRC[:-1]) is None


async def test_external_id_lookup_rows_follow_item_updates(music: MusicController) -> None:
    """The lookup rows are kept in sync when an item is updated or removed."""
    library_track = await music.tracks.add_item_to_library(create_track("spotify_1", "track_abc"))
    assert await _get_lookup_rows(music, library_track.item_id) == {(str(ExternalID.ISRC), ISRC)}

    # an update merges in newly discovered external ids
    update = create_track("spotify_1", "track_abc")
    update.external_ids.add((ExternalID.MB_RECORDING, MBID))
    updated = await music.tracks.update_item_in_library(library_track.item_id, update)
    # the item's external_ids attribute is reconstructed from the lookup table on read
    assert updated.external_ids == {(ExternalID.ISRC, ISRC), (ExternalID.MB_RECORDING, MBID)}
    assert await _get_lookup_rows(music, library_track.item_id) == {
        (str(ExternalID.ISRC), ISRC),
        (str(ExternalID.MB_RECORDING), MBID),
    }

    # an overwrite update replaces the lookup rows
    await music.tracks.update_item_in_library(
        library_track.item_id,
        create_track("spotify_1", "track_abc", isrc="GBUM71029604"),
        overwrite=True,
    )
    assert await _get_lookup_rows(music, library_track.item_id) == {
        (str(ExternalID.ISRC), "GBUM71029604")
    }
    assert await music.tracks.get_library_item_by_external_id(ISRC) is None

    # removal cleans up the lookup rows
    await music.tracks.remove_item_from_library(library_track.item_id)
    assert await _get_lookup_rows(music, library_track.item_id) == set()
