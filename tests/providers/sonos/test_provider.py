"""Tests for Sonos provider discovery."""

import asyncio
import logging
from unittest.mock import MagicMock

import pytest
from music_assistant_models.enums import ConfigEntryType
from music_assistant_models.player import PlayerMedia
from zeroconf import ServiceStateChange

from music_assistant.mass import MusicAssistant
from music_assistant.providers.sonos.const import CONF_ARTWORK_HTTPS_BASE_URL
from music_assistant.providers.sonos.provider import (
    SonosPlayerProvider,
    _normalize_artwork_https_base_url,
    _rewrite_imageproxy_url,
)

PLAYER_ID = "sonos_player"
SERVICE_NAME = "Toilet._sonos._tcp.local."


def _bind_provider(mass: MusicAssistant | MagicMock) -> SonosPlayerProvider:
    """Create a Sonos provider bound to the given MusicAssistant."""
    provider = SonosPlayerProvider.__new__(SonosPlayerProvider)
    provider.mass = mass
    provider.logger = logging.getLogger("test.sonos.discovery")
    provider._ignored_disabled_players = set()
    provider._pending_setup_tasks = set()
    provider._unloaded = False
    provider._artwork_https_base_url = None
    return provider


@pytest.mark.asyncio
async def test_artwork_https_base_url_config_entry_is_optional() -> None:
    """Test the artwork proxy is exposed as an optional advanced provider setting."""
    provider = SonosPlayerProvider.__new__(SonosPlayerProvider)

    entries = {entry.key: entry for entry in await provider.get_config_entries()}
    entry = entries[CONF_ARTWORK_HTTPS_BASE_URL]

    assert entry.type is ConfigEntryType.STRING
    assert entry.default_value == ""
    assert entry.required is False
    assert entry.advanced is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("https://example.com/ma-sonos-artwork/", "https://example.com/ma-sonos-artwork"),
    ],
)
def test_normalize_artwork_https_base_url(value: object, expected: str | None) -> None:
    """Test valid optional artwork base URLs are normalized."""
    assert _normalize_artwork_https_base_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://example.com/artwork",
        "https:///artwork",
        "https://user:secret@example.com/artwork",
        "https://example.com/artwork?token=secret",
        "https://example.com/artwork#fragment",
        "https://example.com/bad path",
    ],
)
def test_normalize_artwork_https_base_url_rejects_invalid_values(value: str) -> None:
    """Test malformed or non-HTTPS artwork base URLs are rejected."""
    with pytest.raises(ValueError, match="Artwork HTTPS base URL"):
        _normalize_artwork_https_base_url(value)


def test_rewrite_imageproxy_url_rewrites_only_valid_ma_artwork() -> None:
    """Test only a 64-character MA imageproxy id is sent through the HTTPS endpoint."""
    image_id = "a" * 64
    source = f"http://192.168.0.143:8097/imageproxy/{image_id}?size=512"

    assert (
        _rewrite_imageproxy_url(
            source,
            "https://example.com/ma-sonos-artwork",
            "http://192.168.0.143:8097",
        )
        == f"https://example.com/ma-sonos-artwork/{image_id}"
    )


@pytest.mark.parametrize(
    "source",
    [
        "http://192.168.0.143:8097/imageproxy/not-a-hash",
        f"https://cdn.example.com/imageproxy/{'a' * 64}",
        f"http://192.168.0.143:8097/other/{'a' * 64}",
    ],
)
def test_rewrite_imageproxy_url_leaves_unmatched_urls_unchanged(source: str) -> None:
    """Test external, malformed, and non-imageproxy URLs retain official behavior."""
    assert (
        _rewrite_imageproxy_url(
            source,
            "https://example.com/ma-sonos-artwork",
            "http://192.168.0.143:8097",
        )
        == source
    )


def test_cloud_queue_rewrites_artwork_but_not_audio() -> None:
    """Test the Cloud Queue item keeps mediaUrl while replacing only imageUrl."""
    mass = MagicMock()
    mass.streams.base_url = "http://192.168.0.143:8097"
    provider = _bind_provider(mass)
    provider._artwork_https_base_url = "https://example.com/ma-sonos-artwork"
    image_id = "b" * 64
    audio_url = "http://192.168.0.143:8097/stream/track.flac"
    media = PlayerMedia(
        uri=audio_url,
        title="Track",
        image_url=f"http://192.168.0.143:8097/imageproxy/{image_id}?size=512",
    )

    track = provider._parse_sonos_queue_item(media)["track"]

    assert track["mediaUrl"] == audio_url
    assert track["imageUrl"] == f"https://example.com/ma-sonos-artwork/{image_id}"


def _make_provider(enabled: bool = False) -> tuple[SonosPlayerProvider, MagicMock]:
    """Create a Sonos provider with mocked discovery dependencies."""
    mass = MagicMock()
    mass.config.get_raw_player_config_value.return_value = enabled
    mass.players.get_player.return_value = None
    return _bind_provider(mass), mass


