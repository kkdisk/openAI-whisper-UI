"""R2：分段辨識接入正式流程；模型以替身取代，VAD 使用本機資產。"""

import json
import wave
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.formatter import export
from app.jobs import JobManager
from app.main import create_app
from app.schemas import AppError, JobMeta, JobRequest, Status
from app.transcriber import transcribe
from tests.helpers import rolling_events_transcribe, wait_terminal

SR = 16000


def wav(path, seconds):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(np.zeros(int(SR * seconds), dtype="<i2").tobytes())
    return path


def rolling(**overrides):
    values = {"segmentation": "rolling", "block_sec": 20, "boundary_search_sec": 5}
    return Settings(**{**values, **overrides})


class Engine:
    """每 5 秒一段，並在每塊末尾多吐一段越過塊尾的片段。"""

    def __init__(self, speech=None):
        self.calls = []
        self.speech = speech or (lambda index, length: length)
        self.hf_tokenizer = SimpleNamespace(
            encode=lambda text, **kw: SimpleNamespace(ids=[ord(c) for c in text])
        )

    def transcribe(self, audio, **options):
        index = len(self.calls)
        self.calls.append(options)
        length = len(audio) / SR
        rows = [
            SimpleNamespace(start=s, end=s + 2, text=f" 第{index}塊{int(s)}秒")
            for s in np.arange(0, length - 2, 5)
        ]
        rows.append(SimpleNamespace(start=length + 1, end=length + 3, text=" 越界"))
        info = SimpleNamespace(language="zh", duration_after_vad=self.speech(index, length))
        return iter(rows), info


@pytest.fixture
def engine(tmp_path, monkeypatch):
    import faster_whisper

    (tmp_path / "ready.json").write_text('{"revision":"r"}', encoding="utf-8")
    monkeypatch.setattr("app.transcriber.model_path", lambda *args: tmp_path)
    holder = {"engine": Engine()}
    monkeypatch.setattr(faster_whisper, "WhisperModel", lambda *a, **k: holder["engine"])
    return holder


class Control:
    def __init__(self, cancel_after_calls=None, engine=None):
        self.cancel_after_calls, self.engine, self.checkpoints = cancel_after_calls, engine, 0

    def __call__(self):
        if self.cancel_after_calls is None:
            return False
        return len(self.engine.calls) >= self.cancel_after_calls

    def checkpoint(self):
        self.checkpoints += 1
        return not self()


def run(tmp_path, settings, seconds=50, resume=None, control=None, **request):
    events = []
    audio = wav(tmp_path / "a.wav", seconds)
    transcribe(
        audio,
        JobRequest(source_name="a.wav", expected_bytes=3, **request),
        settings,
        lambda *event: events.append(event),
        control or Control(),
        resume,
    )
    return events


def of(events, kind):
    return [data for k, data in events if k == kind]


def test_rolling_blocks_offsets_ids_and_prompts(tmp_path, engine):
    events = run(tmp_path, rolling(), language="auto", terms="GLT-123")
    calls = engine["engine"].calls
    statuses = [d["status"] for d in of(events, "status")]
    assert statuses == ["loading_model", "decoding", "transcribing"]
    blocks = of(events, "block")
    assert [b["index"] for b in blocks] == [0, 1, 2] and len(calls) == 3
    assert blocks[0]["offset_sec"] == 0
    offsets = [b["offset_sec"] for b in blocks]
    segments = of(events, "segment")
    assert [s["segment_id"] for s in segments] == list(range(1, len(segments) + 1))
    assert all(s["start_sec"] <= s["end_sec"] for s in segments)
    # 非最後一塊的越界片段成為下一塊起點的零長度片段，並送出 warning
    edges = [s for s in segments if s["raw_text"] == "越界"]
    assert [e["start_sec"] for e in edges[:2]] == pytest.approx(offsets[1:])
    assert edges[-1]["end_sec"] > 50  # 最後一塊不裁切
    assert len([w for w in of(events, "warning") if "timestamp_adjusted" in w["code"]]) == 2
    # 第二塊的片段時間換算為原檔時間
    second = [s for s in segments if s["raw_text"].startswith("第1塊")]
    assert second[0]["start_sec"] == pytest.approx(offsets[1])
    # auto：首塊有語音即鎖定；第二塊起帶上一塊尾端的 token 提示
    assert calls[0]["language"] is None and calls[1]["language"] == "zh"
    assert blocks[0]["locked_language"] == "zh" and blocks[1]["locked_language"] is None
    assert isinstance(calls[0]["initial_prompt"], str)
    prompt = "".join(map(chr, calls[1]["initial_prompt"]))
    assert "GLT-123" in prompt and prompt.endswith("越界")
    info = of(events, "info")
    assert info[0]["duration_sec"] == pytest.approx(50) and info[-1]["duration_sec"] == 50


