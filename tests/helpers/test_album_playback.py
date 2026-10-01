"""Regression tests executable against the installed personal image with unittest."""

# Imports of installed controllers stay inside the image-only tests.
# ruff: noqa: PLC0415

from __future__ import annotations

import json
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from music_assistant_models.enums import ContentType, MediaType, QueueOption, StreamType
from music_assistant_models.errors import MediaNotFoundError
from music_assistant_models.media_items import Album, AudioFormat, ProviderMapping, Track
from music_assistant_models.queue_item import QueueItem
from music_assistant_models.streamdetails import StreamDetails

from music_assistant.helpers.album_playback import (
    ATTR_LOCAL_ALBUM_MAPPINGS,
    album_playback_mappings,
    album_stream_matches,
    local_album_ids,
    pin_local_album,
)

INSTANCE = "filesystem_local--test"
ORIGINAL = "The Cure/Original"
REMASTER = "The Cure/Remastered"


def mapping(path: str, instance: str = INSTANCE) -> ProviderMapping:
    """Build a local source mapping."""
    return ProviderMapping(
        item_id=path, provider_domain="filesystem_local", provider_instance=instance
    )


def album(path: str) -> Album:
    """Build a distinct library album."""
    return Album(item_id=path, provider="library", name=path, provider_mappings={mapping(path)})


def track() -> Track:
    """Build one shared library recording with two edition mappings."""
    return Track(
        item_id="18665",
        provider="library",
        name="Sinking",
        duration=300,
        album=album(ORIGINAL),
        provider_mappings={
            mapping(f"{ORIGINAL}/Sinking.flac"),
            mapping(f"{REMASTER}/Sinking.flac"),
        },
    )


def queue_item() -> QueueItem:
    """Build an independent occurrence of the recording."""
    return QueueItem.from_media_item("q1", track())


def details(path: str) -> StreamDetails:
    """Build unexpired file stream details."""
    return StreamDetails(
        provider=INSTANCE,
        item_id=path,
        media_type=MediaType.TRACK,
        stream_type=StreamType.LOCAL_FILE,
        audio_format=AudioFormat(content_type=ContentType.FLAC),
        duration=300,
        allow_seek=True,
        path=f"/media/{path}",
    )


def mass_mock() -> MagicMock:
    """Build a provider whose parsed tracks identify their actual album."""
    mass = MagicMock()
    provider = MagicMock(instance_id=INSTANCE, available=True)
    provider.exists = AsyncMock(return_value=False)
    provider.get_album = AsyncMock(side_effect=lambda path: album(path))

    async def get_track(path: str) -> Track:
        result = track()
        result.album = album(REMASTER if "Remastered" in path else ORIGINAL)
        return result

    provider.get_track = AsyncMock(side_effect=get_track)
    provider.get_stream_details = AsyncMock(side_effect=lambda path, _kind: details(path))
    mass.get_provider.side_effect = lambda instance, **_kwargs: (
        provider if instance == INSTANCE else None
    )
    mass.providers = []
    mass.player_queues.queue_data_or_none.return_value = None
    mass.streams.get_config_value.return_value = -17
    mass.streams.source_normalizes_audio.return_value = False
    return mass


