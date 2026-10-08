"""Server specific/only models."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ConfigEntry, ConfigValueType, ProviderConfig
    from music_assistant_models.enums import ProviderFeature
    from music_assistant_models.provider import ProviderManifest

    from music_assistant.mass import MusicAssistant

    from .audio_analysis_provider import AudioAnalysisProvider
    from .metadata_provider import MetadataProvider
    from .music_provider import MusicProvider
    from .player_provider import PlayerProvider
    from .plugin import PluginProvider

    type ProviderInstanceType = (
        AudioAnalysisProvider | MetadataProvider | MusicProvider | PlayerProvider | PluginProvider
    )


def __getattr__(name: str) -> Any:
    """Lazily export providers without loading server controllers for submodule consumers."""
    providers = {
        "AudioAnalysisProvider": ".audio_analysis_provider",
        "MetadataProvider": ".metadata_provider",
        "MusicProvider": ".music_provider",
        "PlayerProvider": ".player_provider",
        "PluginProvider": ".plugin",
    }
    if name == "ProviderInstanceType":
        value = (
            __getattr__("AudioAnalysisProvider")
            | __getattr__("MetadataProvider")
            | __getattr__("MusicProvider")
            | __getattr__("PlayerProvider")
            | __getattr__("PluginProvider")
        )
    elif name in providers:
        value = getattr(import_module(providers[name], __name__), name)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value


class ProviderModuleType(Protocol):
    """Model for a provider module to support type hints."""

    """Return the (base) features supported by this Provider."""
    SUPPORTED_FEATURES: set[ProviderFeature]

    @staticmethod
    async def setup(
        mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
    ) -> ProviderInstanceType:
        """Initialize provider(instance) with given configuration."""
        raise NotImplementedError

    @staticmethod
    async def get_config_entries(
        mass: MusicAssistant,
        instance_id: str | None = None,
        action: str | None = None,
        values: dict[str, ConfigValueType] | None = None,
    ) -> tuple[ConfigEntry, ...]:
        """
        Return Config entries to setup this provider.

        instance_id: id of an existing provider instance (None if new instance setup).
        action: [optional] action key called from config entries UI.
        values: the (intermediate) raw values for config entries sent with the action.
        """
        raise NotImplementedError