def test_language_waits_for_speech_block(tmp_path, engine):
    engine["engine"] = Engine(speech=lambda index, length: 0.0 if index == 0 else length)
    events = run(tmp_path, rolling(), language="auto")
    calls = engine["engine"].calls
    assert [c["language"] for c in calls] == [None, None, "zh"]
    assert [b["locked_language"] for b in of(events, "block")] == [None, "zh", None]


@pytest.mark.parametrize(("language", "expected"), [("zh", "zh"), ("mixed", None)])
def test_fixed_and_mixed_language(tmp_path, engine, language, expected):
    run(tmp_path, rolling(), language=language)
    calls = engine["engine"].calls
    assert {c["language"] for c in calls} == {expected}
    assert all(c["multilingual"] is (language == "mixed") for c in calls)


def test_no_previous_text_keeps_plain_prompt(tmp_path, engine):
    run(tmp_path, rolling(), condition_on_previous_text=False)
    assert all(isinstance(c["initial_prompt"], str) for c in engine["engine"].calls)


def test_rolling_resume_starts_at_saved_position(tmp_path, engine):
    resume = {
        "from_sec": 25.0,
        "first_segment_id": 7,
        "context_text": "舊稿結尾",
        "expected_duration_sec": 50.0,
    }
    events = run(tmp_path, rolling(), resume=resume)
    blocks = of(events, "block")
    assert blocks[0]["offset_sec"] == 25.0
    assert sum(b["duration_sec"] for b in blocks) == pytest.approx(25.0)
    segments = of(events, "segment")
    assert segments[0]["segment_id"] == 7 and segments[0]["start_sec"] == 25.0
    first_prompt = "".join(map(chr, engine["engine"].calls[0]["initial_prompt"]))
    assert first_prompt.endswith("舊稿結尾")


def test_rolling_resume_rejects_different_duration(tmp_path, engine):
    resume = {"from_sec": 5.0, "first_segment_id": 2, "expected_duration_sec": 40.0}
    with pytest.raises(AppError) as info:
        run(tmp_path, rolling(), resume=resume)
    assert info.value.code == "source_mismatch"


def test_cancel_between_blocks_stops_inference(tmp_path, engine):
    control = Control(cancel_after_calls=1, engine=engine["engine"])
    events = run(tmp_path, rolling(), control=control)
    assert len(engine["engine"].calls) == 1
    assert of(events, "block") == []  # 第一塊推論後即取消，不送出完成紀錄


def test_pause_checkpoint_between_blocks(tmp_path, engine):
    control = Control()
    events = run(tmp_path, rolling(), control=control)
    segments = len(of(events, "segment"))
    # controlled_segments 每取一段前檢查一次，每塊結束再檢查一次；另含兩個區塊間閘門
    assert control.checkpoints == segments + 3 + 2


def test_rolling_duration_limit(tmp_path, engine):
    with pytest.raises(AppError) as info:
        run(tmp_path, rolling(rolling_max_duration_sec=30))
    assert info.value.code == "duration_limit"
    assert engine["engine"].calls == []


def test_whole_mode_unchanged(tmp_path, engine, monkeypatch):
    monkeypatch.setattr(
        "app.transcriber.decode_media", lambda *a: np.zeros(SR * 50, dtype=np.float32)
    )
    events = run(tmp_path, Settings())
    assert len(engine["engine"].calls) == 1 and of(events, "block") == []