class LocalAlbumPlaybackTests(unittest.IsolatedAsyncioTestCase):
    """Validate exact edition selection and persistence without a running server."""

    async def test_both_editions_keep_one_track_identity(self) -> None:
        """The queue restriction never modifies track identity or mappings."""
        mass = mass_mock()
        original, remaster = queue_item(), queue_item()
        before = deepcopy(remaster.media_item.to_dict())
        await pin_local_album(mass, original, {(INSTANCE, ORIGINAL)})
        await pin_local_album(mass, remaster, {(INSTANCE, REMASTER)})
        assert original.media_item.item_id == remaster.media_item.item_id
        assert before == remaster.media_item.to_dict()
        assert [
            entry.item_id
            for entry in album_playback_mappings(remaster, remaster.media_item.provider_mappings)
        ] == [f"{REMASTER}/Sinking.flac"]

    async def test_existing_album_folder_does_not_use_refresh_heuristics(self) -> None:
        """A real folder is authoritative even if an album refresh would choose another."""
        mass = mass_mock()
        provider = mass.get_provider(INSTANCE)
        provider.exists.return_value = True
        provider.get_album.side_effect = None
        provider.get_album.return_value = album(ORIGINAL)
        assert await local_album_ids(mass, album(REMASTER)) == {(INSTANCE, REMASTER)}
        provider.get_album.assert_not_awaited()

    async def test_restore_and_library_reload_keep_restriction(self) -> None:
        """Restoring the queue and replacing its track cannot restore the original source."""
        item = queue_item()
        await pin_local_album(mass_mock(), item, {(INSTANCE, REMASTER)})
        restored = QueueItem.from_cache(json.loads(json.dumps(item.to_cache())))
        restored.media_item = track()
        assert [
            entry.item_id
            for entry in album_playback_mappings(restored, restored.media_item.provider_mappings)
        ] == [f"{REMASTER}/Sinking.flac"]

    async def test_missing_edition_cannot_fallback(self) -> None:
        """A missing chosen file leaves no permitted candidate."""
        mass, item = mass_mock(), queue_item()
        mass.get_provider(INSTANCE).get_track.side_effect = MediaNotFoundError("gone")
        await pin_local_album(mass, item, {(INSTANCE, REMASTER)})
        assert album_playback_mappings(item, item.media_item.provider_mappings) == []
        assert not album_stream_matches(item, details(f"{ORIGINAL}/Sinking.flac"))

    async def test_synthetic_album_id_is_resolved_by_provider(self) -> None:
        """Stored synthetic IDs are resolved instead of being compared to file prefixes."""
        mass = mass_mock()
        mass.get_provider(INSTANCE).get_album.return_value = album(REMASTER)
        mass.get_provider(INSTANCE).get_album.side_effect = None
        assert await local_album_ids(mass, album("The Cure/The Head on the Door")) == {
            (INSTANCE, REMASTER)
        }

    async def test_other_provider_instance_is_excluded(self) -> None:
        """An identical path on another instance is a different source."""
        item = queue_item()
        item.media_item.provider_mappings.add(mapping(f"{REMASTER}/Sinking.flac", "other"))
        await pin_local_album(mass_mock(), item, {(INSTANCE, REMASTER)})
        assert len(album_playback_mappings(item, item.media_item.provider_mappings)) == 1

    async def test_cue_and_multidisc_use_parsed_album_identity(self) -> None:
        """CUE IDs and disc subdirectories need no path heuristic."""
        item = queue_item()
        item.media_item.provider_mappings = {mapping(f"{REMASTER}/Disc 2/album.cue#track=2")}
        await pin_local_album(mass_mock(), item, {(INSTANCE, REMASTER)})
        assert len(album_playback_mappings(item, item.media_item.provider_mappings)) == 1

    async def test_non_album_playback_is_unchanged(self) -> None:
        """Having an album on a track does not restrict search or playlist playback."""
        item = queue_item()
        assert (
            set(album_playback_mappings(item, item.media_item.provider_mappings))
            == item.media_item.provider_mappings
        )
        assert album_stream_matches(item, details(f"{ORIGINAL}/Sinking.flac"))

    async def test_streaming_album_is_not_restricted(self) -> None:
        """Streaming album selection keeps the official provider fallback."""
        selected_album = album(REMASTER)
        selected_album.provider_mappings = {
            ProviderMapping(item_id="a", provider_domain="spotify", provider_instance="spotify--a")
        }
        assert await local_album_ids(mass_mock(), selected_album) == set()
        item = queue_item()
        await pin_local_album(mass_mock(), item, set())
        assert ATTR_LOCAL_ALBUM_MAPPINGS not in item.extra_attributes

    async def test_unavailable_local_instance_fails(self) -> None:
        """A disconnected selected album cannot silently lose its restriction."""
        mass = mass_mock()
        mass.get_provider.side_effect = None
        mass.get_provider.return_value = None
        with pytest.raises(MediaNotFoundError):
            await local_album_ids(mass, album(REMASTER))


class InstalledImagePlaybackTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the real queue and stream controllers from the built image."""

    async def test_stream_resolution_and_cached_seek_use_remaster(self) -> None:
        """The real resolver rejects the original cache and requests the selected source."""
        from music_assistant.controllers.streams.audio import StreamsAudio

        mass, item = mass_mock(), queue_item()
        await pin_local_album(mass, item, {(INSTANCE, REMASTER)})
        item.streamdetails = details(f"{ORIGINAL}/Sinking.flac")
        audio = StreamsAudio(mass)
        result = await audio.get_stream_details(item, seek_position=30)
        assert result.item_id == f"{REMASTER}/Sinking.flac"
        assert result.seek_position == 30
        item.streamdetails = result
        again = await audio.get_stream_details(item, seek_position=60)
        assert again is result
        mass.get_provider(INSTANCE).get_stream_details.assert_awaited_once()

    async def test_missing_chosen_stream_does_not_try_original(self) -> None:
        """A file disappearing after enqueue fails instead of widening to another edition."""
        from music_assistant.controllers.streams.audio import StreamsAudio

        mass, item = mass_mock(), queue_item()
        await pin_local_album(mass, item, {(INSTANCE, REMASTER)})
        provider = mass.get_provider(INSTANCE)
        provider.get_stream_details.side_effect = MediaNotFoundError("gone")
        with pytest.raises(MediaNotFoundError):
            await StreamsAudio(mass).get_stream_details(item)
        provider.get_stream_details.assert_awaited_once_with(
            f"{REMASTER}/Sinking.flac", MediaType.TRACK
        )

    async def test_buffer_preparation_cannot_reuse_wrong_edition(self) -> None:
        """The buffer path validates an existing stream before reusing it."""
        from music_assistant.controllers.streams.audio import StreamsAudio

        mass, item = mass_mock(), queue_item()
        await pin_local_album(mass, item, {(INSTANCE, REMASTER)})
        item.streamdetails = details(f"{ORIGINAL}/Sinking.flac")
        audio = StreamsAudio(mass)
        audio._has_alternative_match_providers = MagicMock(
            side_effect=AssertionError("must not widen")
        )
        with patch(
            "music_assistant.controllers.streams.audio.AudioBuffer.get_buffer",
            new_callable=AsyncMock,
        ) as get_buffer:
            await audio.get_audio_buffer(item)
        assert get_buffer.call_args.kwargs["streamdetails"].item_id == f"{REMASTER}/Sinking.flac"

    async def test_explicit_album_enqueue_copies_track_and_pins_each_occurrence(self) -> None:
        """Actual enqueue keeps identical library IDs independent across two album requests."""
        from music_assistant_models.player_queue import PlayerQueue

        from music_assistant.controllers.player_queues.queue_loader import QueueLoaderMixin
        from music_assistant.controllers.player_queues.state import PlayerQueueData

        mass = mass_mock()
        source = track()
        before = deepcopy(source.to_dict())
        queue = PlayerQueue(
            queue_id="q1", display_name="Test", available=True, active=True, items=0
        )
        ctrl = MagicMock()
        ctrl.mass = mass
        ctrl.get.return_value = queue
        ctrl._queue_data = {"q1": PlayerQueueData(queue=queue)}
        ctrl._media_resolver._resolve_media_items = AsyncMock(return_value=[source])
        ctrl._apply_shuffle = AsyncMock()
        ctrl._enqueue_with_option = AsyncMock()
        mass.players.get_player.return_value.extra_data = {}
        with patch(
            "music_assistant.controllers.player_queues.queue_loader.get_current_user",
            return_value=None,
        ):
            await QueueLoaderMixin._handle_play_media.__wrapped__(
                ctrl,
                "q1",
                [album(ORIGINAL), album(REMASTER)],
                QueueOption.REPLACE,
                start_item="library://track/18665",
            )
        items = ctrl._enqueue_with_option.call_args.args[1]
        assert len(items) == 2
        assert before == source.to_dict()
        assert items[0].media_item.item_id == items[1].media_item.item_id
        assert items[0].extra_attributes != items[1].extra_attributes
        assert ctrl._enqueue_with_option.call_args.kwargs["pin_first"]


if __name__ == "__main__":
    unittest.main()
