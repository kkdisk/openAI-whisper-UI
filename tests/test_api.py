"""本機存取、串流與歷史 API 驗證。"""

import os

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.jobs import JobManager
from app.main import create_app
from app.schemas import Status
from tests.helpers import fake_transcribe, wait_terminal


def revision_job(c, m):
    from app.schemas import TranscriptSegment

    job_id = new(c).json()["job_id"]
    segment = TranscriptSegment(
        segment_id=1, start_sec=0, end_sec=5, raw_text="原始辨识", text="原始辨識"
    )
    m.segments[job_id] = [segment]
    (m.directory(job_id) / "segments.jsonl").write_text(
        segment.model_dump_json() + "\n", encoding="utf-8"
    )
    m.metas[job_id].segment_count = 1
    m.save(m.metas[job_id])
    return job_id, f"/api/jobs/{job_id}/revisions"


def test_revision_during_transcription_export_and_restart(client):
    c, m = client
    job_id, url = revision_job(c, m)
    m.metas[job_id].status = Status.TRANSCRIBING
    assert c.get(url).json() == {"version": 0, "rows": []}
    assert (
        c.put(url, json={"version": 0, "row": {"key": "s:1", "text": "修訂 API"}}).status_code
        == 200
    )
    inserted = c.put(
        url,
        json={
            "version": 1,
            "row": {
                "key": "new",
                "anchor": 1,
                "text": "補充句",
                "start_sec": 5,
                "end_sec": 7,
            },
        },
    ).json()
    assert inserted["version"] == 2
    assert inserted["rows"][1]["key"].startswith("u:")
    m.segments[job_id].append(
        m.segments[job_id][0].model_copy(
            update={
                "segment_id": 2,
                "text": "後續辨識",
                "start_sec": 7,
                "end_sec": 9,
            }
        )
    )
    text = c.get(f"/api/jobs/{job_id}/export").text
    assert text.index("修訂 API") < text.index("補充句") < text.index("後續辨識")
    assert "補充句" in c.get(f"/api/jobs/{job_id}/export?format=md").text
    assert "補充句" in c.get(f"/api/jobs/{job_id}/export?format=srt").text
    raw = c.get(f"/api/jobs/{job_id}/export?variant=raw").text
    assert "原始辨识" in raw and "修訂 API" not in raw and "補充句" not in raw
    assert m.segments[job_id][0].text == "原始辨識"
    m.cancel(job_id)
    restored = JobManager(m.settings, fake_transcribe, False)
    with TestClient(create_app(m.settings, restored), base_url="http://127.0.0.1:8000") as other:
        other.get("/")
        assert other.get(url).json() == inserted
        assert "修訂 API" in other.get(f"/api/jobs/{job_id}/export").text


def test_revision_delete_restore_and_missing_time(client):
    c, m = client
    job_id, url = revision_job(c, m)
    c.put(url, json={"version": 0, "row": {"key": "s:1", "text": "修訂", "deleted": True}})
    assert "原始辨識" not in c.get(f"/api/jobs/{job_id}/export").text
    c.put(url, json={"version": 1, "row": {"key": "s:1", "text": "原始辨識"}})
    assert "原始辨識" in c.get(f"/api/jobs/{job_id}/export").text
    c.put(url, json={"version": 2, "row": {"key": "new", "anchor": 0, "text": "開場補句"}})
    assert "[時間未設定] 開場補句" in c.get(f"/api/jobs/{job_id}/export").text
    assert c.get(f"/api/jobs/{job_id}/export?format=srt").status_code == 400


def test_revision_conflict_security_and_validation(client):
    c, m = client
    _, url = revision_job(c, m)
    body = {"version": 0, "row": {"key": "s:1", "text": "修訂"}}
    assert c.put(url, json=body, headers={"X-CSRF-Token": "bad"}).status_code == 403
    assert c.put(url, json=body).status_code == 200
    assert c.put(url, json=body).status_code == 409
    for row in [
        {"key": "s:99", "text": "不存在"},
        {"key": "u:missing", "text": "不存在"},
        {"key": "new", "anchor": 9, "text": "尚未辨識"},
        {"key": "new", "text": "單一時間", "start_sec": 1},
        {"key": "new", "text": "反向時間", "start_sec": 5, "end_sec": 2},
        {"key": "new", "text": "無限", "start_sec": "NaN", "end_sec": 2},
        {"key": "s:1", "text": "改原稿時間", "start_sec": 1, "end_sec": 2},
    ]:
        assert c.put(url, json={"version": 1, "row": row}).status_code == 422
    assert c.get(url).json()["version"] == 1


