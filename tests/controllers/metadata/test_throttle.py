"""Tests for global metadata update pacing."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from music_assistant.controllers.metadata import MetaDataController


async def test_global_metadata_throttle(metadata_controller: MetaDataController) -> None:
    """Allow one metadata update every three seconds across all item types."""
    assert metadata_controller._throttler.rate_limit == 1
    assert metadata_controller._throttler.period == 3
