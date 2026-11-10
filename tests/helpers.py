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


def cuda_failure_transcribe(path, request, settings, emit, cancelled):
    from app.schemas import AppError

    with (settings.root / "attempts.txt").open("a") as handle:
        handle.write(f"{os.getpid()} {request.device}\n")
    device = "cpu" if request.device == "cpu" else "cuda"
    emit(
        "device",
        {
            "actual_device": device,
            "actual_device_index": 0 if device == "cuda" else None,
            "compute_type": "int8" if device == "cpu" else "int8_float16",
        },
    )
    if device == "cpu":
        fake_transcribe(path, request, settings, emit, cancelled)
        return
    emit("status", {"status": "transcribing"})
    if request.title == "after_segment":
        fake_transcribe(path, request, settings, emit, cancelled)
    if request.title == "wait_cancel":
        while not cancelled():
            time.sleep(0.01)
    raise AppError(request.terms or "cuda_oom", "模擬故障")


def pausable_transcribe(path, request, settings, emit, cancelled):
    from app.transcriber import controlled_segments

    emit("status", {"status": "transcribing"})
    emit("info", {"duration_sec": 30, "versions": {}, "model_revision": "test"})

    def results():
        for i in range(1, 31):
            time.sleep(0.05)  # 模擬一次不可立即暫停的推論。
            yield i

    for i in controlled_segments(results(), cancelled):
        emit(
            "segment",
            {
                "segment_id": i,
                "start_sec": i - 1,
                "end_sec": i,
                "raw_text": f"raw {i}",
                "text": f"text {i}",
            },
        )


def wait_terminal(manager, job_id, timeout=8):
    from app.schemas import TERMINAL

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if manager.get(job_id).status in TERMINAL:
            return manager.get(job_id)
        time.sleep(0.02)
    raise AssertionError("工作程序未在期限內結束")


def paused_test_worker(emit, cancelled):
    cancelled.checkpoint()


def orphan_parent(marker, bind, paused=False):
    import multiprocessing as mp
    from pathlib import Path

    from app.process_control import JobObject, blocking_test_worker, worker_entry

    ctx = mp.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    gate, cancel = ctx.Event(), ctx.Event()
    pause, sequence = ctx.Event(), ctx.Value("q", 1)
    pause.set()
    extra = (pause, sequence) if paused else ()
    process = ctx.Process(
        target=worker_entry,
        args=(
            gate,
            cancel,
            sender,
            paused_test_worker if paused else blocking_test_worker,
            (),
            *extra,
        ),
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


def resumable_transcribe(path, request, settings, emit, cancelled, resume=None):
    """首次產生 0～3 秒三段；續跑時自 from_sec 接續，並記錄收到的 resume。"""
    import json

    from app.schemas import AppError

    if request.device != "cpu" and request.title == "cuda_first":
        emit("device", {"actual_device": "cuda", "actual_device_index": 0, "compute_type": "f16"})
        emit("status", {"status": "transcribing"})
        raise AppError("cuda_oom", "模擬故障")
    emit("device", {"actual_device": "cpu", "actual_device_index": None, "compute_type": "int8"})
    emit("status", {"status": "transcribing"})
    emit("info", {"duration_sec": 10, "versions": {}, "model_revision": "test"})
    record = {"resume": resume, "model": request.model, "device": request.device}
    with (settings.root / "runs.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    start, first = (resume["from_sec"], resume["first_segment_id"]) if resume else (0, 1)
    for i in range(3):
        emit(
            "segment",
            {
                "segment_id": first + i,
                "start_sec": start + i,
                "end_sec": start + i + 1,
                "raw_text": f"原文{first + i}",
                "text": f"文字{first + i}",
            },
        )
