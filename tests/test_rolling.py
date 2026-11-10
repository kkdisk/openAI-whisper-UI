"""R1 分段串流解碼與切點選擇；不載入模型，VAD 以替身或本機資產執行。"""

import wave

import av
import numpy as np
import pytest

from app.rolling import ENERGY_FRAME, SAMPLE_RATE, find_cut, iter_audio_blocks, iter_pcm
from app.schemas import AppError
from app.transcriber import decode_media

SR = SAMPLE_RATE


def no_speech(window):
    return []


def all_speech(window):
    return [{"start": 0, "end": len(window)}]


def write_wav(path, samples, rate=SR, channels=1):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.astype("<i2").tobytes())
    return path


def noise_wav(tmp_path, seconds, rate=SR, channels=1, name="noise.wav"):
    rng = np.random.default_rng(7)
    samples = rng.integers(-8000, 8000, size=int(seconds * rate) * channels)
    return write_wav(tmp_path / name, samples, rate, channels)


def joined(blocks):
    return np.concatenate([b.audio for b in blocks])


# --- 與整段解碼逐樣本一致 -------------------------------------------------


@pytest.mark.parametrize(("rate", "channels"), [(16000, 1), (44100, 2)])
@pytest.mark.parametrize("speech_fn", [no_speech, all_speech])
def test_blocks_match_whole_decode(tmp_path, rate, channels, speech_fn):
    path = noise_wav(tmp_path, 25.3, rate, channels)
    whole = decode_media(path, 60)
    blocks = list(iter_audio_blocks(path, 10, 3, 60, speech_fn=speech_fn))
    assert len(blocks) == 3
    assert np.array_equal(joined(blocks), whole)
    assert blocks[0].offset_samples == 0
    for previous, current in zip(blocks, blocks[1:], strict=False):
        assert current.offset_samples == previous.offset_samples + len(previous.audio)
    # 切點落在 [block - search, block]
    for b in blocks[:-1]:
        assert 7 * SR <= len(b.audio) <= 10 * SR
    assert blocks[-1].forced_cut is False


def test_compressed_media_matches_whole_decode(tmp_path):
    path = tmp_path / "tone.m4a"
    t = np.arange(int(23.7 * 44100)) / 44100
    pcm = (np.sin(2 * np.pi * 440 * t) * 8000).astype(np.int16)
    with av.open(str(path), "w") as container:
        stream = container.add_stream("aac", rate=44100)
        stream.layout = "mono"
        for start in range(0, len(pcm), 1024):
            frame = av.AudioFrame.from_ndarray(
                pcm[None, start : start + 1024], format="s16", layout="mono"
            )
            frame.sample_rate = 44100
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    whole = decode_media(path, 60)
    blocks = list(iter_audio_blocks(path, 8, 2, 60, speech_fn=no_speech))
    assert np.array_equal(joined(blocks), whole)


def test_iter_pcm_matches_whole_decode(tmp_path):
    path = noise_wav(tmp_path, 5.1, 44100, 2)
    assert np.array_equal(np.concatenate(list(iter_pcm(path, 60))), decode_media(path, 60))


# --- 切點選擇 -------------------------------------------------------------


def ranges(*pairs):
    return lambda window: [{"start": int(s * SR), "end": int(e * SR)} for s, e in pairs]


WINDOW = np.full(30 * SR, 0.1, dtype=np.float32)


@pytest.mark.parametrize("gap", [0.3, 0.5, 1.0])
@pytest.mark.parametrize("where", ["start", "middle", "end"])
def test_gap_found_at_each_position(gap, where):
    if where == "start":
        speech, expected = ranges((gap, 30)), (0, gap)
    elif where == "middle":
        speech, expected = ranges((0, 15), (15 + gap, 30)), (15, 15 + gap)
    else:
        speech, expected = ranges((0, 30 - gap)), (30 - gap, 30)
    cut = find_cut(WINDOW, 0.3, speech)
    assert cut.forced is False
    assert cut.index == pytest.approx((expected[0] + expected[1]) / 2 * SR, abs=1)


def test_longest_gap_wins():
    cut = find_cut(WINDOW, 0.3, ranges((0, 5), (5.4, 20), (21, 30)))
    assert cut.forced is False and cut.index == int(20.5 * SR)


def test_no_speech_cuts_middle():
    cut = find_cut(WINDOW, 0.3, no_speech)
    assert cut == type(cut)(index=15 * SR, forced=False)


def test_short_gap_is_forced_to_quietest_frame():
    window = WINDOW.copy()
    quiet = 12 * SR
    window[quiet : quiet + ENERGY_FRAME] = 0
    cut = find_cut(window, 0.3, ranges((0, 10), (10.2, 30)))
    assert cut.forced is True
    assert cut.index == quiet + ENERGY_FRAME // 2


