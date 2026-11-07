"""媒體解碼與禁止連網的真實模型測試。"""

import os
import wave
from pathlib import Path

import av
import numpy as np
import pytest

from app.config import ROOT, Settings
from app.models import model_path
from app.schemas import AppError, JobRequest
from app.transcriber import decode_media, transcribe, transcription_options


def test_silent_wav_and_duration_limit(tmp_path):
    path = tmp_path / "silence.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * 32000)
    audio = decode_media(path, 3)
    assert len(audio) == 32000 and audio.dtype == np.float32
    with pytest.raises(AppError, match="時長"):
        decode_media(path, 1)
    with pytest.raises(AppError, match="取消"):
        decode_media(path, 3, lambda: True)


def test_missing_model_stays_local(tmp_path):
    with pytest.raises(AppError, match="不完整"):
        model_path(Settings(root=tmp_path), "small")
    with pytest.raises(AppError, match="允許清單"):
        model_path(Settings(root=tmp_path), "https://example.test/model")


def test_bad_file(tmp_path):
    path = tmp_path / "bad.mp4"
    path.write_bytes(b"invalid media")
    with pytest.raises(AppError, match="無法解碼"):
        decode_media(path, 120)


def test_video_without_audio(tmp_path):
    path = tmp_path / "no-audio.mp4"
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=1)
        stream.width, stream.height = 16, 16
        frame = av.VideoFrame.from_ndarray(np.zeros((16, 16, 3), dtype=np.uint8), format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    with pytest.raises(AppError, match="沒有"):
        decode_media(path, 120)


def test_prompt_limit_before_decode(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import faster_whisper

    monkeypatch.setattr("app.transcriber.model_path", lambda *args: tmp_path)
    fake = SimpleNamespace(
        hf_tokenizer=SimpleNamespace(encode=lambda text: SimpleNamespace(ids=[1] * 201))
    )
    monkeypatch.setattr(faster_whisper, "WhisperModel", lambda *args, **kwargs: fake)
    request = JobRequest(source_name="meeting.mp4", expected_bytes=1)
    with pytest.raises(AppError, match="200 tokens"):
        transcribe(tmp_path / "absent.mp4", request, Settings(), lambda *args: None, lambda: False)


@pytest.mark.parametrize(
    ("language", "expected_language", "multilingual"),
    [("zh", "zh", False), ("en", "en", False), ("auto", None, False), ("mixed", None, True)],
)
def test_language_and_context_reach_engine(
    tmp_path, monkeypatch, language, expected_language, multilingual
):
    from types import SimpleNamespace

    import faster_whisper

    (tmp_path / "ready.json").write_text('{"revision":"test-revision"}', encoding="utf-8")
    monkeypatch.setattr("app.transcriber.model_path", lambda *args: tmp_path)
    monkeypatch.setattr("app.transcriber.decode_media", lambda *args: np.zeros(16000, np.float32))
    calls = []

    def recognize(audio, **kwargs):
        calls.append(kwargs)
        return iter([SimpleNamespace(start=0, end=1, text=" API GLT-123 ")]), None

    fake = SimpleNamespace(
        hf_tokenizer=SimpleNamespace(encode=lambda text: SimpleNamespace(ids=[1])),
        transcribe=recognize,
    )
    monkeypatch.setattr(faster_whisper, "WhisperModel", lambda *args, **kwargs: fake)
    request = JobRequest(
        source_name="meeting.wav",
        expected_bytes=3,
        language=language,
        terms="GLT-123",
        condition_on_previous_text=False,
    )
    events = []

    class Control:
        checkpoints = 0

        def __call__(self):
            return False

        def checkpoint(self):
            self.checkpoints += 1
            return True

    control = Control()
    transcribe(
        tmp_path / "meeting.wav",
        request,
        Settings(),
        lambda *event: events.append(event),
        control,
    )
    assert calls[0]["language"] == expected_language
    assert calls[0]["multilingual"] is multilingual
    assert calls[0]["condition_on_previous_text"] is False
    assert calls[0]["initial_prompt"].endswith("GLT-123")
    assert next(data for kind, data in events if kind == "segment")["raw_text"] == "API GLT-123"
    assert control.checkpoints == 2  # 包含最後一次取值／StopIteration 前的閘門。


def test_legacy_request_keeps_previous_defaults():
    request = JobRequest.model_validate({"source_name": "meeting.wav", "expected_bytes": 3})
    options = transcription_options(request)
    assert options["condition_on_previous_text"] is True
    assert options["multilingual"] is False and options["language"] == "zh"


def encode_aac(source, destination):
    """使用 PyAV 將本機語音轉成 MP4 AAC，無全域 FFmpeg。"""
    with av.open(str(source)) as inp, av.open(str(destination), "w") as out:
        stream = out.add_stream("aac", rate=16000)
        stream.layout = "mono"
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
        for frame in inp.decode(audio=0):
            for converted in resampler.resample(frame):
                for packet in stream.encode(converted):
                    out.mux(packet)
        for converted in resampler.resample(None):
            for packet in stream.encode(converted):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)


@pytest.mark.real_model
def test_real_offline_mp4(tmp_path, monkeypatch):
    source = Path(os.environ.get("WHISPER_TEST_AUDIO", ROOT / "temp" / "fixtures" / "meeting.wav"))
    if not source.is_file():
        pytest.skip("缺少本機語音素材，請設定 WHISPER_TEST_AUDIO")
    try:
        model_path(Settings(), "small")
    except AppError:
        pytest.skip("缺少已準備 small 模型")
    path = tmp_path / "meeting.mp4"
    encode_aac(source, path)
    monkeypatch.setenv("PATH", "")
    events = []
    request = JobRequest(source_name="meeting.mp4", expected_bytes=path.stat().st_size)
    transcribe(
        path,
        request,
        Settings(),
        lambda kind, payload: events.append((kind, payload)),
        lambda: False,
    )
    segments = [payload for kind, payload in events if kind == "segment"]
    assert segments, "有語音 fixture 應產出非空文字"
    assert "raw_text" in segments[0]
    # 合成語音只驗證核心路徑，不作真實會議辨識品質的接受依據。
    (ROOT / "temp" / "real-model-result.json").write_text(
        __import__("json").dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
    )