def _bind_discovering_provider(mass: MusicAssistant) -> SonosPlayerProvider:
    """Create a Sonos provider that discovers players on the real timer machinery."""
    mass.config = MagicMock()
    mass.config.get_raw_player_config_value.return_value = True
    mass.players = MagicMock()
    mass.players.get_player.return_value = None
    mass.streams = MagicMock()
    return _bind_provider(mass)


def _make_discovery_info(player_id: str = PLAYER_ID) -> MagicMock:
    """Create minimal Sonos mDNS discovery information."""
    info = MagicMock()
    info.decoded_properties = {"uuid": player_id}
    return info


@pytest.mark.asyncio
async def test_disabled_discovery_is_not_scheduled_repeatedly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test repeated announcements for a disabled player are ignored once."""
    provider, mass = _make_provider()
    info = _make_discovery_info()

    with caplog.at_level(logging.DEBUG, logger=provider.logger.name):
        await provider.on_mdns_service_state_change(SERVICE_NAME, ServiceStateChange.Added, info)
        await provider.on_mdns_service_state_change(SERVICE_NAME, ServiceStateChange.Updated, info)

    mass.call_later.assert_not_called()
    ignored_records = [
        record for record in caplog.records if "in discovery as it is disabled" in record.message
    ]
    assert len(ignored_records) == 1


@pytest.mark.asyncio
async def test_disabled_discovery_logs_again_after_reenable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test a new disabled period produces a new discovery diagnostic."""
    provider, mass = _make_provider()
    info = _make_discovery_info()

    with caplog.at_level(logging.DEBUG, logger=provider.logger.name):
        await provider.on_mdns_service_state_change(SERVICE_NAME, ServiceStateChange.Added, info)
        mass.config.get_raw_player_config_value.return_value = True
        await provider.on_mdns_service_state_change(SERVICE_NAME, ServiceStateChange.Updated, info)
        mass.config.get_raw_player_config_value.return_value = False
        await provider.on_mdns_service_state_change(SERVICE_NAME, ServiceStateChange.Updated, info)

    mass.call_later.assert_called_once()
    ignored_records = [
        record for record in caplog.records if "in discovery as it is disabled" in record.message
    ]
    assert len(ignored_records) == 2


@pytest.mark.asyncio
async def test_unload_cancels_a_pending_player_setup(timer_mass: MusicAssistant) -> None:
    """Test a discovered player that is still waiting to be set up is dropped on unload."""
    provider = _bind_discovering_provider(timer_mass)

    await provider.on_mdns_service_state_change(
        SERVICE_NAME, ServiceStateChange.Added, _make_discovery_info()
    )
    handle = timer_mass._tracked_timers[f"setup_sonos_{PLAYER_ID}"]

    await provider.unload(is_removed=True)

    assert handle.cancelled()
    assert provider._pending_setup_tasks == set()


@pytest.mark.asyncio
async def test_unload_cancels_a_player_setup_that_already_started(
    timer_mass: MusicAssistant,
) -> None:
    """Test a player setup that is already running is aborted when the provider unloads."""
    provider = _bind_discovering_provider(timer_mass)
    setup_started = asyncio.Event()

    async def _setup_player(*_args: object) -> None:
        setup_started.set()
        await asyncio.sleep(5)

    provider._setup_player = _setup_player  # type: ignore[assignment]
    await provider.on_mdns_service_state_change(
        SERVICE_NAME, ServiceStateChange.Added, _make_discovery_info()
    )
    task_id = f"setup_sonos_{PLAYER_ID}"
    # let the armed timer fire now instead of waiting out the discovery debounce
    timer_mass.call_later(0, provider._setup_player, task_id=task_id)
    await setup_started.wait()
    task = timer_mass._tracked_tasks[task_id]

    await provider.unload(is_removed=True)

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_an_announcement_arriving_after_the_unload_is_ignored(
    timer_mass: MusicAssistant,
) -> None:
    """Test a discovery callback that lands after the unload does not arm a new setup."""
    provider = _bind_discovering_provider(timer_mass)

    await provider.unload(is_removed=True)
    await provider.on_mdns_service_state_change(
        SERVICE_NAME, ServiceStateChange.Added, _make_discovery_info()
    )

    assert timer_mass._tracked_timers == {}
    assert provider._pending_setup_tasks == set()


@pytest.mark.asyncio
async def test_unload_sweeps_every_discovered_player(timer_mass: MusicAssistant) -> None:
    """Test a burst of discovered players leaves no setup behind after unload."""
    provider = _bind_discovering_provider(timer_mass)

    for index in range(3):
        await provider.on_mdns_service_state_change(
            f"Speaker{index}._sonos._tcp.local.",
            ServiceStateChange.Added,
            _make_discovery_info(f"sonos_player_{index}"),
        )
    assert len(timer_mass._tracked_timers) == 3

    await provider.unload(is_removed=True)

    assert timer_mass._tracked_timers == {}
    assert provider._pending_setup_tasks == set()
