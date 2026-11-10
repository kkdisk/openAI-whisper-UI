"""單一程序的本機 HTTP 服務。"""

import asyncio
import contextlib
import hashlib
import json
import secrets
import socket
import time
import webbrowser
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import ValidationError
from starlette.staticfiles import StaticFiles

from app.config import ROOT, Settings
from app.formatter import export, export_name
from app.jobs import JobManager
from app.models import list_models, offline_mode
from app.revisions import RevisionWrite, read_revisions, revised_segments, write_revision
from app.schemas import (
    TERMINAL,
    AppError,
    ContinueRequest,
    ErrorResponse,
    JobRequest,
    ProgressData,
    Status,
    TerminalData,
)


class LocalAccess:
    """在讀取請求內容之前驗證本機來源與記憶體憑證。"""

    def __init__(self, app, session, csrf, port):
        self.app, self.session, self.csrf = app, session, csrf
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        host = headers.get("host", "")
        origin = headers.get("origin")
        allowed = (
            host in self.hosts
            and (origin is None or origin == f"http://{host}")
            and headers.get("sec-fetch-site") != "cross-site"
        )
        request = Request(scope)
        path = scope["path"]
        if path.startswith("/api/"):
            allowed = allowed and secrets.compare_digest(
                request.cookies.get("session", ""), self.session
            )
            if scope["method"] not in {"GET", "HEAD"}:
                allowed = allowed and secrets.compare_digest(
                    headers.get("x-csrf-token", ""), self.csrf
                )
        if not allowed:
            response = JSONResponse(
                {"code": "local_access", "message": "請由本機工具頁面操作。"}, 403
            )
            return await response(scope, receive, send)

        async def safe_send(message):
            if message["type"] == "http.response.start":
                message["headers"].extend(
                    [
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                        (
                            b"content-security-policy",
                            b"default-src 'self'; frame-ancestors 'none'; "
                            b"object-src 'none'; base-uri 'none'",
                        ),
                    ]
                )
            await send(message)

        await self.app(scope, receive, safe_send)


def write_chunk(handle, digest, chunk):
    handle.write(chunk)
    digest.update(chunk)


async def read_json(request, limit):
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise AppError("json_limit", "設定內容過大。", 413)
        body.extend(chunk)
    try:
        return json.loads(body)
    except (ValueError, UnicodeError) as exc:
        raise AppError("invalid_json", "設定必須為有效 JSON。") from exc


