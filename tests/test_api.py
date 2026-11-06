"""本機存取、串流與歷史 API 驗證。"""

import os

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.jobs import JobManager
from app.main import create_app
from app.schemas import Status
from tests.helpers import fake_transcribe, wait_terminal


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
