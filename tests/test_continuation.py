"""H50a：從已保存位置續跑。"""

import hashlib
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
from app.rolling import decode_from
from app.schemas import AppError, ContinueRequest, JobRequest, Status, TranscriptSegment
from app.transcriber import decode_media, transcribe
from tests.helpers import resumable_transcribe, wait_terminal


def make(tmp_path):
    return JobManager(
        Settings(root=tmp_path, disk_reserve_bytes=0, cancel_grace_sec=0.1),
        resumable_transcribe,
        False,
    )


def partial_job(manager, saved=2, duration=10.0, **request):
    """建立已取消、已保存 saved 段的任務。"""
    meta = manager.create(JobRequest(source_name="meeting.m4a", expected_bytes=3, **request))
    manager.cancel(meta.job_id)
    rows = [
        TranscriptSegment(
            segment_id=i, start_sec=i - 1, end_sec=i, raw_text=f"旧{i}", text=f"舊{i}"
        )
        for i in range(1, saved + 1)
    ]
    with (manager.directory(meta.job_id) / "segments.jsonl").open("w", encoding="utf-8") as h:
        h.writelines(r.model_dump_json() + "\n" for r in rows)
    with manager.lock:
        m = manager.metas[meta.job_id]
        manager.segments[meta.job_id] = rows
        m.segment_count, m.duration_sec = len(rows), duration
        m.last_position_sec = rows[-1].end_sec if rows else 0
        m.elapsed_sec, m.paused_sec = 120, 20
        manager.save(m)
    return meta.job_id


def upload(manager, job_id, data=b"abc"):
    manager.begin_upload(job_id).write_bytes(data)
    try:
        manager.record_source(job_id, hashlib.sha256(data).hexdigest())
    finally:
        manager.end_upload(job_id)
    manager.start(job_id)


def runs(tmp_path):
    path = tmp_path / "runs.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- 解碼 -------------------------------------------------------------------


def test_decode_from_keeps_only_remainder(tmp_path):
    path = tmp_path / "a.wav"
    rng = np.random.default_rng(3)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes(rng.integers(-9000, 9000, 44100 * 2 * 5).astype("<i2").tobytes())
    whole = decode_media(path, 60)
    for start in (0, 1.2345, 4.99):
        audio, total = decode_from(path, 60, start)
        assert total == len(whole)
        assert np.array_equal(audio, whole[int(round(start * 16000)) :])
    audio, total = decode_from(path, 60, 99)
    assert len(audio) == 0 and total == len(whole)


# --- 任務流程 ---------------------------------------------------------------


def test_continue_appends_after_saved_segments(tmp_path):
    manager = make(tmp_path)
    job_id = partial_job(manager)
    meta = manager.continue_job(job_id, ContinueRequest(expected_bytes=3, model="medium"))
    assert meta.status == Status.UPLOADING and manager.active == job_id
    record = meta.continuations[0]
    assert (record.from_sec, record.first_segment_id, record.model) == (2, 3, "medium")
    assert record.previous_status == "cancelled" and record.prior_elapsed_sec == 120
    assert meta.elapsed_sec == 0 and meta.paused_sec == 0
    upload(manager, job_id)
    done = wait_terminal(manager, job_id)
    assert done.status == Status.COMPLETED and manager.active is None
    assert [s.segment_id for s in manager.segments[job_id]] == [1, 2, 3, 4, 5]
    assert manager.segments[job_id][2].start_sec == 2
    assert done.request.model == "small"  # 原任務設定不變；續跑設定另記
    sent = runs(tmp_path)[0]
    assert sent["model"] == "medium"
    assert sent["resume"]["from_sec"] == 2 and sent["resume"]["first_segment_id"] == 3
    assert sent["resume"]["context_text"].endswith("旧1 旧2")
    assert sent["resume"]["expected_duration_sec"] == 10
    assert done.source_sha256 == hashlib.sha256(b"abc").hexdigest()
    assert done.continuations[0].source_check == "size_duration"
    # 第二次續跑：已有指紋時必須相符
    with manager.lock:
        manager.metas[job_id].status = Status.CANCELLED
        manager.metas[job_id].duration_sec = 60
    manager.continue_job(job_id, ContinueRequest(expected_bytes=3))
    upload(manager, job_id)
    done = wait_terminal(manager, job_id)
    assert done.continuations[1].source_check == "sha256"
    assert [s.segment_id for s in manager.segments[job_id]][-1] == 8
    # 重啟後續跑紀錄與片段保留
    restored = make(tmp_path)
    assert len(restored.get(job_id).continuations) == 2
    assert len(restored.segments[job_id]) == 8


