"""Tests for precomputed audio-analysis sidecars."""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from music_assistant.helpers.audio_analysis_sidecar import (
    AudioAnalysisSidecarError,
    get_flac_source_identity,
    get_sidecar_path,
    read_audio_analysis_sidecar,
    write_audio_analysis_sidecar,
)
from music_assistant.models.audio_analysis import AudioAnalysisData


def _write_test_flac(path: Path, audio_md5: bytes = bytes.fromhex("11" * 16)) -> None:
    """Write the FLAC header and STREAMINFO fields used by sidecar identity checks."""
    sample_rate = 44_100
    total_samples = sample_rate * 10
    packed = (sample_rate << 44) | (1 << 41) | (15 << 36) | total_samples
    streaminfo = bytes(10) + packed.to_bytes(8, "big") + audio_md5
    path.write_bytes(b"fLaC" + b"\x80" + len(streaminfo).to_bytes(3, "big") + streaminfo)


def test_sidecar_round_trip_and_incremental_update(tmp_path: Path) -> None:
    """Provider updates preserve the other valid results in the same sidecar."""
    audio_path = tmp_path / "track.flac"
    _write_test_flac(audio_path)

    sidecar_path = write_audio_analysis_sidecar(
        audio_path,
        {"loudness_analysis": (2, AudioAnalysisData(loudness_integrated=-12.5))},
    )
    write_audio_analysis_sidecar(
        audio_path,
        {"smart_fades": (3, AudioAnalysisData(bpm=92.4, beats=[0.5, 1.0]))},
    )

    assert sidecar_path == tmp_path / "track.lda"
    result = read_audio_analysis_sidecar(audio_path, {"loudness_analysis": 2, "smart_fades": 3})
    assert result["loudness_analysis"][1].loudness_integrated == -12.5
    assert result["smart_fades"][1].beats == [0.5, 1.0]


def test_sidecar_ignores_independently_stale_and_empty_results(tmp_path: Path) -> None:
    """A stale or empty provider entry does not invalidate another provider's result."""
    audio_path = tmp_path / "track.flac"
    _write_test_flac(audio_path)
    write_audio_analysis_sidecar(
        audio_path,
        {
            "loudness_analysis": (1, AudioAnalysisData(loudness_integrated=-10.0)),
            "smart_fades": (3, AudioAnalysisData()),
        },
    )

    result = read_audio_analysis_sidecar(audio_path, {"loudness_analysis": 2, "smart_fades": 3})

    assert result == {}


def test_sidecar_survives_changes_outside_streaminfo(tmp_path: Path) -> None:
    """Changes outside STREAMINFO, such as tag edits, do not change audio identity."""
    audio_path = tmp_path / "track.flac"
    _write_test_flac(audio_path)
    identity = get_flac_source_identity(audio_path)
    with audio_path.open("ab") as audio_file:
        audio_file.write(b"changed metadata or frames not read by the identity helper")

    assert get_flac_source_identity(audio_path) == identity


def test_sidecar_rejects_different_audio(tmp_path: Path) -> None:
    """A sidecar is rejected after the FLAC audio MD5 changes."""
    audio_path = tmp_path / "track.flac"
    _write_test_flac(audio_path)
    write_audio_analysis_sidecar(
        audio_path,
        {"loudness_analysis": (2, AudioAnalysisData(loudness_integrated=-12.5))},
    )
    _write_test_flac(audio_path, bytes.fromhex("22" * 16))

    with pytest.raises(AudioAnalysisSidecarError, match="fingerprint"):
        read_audio_analysis_sidecar(audio_path, {"loudness_analysis": 2})


def test_sidecar_rejects_missing_flac_md5(tmp_path: Path) -> None:
    """A source without a strong tag-independent identity is not trusted."""
    audio_path = tmp_path / "track.flac"
    _write_test_flac(audio_path, bytes(16))

    with pytest.raises(AudioAnalysisSidecarError, match="no audio MD5"):
        get_flac_source_identity(audio_path)


def test_sidecar_rejects_invalid_gzip(tmp_path: Path) -> None:
    """An uncompressed or corrupt .lda payload fails closed."""
    audio_path = tmp_path / "track.flac"
    _write_test_flac(audio_path)
    get_sidecar_path(audio_path).write_text("not gzip", encoding="utf-8")

    with pytest.raises(AudioAnalysisSidecarError, match="unable to read sidecar"):
        read_audio_analysis_sidecar(audio_path, {"loudness_analysis": 2})


def test_sidecar_is_gzip_json(tmp_path: Path) -> None:
    """The .lda wire format is readable as gzip-compressed JSON."""
    audio_path = tmp_path / "track.flac"
    _write_test_flac(audio_path)
    sidecar_path = write_audio_analysis_sidecar(
        audio_path,
        {"loudness_analysis": (2, AudioAnalysisData(loudness_integrated=-12.5))},
    )

    with gzip.open(sidecar_path, "rt", encoding="utf-8") as sidecar_file:
        contents = sidecar_file.read()
    assert '"format":"music-assistant-lda"' in contents
