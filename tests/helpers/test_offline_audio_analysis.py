"""Tests for the PC-side offline audio-analysis runner."""

from __future__ import annotations

import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from music_assistant.helpers.offline_audio_analysis import OfflineAudioAnalysisRunner


@pytest.mark.asyncio
async def test_dry_run_reports_all_missing_without_importing_provider_dependencies(
    tmp_path: pathlib.Path,
) -> None:
    """Dry-run can inventory work before optional provider dependencies are installed."""
    audio_path = tmp_path / "track.flac"
    sample_rate = 44_100
    packed = (sample_rate << 44) | (1 << 41) | (15 << 36) | (sample_rate * 10)
    streaminfo = bytes(10) + packed.to_bytes(8, "big") + bytes.fromhex("11" * 16)
    audio_path.write_bytes(b"fLaC" + b"\x80\x00\x00\x22" + streaminfo)
    runner = OfflineAudioAnalysisRunner(("loudness_analysis", "smart_fades", "sonic_analysis"))

    result = await runner.analyze_file(audio_path, dry_run=True)

    assert result.status == "WOULD_ANALYZE"
    assert result.reused == []
    assert result.requested == ["loudness_analysis", "smart_fades", "sonic_analysis"]
    assert runner.provider_versions == {
        "loudness_analysis": 2,
        "smart_fades": 3,
        "sonic_analysis": 1,
    }


@pytest.mark.parametrize("domain", ["smart_fades", "sonic_analysis"])
@pytest.mark.asyncio
async def test_requested_device_is_set_before_model_loading(domain: str) -> None:
    """Both offline model providers receive CUDA before loading their assets."""
    provider = MagicMock()
    provider._load_models = AsyncMock()
    provider.has_unloadable_models = True
    runner = OfflineAudioAnalysisRunner((domain,), device="cuda")
    with (
        patch.object(runner, "_provider_class", return_value=MagicMock(return_value=provider)),
        patch.object(runner, "_resolve_torch_device", return_value="cuda"),
    ):
        assert await runner._get_ready_provider(domain) is provider
        assert await runner._get_ready_provider(domain) is provider
    assert provider._device == "cuda"
    provider._load_models.assert_awaited_once()


def test_explicit_cuda_does_not_silently_fall_back_to_cpu() -> None:
    """A CPU-only installation cannot report a successful GPU configuration."""
    runner = OfflineAudioAnalysisRunner(("sonic_analysis",), device="cuda")
    with (
        patch("torch.cuda.is_available", return_value=False),
        pytest.raises(RuntimeError, match="CUDA was requested"),
    ):
        runner._resolve_torch_device()