def create_app(settings: Settings | None = None, manager: JobManager | None = None):
    settings = settings or Settings()
    offline_mode()
    manager = manager or JobManager(settings)
    session, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        async def expire():
            while True:
                await asyncio.to_thread(manager.expire)
                await asyncio.sleep(0.5)

        task = asyncio.create_task(expire())
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await asyncio.to_thread(manager.close)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.manager = manager
    app.add_middleware(LocalAccess, session=session, csrf=csrf, port=settings.port)

    @app.exception_handler(AppError)
    async def app_error(request, exc):
        return JSONResponse(
            ErrorResponse(code=exc.code, message=exc.message).model_dump(), exc.status_code
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"code": "invalid_parameter", "message": "參數格式不正確。"}, 422)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return JSONResponse(
            {"code": "internal_error", "message": "本機操作失敗，請檢查磁碟與環境。"}, 500
        )

    @app.get("/")
    async def index():
        response = FileResponse(ROOT / "app" / "static" / "index.html")
        response.set_cookie("session", session, httponly=True, samesite="strict")
        return response

    @app.get("/api/session")
    async def bootstrap():
        return {
            "csrf": csrf,
            "max_upload_bytes": settings.max_upload_bytes,
            "max_duration_sec": settings.max_duration_sec,
        }

    @app.get("/api/models")
    async def models():
        return await asyncio.to_thread(list_models, settings)

    @app.post("/api/jobs", status_code=201)
    async def create(request: Request):
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise AppError("content_type", "建立任務必須使用 JSON。", 415)
        body = await read_json(request, settings.max_json_bytes)
        try:
            data = JobRequest.model_validate(body)
        except ValidationError as exc:
            raise AppError("invalid_parameter", "請檢查檔名、大小、模型及設定。", 422) from exc
        return await asyncio.to_thread(manager.create, data)

    @app.put("/api/jobs/{job_id}/media", status_code=202)
    async def media(job_id: str, request: Request):
        if request.headers.get("content-type", "").split(";")[0] != "application/octet-stream":
            raise AppError("content_type", "媒體上傳必須使用原始位元組串流。", 415)
        path = await asyncio.to_thread(manager.begin_upload, job_id)
        expected = manager.get(job_id).request.expected_bytes
        started, last = time.monotonic(), time.monotonic()
        size = 0
        digest = hashlib.sha256()
        failure = None
        handle = None
        stream = request.stream().__aiter__()
        pending = asyncio.create_task(stream.__anext__())
        try:
            handle = await asyncio.to_thread(path.open, "xb")
            while True:
                done, _ = await asyncio.wait({pending}, timeout=0.2)
                if manager.get(job_id).status != Status.UPLOADING:
                    raise AppError("cancelled", "上傳已取消。", 409)
                now = time.monotonic()
                if (
                    now - last > settings.upload_idle_sec
                    or now - started > settings.upload_total_sec
                ):
                    raise AppError("upload_timeout", "上傳逾時。", 408)
                if not done:
                    continue
                try:
                    chunk = pending.result()
                except StopAsyncIteration:
                    break
                if chunk:
                    size += len(chunk)
                    if size > expected or size > settings.max_upload_bytes:
                        raise AppError("size_limit", "實際檔案大小超過宣告或限制。", 413)
                    await asyncio.to_thread(write_chunk, handle, digest, chunk)
                    await asyncio.to_thread(manager.upload_progress, job_id, size)
                    last = now
                pending = asyncio.create_task(stream.__anext__())
            if size != expected:
                raise AppError("size_mismatch", "收到的檔案大小與宣告不符。")
            await asyncio.to_thread(manager.record_source, job_id, digest.hexdigest())
        except BaseException as exc:
            failure = exc
        finally:
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pending
            if handle is not None:
                try:
                    await asyncio.to_thread(handle.close)
                except Exception as exc:
                    # close 也可能在 flush 時失敗；仍須釋放 receiving 及活動槽位。
                    if failure is None:
                        failure = exc
            try:
                await asyncio.to_thread(manager.end_upload, job_id)
            finally:
                if failure is not None:
                    code = failure.code if isinstance(failure, AppError) else "upload_failed"
                    message = (
                        failure.message
                        if isinstance(failure, AppError)
                        else "上傳中斷或磁碟寫入失敗。"
                    )
                    await asyncio.to_thread(manager.finish, job_id, Status.FAILED, message, code)
        if failure is not None:
            if isinstance(failure, AppError):
                raise failure
            raise AppError("upload_failed", "上傳中斷或磁碟寫入失敗。") from failure
        await asyncio.to_thread(manager.start, job_id)
        return manager.get(job_id)

    @app.post("/api/jobs/{job_id}/continue", status_code=201)
    async def continue_job(job_id: str, request: Request):
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise AppError("content_type", "續跑必須使用 JSON。", 415)
        body = await read_json(request, settings.max_json_bytes)
        try:
            data = ContinueRequest.model_validate(body)
        except ValidationError as exc:
            raise AppError("invalid_parameter", "請檢查檔案大小、模型及設定。", 422) from exc
        return await asyncio.to_thread(manager.continue_job, job_id, data)

    @app.get("/api/jobs")
    async def jobs(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
        with manager.lock:
            rows = sorted(manager.metas.values(), key=lambda m: m.created_at, reverse=True)
            return {
                "items": [manager.get(m.job_id) for m in rows[offset : offset + limit]],
                "total": len(rows),
                "active_id": manager.active,
            }

    @app.get("/api/jobs/latest")
    async def latest():
        with manager.lock:
            if manager.active:
                return manager.get(manager.active)
            return (
                manager.get(max(manager.metas.values(), key=lambda m: m.created_at).job_id)
                if manager.metas
                else None
            )

    @app.get("/api/jobs/{job_id}")
    async def job(job_id: str):
        return manager.get(job_id)

    @app.get("/api/jobs/{job_id}/segments")
    async def segments(
        job_id: str, after_id: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500)
    ):
        with manager.lock:
            manager.get(job_id)
            return manager.segments[job_id][after_id : after_id + limit]

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel(job_id: str):
        await asyncio.to_thread(manager.cancel, job_id)
        return manager.get(job_id)

    @app.post("/api/jobs/{job_id}/pause")
    async def pause(job_id: str):
        return await asyncio.to_thread(manager.set_paused, job_id, True)

    @app.post("/api/jobs/{job_id}/resume")
    async def resume(job_id: str):
        return await asyncio.to_thread(manager.set_paused, job_id, False)

    @app.get("/api/jobs/{job_id}/revisions")
    def revisions(job_id: str):
        with manager.lock:
            manager.get(job_id)
            return read_revisions(manager.directory(job_id))

    @app.put("/api/jobs/{job_id}/revisions")
    async def revise(job_id: str, request: Request):
        body = await read_json(request, settings.max_json_bytes)
        try:
            change = RevisionWrite.model_validate(body)
        except ValidationError as exc:
            raise AppError("invalid_revision", "修訂格式不正確或文字過長。", 422) from exc

        def save_revision():
            with manager.lock:
                manager.get(job_id)
                return write_revision(manager.directory(job_id), manager.segments[job_id], change)

        return await asyncio.to_thread(save_revision)

    @app.delete("/api/jobs/{job_id}", status_code=204)
    async def delete(job_id: str):
        await asyncio.to_thread(manager.delete, job_id)
        return Response(status_code=204)

    @app.get("/api/jobs/{job_id}/export")
    async def download(job_id: str, format: str = "txt", variant: str = "display"):
        if variant not in {"display", "raw"}:
            raise AppError("invalid_variant", "不支援的文字版本。")
        with manager.lock:
            meta = manager.get(job_id)
            rows = list(manager.segments[job_id])
            if variant != "raw":
                rows = revised_segments(rows, read_revisions(manager.directory(job_id)))
        text = await asyncio.to_thread(export, meta, rows, format, variant == "raw")
        name = quote(export_name(meta, format, variant == "raw"))
        return Response(
            text.encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{name}",
            },
        )

    @app.get("/api/jobs/{job_id}/events")
    async def events(job_id: str, request: Request, after_id: int = Query(0, ge=0)):
        manager.get(job_id)

        async def generate():
            after = after_id
            sequence = 0

            def event(kind, data):
                nonlocal sequence
                sequence += 1
                payload = json.dumps(
                    {"job_id": job_id, "event_id": sequence, **data}, ensure_ascii=False
                )
                return f"id: {sequence}\nevent: {kind}\ndata: {payload}\n\n"

            while not await request.is_disconnected():
                with manager.lock:
                    meta = manager.get(job_id)
                    rows = list(manager.segments[job_id][after : after + 200])
                yield event("status", meta.model_dump(mode="json"))
                for row in rows:
                    yield event("segment", row.model_dump(mode="json"))
                    after = row.segment_id
                yield event(
                    "progress",
                    ProgressData(
                        position_sec=meta.last_position_sec, duration_sec=meta.duration_sec
                    ).model_dump(),
                )
                if meta.status in TERMINAL and after >= meta.segment_count:
                    kind = (
                        "done"
                        if meta.status == Status.COMPLETED
                        else "cancelled"
                        if meta.status == Status.CANCELLED
                        else "error"
                    )
                    yield event(
                        kind,
                        TerminalData(status=meta.status, message=meta.error).model_dump(
                            mode="json"
                        ),
                    )
                    break
                await asyncio.sleep(1)

        return StreamingResponse(generate(), media_type="text/event-stream")

    app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")
    return app


def main():
    import threading

    import uvicorn

    settings = Settings()
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", settings.port))
        sock.listen(128)
    except OSError:
        sock.close()
        raise SystemExit("8000 埠已被占用，請關閉衝突服務後再啟動。") from None
    app = create_app(settings)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=settings.port,
            log_level="warning",
            access_log=False,
            timeout_graceful_shutdown=5,
        )
    )

    def open_when_ready():
        while not server.started and not server.should_exit:
            time.sleep(0.1)
        if server.started:
            webbrowser.open(f"http://127.0.0.1:{settings.port}")

    threading.Thread(target=open_when_ready, daemon=True).start()
    try:
        print("本機逐字稿工具：http://127.0.0.1:8000　停止服務請按 Ctrl+C。")
        server.run(sockets=[sock])
    except KeyboardInterrupt:
        # Python 3.10 的 Uvicorn 相容層會在正常關機後重新拋出 SIGINT。
        print("本機逐字稿服務已停止。")
    finally:
        # 也涵蓋 lifespan 尚未開始就中斷的情況；close 必須可重複呼叫。
        try:
            app.state.manager.close()
        finally:
            sock.close()


if __name__ == "__main__":
    main()
