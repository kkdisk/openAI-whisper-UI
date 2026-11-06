"""確認比較工具變速後仍維持音高，且原速不改動樣本。"""

import numpy as np
import pytest

from scripts.benchmark_speed import change_tempo


@pytest.mark.parametrize("speed", [0.9, 0.85])
def test_tempo_duration_and_pitch(speed):
    sample_rate = 16000
    positions = np.arange(sample_rate * 3) / sample_rate
    audio = (0.2 * np.sin(2 * np.pi * 440 * positions)).astype(np.float32)
    result = change_tempo(audio, speed)
    assert abs(len(result) / sample_rate - 3 / speed) < 0.1
    # 改採樣率會讓音高變成 440 * speed；atempo 應維持 440 Hz。
    spectrum = np.abs(np.fft.rfft(result * np.hanning(len(result))))
    peak_hz = np.fft.rfftfreq(len(result), 1 / sample_rate)[spectrum.argmax()]
    assert abs(peak_hz - 440) < 2
    assert np.isfinite(result).all()


def test_original_speed_keeps_samples():
    audio = np.array([0, 0.1, -0.2, 0.3], dtype=np.float32)
    assert np.array_equal(change_tempo(audio, 1), audio)
    with pytest.raises(ValueError):
        change_tempo(audio, 0)
