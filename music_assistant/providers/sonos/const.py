"""Constants for the Sonos (S2) provider."""

from __future__ import annotations

from aiosonos.api.models import PlayBackState as SonosPlayBackState
from music_assistant_models.config_entries import ConfigEntry
from music_assistant_models.enums import ConfigEntryType, PlaybackState, PlayerFeature

from music_assistant.models.player import PlayerSource

CONF_ARTWORK_HTTPS_BASE_URL = "artwork_https_base_url"
CONF_ENTRY_ARTWORK_HTTPS_BASE_URL = ConfigEntry(
    key=CONF_ARTWORK_HTTPS_BASE_URL,
    type=ConfigEntryType.STRING,
    label="Artwork HTTPS base URL",
    description=(
        "Optional HTTPS endpoint used only for Music Assistant artwork sent to Sonos. "
        "Leave empty to keep the default image URLs."
    ),
    default_value="",
    required=False,
    advanced=True,
)

PLAYBACK_STATE_MAP = {
    SonosPlayBackState.PLAYBACK_STATE_BUFFERING: PlaybackState.PLAYING,
    SonosPlayBackState.PLAYBACK_STATE_IDLE: PlaybackState.IDLE,
    SonosPlayBackState.PLAYBACK_STATE_PAUSED: PlaybackState.PAUSED,
    SonosPlayBackState.PLAYBACK_STATE_PLAYING: PlaybackState.PLAYING,
}

PLAYER_FEATURES_BASE = {
    PlayerFeature.SET_MEMBERS,
    PlayerFeature.PAUSE,
    PlayerFeature.ENQUEUE,
    PlayerFeature.NEXT_PREVIOUS,
    PlayerFeature.SEEK,
    PlayerFeature.SELECT_SOURCE,
    PlayerFeature.GAPLESS_PLAYBACK,
}

SOURCE_LINE_IN = "line_in"
SOURCE_AIRPLAY = "airplay"
SOURCE_SPOTIFY = "spotify"
SOURCE_UNKNOWN = "unknown"
SOURCE_TV = "tv"
SOURCE_RADIO = "radio"

PLAYER_SOURCE_MAP = {
    SOURCE_LINE_IN: PlayerSource(
        id=SOURCE_LINE_IN,
        name="Line-in",
        passive=False,
        can_play_pause=False,
        can_next_previous=False,
        can_seek=False,
    ),
    SOURCE_TV: PlayerSource(
        id=SOURCE_TV,
        name="TV",
        passive=False,
        can_play_pause=False,
        can_next_previous=False,
        can_seek=False,
    ),
    SOURCE_AIRPLAY: PlayerSource(
        id=SOURCE_AIRPLAY,
        name="AirPlay",
        passive=True,
        can_play_pause=True,
        can_next_previous=True,
        can_seek=True,
    ),
    SOURCE_SPOTIFY: PlayerSource(
        id=SOURCE_SPOTIFY,
        name="Spotify",
        passive=True,
        can_play_pause=True,
        can_next_previous=True,
        can_seek=True,
    ),
    SOURCE_RADIO: PlayerSource(
        id=SOURCE_RADIO,
        name="Radio",
        passive=True,
        can_play_pause=True,
        can_next_previous=True,
        can_seek=True,
    ),
}

UNSUPPORTED_MODELS_NATIVE_ANNOUNCEMENTS = ("Play:1", "Play:3")
NON_HIRES_MODELS = (
    "Play:1",
    "Play:3",
    "Connect",
    "Connect:Amp",
    "Table lamp",
)