def test_unsorted_and_out_of_range_speech_is_tolerated():
    def messy(window):
        return [{"start": 20 * SR, "end": 40 * SR}, {"start": -5, "end": 10 * SR}]

    cut = find_cut(WINDOW, 0.3, messy)
    assert cut.forced is False and cut.index == 15 * SR


def test_forced_cut_propagates_to_block(tmp_path):
    path = noise_wav(tmp_path, 25)
    blocks = list(iter_audio_blocks(path, 10, 3, 60, speech_fn=all_speech))
    assert [b.forced_cut for b in blocks] == [True, True, False]


def test_short_last_block(tmp_path):
    path = noise_wav(tmp_path, 20.25)
    blocks = list(iter_audio_blocks(path, 10, 0.5, 60, speech_fn=no_speech))
    assert sum(len(b.audio) for b in blocks) == int(20.25 * SR)
    assert 0 < len(blocks[-1].audio) < 2 * SR


def test_audio_shorter_than_block_is_single_block(tmp_path):
    path = noise_wav(tmp_path, 3)
    blocks = list(iter_audio_blocks(path, 10, 3, 60, speech_fn=no_speech))
    assert len(blocks) == 1 and len(blocks[0].audio) == 3 * SR


def test_invalid_search_window(tmp_path):
    with pytest.raises(ValueError):
        next(iter_audio_blocks(noise_wav(tmp_path, 1), 10, 10, 60))


# --- 與 decode_media 相同的錯誤 -------------------------------------------


def test_duration_limit_and_cancel(tmp_path):
    path = noise_wav(tmp_path, 3)
    with pytest.raises(AppError, match="時長") as info:
        list(iter_audio_blocks(path, 1, 0.5, 2, speech_fn=no_speech))
    assert info.value.code == "duration_limit"
    with pytest.raises(AppError, match="取消"):
        list(iter_audio_blocks(path, 1, 0.5, 60, lambda: True, speech_fn=no_speech))


def test_duration_limit_without_container_duration(tmp_path, monkeypatch):
    path = noise_wav(tmp_path, 3)
    original = av.open

    class Stream:
        duration = None

        def __init__(self, real):
            self.real, self.time_base = real, real.time_base

    class Container:
        def __init__(self, real):
            self.real = real
            self.streams = type("S", (), {"audio": [Stream(real.streams.audio[0])]})()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.real.close()

        def decode(self, stream):
            return self.real.decode(stream.real)

    monkeypatch.setattr(av, "open", lambda *a, **k: Container(original(*a, **k)))
    with pytest.raises(AppError) as info:
        list(iter_pcm(path, 2))
    assert info.value.code == "duration_limit"
    # 未超限時仍完整產出
    assert sum(a.size for a in iter_pcm(path, 60)) == 3 * SR


def test_bad_and_empty_media(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"invalid media")
    with pytest.raises(AppError) as info:
        list(iter_pcm(bad, 60))
    assert info.value.code == "decode_failed"
    empty = write_wav(tmp_path / "empty.wav", np.zeros(0, dtype=np.int16))
    with pytest.raises(AppError) as info:
        list(iter_pcm(empty, 60))
    assert info.value.code == decode_error_code(empty)


def decode_error_code(path):
    try:
        decode_media(path, 60)
    except AppError as exc:
        return exc.code
    raise AssertionError("decode_media 應拒絕空音訊")


# --- 真實 Silero：邊界參數與辨識預設對照 ---------------------------------


def test_boundary_vad_finds_gaps_default_vad_merges(tmp_path):
    """以本機 VAD 資產比較；說話以合成機率替代無法保證，故只驗證參數傳遞的差異。"""
    pytest.importorskip("faster_whisper")
    from faster_whisper import vad

    probs_per_chunk = 512
    window_chunks = 30 * SR // probs_per_chunk
    probs = np.full(window_chunks, 0.9, dtype=np.float32)
    gap_start = window_chunks // 2
    gap_chunks = int(0.5 * SR / probs_per_chunk)
    probs[gap_start : gap_start + gap_chunks] = 0.05

    class FakeModel:
        def __call__(self, audio):
            return probs[: len(audio) // probs_per_chunk]

    original = vad.get_vad_model
    vad.get_vad_model = lambda: FakeModel()
    try:
        from app.rolling import BOUNDARY_VAD, silero_speech

        window = np.zeros(30 * SR, dtype=np.float32)
        boundary = find_cut(window, 0.3, lambda w: silero_speech(w, BOUNDARY_VAD))
        default = find_cut(window, 0.3, lambda w: silero_speech(w, {}))
    finally:
        vad.get_vad_model = original
    assert boundary.forced is False
    assert abs(boundary.index - (gap_start + gap_chunks / 2) * probs_per_chunk) < SR * 0.1
    assert default.forced is True
