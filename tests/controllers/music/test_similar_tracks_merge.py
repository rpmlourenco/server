"""Regression tests for combined music, metadata and plugin similarity results."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from music_assistant_models.enums import ProviderFeature
from music_assistant_models.errors import ProviderUnavailableError
from music_assistant_models.media_items import Artist, ProviderMapping, Track, UniqueList

from music_assistant.controllers.music.media.tracks import TracksController
from music_assistant.models.music_provider import MusicProvider


def _track(name: str, provider: str = "library", version: str = "") -> Track:
    return Track(
        item_id=name + version,
        provider=provider,
        name=name,
        version=version,
        duration=240,
        artists=UniqueList(
            [Artist(item_id="artist", provider="library", name="Artist", provider_mappings=set())]
        ),
        provider_mappings=set(),
    )


def _controller() -> TracksController:
    mass = MagicMock()
    mass.get_provider.return_value = None
    mass.get_providers_supporting_feature.return_value = []
    return TracksController(mass)


def test_merge_interleaves_deduplicates_and_applies_total_limit() -> None:
    """A short Last.fm list must not hide the Sonic list or inflate the total limit."""
    seed = _track("Seed")
    lists = [[_track("A"), _track("B"), seed], [_track("A"), _track("C"), _track("D")]]
    merged = TracksController._merge_similar_track_results(seed, lists, 4)
    assert [track.name for track in merged] == ["A", "B", "C", "D"]
    assert [
        track.name for track in TracksController._merge_similar_track_results(seed, lists, 2)
    ] == ["A", "B"]


def test_merge_deduplicates_provider_and_library_representations() -> None:
    """Recognize a shared provider mapping, while keeping distinct song versions."""
    library = _track("Song")
    provider_track = _track("Song", "filesystem")
    library.provider_mappings.add(
        ProviderMapping(
            item_id=provider_track.item_id,
            provider_domain="filesystem",
            provider_instance="filesystem",
        )
    )
    live = _track("Song", "filesystem", "Live")
    merged = TracksController._merge_similar_track_results(
        _track("Seed"), [[library], [provider_track, live]], 25
    )
    assert merged == [library, live]


@pytest.mark.asyncio
async def test_all_active_cross_providers_contribute_even_when_first_has_results() -> None:
    """The My Desire regression: three metadata results plus twenty-five local matches."""
    controller = _controller()
    metadata, plugin = MagicMock(), MagicMock()
    metadata.instance_id, plugin.instance_id = "lastfm", "sonic"
    cast("MagicMock", controller.mass).get_providers_supporting_feature.return_value = [
        metadata,
        plugin,
    ]
    lastfm = [_track(f"Last.fm {index}") for index in range(3)]
    sonic = [_track(f"Sonic {index}") for index in range(25)]
    with (
        patch.object(controller, "get", AsyncMock(return_value=_track("Seed"))),
        patch.object(
            controller,
            "_get_similar_tracks_from_provider",
            AsyncMock(side_effect=[(lastfm, None), (sonic, None)]),
        ) as fetch,
        patch.object(controller, "_lookup_similar_tracks_provider") as lookup,
    ):
        results = await controller.similar_tracks("seed", "library", limit=25, allow_lookup=True)
    assert len(results) == 25
    assert [track.name for track in results[:6]] == [
        "Last.fm 0",
        "Sonic 0",
        "Last.fm 1",
        "Sonic 1",
        "Last.fm 2",
        "Sonic 2",
    ]
    assert fetch.await_count == 2
    lookup.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first", [(None, ProviderUnavailableError("offline")), ([], None), (None, None)]
)
async def test_healthy_provider_survives_failed_empty_or_unsupported_provider(
    first: tuple[list[Track] | None, ProviderUnavailableError | None],
) -> None:
    """Keep independent providers' results; unsupported and empty are not errors."""
    controller = _controller()
    metadata, plugin = MagicMock(), MagicMock()
    metadata.instance_id, plugin.instance_id = "lastfm", "sonic"
    cast("MagicMock", controller.mass).get_providers_supporting_feature.return_value = [
        metadata,
        plugin,
    ]
    expected = [_track("Sonic")]
    with (
        patch.object(controller, "get", AsyncMock(return_value=_track("Seed"))),
        patch.object(
            controller,
            "_get_similar_tracks_from_provider",
            AsyncMock(side_effect=[first, (expected, None)]),
        ),
    ):
        assert await controller.similar_tracks("seed", "library") == expected


@pytest.mark.asyncio
async def test_single_provider_and_optional_lookup_remain_supported() -> None:
    """Use lookup only when no registered provider returns useful results."""
    controller = _controller()
    with (
        patch.object(controller, "get", AsyncMock(return_value=_track("Seed"))),
        patch.object(
            controller,
            "_lookup_similar_tracks_provider",
            AsyncMock(return_value=([_track("Lookup")], None)),
        ) as lookup,
    ):
        assert await controller.similar_tracks("seed", "library") == []
        lookup.assert_not_called()
        results = await controller.similar_tracks("seed", "library", allow_lookup=True)
        assert [track.name for track in results] == ["Lookup"]


@pytest.mark.asyncio
async def test_all_provider_errors_are_not_silently_hidden() -> None:
    """Keep the existing error semantics if every responding provider fails."""
    controller = _controller()
    provider = MagicMock(instance_id="sonic")
    cast("MagicMock", controller.mass).get_providers_supporting_feature.return_value = [provider]
    with (
        patch.object(controller, "get", AsyncMock(return_value=_track("Seed"))),
        patch.object(
            controller,
            "_get_similar_tracks_from_provider",
            AsyncMock(return_value=(None, ProviderUnavailableError("offline"))),
        ),
        pytest.raises(ProviderUnavailableError),
    ):
        await controller.similar_tracks("seed", "library")


@pytest.mark.asyncio
async def test_music_provider_called_once_for_multiple_seed_mappings() -> None:
    """Avoid duplicate remote calls and retain music-provider ID dispatch."""
    controller = _controller()
    seed = _track("Seed")
    seed.provider_mappings = {
        ProviderMapping(item_id=item_id, provider_domain="music", provider_instance="music")
        for item_id in ("seed1", "seed2")
    }
    provider = MagicMock(spec=MusicProvider)
    provider.instance_id = "music"
    provider.available = True
    provider.supported_features = {ProviderFeature.SIMILAR_TRACKS}
    cast("MagicMock", controller.mass).get_provider.return_value = provider
    with (
        patch.object(controller, "get", AsyncMock(return_value=seed)),
        patch.object(
            controller,
            "_get_similar_tracks_from_provider",
            AsyncMock(return_value=([_track("Music")], None)),
        ) as fetch,
    ):
        assert len(await controller.similar_tracks("seed", "library")) == 1
    fetch.assert_awaited_once()
    assert fetch.call_args.kwargs["provider_track_id"] in {"seed1", "seed2"}


@pytest.mark.asyncio
async def test_zero_limit_does_not_request_providers() -> None:
    """An empty requested list does not trigger any provider/API requests."""
    controller = _controller()
    with patch.object(controller, "get", AsyncMock()) as get:
        assert await controller.similar_tracks("seed", "library", limit=0) == []
    get.assert_not_called()
