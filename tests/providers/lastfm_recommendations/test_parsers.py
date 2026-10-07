"""Tests for Last.fm resolution against a local FLAC library."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from music_assistant_models.enums import ContentType, ExternalID, ProviderFeature
from music_assistant_models.media_items import (
    Artist,
    AudioFormat,
    ProviderMapping,
    Track,
    UniqueList,
)

from music_assistant.providers.lastfm_recommendations.parsers import parse_track


def _track(
    item_id: str = "1",
    *,
    name: str = "Take On Me",
    artist: str = "a-ha",
    version: str = "",
    content_type: ContentType = ContentType.FLAC,
) -> Track:
    return Track(
        item_id=item_id,
        provider="library",
        name=name,
        version=version,
        artists=UniqueList(
            [Artist(item_id="a1", provider="library", name=artist, provider_mappings=set())]
        ),
        provider_mappings={
            ProviderMapping(
                item_id=f"/music/{item_id}.flac",
                provider_domain="filesystem_local",
                provider_instance="filesystem_local--test",
                audio_format=AudioFormat(content_type=content_type),
            )
        },
    )


@pytest.fixture
def mass_mock() -> Mock:
    """Provide a local-only Music Assistant library."""
    mass = Mock()
    mass.music.providers = []
    mass.music.tracks.get_library_item_by_external_ids = AsyncMock(return_value=None)
    mass.music.tracks.library_items = AsyncMock(return_value=[])
    return mass


def _lastfm_track(name: str = "Take On Me") -> dict[str, Any]:
    return {"name": name, "artist": {"name": "a-ha"}, "mbid": "lastfm-recording-id"}


async def test_recording_id_match_keeps_priority(mass_mock: Mock) -> None:
    """An exact Recording ID hit remains the first and final resolution step."""
    expected = _track()
    mass_mock.music.tracks.get_library_item_by_external_ids.return_value = expected

    assert await parse_track(_lastfm_track(), mass_mock, "lastfm") is expected
    mass_mock.music.tracks.get_library_item_by_external_ids.assert_awaited_once_with(
        {(ExternalID.MB_RECORDING, "lastfm-recording-id")}
    )
    mass_mock.music.tracks.library_items.assert_not_awaited()


async def test_different_recording_id_finds_local_flac(mass_mock: Mock) -> None:
    """A safe title and artist match rescues a different Last.fm Recording ID."""
    expected = _track()
    mass_mock.music.tracks.library_items.return_value = [expected]

    assert await parse_track(_lastfm_track(), mass_mock, "lastfm") is expected
    mass_mock.music.tracks.library_items.assert_awaited_once_with(
        search="a-ha - Take On Me", limit=50, summary=False
    )


@pytest.mark.parametrize(
    ("candidate", "lastfm_name"),
    [
        (_track(artist="A-ha Tribute"), "Take On Me"),
        (_track(name="Take On Me Again"), "Take On Me"),
        (_track(version="Live"), "Take On Me"),
        (_track(content_type=ContentType.MP3), "Take On Me"),
        (_track(), "Take On Me (Live)"),
    ],
)
async def test_rejects_nonidentical_or_nonflac_match(
    mass_mock: Mock, candidate: Track, lastfm_name: str
) -> None:
    """A cover, alternate version, different title or non-FLAC file cannot pass."""
    mass_mock.music.tracks.library_items.return_value = [candidate]

    assert await parse_track(_lastfm_track(lastfm_name), mass_mock, "lastfm") is None


async def test_rejects_ambiguous_local_match(mass_mock: Mock) -> None:
    """Two distinct library recordings with the same display name are not guessed."""
    mass_mock.music.tracks.library_items.return_value = [_track("1"), _track("2")]

    assert await parse_track(_lastfm_track(), mass_mock, "lastfm") is None


async def test_version_suffix_matches_local_version(mass_mock: Mock) -> None:
    """An equivalent version in the local version field can be resolved."""
    expected = _track(version="2015 Remaster")
    mass_mock.music.tracks.library_items.return_value = [expected]

    assert (
        await parse_track(_lastfm_track("Take On Me (2015 Remaster)"), mass_mock, "lastfm")
        is expected
    )
    mass_mock.music.tracks.library_items.assert_awaited_once_with(
        search="a-ha - Take On Me", limit=50, summary=False
    )


async def test_streaming_resolution_path_is_preserved(mass_mock: Mock) -> None:
    """A streaming provider retains the existing search path."""
    provider = Mock()
    provider.instance_id = "spotify--test"
    provider.is_streaming_provider = True
    provider.supported_features = {ProviderFeature.LIBRARY_TRACKS}
    mass_mock.music.providers = [provider]
    mass_mock.music.tracks.search = AsyncMock(return_value=[])

    assert await parse_track(_lastfm_track(), mass_mock, "lastfm") is None
    mass_mock.music.tracks.library_items.assert_not_awaited()
    mass_mock.music.tracks.search.assert_awaited_once()
