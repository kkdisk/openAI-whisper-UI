"""關閉服務時的收尾：串流主動結束、工作程序不被 Ctrl+C 直接打斷。"""

import multiprocessing as mp
import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import Settings
from app.jobs import JobManager
from app.main import create_app
from app.process_control import worker_entry
from tests.helpers import fake_transcribe, sigint_probe


def test_event_stream_ends_when_server_is_exiting(tmp_path):
    settings = Settings(root=tmp_path, disk_reserve_bytes=0)
    manager = JobManager(settings, fake_transcribe, False)
    app = create_app(settings, manager)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        client.get("/")
        client.headers["X-CSRF-Token"] = client.get("/api/session").json()["csrf"]
        job_id = client.post(
            "/api/jobs", json={"source_name": "a.mp4", "expected_bytes": 3}
        ).json()["job_id"]
        app.state.server = SimpleNamespace(should_exit=True)
        result = {}

        def read():
            result["body"] = client.get(f"/api/jobs/{job_id}/events").text

        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        thread.join(10)
        assert not thread.is_alive(), "未完成任務的串流應在服務關閉時結束"
        assert "event:" not in result["body"]
        app.state.server = None
        client.post(f"/api/jobs/{job_id}/cancel")


def test_worker_ignores_console_interrupt():
    ctx = mp.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    gate, cancel = ctx.Event(), ctx.Event()
    process = ctx.Process(target=worker_entry, args=(gate, cancel, sender, sigint_probe, ()))
    process.start()
    sender.close()
    gate.set()
    try:
        assert receiver.poll(20)
        kind, payload = receiver.recv()
        assert kind == "probe" and payload["ignored"] is True
    finally:
        process.join(10)
        receiver.close()