def test_fingerprint_mismatch_fails_but_keeps_draft(tmp_path):
    manager = make(tmp_path)
    job_id = partial_job(manager)
    with manager.lock:
        manager.metas[job_id].source_sha256 = hashlib.sha256(b"abc").hexdigest()
    manager.continue_job(job_id, ContinueRequest(expected_bytes=3))
    manager.begin_upload(job_id).write_bytes(b"xyz")
    with pytest.raises(AppError) as info:
        manager.record_source(job_id, hashlib.sha256(b"xyz").hexdigest())
    assert info.value.code == "source_mismatch"
    manager.end_upload(job_id)
    manager.finish(job_id, Status.FAILED, info.value.message, info.value.code)
    meta = manager.get(job_id)
    assert meta.status == Status.FAILED and meta.segment_count == 2
    assert manager.active is None
    manager.continue_job(job_id, ContinueRequest(expected_bytes=3))  # 失敗後仍可再續跑


@pytest.mark.parametrize(
    ("setup", "body", "code"),
    [
        ("busy", {}, "busy"),
        ("completed", {}, "not_continuable"),
        ("empty", {}, "not_continuable"),
        ("at_end", {}, "not_continuable"),
        ("size", {"expected_bytes": 4}, "source_mismatch"),
    ],
)
def test_continue_rejections(tmp_path, setup, body, code):
    manager = make(tmp_path)
    job_id = partial_job(manager, saved=0 if setup == "empty" else 2)
    with manager.lock:
        meta = manager.metas[job_id]
        if setup == "completed":
            meta.status = Status.COMPLETED
        if setup == "at_end":
            meta.duration_sec = 2.2
    if setup == "busy":
        manager.create(JobRequest(source_name="b.mp4", expected_bytes=3))
    before = manager.get(job_id)
    with pytest.raises(AppError) as info:
        manager.continue_job(job_id, ContinueRequest(**{"expected_bytes": 3, **body}))
    assert info.value.code == code
    after = manager.get(job_id)
    assert after.status == before.status and after.continuations == []


def test_gpu_fallback_before_new_segment_keeps_resume(tmp_path):
    manager = make(tmp_path)
    job_id = partial_job(manager, title="cuda_first", device="cpu")
    manager.continue_job(job_id, ContinueRequest(expected_bytes=3, device="auto"))
    upload(manager, job_id)
    done = wait_terminal(manager, job_id, timeout=20)
    assert done.status == Status.COMPLETED
    assert done.fallback_reason == "cuda_oom" and done.actual_device == "cpu"
    sent = runs(tmp_path)
    assert len(sent) == 1 and sent[0]["device"] == "cpu"
    assert sent[0]["resume"]["first_segment_id"] == 3
    assert [s.segment_id for s in manager.segments[job_id]] == [1, 2, 3, 4, 5]


def test_continuation_header_in_exports(tmp_path):
    manager = make(tmp_path)
    job_id = partial_job(manager)
    manager.continue_job(job_id, ContinueRequest(expected_bytes=3))
    upload(manager, job_id)
    meta = wait_terminal(manager, job_id)
    text = export(meta, manager.segments[job_id], "md")
    assert "續跑 1：自 00:00:02 起（第 3 段起），模型 small" in text
    assert "未以指紋驗證" in text and "請回聽確認" in text


# --- API ----------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    settings = Settings(root=tmp_path, disk_reserve_bytes=0, max_upload_bytes=10)
    manager = JobManager(settings, resumable_transcribe, False)
    with TestClient(create_app(settings, manager), base_url="http://127.0.0.1:8000") as client:
        client.get("/")
        client.headers["X-CSRF-Token"] = client.get("/api/session").json()["csrf"]
        yield client, manager


def put(c, job_id, data):
    return c.put(
        f"/api/jobs/{job_id}/media",
        content=data,
        headers={"Content-Type": "application/octet-stream"},
    )


