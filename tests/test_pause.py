"""合作暫停、IPC 確認及計時；不依賴模型。"""

import json
import multiprocessing as mp
import os
import threading
import time

import pytest

from app.jobs import JobManager
from app.process_control import CooperativeControl
from app.revisions import RevisionWrite, read_revisions, revised_segments, write_revision
from app.schemas import AppError, ExecutionState, Status
from app.transcriber import controlled_segments
from tests.helpers import pausable_transcribe, wait_terminal
from tests.test_jobs import make, request, upload


def controlled_job(tmp_path):
    manager = make(tmp_path)
    job_id = manager.create(request()).job_id
    manager.metas[job_id].status = Status.TRANSCRIBING
    ctx = mp.get_context("spawn")
    manager.controls[job_id] = (ctx.Event(), ctx.Value("q", 0))
    return manager, job_id


def test_pause_requires_confirmed_ack_and_excludes_paused_time(tmp_path, monkeypatch):
    manager, job_id = controlled_job(tmp_path)
    clock = [100.0]
    monkeypatch.setattr("app.jobs.time.monotonic", lambda: clock[0])
    manager.started[job_id] = clock[0]
    clock[0] = 110
    result = manager.set_paused(job_id, True)
    assert result.execution_state == ExecutionState.PAUSE_REQUESTED
    assert result.paused_sec == 0
    assert manager.set_paused(job_id, True).control_seq == 1
    with pytest.raises(AppError, match="等待"):
        manager.set_paused(job_id, False)
    manager.ack_control(job_id, {"sequence": 0, "state": "paused"})
    assert manager.get(job_id).execution_state == ExecutionState.PAUSE_REQUESTED
    clock[0] = 115
    manager.ack_control(job_id, {"sequence": 1, "state": "paused"})
    clock[0] = 135
    manager.ack_control(job_id, {"sequence": 1, "state": "paused"})
    meta = manager.get(job_id)
    assert (meta.elapsed_sec, meta.active_elapsed_sec, meta.paused_sec) == (35, 15, 20)
    manager.save(manager.metas[job_id])
    stored = json.loads((manager.directory(job_id) / "metadata.json").read_text("utf-8"))
    assert stored["paused_sec"] == 20
    manager.set_paused(job_id, False)
    assert manager.get(job_id).execution_state == ExecutionState.RESUME_REQUESTED
    assert manager.set_paused(job_id, False).control_seq == 2
    manager.ack_control(job_id, {"sequence": 1, "state": "running"})
    clock[0] = 140
    manager.ack_control(job_id, {"sequence": 2, "state": "running"})
    clock[0] = 150
    assert manager.get(job_id).active_elapsed_sec == 25
    assert manager.get(job_id).paused_sec == 25
    manager.finish(job_id, Status.COMPLETED)
    manager.ack_control(job_id, {"sequence": 2, "state": "paused"})
    assert manager.get(job_id).status == Status.COMPLETED
    assert manager.get(job_id).execution_state == ExecutionState.RUNNING


def test_control_not_available_before_transcribing_or_after_cancel(tmp_path):
    manager = make(tmp_path)
    job_id = manager.create(request()).job_id
    for status in [Status.UPLOADING, Status.LOADING_MODEL, Status.DECODING]:
        manager.metas[job_id].status = status
        with pytest.raises(AppError):
            manager.set_paused(job_id, True)
    manager.cancel(job_id)
    with pytest.raises(AppError):
        manager.set_paused(job_id, False)


def test_failed_control_metadata_does_not_signal_worker(tmp_path, monkeypatch):
    manager, job_id = controlled_job(tmp_path)

    def fail(meta):
        raise OSError("disk full")

    monkeypatch.setattr(manager, "save", fail)
    with pytest.raises(OSError):
        manager.set_paused(job_id, True)
    event, sequence = manager.controls[job_id]
    assert not event.is_set() and sequence.value == 0
    assert manager.get(job_id).execution_state == ExecutionState.RUNNING


def test_restart_of_paused_task_is_interrupted_without_resume(tmp_path):
    manager, job_id = controlled_job(tmp_path)
    manager.set_paused(job_id, True)
    manager.ack_control(job_id, {"sequence": 1, "state": "paused"})
    restored = JobManager(manager.settings, check_model=False)
    assert restored.get(job_id).status == Status.INTERRUPTED
    assert restored.get(job_id).execution_state == ExecutionState.RUNNING
    assert restored.active is None
    with pytest.raises(AppError):
        restored.set_paused(job_id, False)


def test_gate_does_not_advance_iterator_while_paused_and_cancel_wakes_it():
    ctx = mp.get_context("spawn")
    cancel, pause, sequence = ctx.Event(), ctx.Event(), ctx.Value("q", 1)
    pause.set()
    acknowledged = threading.Event()
    events, advanced = [], []

    def emit(kind, data):
        events.append(data)
        acknowledged.set()

    def results():
        advanced.append(1)
        yield 1

    control = CooperativeControl(cancel, pause, sequence, emit)
    output = []
    thread = threading.Thread(target=lambda: output.extend(controlled_segments(results(), control)))
    thread.start()
    try:
        assert acknowledged.wait(2)
        assert events == [{"sequence": 1, "state": "paused"}]
        assert advanced == []
    finally:
        cancel.set()
        thread.join(2)
    assert not thread.is_alive() and output == [] and advanced == []


def wait_for(manager, job_id, predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = manager.get(job_id)
        if predicate(result):
            return result
        time.sleep(0.01)
    raise AssertionError("工作程序未在期限內確認控制狀態")


@pytest.mark.windows
@pytest.mark.skipif(os.name != "nt", reason="Windows 專用")
@pytest.mark.parametrize("end", ["complete", "cancel", "close"])
def test_real_worker_pause_resume_edit_and_finish(tmp_path, end):
    manager = make(tmp_path, pausable_transcribe)
    job_id = manager.create(request()).job_id
    try:
        upload(manager, job_id)
        wait_for(manager, job_id, lambda m: m.segment_count >= 1)
        for _ in range(2):
            manager.set_paused(job_id, True)
            paused = wait_for(manager, job_id, lambda m: m.execution_state == ExecutionState.PAUSED)
            time.sleep(0.12)
            current = manager.get(job_id)
            assert current.segment_count == paused.segment_count
            assert abs(current.active_elapsed_sec - paused.active_elapsed_sec) < 0.02
            assert current.paused_sec > paused.paused_sec
            assert manager.active == job_id
            assert (manager.directory(job_id) / "media.upload").exists()
            state = read_revisions(manager.directory(job_id))
            with manager.lock:
                write_revision(
                    manager.directory(job_id),
                    manager.segments[job_id],
                    RevisionWrite(
                        version=state.version,
                        row={"key": "s:1", "text": "人工修訂"},
                    ),
                )
            if end != "complete":
                break
            manager.set_paused(job_id, False)
            wait_for(manager, job_id, lambda m, count=paused.segment_count: m.segment_count > count)
        if end == "cancel":
            manager.cancel(job_id)
        elif end == "close":
            manager.close()
        result = wait_terminal(manager, job_id)
        assert result.status == (Status.COMPLETED if end == "complete" else Status.CANCELLED)
        assert manager.active is None and job_id not in manager.runtime
        assert job_id not in manager.controls
        assert not (manager.directory(job_id) / "media.upload").exists()
        assert (
            revised_segments(manager.segments[job_id], read_revisions(manager.directory(job_id)))[
                0
            ].text
            == "人工修訂"
        )
        if end == "complete":
            assert [s.segment_id for s in manager.segments[job_id]] == list(range(1, 31))
    finally:
        manager.close()
