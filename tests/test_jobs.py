"""真實程序取消與磁碟恢復驗證。"""

import os
import time

import pytest

from app.config import Settings
from app.jobs import JobManager
from app.schemas import AppError, JobRequest, Status
from tests.helpers import blocked_transcribe, crash_transcribe, fake_transcribe, wait_terminal


def make(tmp_path, target=fake_transcribe):
    return JobManager(
        Settings(root=tmp_path, disk_reserve_bytes=0, cancel_grace_sec=0.1), target, False
    )


def request():
    return JobRequest(source_name="meeting.mp4", expected_bytes=3)


def upload(manager, job_id):
    manager.begin_upload(job_id).write_bytes(b"abc")
    manager.end_upload(job_id)
    manager.start(job_id)


def test_exclusive_expire_and_history(tmp_path):
    manager = make(tmp_path)
    a = manager.create(request())
    with pytest.raises(AppError, match="活動任務"):
        manager.create(request())
    manager.deadlines[a.job_id] = 0
    manager.expire()
    assert manager.get(a.job_id).status == Status.FAILED
    b = manager.create(request())
    manager.cancel(b.job_id)
    restored = make(tmp_path)
    assert len(restored.metas) == 2
    restored.delete(a.job_id)
    assert b.job_id in restored.metas


def test_upload_cancel_keeps_slot_until_closed(tmp_path):
    manager = make(tmp_path)
    meta = manager.create(request())
    path = manager.begin_upload(meta.job_id)
    path.write_bytes(b"a")
    manager.cancel(meta.job_id)
    assert manager.active == meta.job_id
    assert path.exists()
    with pytest.raises(AppError):
        manager.upload_progress(meta.job_id, 1)
    manager.end_upload(meta.job_id)
    manager.finish(meta.job_id, Status.FAILED)
    assert manager.get(meta.job_id).status == Status.CANCELLED
    assert manager.active is None and not path.exists()


@pytest.mark.windows
@pytest.mark.skipif(os.name != "nt", reason="Windows 專用")
@pytest.mark.parametrize("target", [fake_transcribe, crash_transcribe, blocked_transcribe])
def test_spawn_cancel_and_restore(tmp_path, target):
    manager = make(tmp_path, target)
    meta = manager.create(request())
    upload(manager, meta.job_id)
    if target is blocked_transcribe:
        deadline = time.monotonic() + 5
        while manager.get(meta.job_id).segment_count == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        started = time.monotonic()
        manager.cancel(meta.job_id)
    result = wait_terminal(manager, meta.job_id)
    if target is blocked_transcribe:
        assert time.monotonic() - started < 5
        assert result.status == Status.CANCELLED
    elif target is fake_transcribe:
        assert result.status == Status.COMPLETED
    else:
        assert result.status == Status.FAILED
    assert not (manager.directory(meta.job_id) / "media.upload").exists()
    restored = make(tmp_path)
    assert restored.get(meta.job_id).segment_count == (0 if target is crash_transcribe else 1)


def test_restore_truncated_jsonl(tmp_path):
    manager = make(tmp_path)
    meta = manager.create(request())
    directory = manager.directory(meta.job_id)
    valid = '{"segment_id":1,"start_sec":0,"end_sec":1,"raw_text":"API","text":"API"}\n'
    (directory / "segments.jsonl").write_text(valid + '{"segment_', encoding="utf-8")
    (directory / "media.upload").write_bytes(b"source")
    restored = make(tmp_path)
    assert restored.get(meta.job_id).status == Status.INTERRUPTED
    assert restored.get(meta.job_id).segment_count == 1
    assert restored.get(meta.job_id).warnings
    assert not (directory / "media.upload").exists()
    assert (directory / "segments.jsonl").read_text("utf-8") == valid