def test_revision_write_failure_preserves_saved_version(client, monkeypatch):
    from app import revisions

    c, m = client
    _, url = revision_job(c, m)
    body = {"version": 0, "row": {"key": "s:1", "text": "已保存"}}
    assert c.put(url, json=body).status_code == 200
    original = c.get(url).json()

    def fail(*args):
        raise PermissionError("locked")

    with monkeypatch.context() as patch:
        patch.setattr(revisions.os, "replace", fail)
        with pytest.raises(PermissionError):
            c.put(url, json={"version": 1, "row": {"key": "s:1", "text": "不能保存"}})
    assert c.get(url).json() == original


def test_corrupt_revision_blocks_display_but_preserves_raw(client):
    c, m = client
    job_id, url = revision_job(c, m)
    (m.directory(job_id) / "revisions.json").write_text("broken", encoding="utf-8")
    assert c.get(url).status_code == 500
    assert c.get(f"/api/jobs/{job_id}/export").status_code == 500
    assert "原始辨识" in c.get(f"/api/jobs/{job_id}/export?variant=raw").text


def test_pause_resume_api_confirmation_and_security(client):
    import multiprocessing as mp

    c, m = client
    job_id, revisions_url = revision_job(c, m)
    base = f"/api/jobs/{job_id}"
    assert c.post(base + "/pause").status_code == 409
    m.metas[job_id].status = Status.TRANSCRIBING
    ctx = mp.get_context("spawn")
    m.controls[job_id] = (ctx.Event(), ctx.Value("q", 0))
    assert c.post(base + "/pause", headers={"X-CSRF-Token": "bad"}).status_code == 403
    pending = c.post(base + "/pause").json()
    assert pending["execution_state"] == "pause_requested"
    assert c.post(base + "/pause").json()["control_seq"] == 1
    assert c.post(base + "/resume").status_code == 409
    with m.lock:
        m.ack_control(job_id, {"sequence": 1, "state": "paused"})
    assert c.get(base).json()["execution_state"] == "paused"
    assert (
        c.put(
            revisions_url,
            json={
                "version": 0,
                "row": {"key": "s:1", "text": "暫停中校稿"},
            },
        ).status_code
        == 200
    )
    assert "暫停中校稿" in c.get(base + "/export").text
    assert c.post(base + "/resume").json()["execution_state"] == "resume_requested"
    with m.lock:
        m.ack_control(job_id, {"sequence": 2, "state": "running"})
    assert c.get(base).json()["execution_state"] == "running"
    c.post(base + "/cancel")
    assert c.post(base + "/resume").status_code == 409


@pytest.fixture
def client(tmp_path):
    settings = Settings(root=tmp_path, disk_reserve_bytes=0, max_upload_bytes=10)
    manager = JobManager(settings, fake_transcribe, False)
    with TestClient(create_app(settings, manager), base_url="http://127.0.0.1:8000") as client:
        client.get("/")
        csrf = client.get("/api/session").json()["csrf"]
        client.headers["X-CSRF-Token"] = csrf
        yield client, manager


def new(client, **overrides):
    return client.post(
        "/api/jobs", json={"source_name": "meeting.mp4", "expected_bytes": 3, **overrides}
    )


def test_local_security(client):
    c, _ = client
    assert c.get("/api/jobs", headers={"Host": "evil.test:8000"}).status_code == 403
    assert c.get("/api/jobs", headers={"Origin": "https://evil.test"}).status_code == 403
    assert new(c).status_code == 201
    assert c.post("/api/jobs", json={}, headers={"X-CSRF-Token": "bad"}).status_code == 403
    c.cookies.clear()
    assert c.get("/api/jobs").status_code == 403


def test_creation_busy_and_size(client):
    c, _ = client
    assert new(c, expected_bytes=11).status_code == 413
    assert new(c).status_code == 201
    assert new(c).status_code == 409


