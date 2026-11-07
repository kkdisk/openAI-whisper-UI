"""主程序唯一持有任務狀態與磁碟結果。"""

import logging
import math
import multiprocessing as mp
import os
import re
import shutil
import threading
import time
import traceback
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.devices import CUDA_ERRORS
from app.formatter import CONVERSION
from app.models import model_path
from app.process_control import JobObject, stop_process, worker_entry
from app.schemas import (
    TERMINAL,
    AppError,
    ExecutionState,
    JobMeta,
    JobRequest,
    Status,
    TranscriptSegment,
    now_iso,
)
from app.transcriber import transcribe


class JobManager:
    def __init__(self, settings: Settings, target=transcribe, check_model=True):
        self.settings = settings
        self.target = target
        self.check_model = check_model
        self.lock = threading.RLock()
        self.active: str | None = None
        self.runtime = {}
        self.started = {}
        self.controls = {}
        self.paused_started = {}
        self.control_requested = {}
        self.metas: dict[str, JobMeta] = {}
        self.segments: dict[str, list[TranscriptSegment]] = {}
        self.deadlines = {}
        self.receiving = set()
        settings.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.restore()

    def directory(self, job_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise AppError("not_found", "找不到任務。", 404)
        directory = self.settings.jobs_dir / job_id
        if directory.is_symlink() or not directory.resolve().is_relative_to(
            self.settings.jobs_dir.resolve()
        ):
            raise AppError("invalid_path", "任務路徑無效。", 400)
        return directory

    def get(self, job_id):
        with self.lock:
            self.directory(job_id)
            if job_id not in self.metas:
                raise AppError("not_found", "找不到任務。", 404)
            return self.timing_snapshot(self.metas[job_id])

    def timing_snapshot(self, meta):
        result = meta.model_copy(deep=True)
        now = time.monotonic()
        job_id = meta.job_id
        if job_id in self.started:
            result.elapsed_sec = now - self.started[job_id]
        if job_id in self.paused_started:
            result.paused_sec += now - self.paused_started[job_id]
        result.active_elapsed_sec = max(0, result.elapsed_sec - result.paused_sec)
        result.control_wait_sec = (
            now - self.control_requested[job_id] if job_id in self.control_requested else 0
        )
        return result

    def end_pause(self, job_id):
        start = self.paused_started.pop(job_id, None)
        if start is not None:
            self.metas[job_id].paused_sec += time.monotonic() - start

    def save(self, meta):
        meta.updated_at = now_iso()
        directory = self.directory(meta.job_id)
        temp = directory / "metadata.tmp"
        with temp.open("w", encoding="utf-8") as handle:
            handle.write(self.timing_snapshot(meta).model_dump_json(indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        # Windows 讀取者／掃描器短暫持有檔案時，原子替換可能暫時被拒絕。
        for attempt in range(6):
            try:
                os.replace(temp, directory / "metadata.json")
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.02 * (attempt + 1))

    def restore(self):
        for directory in self.settings.jobs_dir.iterdir():
            if not directory.is_dir() or directory.is_symlink():
                continue
            if not directory.resolve().is_relative_to(self.settings.jobs_dir.resolve()):
                continue
            if not re.fullmatch(r"[a-f0-9]{32}", directory.name):
                continue
            warnings = []
            try:
                meta = JobMeta.model_validate_json((directory / "metadata.json").read_text("utf-8"))
                if meta.job_id != directory.name:
                    raise ValueError
            except Exception:
                meta = JobMeta(
                    job_id=directory.name,
                    request=JobRequest(source_name="壞損任務", expected_bytes=1),
                    status=Status.FAILED,
                    error="任務資訊損毀；可刪除此項目。",
                )
            rows = []
            path = directory / "segments.jsonl"
            if path.exists():
                good = bytearray()
                with path.open("rb") as handle:
                    for line in handle:
                        try:
                            segment = TranscriptSegment.model_validate_json(line)
                            if segment.segment_id != len(rows) + 1 or not line.endswith(b"\n"):
                                raise ValueError
                        except Exception:
                            warnings.append("片段檔尾端不完整，已保留先前完整片段。")
                            break
                        rows.append(segment)
                        good.extend(line)
                if warnings:
                    path.write_bytes(good)
            # 未完成任務可能因 JSONL 先於 metadata 落盤而多出完整片段；
            # 已完成任務的片段數則必須與最後保存的快照一致。
            if meta.status == Status.COMPLETED and len(rows) != meta.segment_count:
                warnings.append("已保存片段數與完成紀錄不符，結果可能遺失，請核對內容。")
            if meta.status not in TERMINAL:
                meta.status = Status.INTERRUPTED
            meta.execution_state = ExecutionState.RUNNING
            meta.control_wait_sec = 0
            if warnings and meta.status == Status.COMPLETED:
                meta.status = Status.INTERRUPTED
                meta.error = "部分保存資料損毀，請核對已恢復內容。"
            meta.warnings.extend(warnings)
            meta.segment_count = len(rows)
            meta.last_position_sec = max(
                (s.end_sec for s in rows if math.isfinite(s.end_sec) and s.end_sec >= 0), default=0
            )
            self.metas[meta.job_id] = meta
            self.segments[meta.job_id] = rows
            (directory / "media.upload").unlink(missing_ok=True)
            self.save(meta)

    def create(self, request: JobRequest):
        with self.lock:
            if self.active:
                raise AppError("busy", "已有活動任務，請完成或取消後再試。", 409)
            if request.expected_bytes > self.settings.max_upload_bytes:
                raise AppError("size_limit", "檔案超過上傳上限。", 413)
            if Path(request.source_name).suffix.lower() not in {".mp4", ".mp3", ".wav", ".m4a"}:
                raise AppError("unsupported_file", "請選擇 MP4、MP3、WAV 或 M4A。")
            if self.check_model:
                model_path(self.settings, request.model)
            free = shutil.disk_usage(self.settings.jobs_dir).free
            if free < request.expected_bytes + self.settings.disk_reserve_bytes:
                raise AppError("disk_full", "磁碟空間不足。", 507)
            meta = JobMeta(
                job_id=uuid4().hex,
                request=request,
                conversion=CONVERSION if request.traditional else "none",
            )
            self.directory(meta.job_id).mkdir()
            self.save(meta)
            self.metas[meta.job_id] = meta
            self.segments[meta.job_id] = []
            self.active = meta.job_id
            self.deadlines[meta.job_id] = time.monotonic() + self.settings.upload_wait_sec
            return meta.model_copy(deep=True)

    def begin_upload(self, job_id):
        with self.lock:
            self.expire()
            meta = self.get(job_id)
            if meta.status != Status.UPLOADING or job_id in self.receiving:
                raise AppError("upload_conflict", "此任務已開始上傳或已終止。", 409)
            self.receiving.add(job_id)
            self.deadlines.pop(job_id, None)
            return self.directory(job_id) / "media.upload"

    def upload_progress(self, job_id, size):
        with self.lock:
            meta = self.metas[job_id]
            if meta.status != Status.UPLOADING:
                raise AppError("cancelled", "上傳已取消。", 409)
            meta.uploaded_bytes = size

    def end_upload(self, job_id):
        with self.lock:
            self.receiving.discard(job_id)

    def finish(self, job_id, status, error=None, code=None):
        with self.lock:
            meta = self.metas[job_id]
            if meta.status in TERMINAL:
                return
            if job_id in self.receiving:
                raise RuntimeError("媒體仍開啟，不能釋放任務。")
            meta.status = Status.CANCELLED if meta.status == Status.CANCELLING else status
            meta.error = None if meta.status == Status.CANCELLED else error
            meta.error_code = None if meta.status == Status.CANCELLED else code
            self.end_pause(job_id)
            self.control_requested.pop(job_id, None)
            meta.execution_state = ExecutionState.RUNNING
            meta.control_wait_sec = 0
            if job_id in self.started:
                meta.elapsed_sec = time.monotonic() - self.started.pop(job_id)
            meta.active_elapsed_sec = max(0, meta.elapsed_sec - meta.paused_sec)
            try:
                self.save(meta)
            finally:
                try:
                    (self.directory(job_id) / "media.upload").unlink(missing_ok=True)
                finally:
                    if self.active == job_id:
                        self.active = None
                    self.deadlines.pop(job_id, None)

    def start(self, job_id):
        with self.lock:
            if self.metas[job_id].status != Status.UPLOADING:
                self.finish(job_id, Status.CANCELLED)
                return
            self.launch(job_id, self.metas[job_id].request)

    def launch(self, job_id, request):
        # 呼叫者持有 lock；回退只在舊工作程序已退出後建立乾淨程序。
        ctx = mp.get_context("spawn")
        receiver, sender = ctx.Pipe(duplex=False)
        cancel, gate = ctx.Event(), ctx.Event()
        pause, sequence = ctx.Event(), ctx.Value("q", 0)
        meta = self.metas[job_id]
        args = (self.directory(job_id) / "media.upload", request, self.settings)
        process = ctx.Process(
            target=worker_entry,
            args=(gate, cancel, sender, self.target, args, pause, sequence),
        )
        job = None
        try:
            process.start()
            sender.close()
            job = JobObject(process)
            meta.status = Status.VALIDATING
            self.save(meta)
            self.runtime[job_id] = (process, cancel, job)
            self.controls[job_id] = (pause, sequence)
            self.started.setdefault(job_id, time.monotonic())
            threading.Thread(target=self.monitor, args=(job_id, receiver), daemon=True).start()
            gate.set()
        except Exception as exc:
            if process.pid is not None:
                stop_process(process, cancel, 0)
            if job is not None:
                job.close()
            sender.close()
            receiver.close()
            self.runtime.pop(job_id, None)
            self.controls.pop(job_id, None)
            self.finish(job_id, Status.FAILED, "無法建立 Windows 工作程序保護。", "job_object")
            raise AppError("job_object", "無法建立 Windows 工作程序保護。", 500) from exc

    def monitor(self, job_id, receiver):
        process, cancel, job = self.runtime[job_id]
        start = time.monotonic()
        outcome, error, code = Status.FAILED, "工作程序意外結束。", "worker_exit"
        retry = False
        try:
            while True:
                if not receiver.poll(0.1):
                    if not process.is_alive():
                        break
                    continue
                try:
                    kind, payload = receiver.recv()
                except EOFError:
                    break
                with self.lock:
                    meta = self.metas[job_id]
                    meta.elapsed_sec = time.monotonic() - start
                    if kind == "done":
                        outcome, error, code = Status.COMPLETED, None, None
                        break
                    if kind == "error":
                        error, code = payload["message"], payload["code"]
                        retry = (
                            meta.request.device == "auto"
                            and meta.actual_device == "cuda"
                            and meta.fallback_reason is None
                            and code in CUDA_ERRORS
                            and meta.segment_count == 0
                            and meta.status != Status.CANCELLING
                        )
                        break
                    if meta.status == Status.CANCELLING:
                        continue
                    if kind == "device":
                        meta.actual_device = payload["actual_device"]
                        meta.actual_device_index = payload["actual_device_index"]
                        meta.compute_type = payload["compute_type"]
                        if payload.get("fallback_reason"):
                            meta.fallback_reason = payload["fallback_reason"]
                        self.save(meta)
                    elif kind == "control":
                        self.ack_control(job_id, payload)
                    elif kind == "status":
                        stage = Status(payload["status"])
                        order = [
                            Status.VALIDATING,
                            Status.LOADING_MODEL,
                            Status.DECODING,
                            Status.TRANSCRIBING,
                        ]
                        if stage not in order or order.index(stage) < order.index(meta.status):
                            raise ValueError("工作階段順序異常")
                        meta.status = stage
                        self.save(meta)
                    elif kind == "info":
                        meta.duration_sec = payload["duration_sec"]
                        meta.versions = payload["versions"]
                        meta.model_revision = payload["model_revision"]
                        self.save(meta)
                    elif kind == "segment":
                        segment = TranscriptSegment.model_validate(payload)
                        if segment.segment_id != len(self.segments[job_id]) + 1:
                            raise ValueError("片段序號異常")
                        with (self.directory(job_id) / "segments.jsonl").open(
                            "a", encoding="utf-8"
                        ) as h:
                            h.write(segment.model_dump_json() + "\n")
                            h.flush()
                            os.fsync(h.fileno())
                        self.segments[job_id].append(segment)
                        meta.segment_count += 1
                        if math.isfinite(segment.end_sec) and segment.end_sec >= 0:
                            meta.last_position_sec = max(meta.last_position_sec, segment.end_sec)
                        self.save(meta)
        except Exception as exc:
            frame = traceback.extract_tb(exc.__traceback__)[-1]
            # 不記錄例外字串或 payload，避免 ValidationError 洩漏逐字稿內容。
            diagnostic = (
                f"{type(exc).__name__} at {Path(frame.filename).name}:{frame.lineno}; "
                f"errno={getattr(exc, 'errno', None)}, winerror={getattr(exc, 'winerror', None)}"
            )
            logging.getLogger(__name__).error("任務 %s：%s", job_id, diagnostic)
            with self.lock:
                self.metas[job_id].warnings.append(diagnostic)
            outcome, error, code = Status.FAILED, "無法保存或接收轉錄結果。", "storage_or_ipc"
        finally:
            stopped = stop_process(process, cancel, self.settings.cancel_grace_sec)
            job.close()
            receiver.close()
            with self.lock:
                self.runtime.pop(job_id, None)
                self.controls.pop(job_id, None)
                self.metas[job_id].elapsed_sec = time.monotonic() - start
                meta = self.metas[job_id]
                if (
                    retry
                    and stopped
                    and meta.status not in TERMINAL
                    and meta.status != Status.CANCELLING
                ):
                    self.end_pause(job_id)
                    self.control_requested.pop(job_id, None)
                    meta.execution_state = ExecutionState.RUNNING
                    meta.fallback_reason = code
                    meta.actual_device = None
                    meta.actual_device_index = None
                    try:
                        self.save(meta)
                        self.launch(job_id, meta.request.model_copy(update={"device": "cpu"}))
                    except Exception:
                        self.finish(
                            job_id,
                            Status.FAILED,
                            "無法建立或保存 CPU 回退任務。",
                            "fallback_failed",
                        )
                else:
                    self.finish(job_id, outcome, error, code)

    def set_paused(self, job_id, paused):
        with self.lock:
            meta = self.metas.get(job_id)
            if meta is None:
                self.get(job_id)
            if meta.status != Status.TRANSCRIBING or job_id not in self.controls:
                raise AppError("control_unavailable", "只能暫停或繼續正在轉錄的任務。", 409)
            state = meta.execution_state
            if paused and state in {ExecutionState.PAUSE_REQUESTED, ExecutionState.PAUSED}:
                return self.get(job_id)
            if not paused and state in {ExecutionState.RUNNING, ExecutionState.RESUME_REQUESTED}:
                return self.get(job_id)
            if state in {ExecutionState.PAUSE_REQUESTED, ExecutionState.RESUME_REQUESTED}:
                raise AppError("control_pending", "請等待目前的暫停或繼續操作完成。", 409)
            previous = meta.model_copy(deep=True)
            meta.execution_state = (
                ExecutionState.PAUSE_REQUESTED if paused else ExecutionState.RESUME_REQUESTED
            )
            meta.control_seq += 1
            try:
                self.save(meta)
            except Exception:
                self.metas[job_id] = previous
                raise
            event, sequence = self.controls[job_id]
            with sequence.get_lock():
                sequence.value = meta.control_seq
                event.set() if paused else event.clear()
            self.control_requested[job_id] = time.monotonic()
            return self.get(job_id)

    def ack_control(self, job_id, payload):
        # 由 monitor 持鎖呼叫；忽略終態、取消及過期確認。
        meta = self.metas[job_id]
        if meta.status != Status.TRANSCRIBING or payload.get("sequence") != meta.control_seq:
            return
        expected = {
            ExecutionState.PAUSE_REQUESTED: "paused",
            ExecutionState.RESUME_REQUESTED: "running",
        }.get(meta.execution_state)
        if expected is None or payload.get("state") != expected:
            return
        if expected == "paused":
            self.paused_started[job_id] = time.monotonic()
            meta.execution_state = ExecutionState.PAUSED
        else:
            self.end_pause(job_id)
            meta.execution_state = ExecutionState.RUNNING
        self.control_requested.pop(job_id, None)
        self.save(meta)

    def cancel(self, job_id):
        with self.lock:
            meta = self.metas.get(job_id)
            if meta is None:
                self.get(job_id)
            if meta.status in TERMINAL or meta.status == Status.CANCELLING:
                return
            meta.status = Status.CANCELLING
            self.end_pause(job_id)
            self.control_requested.pop(job_id, None)
            meta.execution_state = ExecutionState.RUNNING
            self.save(meta)
            runtime = self.runtime.get(job_id)
            if runtime:
                process, event, _ = runtime
                threading.Thread(
                    target=stop_process,
                    args=(process, event, self.settings.cancel_grace_sec),
                    daemon=True,
                ).start()
            elif job_id not in self.receiving:
                self.finish(job_id, Status.CANCELLED)

    def expire(self):
        with self.lock:
            for job_id, deadline in list(self.deadlines.items()):
                if time.monotonic() >= deadline:
                    self.finish(job_id, Status.FAILED, "等待上傳逾時。", "upload_timeout")

    def delete(self, job_id):
        with self.lock:
            if self.get(job_id).status not in TERMINAL:
                raise AppError("busy", "活動任務不可刪除。", 409)
            shutil.rmtree(self.directory(job_id))
            del self.metas[job_id]
            del self.segments[job_id]

    def close(self):
        if self.active:
            self.cancel(self.active)
        deadline = time.monotonic() + 5
        while self.runtime and time.monotonic() < deadline:
            time.sleep(0.05)
