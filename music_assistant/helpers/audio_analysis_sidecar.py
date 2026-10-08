"""Read and write precomputed audio-analysis sidecars."""

from __future__ import annotations

import gzip
import os
import tempfile
from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path
from typing import Any, BinaryIO

from music_assistant.helpers.json import json_dumps, json_loads
from music_assistant.models.audio_analysis import AudioAnalysisData

SIDECAR_FORMAT = "music-assistant-lda"
SIDECAR_SCHEMA_VERSION = 1
SIDECAR_SUFFIX = ".lda"
FLAC_FINGERPRINT_PREFIX = "flac-md5:"
MAX_SIDECAR_UNCOMPRESSED_BYTES = 64 * 1024 * 1024


class AudioAnalysisSidecarError(ValueError):
    """Raised when an audio-analysis sidecar cannot be trusted or parsed."""


def get_sidecar_path(audio_path: str | Path) -> Path:
    """
    Return the sidecar path for a supported audio file.

    :param audio_path: Path to the source FLAC file.
    :raises AudioAnalysisSidecarError: When the source is not a FLAC file.
    """
    path = Path(audio_path)
    if path.suffix.lower() != ".flac":
        raise AudioAnalysisSidecarError("analysis sidecars are only supported for FLAC files")
    return path.with_suffix(SIDECAR_SUFFIX)


def get_flac_source_identity(audio_path: str | Path) -> dict[str, str | float]:
    """
    Return a tag-independent identity from a FLAC STREAMINFO block.

    :param audio_path: Path to the source FLAC file.
    :raises AudioAnalysisSidecarError: When STREAMINFO is invalid or has no audio MD5.
    """
    path = Path(audio_path)
    try:
        with path.open("rb") as audio_file:
            streaminfo = _read_flac_streaminfo(audio_file)
    except OSError as err:
        raise AudioAnalysisSidecarError(f"unable to read source FLAC: {err}") from err

    packed = int.from_bytes(streaminfo[10:18], "big")
    sample_rate = packed >> 44
    total_samples = packed & ((1 << 36) - 1)
    audio_md5 = streaminfo[18:34]
    if sample_rate <= 0 or total_samples <= 0:
        raise AudioAnalysisSidecarError("FLAC STREAMINFO has no usable duration")
    if not any(audio_md5):
        raise AudioAnalysisSidecarError("FLAC STREAMINFO has no audio MD5")
    return {
        "audio_fingerprint": f"{FLAC_FINGERPRINT_PREFIX}{audio_md5.hex()}",
        "duration": total_samples / sample_rate,
    }


def read_audio_analysis_sidecar(
    audio_path: str | Path,
    expected_versions: Mapping[str, int],
) -> dict[str, tuple[int, AudioAnalysisData]]:
    """
    Read current provider results from the sidecar belonging to a FLAC file.

    Only requested providers whose algorithm version exactly matches are returned.

    :param audio_path: Path to the source FLAC file.
    :param expected_versions: Provider domains and algorithm versions to import.
    :raises FileNotFoundError: When the sidecar does not exist.
    :raises AudioAnalysisSidecarError: When the sidecar is invalid or belongs to other audio.
    """
    sidecar_path = get_sidecar_path(audio_path)
    payload = _read_payload(sidecar_path)
    source = get_flac_source_identity(audio_path)
    if payload["source"].get("audio_fingerprint") != source["audio_fingerprint"]:
        raise AudioAnalysisSidecarError("sidecar audio fingerprint does not match source FLAC")

    result: dict[str, tuple[int, AudioAnalysisData]] = {}
    analyses = payload["analyses"]
    for domain, expected_version in expected_versions.items():
        entry = analyses.get(domain)
        if not isinstance(entry, dict) or entry.get("version") != expected_version:
            continue
        data = entry.get("data")
        if not isinstance(data, dict):
            continue
        try:
            analysis = AudioAnalysisData.from_dict(data)
        except TypeError, ValueError:
            continue
        if not any(getattr(analysis, field.name) is not None for field in fields(analysis)):
            continue
        result[domain] = (expected_version, analysis)
    return result


