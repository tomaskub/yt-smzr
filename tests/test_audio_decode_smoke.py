"""Exercise the installed decoder without models, providers, or network access."""

import importlib
import math
import struct
import wave
from collections.abc import Callable
from pathlib import Path
from typing import cast

import numpy as np
import numpy.typing as npt


def test_installed_faster_whisper_decodes_local_audio(tmp_path: Path) -> None:
    """Catch incompatible PyAV APIs in the real faster-whisper decode path."""
    sample_rate = 16_000
    samples = [
        round(8_192 * math.sin(2 * math.pi * 440 * index / sample_rate))
        for index in range(sample_rate // 10)
    ]
    pcm = struct.pack(f"<{len(samples)}h", *samples)
    audio_path = tmp_path / "tone.wav"
    with wave.open(str(audio_path), "wb") as fixture:
        fixture.setnchannels(1)
        fixture.setsampwidth(2)
        fixture.setframerate(sample_rate)
        fixture.writeframes(pcm)

    # The dependency has no type annotations for decode_audio's return value.
    decode_audio = cast(
        Callable[[str], npt.NDArray[np.float32]],
        importlib.import_module("faster_whisper.audio").decode_audio,
    )
    decoded = decode_audio(str(audio_path))

    assert decoded.dtype == np.float32
    assert decoded.shape == (len(samples),)
    expected = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32_768
    np.testing.assert_array_equal(decoded, expected)