def test_api_continue_flow(client, tmp_path):
    c, m = client
    created = c.post("/api/jobs", json={"source_name": "meeting.m4a", "expected_bytes": 3})
    job_id = created.json()["job_id"]
    assert put(c, job_id, b"abc").status_code == 202
    first = wait_terminal(m, job_id)
    assert first.source_sha256 == hashlib.sha256(b"abc").hexdigest()
    with m.lock:
        m.metas[job_id].status = Status.INTERRUPTED
    url = f"/api/jobs/{job_id}/continue"
    assert c.post(url, content=b"{}", headers={"Content-Type": "text/plain"}).status_code == 415
    assert c.post(url, json={"expected_bytes": 3, "extra": 1}).status_code == 422
    assert c.post(url, json={"expected_bytes": 3, "model": "huge"}).status_code == 422
    response = c.post(url, json={"expected_bytes": 3, "cpu_threads": 8})
    assert response.status_code == 201 and response.json()["status"] == "uploading"
    assert c.post(url, json={"expected_bytes": 3}).json()["code"] == "busy"
    # 同大小但內容不同 → 指紋不符，任務失敗但保留原稿
    response = put(c, job_id, b"xyz")
    assert response.status_code == 409 and response.json()["code"] == "source_mismatch"
    meta = c.get(f"/api/jobs/{job_id}").json()
    assert meta["status"] == "failed" and meta["segment_count"] == 3
    assert c.post(url, json={"expected_bytes": 3}).status_code == 201
    assert put(c, job_id, b"abc").status_code == 202
    done = wait_terminal(m, job_id)
    assert done.status == Status.COMPLETED and done.segment_count == 6
    ids = [r["segment_id"] for r in c.get(f"/api/jobs/{job_id}/segments").json()]
    assert ids == list(range(1, 7))
    assert runs(tmp_path)[-1]["resume"]["first_segment_id"] == 4


# --- 轉錄器續跑路徑 -----------------------------------------------------------


def fake_engine(tmp_path, monkeypatch, total_sec=10.0):
    import faster_whisper

    (tmp_path / "ready.json").write_text('{"revision":"r"}', encoding="utf-8")
    monkeypatch.setattr("app.transcriber.model_path", lambda *args: tmp_path)
    remainder = np.zeros(int(16000 * 4), np.float32)
    monkeypatch.setattr(
        "app.rolling.decode_from",
        lambda path, limit, start, cancelled: (remainder, int(total_sec * 16000)),
    )
    calls = []

    def recognize(audio, **kwargs):
        calls.append(kwargs)
        return iter([SimpleNamespace(start=0.5, end=1.5, text=" 接續 ")]), None

    tokenizer = SimpleNamespace(
        encode=lambda text, **kw: SimpleNamespace(ids=[ord(c) for c in text])
    )
    fake = SimpleNamespace(hf_tokenizer=tokenizer, transcribe=recognize)
    monkeypatch.setattr(faster_whisper, "WhisperModel", lambda *args, **kwargs: fake)
    return calls


def run_resume(tmp_path, resume, **request):
    events = []
    transcribe(
        tmp_path / "a.wav",
        JobRequest(source_name="a.wav", expected_bytes=3, **request),
        Settings(),
        lambda *event: events.append(event),
        lambda: False,
        resume,
    )
    return events


def test_transcriber_resume_offsets_ids_and_prompt(tmp_path, monkeypatch):
    calls = fake_engine(tmp_path, monkeypatch)
    resume = {
        "from_sec": 6.0,
        "first_segment_id": 9,
        "context_text": "上一段的結尾",
        "expected_duration_sec": 10.0,
    }
    events = run_resume(tmp_path, resume, terms="GLT-123")
    segment = next(data for kind, data in events if kind == "segment")
    assert segment["segment_id"] == 9
    assert (segment["start_sec"], segment["end_sec"]) == (6.5, 7.5)
    assert next(data for kind, data in events if kind == "info")["duration_sec"] == 10
    prompt = calls[0]["initial_prompt"]
    assert isinstance(prompt, list)
    assert "".join(map(chr, prompt)).endswith("GLT-123 上一段的結尾")
    run_resume(tmp_path, resume, condition_on_previous_text=False)
    assert isinstance(calls[1]["initial_prompt"], str)  # 不參考前文時不帶舊文字


def test_transcriber_resume_rejects_different_duration(tmp_path, monkeypatch):
    fake_engine(tmp_path, monkeypatch, total_sec=12.5)
    resume = {"from_sec": 6, "first_segment_id": 3, "expected_duration_sec": 10.0}
    with pytest.raises(AppError) as info:
        run_resume(tmp_path, resume)
    assert info.value.code == "source_mismatch"