@pytest.mark.parametrize("damage", ["missing", "empty", "short", "extra", "partial"])
def test_completed_restore_detects_lost_or_changed_segments(tmp_path, damage):
    from app.formatter import export
    from app.schemas import TranscriptSegment

    manager = make(tmp_path)
    meta = manager.create(request())
    row = TranscriptSegment(segment_id=1, start_sec=0, end_sec=1, raw_text="API", text="API")
    path = manager.directory(meta.job_id) / "segments.jsonl"
    lines = [row.model_dump_json(), row.model_copy(update={"segment_id": 2}).model_dump_json()]
    manager.metas[meta.job_id].segment_count = 2
    manager.finish(meta.job_id, Status.COMPLETED)
    if damage != "missing":
        content = {
            "empty": "",
            "short": lines[0] + "\n",
            "extra": "\n".join(lines + [row.model_copy(update={"segment_id": 3}).model_dump_json()])
            + "\n",
            "partial": lines[0] + '\n{"segment_id":',
        }[damage]
        path.write_text(content, encoding="utf-8")
    restored = make(tmp_path)
    result = restored.get(meta.job_id)
    assert result.status == Status.INTERRUPTED
    assert result.warnings and result.error
    assert "不完整" in export(result, restored.segments[meta.job_id], "md")


def test_completed_silence_and_unfinished_tail_restore(tmp_path):
    from app.schemas import TranscriptSegment

    manager = make(tmp_path)
    silent = manager.create(request())
    manager.finish(silent.job_id, Status.COMPLETED)
    pending = manager.create(request())
    row = TranscriptSegment(segment_id=1, start_sec=0, end_sec=1, raw_text="API", text="API")
    (manager.directory(pending.job_id) / "segments.jsonl").write_text(
        row.model_dump_json() + "\n", encoding="utf-8"
    )
    restored = make(tmp_path)
    assert restored.get(silent.job_id).status == Status.COMPLETED
    assert not restored.get(silent.job_id).warnings
    assert restored.get(pending.job_id).status == Status.INTERRUPTED
    assert restored.get(pending.job_id).segment_count == 1
    assert not restored.get(pending.job_id).warnings


def test_invalid_times_survive_persistence(tmp_path):
    from app.formatter import export
    from app.schemas import TranscriptSegment

    manager = make(tmp_path)
    meta = manager.create(request())
    row = TranscriptSegment(
        segment_id=1,
        start_sec=float("nan"),
        end_sec=float("inf"),
        raw_text="重要內容 API",
        text="重要內容 API",
    )
    path = manager.directory(meta.job_id) / "segments.jsonl"
    path.write_text(row.model_dump_json() + "\n", encoding="utf-8")
    restored = make(tmp_path)
    rows = restored.segments[meta.job_id]
    assert len(rows) == 1
    assert "重要內容 API" in export(restored.get(meta.job_id), rows, "md")


def test_metadata_replace_retries_transient_lock(tmp_path, monkeypatch):
    manager = make(tmp_path)
    real = os.replace
    calls = []

    def transient(source, destination):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError("模擬 Windows 短暫檔案鎖")
        return real(source, destination)

    monkeypatch.setattr(os, "replace", transient)
    meta = manager.create(request())
    assert manager.get(meta.job_id).status == Status.UPLOADING
    assert len(calls) == 2


@pytest.mark.windows
@pytest.mark.skipif(os.name != "nt", reason="Windows 專用")
@pytest.mark.parametrize("bind", [False, True])
def test_parent_kill_leaves_no_worker(tmp_path, bind):
    import ctypes
    import multiprocessing as mp
    from ctypes import wintypes

    from tests.helpers import orphan_parent

    marker = tmp_path / "pid.txt"
    parent = mp.get_context("spawn").Process(target=orphan_parent, args=(str(marker), bind))
    parent.start()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = None
    try:
        deadline = time.monotonic() + 8
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), "父程序未就緒"
        handle = kernel.OpenProcess(0x100001, False, int(marker.read_text("ascii")))
        assert handle
        parent.terminate()
        parent.join(2)
        assert kernel.WaitForSingleObject(handle, 5000) == 0, "工作程序在父程序死亡後殘留"
    finally:
        if parent.is_alive():
            parent.terminate()
            parent.join(2)
        if handle:
            if kernel.WaitForSingleObject(handle, 0) != 0:
                kernel.TerminateProcess(handle, 1)
            kernel.CloseHandle(handle)
