"""Exercise real lazy faster-whisper progress setup in an isolated Textual worker."""

import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize("lock_state", ["fresh", "initialized"])
def test_textual_worker_consumes_whisper_segments_without_progress(
    lock_state: str,
) -> None:
    # A subprocess prevents earlier downloads/tests from initializing tqdm's lock.
    script = textwrap.dedent(
        """
        import asyncio
        import multiprocessing
        import sys
        import threading
        from multiprocessing.resource_tracker import _resource_tracker
        from pathlib import Path
        from types import SimpleNamespace

        import faster_whisper
        import numpy as np
        from textual.app import App
        from tqdm import tqdm
        from yt_smzr.transcription.faster_whisper import FasterWhisperTranscriber

        # Use spawn on every platform to exercise macOS's resource-tracker path.
        multiprocessing.set_start_method("spawn")
        assert not hasattr(tqdm, "_lock"), "tqdm lock initialized before app startup"
        prior_lock = threading.RLock() if sys.argv[1] == "initialized" else None
        if prior_lock is not None:
            tqdm.set_lock(prior_lock)
        locks = []

        class OfflineModel(faster_whisper.WhisperModel):
            def __init__(self, model, *, device, compute_type):
                assert device == "cpu" and compute_type == "int8"
                self.feature_extractor = SimpleNamespace(time_per_frame=0.02)
                self.frames_per_second = 50

            def transcribe(self, audio, *, language, log_progress=False):
                assert language == "en"
                assert log_progress is False
                return self.segments(log_progress), object()

            def segments(self, log_progress):
                # Real generate_segments creates tqdm even for an empty clip.
                # No decoding/model inference is needed to reach that code path.
                yield from self.generate_segments(
                    np.zeros((80, 1)), None,
                    SimpleNamespace(clip_timestamps=[0.0], initial_prompt=None),
                    log_progress,
                )
                locks.append(tqdm.get_lock())
                yield SimpleNamespace(start=0.25, end=1.5, text=" hello ")

        faster_whisper.WhisperModel = OfflineModel

        class TranscriptionApp(App):
            def transcribe(self):
                assert sys.stderr.fileno() == -1
                return FasterWhisperTranscriber().transcribe(Path("unused.opus"))

        async def main():
            app = TranscriptionApp()
            async with app.run_test():
                workers = [
                    app.run_worker(app.transcribe, thread=True) for _ in range(2)
                ]
                for transcript in await asyncio.gather(*(w.wait() for w in workers)):
                    assert transcript.segments[0].text == "hello"
                    assert transcript.segments[0].start_seconds == 0.25
                assert locks[0] is locks[1] is tqdm.get_lock()
                if prior_lock is not None:
                    assert tqdm.get_lock() is prior_lock
                assert _resource_tracker._pid is None

        asyncio.run(main())
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script, lock_state],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