def test_settings_validation(monkeypatch):
    with pytest.raises(ValueError):
        Settings(segmentation="chunky")
    with pytest.raises(ValueError):
        Settings(block_sec=10, boundary_search_sec=10)
    assert Settings().effective_max_duration_sec == 180 * 60
    assert rolling().effective_max_duration_sec == 12 * 3600
    monkeypatch.delenv("WHISPER_SEGMENTATION")
    assert Settings().segmentation == "rolling"  # 產品預設
    monkeypatch.setenv("WHISPER_SEGMENTATION", "whole")
    assert Settings().segmentation == "whole"


# --- 主程序記錄與匯出 ---------------------------------------------------------


def test_manager_records_rolling_metadata(tmp_path):
    settings = Settings(root=tmp_path, disk_reserve_bytes=0, segmentation="rolling")
    manager = JobManager(settings, rolling_events_transcribe, False)
    meta = manager.create(JobRequest(source_name="a.m4a", expected_bytes=3))
    manager.begin_upload(meta.job_id).write_bytes(b"abc")
    manager.end_upload(meta.job_id)
    manager.start(meta.job_id)
    done = wait_terminal(manager, meta.job_id)
    assert done.status == Status.COMPLETED
    assert done.segmentation == "rolling" and done.block_sec == 600
    assert done.boundaries_sec == [598.5, 1201.0]
    assert done.detected_language == "zh" and done.duration_sec == 1300
    assert "boundary_forced@1201.0" in done.warnings
    assert any("timestamp_adjusted" in w for w in done.warnings)
    header = export(done, manager.segments[meta.job_id], "txt")
    assert "分段辨識：rolling，區塊約 600 秒，切點 2 個" in header
    restored = JobManager(settings, rolling_events_transcribe, False)
    assert restored.get(meta.job_id).boundaries_sec == [598.5, 1201.0]


def test_legacy_metadata_reads_as_whole():
    meta = JobMeta.model_validate(
        {"job_id": "a" * 32, "request": {"source_name": "a.wav", "expected_bytes": 1}}
    )
    assert meta.segmentation == "whole" and meta.boundaries_sec == []
    text = export(meta, [], "txt")
    assert "分段辨識" not in text


def test_session_reports_effective_limit(tmp_path):
    settings = Settings(root=tmp_path, disk_reserve_bytes=0, segmentation="rolling")
    manager = JobManager(settings, rolling_events_transcribe, False)
    with TestClient(create_app(settings, manager), base_url="http://127.0.0.1:8000") as c:
        c.get("/")
        data = c.get("/api/session").json()
    assert data["max_duration_sec"] == 12 * 3600 and data["segmentation"] == "rolling"
    assert json.dumps(data)


@pytest.mark.real_model
def test_real_model_rolling_blocks(tmp_path):
    """真實 small：取 70 秒語音以 20 秒分塊，確認切點、時間軸與連號。"""
    import os

    from app.config import ROOT
    from app.models import model_path
    from app.rolling import decode_from

    source = os.environ.get("WHISPER_TEST_AUDIO", str(ROOT / "temp" / "mixed_zh_en_5min.wav"))
    if not os.path.isfile(source):
        pytest.skip("缺少本機語音素材，請設定 WHISPER_TEST_AUDIO")
    try:
        model_path(Settings(), "small")
    except AppError:
        pytest.skip("缺少已準備 small 模型")
    audio, _ = decode_from(source, 3600, 0)
    clip = tmp_path / "clip.wav"
    with wave.open(str(clip), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(np.rint(audio[: SR * 70] * 32768).astype("<i2").tobytes())
    events = []
    transcribe(
        clip,
        JobRequest(source_name="clip.wav", expected_bytes=clip.stat().st_size, cpu_threads=4),
        rolling(),
        lambda *event: events.append(event),
        lambda: False,
    )
    segments, blocks = of(events, "segment"), of(events, "block")
    assert len(blocks) >= 3 and segments
    assert [s["segment_id"] for s in segments] == list(range(1, len(segments) + 1))
    assert all(s["start_sec"] <= s["end_sec"] for s in segments)
    assert max(s["end_sec"] for s in segments) > 50
    (tmp_path / "events.json").write_text(json.dumps(events, ensure_ascii=False), "utf-8")
