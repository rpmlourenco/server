"""Regression checks for standalone provider imports."""

import subprocess
import sys


def test_offline_imports_do_not_load_server_controllers() -> None:
    """Import all offline providers without starting or importing server controllers."""
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from music_assistant.helpers.offline_audio_analysis import OfflineAudioAnalysisRunner
for domain in ('loudness_analysis', 'smart_fades', 'sonic_analysis'):
    OfflineAudioAnalysisRunner._provider_class(domain)
assert not any(name.startswith('music_assistant.controllers') for name in sys.modules)
from music_assistant.models import AudioAnalysisProvider
from music_assistant.models.audio_analysis_provider import AudioAnalysisProvider as Direct
assert AudioAnalysisProvider is Direct
""",
        ],
        check=True,
    )
