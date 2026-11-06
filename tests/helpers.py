"""不載入模型的真實子程序測試替身。"""

import os
import time


def fake_transcribe(path, request, settings, emit, cancelled):
    emit("status", {"status": "transcribing"})
    emit("info", {"duration_sec": 3, "versions": {"test": "1"}, "model_revision": "fake"})
    emit(
        "segment",
        {
            "segment_id": 1,
            "start_sec": 0,
            "end_sec": 3,
            "raw_text": "会议讨论 API",
            "text": "會議討論 API",
        },
    )


def blocked_transcribe(path, request, settings, emit, cancelled):
    fake_transcribe(path, request, settings, emit, cancelled)
    while True:
        time.sleep(0.1)


def crash_transcribe(path, request, settings, emit, cancelled):
    os._exit(7)


def wait_terminal(manager, job_id, timeout=8):
    from app.schemas import TERMINAL

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if manager.get(job_id).status in TERMINAL:
            return manager.get(job_id)
        time.sleep(0.02)
    raise AssertionError("工作程序未在期限內結束")


def orphan_parent(marker, bind):
    import multiprocessing as mp
    from pathlib import Path

    from app.process_control import JobObject, blocking_test_worker, worker_entry

    ctx = mp.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    gate, cancel = ctx.Event(), ctx.Event()
    process = ctx.Process(
        target=worker_entry, args=(gate, cancel, sender, blocking_test_worker, ())
    )
    process.start()
    sender.close()
    _job = None
    if bind:
        _job = JobObject(process)
        gate.set()
        assert receiver.poll(5)
        receiver.recv()
    marker_path = Path(marker)
    temporary = marker_path.with_suffix(".tmp")
    temporary.write_text(str(process.pid), encoding="ascii")
    os.replace(temporary, marker_path)
    while True:
        time.sleep(0.1)