def write_audio_analysis_sidecar(
    audio_path: str | Path,
    analyses: Mapping[str, tuple[int, AudioAnalysisData]],
) -> Path:
    """
    Atomically add or replace provider results in a FLAC sidecar.

    Existing provider results are preserved when the sidecar belongs to the same audio.

    :param audio_path: Path to the source FLAC file.
    :param analyses: Provider domains mapped to algorithm version and complete result.
    :return: Path of the written sidecar.
    """
    sidecar_path = get_sidecar_path(audio_path)
    source = get_flac_source_identity(audio_path)
    stored_analyses: dict[str, Any] = {}
    try:
        existing = _read_payload(sidecar_path)
    except FileNotFoundError, AudioAnalysisSidecarError:
        pass
    else:
        if existing["source"].get("audio_fingerprint") == source["audio_fingerprint"]:
            stored_analyses.update(existing["analyses"])

    for domain, (version, analysis) in analyses.items():
        if not domain or isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise AudioAnalysisSidecarError("provider domain and version must be valid")
        stored_analyses[domain] = {"version": version, "data": analysis.to_dict()}

    payload = {
        "format": SIDECAR_FORMAT,
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "source": source,
        "analyses": stored_analyses,
    }
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{sidecar_path.name}.", suffix=".tmp", dir=sidecar_path.parent
    )
    os.close(fd)
    temporary_path = Path(temporary_name)
    try:
        with gzip.open(temporary_path, "wt", encoding="utf-8", newline="") as sidecar_file:
            sidecar_file.write(json_dumps(payload))
        temporary_path.replace(sidecar_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return sidecar_path


def _read_payload(sidecar_path: Path) -> dict[str, Any]:
    """Read and validate the common sidecar envelope."""
    try:
        with gzip.open(sidecar_path, "rb") as sidecar_file:
            raw_payload = sidecar_file.read(MAX_SIDECAR_UNCOMPRESSED_BYTES + 1)
        if len(raw_payload) > MAX_SIDECAR_UNCOMPRESSED_BYTES:
            raise AudioAnalysisSidecarError("sidecar exceeds the maximum uncompressed size")
        payload = json_loads(raw_payload)
    except FileNotFoundError:
        raise
    except (OSError, TypeError, ValueError) as err:
        raise AudioAnalysisSidecarError(f"unable to read sidecar: {type(err).__name__}") from err

    if not isinstance(payload, dict):
        raise AudioAnalysisSidecarError("sidecar root must be an object")
    if payload.get("format") != SIDECAR_FORMAT:
        raise AudioAnalysisSidecarError("unsupported sidecar format")
    if payload.get("schema_version") != SIDECAR_SCHEMA_VERSION:
        raise AudioAnalysisSidecarError("unsupported sidecar schema version")
    if not isinstance(payload.get("source"), dict):
        raise AudioAnalysisSidecarError("sidecar source must be an object")
    if not isinstance(payload.get("analyses"), dict):
        raise AudioAnalysisSidecarError("sidecar analyses must be an object")
    return payload


def _read_flac_streaminfo(audio_file: BinaryIO) -> bytes:
    """Read the mandatory first FLAC metadata block."""
    if audio_file.read(4) != b"fLaC":
        raise AudioAnalysisSidecarError("source is not a native FLAC file")
    header = audio_file.read(4)
    if len(header) != 4 or header[0] & 0x7F != 0:
        raise AudioAnalysisSidecarError("FLAC has no leading STREAMINFO block")
    block_length = int.from_bytes(header[1:4], "big")
    if block_length != 34:
        raise AudioAnalysisSidecarError("FLAC STREAMINFO has an invalid length")
    streaminfo = audio_file.read(block_length)
    if len(streaminfo) != block_length:
        raise AudioAnalysisSidecarError("FLAC STREAMINFO is truncated")
    return streaminfo