@pytest.mark.parametrize("body", [b"ab", b"abcd"])
def test_size_mismatch_cleanup(client, body):
    c, m = client
    job_id = new(c).json()["job_id"]
    response = c.put(
        f"/api/jobs/{job_id}/media",
        content=body,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code in {400, 413}
    assert m.get(job_id).status == Status.FAILED and m.active is None
    assert not (m.directory(job_id) / "media.upload").exists()


@pytest.mark.windows
@pytest.mark.skipif(os.name != "nt", reason="Windows 專用")
def test_success_history_sse_and_download(client):
    c, m = client
    job_id = new(c).json()["job_id"]
    response = c.put(
        f"/api/jobs/{job_id}/media",
        content=b"abc",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 202
    wait_terminal(m, job_id)
    assert c.get("/api/jobs").json()["total"] == 1
    assert c.get("/api/jobs/latest").json()["job_id"] == job_id
    assert c.get(f"/api/jobs/{job_id}/segments").json()[0]["text"] == "會議討論 API"
    events = c.get(f"/api/jobs/{job_id}/events").text
    assert "event: segment" in events and "event: done" in events
    assert "未指定" in c.get(f"/api/jobs/{job_id}/export?format=md").text
    assert "會議討論 API" in c.get(f"/api/jobs/{job_id}/export").text
    assert (
        c.put(
            f"/api/jobs/{job_id}/media",
            content=b"abc",
            headers={"Content-Type": "application/octet-stream"},
        ).status_code
        == 409
    )
    assert c.delete(f"/api/jobs/{job_id}").status_code == 204
    assert c.get("/api/jobs/latest").json() is None


def test_parameters_and_cancel(client):
    c, m = client
    assert new(c, source_name="a.exe").status_code == 400
    assert (
        c.post("/api/jobs", content=b"{", headers={"Content-Type": "application/json"}).status_code
        == 400
    )
    job_id = new(c).json()["job_id"]
    assert c.delete(f"/api/jobs/{job_id}").status_code == 409
    assert c.post(f"/api/jobs/{job_id}/cancel").json()["status"] == "cancelled"
    assert c.post(f"/api/jobs/{job_id}/cancel").json()["status"] == "cancelled"
    assert c.get(f"/api/jobs/{job_id}/export?format=md&variant=raw").status_code == 400
    assert not m.active


def test_early_reject_never_reads_media(client):
    import asyncio

    c, m = client
    job_id = new(c).json()["job_id"]
    m.cancel(job_id)
    responses = []

    async def receive():
        raise AssertionError("已終止任務不應讀取媒體")

    async def send(message):
        responses.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "PUT",
        "scheme": "http",
        "path": f"/api/jobs/{job_id}/media",
        "query_string": b"",
        "root_path": "",
        "server": ("127.0.0.1", 8000),
        "client": ("127.0.0.1", 12345),
        "headers": [
            (b"host", b"127.0.0.1:8000"),
            (b"content-type", b"application/octet-stream"),
            (b"cookie", f"session={c.cookies.get('session')}".encode()),
            (b"x-csrf-token", c.headers["X-CSRF-Token"].encode()),
        ],
    }
    asyncio.run(c.app(scope, receive, send))
    assert responses[0]["status"] == 409


def test_json_size_limit(client):
    c, _ = client
    response = c.post(
        "/api/jobs", content=b"x" * 65537, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413


def test_turbo_model_api_and_preparation_gate(client):
    import json

    from app.config import MODEL_REPOS

    c, manager = client
    manager.check_model = True
    rows = c.get("/api/models").json()
    assert not next(row for row in rows if row["name"] == "large-v3-turbo")["ready"]
    response = new(c, model="large-v3-turbo")
    assert response.status_code == 400
    assert response.json()["code"] == "model_missing"
    assert manager.active is None
    directory = manager.settings.models_dir / "large-v3-turbo"
    directory.mkdir(parents=True)
    for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
        (directory / name).write_text("{}", encoding="utf-8")
    (directory / "ready.json").write_text(
        json.dumps(
            {
                "repo": MODEL_REPOS["large-v3-turbo"],
                "revision": "b" * 40,
            }
        ),
        encoding="utf-8",
    )
    rows = c.get("/api/models").json()
    assert next(row for row in rows if row["name"] == "large-v3-turbo")["ready"]
    response = new(c, model="large-v3-turbo")
    assert response.status_code == 201
    assert response.json()["request"]["model"] == "large-v3-turbo"


@pytest.mark.parametrize("write_failure", [False, True])
def test_upload_close_failure_releases_slot(client, monkeypatch, write_failure):
    from pathlib import Path

    c, manager = client
    job_id = new(c).json()["job_id"]
    real_open = Path.open

    class BrokenClose:
        def __init__(self, handle):
            self.handle = handle

        def write(self, data):
            if write_failure:
                raise OSError(28, "simulated write failure")
            return self.handle.write(data)

        def close(self):
            self.handle.close()
            raise OSError(28, "simulated flush failure")

    def open_media(path, *args, **kwargs):
        handle = real_open(path, *args, **kwargs)
        return BrokenClose(handle) if path.name == "media.upload" and args == ("xb",) else handle

    monkeypatch.setattr(Path, "open", open_media)
    response = c.put(
        f"/api/jobs/{job_id}/media",
        content=b"abc",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "upload_failed"
    assert manager.get(job_id).status == Status.FAILED
    assert job_id not in manager.receiving and manager.active is None
    assert not (manager.directory(job_id) / "media.upload").exists()
    assert new(c).status_code == 201


def test_mixed_language_settings_persist_and_export(client):
    c, manager = client
    response = new(c, language="mixed", condition_on_previous_text=False)
    assert response.status_code == 201
    job_id = response.json()["job_id"]
    manager.cancel(job_id)
    restored = JobManager(manager.settings, check_model=False)
    request = restored.get(job_id).request
    assert request.language == "mixed" and request.condition_on_previous_text is False
    exported = c.get(f"/api/jobs/{job_id}/export?format=md").text
    assert "語言：mixed" in exported and "參考前文：關閉" in exported
