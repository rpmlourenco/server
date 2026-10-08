"""Offline execution of Music Assistant audio-analysis providers."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from music_assistant_models.config_entries import ProviderConfig
from music_assistant_models.enums import (
    ContentType,
    MediaType,
    ProviderType,
    StreamType,
    VolumeNormalizationMode,
)
from music_assistant_models.media_items import AudioFormat
from music_assistant_models.provider import ProviderManifest
from music_assistant_models.streamdetails import StreamDetails

from music_assistant.helpers.audio_analysis_sidecar import (
    AudioAnalysisSidecarError,
    read_audio_analysis_sidecar,
    write_audio_analysis_sidecar,
)
from music_assistant.helpers.ffmpeg import get_ffmpeg_stream
from music_assistant.helpers.json import json_dumps, json_loads
from music_assistant.helpers.tags import async_parse_tags
from music_assistant.models.audio_analysis import AudioAnalysisData, AudioAnalysisError
from music_assistant.models.audio_analysis_provider import (
    AnalysisSessionData,
    AudioAnalysisProvider,
)
from music_assistant.providers.audio_analysis_versions import ANALYSIS_VERSIONS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from music_assistant.mass import MusicAssistant

SUPPORTED_ANALYSIS_DOMAINS = tuple(ANALYSIS_VERSIONS)
RESULT_PREFIX = "FLACCONVERTER_RESULT "
LOGGER = logging.getLogger("music_assistant.offline_audio_analysis")


@dataclass(slots=True)
class OfflineAnalysisResult:
    """Outcome of processing one FLAC file."""

    path: str
    status: str
    requested: list[str]
    reused: list[str]
    analyzed: list[str]
    errors: dict[str, str]
    elapsed_seconds: float


class _OfflineAnalysisController:
    """Minimal controller surface used by provider offload helpers."""

    def __init__(self) -> None:
        self.analysis_semaphore = None
        self.analysis_solo_lock = None
        self.analysis_executor = None

    @staticmethod
    def playback_active() -> bool:
        return False


class _OfflineMass:
    """Minimal MusicAssistant surface needed by provider computation code."""

    def __init__(self) -> None:
        self.cache = None
        self.streams = SimpleNamespace(audio_analysis=_OfflineAnalysisController())
        self.config = SimpleNamespace(get=lambda *_args, **_kwargs: {})

    @staticmethod
    def create_task(coro: Any, task_id: str | None = None) -> asyncio.Task[Any]:
        del task_id
        return asyncio.create_task(coro)


class OfflineAudioAnalysisRunner:
    """Run the server's providers against local FLAC files and update sidecars."""

    def __init__(self, domains: Sequence[str], device: str = "cpu") -> None:
        """Initialize an offline runner for the selected provider domains."""
        invalid = set(domains) - set(SUPPORTED_ANALYSIS_DOMAINS)
        if invalid:
            raise ValueError(f"unsupported audio-analysis providers: {', '.join(sorted(invalid))}")
        if device not in ("auto", "cpu", "cuda"):
            raise ValueError(f"unsupported analysis device: {device}")
        self.domains = tuple(dict.fromkeys(domains))
        self.device = device
        self._mass = _OfflineMass()
        self._providers: dict[str, AudioAnalysisProvider] = {}
        self._provider_locks = {domain: asyncio.Lock() for domain in self.domains}
        self._models_ready: set[str] = set()

    @property
    def provider_versions(self) -> dict[str, int]:
        """Return current algorithm versions without loading model assets."""
        return {domain: ANALYSIS_VERSIONS[domain] for domain in self.domains}

    async def analyze_file(
        self,
        audio_path: str | Path,
        *,
        force: bool = False,
        dry_run: bool = False,
    ) -> OfflineAnalysisResult:
        """
        Analyze one FLAC and atomically update its sidecar.

        :param audio_path: Path to the source FLAC.
        :param force: Recompute requested providers even when current results exist.
        :param dry_run: Report missing providers without loading models or writing a sidecar.
        """
        started = time.monotonic()
        path = Path(audio_path)
        versions = self.provider_versions
        reused: dict[str, tuple[int, AudioAnalysisData]] = {}
        sidecar_error: str | None = None
        if not force:
            try:
                reused = await asyncio.to_thread(read_audio_analysis_sidecar, path, versions)
            except FileNotFoundError:
                pass
            except AudioAnalysisSidecarError as err:
                sidecar_error = str(err)

        missing = [domain for domain in self.domains if domain not in reused]
        if not missing:
            return self._result(path, "REUSED", reused, {}, {}, started)
        if dry_run:
            errors = {"sidecar": sidecar_error} if sidecar_error else {}
            return self._result(path, "WOULD_ANALYZE", reused, {}, errors, started)

        analyzed, errors = await self._compute(path, missing)
        if analyzed:
            try:
                await asyncio.to_thread(write_audio_analysis_sidecar, path, analyzed)
            except Exception as err:
                errors["sidecar_write"] = str(err)
                analyzed = {}

        status = "ANALYZED" if not errors else ("PARTIAL" if analyzed else "ERROR")
        return self._result(path, status, reused, analyzed, errors, started)

    async def close(self) -> None:
        """Release provider model references."""
        for provider in self._providers.values():
            provider._free_models()
        self._providers.clear()
        self._models_ready.clear()

    async def _compute(  # noqa: PLR0915
        self, path: Path, domains: Sequence[str]
    ) -> tuple[dict[str, tuple[int, AudioAnalysisData]], dict[str, str]]:
        """Decode a FLAC once and fan its PCM out to the requested providers."""
        errors: dict[str, str] = {}
        try:
            tags = await async_parse_tags(str(path), path.stat().st_size, require_duration=True)
        except Exception as err:
            return {}, {"source": str(err)}
        if tags.format.lower() != "flac":
            return {}, {"source": f"expected FLAC, got {tags.format}"}
        if not tags.duration:
            return {}, {"source": "source duration is unavailable"}

        input_format = AudioFormat(
            content_type=ContentType.FLAC,
            codec_type=ContentType.FLAC,
            sample_rate=tags.sample_rate,
            bit_depth=tags.bits_per_sample,
            channels=tags.channels,
            bit_rate=tags.bit_rate,
        )
        pcm_format = AudioFormat(
            content_type=ContentType.from_bit_depth(tags.bits_per_sample),
            codec_type=ContentType.UNKNOWN,
            sample_rate=tags.sample_rate,
            bit_depth=tags.bits_per_sample,
            channels=tags.channels,
        )
        streamdetails = StreamDetails(
            provider="offline",
            item_id=str(path),
            audio_format=input_format,
            media_type=MediaType.TRACK,
            stream_type=StreamType.LOCAL_FILE,
            duration=int(tags.duration),
            size=path.stat().st_size,
            path=str(path),
            volume_normalization_mode=VolumeNormalizationMode.DYNAMIC,
        )
        session_id = f"offline:{path}"
        active: dict[str, AudioAnalysisProvider] = {}
        for domain in domains:
            try:
                provider = await self._get_ready_provider(domain)
            except Exception as err:
                errors[domain] = f"provider initialization failed: {err}"
                continue
            maximum = provider.max_analysis_duration
            if maximum is not None and tags.duration > maximum:
                errors[domain] = (
                    f"duration {tags.duration:.1f}s exceeds provider limit {maximum:.1f}s"
                )
                continue
            provider._sessions[session_id] = AnalysisSessionData(
                streamdetails=streamdetails,
                audio_format=pcm_format,
            )
            try:
                accepted = await provider._start_analysis(session_id, streamdetails, pcm_format)
            except Exception as err:
                provider._sessions.pop(session_id, None)
                errors[domain] = str(err)
                continue
            if accepted:
                active[domain] = provider
            else:
                provider._sessions.pop(session_id, None)
                errors[domain] = "provider declined the source"

        if active:
            bytes_per_second = (
                pcm_format.sample_rate * pcm_format.channels * (pcm_format.bit_depth // 8)
            )
            try:
                async for chunk in get_ffmpeg_stream(
                    str(path),
                    input_format,
                    pcm_format,
                    chunk_size=bytes_per_second,
                ):
                    for domain, provider in tuple(active.items()):
                        try:
                            await provider.process_pcm_chunk(session_id, chunk)
                        except Exception as err:
                            errors[domain] = str(err)
                            await provider.cancel(session_id)
                            active.pop(domain, None)
            except Exception as err:
                for domain, provider in active.items():
                    errors[domain] = f"source decode failed: {err}"
                    await provider.cancel(session_id)
                active.clear()

        analyzed: dict[str, tuple[int, AudioAnalysisData]] = {}
        for domain, provider in active.items():
            try:
                analysis = await provider._finalize(session_id)
            except AudioAnalysisError as err:
                errors[domain] = err.reason
            except Exception as err:
                errors[domain] = str(err)
            else:
                if analysis is None:
                    errors[domain] = "provider produced no result"
                else:
                    analyzed[domain] = (provider.analysis_version, analysis)
            finally:
                provider._sessions.pop(session_id, None)
        return analyzed, errors

    async def _get_ready_provider(self, domain: str) -> AudioAnalysisProvider:
        """Create a provider and load its model assets once."""
        async with self._provider_locks[domain]:
            if provider := self._providers.get(domain):
                if not provider.has_unloadable_models or domain in self._models_ready:
                    return provider
            else:
                provider_class = self._provider_class(domain)
                manifest = ProviderManifest(
                    type=ProviderType.AUDIO_ANALYSIS,
                    domain=domain,
                    name=domain,
                    description="Offline audio analysis",
                    codeowners=[],
                )
                config = ProviderConfig(
                    values={},
                    type=ProviderType.AUDIO_ANALYSIS,
                    domain=domain,
                    instance_id=f"offline_{domain}",
                )
                provider = provider_class(
                    cast("MusicAssistant", self._mass), manifest, config, set()
                )
                provider.available = True
                self._providers[domain] = provider

            if domain in ("smart_fades", "sonic_analysis"):
                provider._device = self._resolve_torch_device()  # type: ignore[attr-defined]
                LOGGER.info("Offline %s model device: %s", domain, provider._device)  # type: ignore[attr-defined]
            if provider.has_unloadable_models:
                await provider._load_models()
                provider._models_loaded = True
                self._models_ready.add(domain)
            return provider

    def _resolve_torch_device(self) -> str:
        """Resolve the requested torch device, retaining CPU as the safe default."""
        if self.device == "cpu":
            return "cpu"
        import torch  # noqa: PLC0415

        if torch.cuda.is_available():
            return "cuda"
        if self.device == "cuda":
            raise RuntimeError("CUDA was requested but is not available")
        return "cpu"

    @staticmethod
    def _provider_class(domain: str) -> type[AudioAnalysisProvider]:
        """Import and return one of the server's provider implementations."""
        if domain == "loudness_analysis":
            from music_assistant.providers.loudness_analysis.provider import (  # noqa: PLC0415
                LoudnessAnalysisProvider,
            )

            return LoudnessAnalysisProvider
        if domain == "smart_fades":
            from music_assistant.providers.smart_fades.provider import (  # noqa: PLC0415
                SmartFadesProvider,
            )

            return SmartFadesProvider
        if domain == "sonic_analysis":
            from music_assistant.providers.sonic_analysis import (  # noqa: PLC0415
                SonicAnalysisProvider,
            )

            return SonicAnalysisProvider
        raise ValueError(f"unsupported audio-analysis provider: {domain}")

    def _result(
        self,
        path: Path,
        status: str,
        reused: dict[str, tuple[int, AudioAnalysisData]],
        analyzed: dict[str, tuple[int, AudioAnalysisData]],
        errors: dict[str, str],
        started: float,
    ) -> OfflineAnalysisResult:
        """Build a serializable per-file result."""
        return OfflineAnalysisResult(
            path=str(path),
            status=status,
            requested=list(self.domains),
            reused=sorted(reused),
            analyzed=sorted(analyzed),
            errors=errors,
            elapsed_seconds=round(time.monotonic() - started, 3),
        )


async def _run_manifest(args: argparse.Namespace) -> int:
    """Process paths from a JSON manifest and emit prefixed JSON results."""
    payload = json_loads(args.manifest.read_bytes())
    if not isinstance(payload, dict) or not isinstance(payload.get("paths"), list):
        raise TypeError("manifest must contain a paths array")
    paths = [Path(path) for path in payload["paths"] if isinstance(path, str)]
    runner = OfflineAudioAnalysisRunner(args.providers, args.device)
    semaphore = asyncio.Semaphore(args.jobs)
    had_errors = False

    async def _process(index: int, path: Path) -> None:
        nonlocal had_errors
        async with semaphore:
            try:
                result = await runner.analyze_file(path, force=args.force, dry_run=args.dry_run)
            except Exception as err:
                result = OfflineAnalysisResult(
                    path=str(path),
                    status="ERROR",
                    requested=list(args.providers),
                    reused=[],
                    analyzed=[],
                    errors={"worker": str(err)},
                    elapsed_seconds=0.0,
                )
            if result.status in ("ERROR", "PARTIAL"):
                had_errors = True
            output = {"index": index, "total": len(paths), **asdict(result)}
            print(f"{RESULT_PREFIX}{json_dumps(output)}", flush=True)  # noqa: T201

    try:
        await asyncio.gather(*(_process(index, path) for index, path in enumerate(paths, 1)))
    finally:
        await runner.close()
    return 1 if had_errors else 0


def _parser() -> argparse.ArgumentParser:
    """Build the offline worker command-line parser."""
    parser = argparse.ArgumentParser(description="Run Music Assistant analysis on FLAC files")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--providers",
        nargs="+",
        choices=SUPPORTED_ANALYSIS_DOMAINS,
        default=list(SUPPORTED_ANALYSIS_DOMAINS),
    )
    parser.add_argument("--jobs", type=int, choices=range(1, 5), default=1)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="cpu")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the offline analysis worker."""
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    return asyncio.run(_run_manifest(args))


if __name__ == "__main__":
    raise SystemExit(main())
