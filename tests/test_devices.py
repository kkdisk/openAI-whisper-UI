"""裝置政策與真實 Windows 程序回退邊界。"""

import os
import time
from pathlib import Path

import pytest

from app.config import ROOT, Settings
from app.devices import CUDA_ERRORS, cuda_error, select_device
from app.jobs import JobManager
from app.schemas import AppError, JobMeta, JobRequest, Status
from tests.helpers import cuda_failure_transcribe, wait_terminal


def test_cpu_and_legacy_never_query_cuda(monkeypatch):
    import ctranslate2

    def forbidden(*args):
        raise AssertionError("CPU 不應查詢 CUDA")

    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", forbidden)
    monkeypatch.setattr("app.devices.prepare_cuda_runtime", forbidden)
    request = JobRequest(source_name="test.wav", expected_bytes=1)
    assert request.device == "cpu"
    assert JobMeta(job_id="a" * 32, request=request).actual_device is None
    assert select_device(request.device).model_kwargs()["compute_type"] == "int8"


def test_cuda_precision_index_and_missing_device(monkeypatch):
    import ctranslate2

    monkeypatch.setattr("app.devices.prepare_cuda_runtime", lambda: None)
    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: 2)
    calls = []

    def types(device, index):
        calls.append((device, index))
        return {"float16", "int8_float16"}

    monkeypatch.setattr(ctranslate2, "get_supported_compute_types", types)
    assert select_device("auto", device_index=1).compute_type == "int8_float16"
    assert calls == [("cuda", 1)]
    assert select_device("cuda", "float16", 1).compute_type == "float16"
    assert select_device("auto", device_index=2).fallback_reason == "cuda_unavailable"
    with pytest.raises(AppError) as exc:
        select_device("cuda", device_index=2)
    assert exc.value.code == "cuda_unavailable"
    monkeypatch.setattr(ctranslate2, "get_supported_compute_types", lambda *a: {"float32"})
    assert select_device("auto").fallback_reason == "cuda_compute"


@pytest.mark.parametrize(
    "message,code",
    [
        ("CUDA failed with error out of memory", "cuda_oom"),
        ("Library cublas64_12.dll is not found or cannot be loaded", "cuda_runtime"),
        ("Library cudnn_ops64_9.dll is missing", "cuda_runtime"),
        ("corrupt model", None),
        ("invalid audio", None),
        ("out of memory", None),
    ],
)
def test_classification_is_narrow(message, code):
    assert cuda_error(RuntimeError(message)) == code
    assert cuda_error(AppError("prompt_limit", "cublas")) is None


def manager_job(tmp_path, device="auto", title="", code="cuda_oom"):
    manager = JobManager(
        Settings(root=tmp_path, disk_reserve_bytes=0), cuda_failure_transcribe, False
    )
    meta = manager.create(
        JobRequest(
            source_name="speech.wav", expected_bytes=1, device=device, title=title, terms=code
        )
    )
    manager.begin_upload(meta.job_id).write_bytes(b"x")
    manager.end_upload(meta.job_id)
    manager.start(meta.job_id)
    return manager, meta.job_id


@pytest.mark.parametrize("code", CUDA_ERRORS)
def test_auto_restarts_clean_process_only_before_first_segment(tmp_path, code):
    manager, job_id = manager_job(tmp_path, code=code)
    meta = wait_terminal(manager, job_id)
    assert meta.status == Status.COMPLETED
    assert meta.request.device == "auto"
    assert meta.actual_device == "cpu" and meta.compute_type == "int8"
    assert meta.fallback_reason == code and meta.segment_count == 1
    attempts = (tmp_path / "attempts.txt").read_text().splitlines()
    assert len(attempts) == 2
    assert attempts[0].split()[0] != attempts[1].split()[0]
    assert not (manager.directory(job_id) / "media.upload").exists()
    restored = JobManager(manager.settings, check_model=False).get(job_id)
    assert restored.fallback_reason == code and restored.actual_device == "cpu"


@pytest.mark.parametrize(
    "device,title,code",
    [
        ("cuda", "", "cuda_oom"),
        ("auto", "after_segment", "cuda_oom"),
        ("auto", "", "prompt_limit"),
    ],
)
def test_no_retry_for_explicit_cuda_partial_or_non_cuda_error(tmp_path, device, title, code):
    manager, job_id = manager_job(tmp_path, device, title, code)
    meta = wait_terminal(manager, job_id)
    assert meta.status == Status.FAILED and meta.error_code == code
    assert meta.segment_count == (1 if title == "after_segment" else 0)
    assert len((tmp_path / "attempts.txt").read_text().splitlines()) == 1


def test_cancel_does_not_fallback(tmp_path):
    manager, job_id = manager_job(tmp_path, title="wait_cancel")
    deadline = time.monotonic() + 5
    while manager.get(job_id).status != Status.TRANSCRIBING:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    manager.cancel(job_id)
    assert wait_terminal(manager, job_id).status == Status.CANCELLED
    assert len((tmp_path / "attempts.txt").read_text().splitlines()) == 1


@pytest.mark.parametrize("before_first", [True, False])
def test_lazy_cuda_failure_is_classified_without_discarding_emitted_text(
    tmp_path, monkeypatch, before_first
):
    from types import SimpleNamespace

    import faster_whisper
    import numpy as np

    from app.devices import DeviceSelection
    from app.transcriber import transcribe

    (tmp_path / "ready.json").write_text('{"revision":"fixed"}', encoding="utf-8")
    monkeypatch.setattr(
        "app.transcriber.select_device", lambda *a: DeviceSelection("cuda", "float16")
    )
    monkeypatch.setattr("app.transcriber.model_path", lambda *a: tmp_path)
    monkeypatch.setattr("app.transcriber.decode_media", lambda *a: np.zeros(16000, np.float32))

    def segments():
        if not before_first:
            yield SimpleNamespace(start=0, end=1, text="Saved original")
        raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")

    model = SimpleNamespace(
        hf_tokenizer=SimpleNamespace(encode=lambda t: SimpleNamespace(ids=[1])),
        transcribe=lambda *a, **kw: (segments(), None),
    )
    monkeypatch.setattr(faster_whisper, "WhisperModel", lambda *a, **kw: model)
    events = []
    with pytest.raises(AppError) as error:
        transcribe(
            tmp_path / "audio.wav",
            JobRequest(source_name="audio.wav", expected_bytes=1, device="cuda"),
            Settings(),
            lambda k, p: events.append((k, p)),
            lambda: False,
        )
    assert error.value.code == "cuda_runtime"
    emitted = [p for k, p in events if k == "segment"]
    assert len(emitted) == (0 if before_first else 1)
    if emitted:
        assert emitted[0]["raw_text"] == "Saved original"


@pytest.mark.gpu
@pytest.mark.parametrize(
    "device,precision", [("cuda", "int8_float16"), ("cuda", "float16"), ("auto", "auto")]
)
def test_real_gpu_job_and_exports(tmp_path, device, precision):
    from app.formatter import export
    from app.transcriber import transcribe

    source = Path(os.environ.get("WHISPER_TEST_AUDIO", ROOT / "temp/fixtures/meeting.wav"))
    if not source.is_file():
        pytest.skip("缺少本機語音素材，請設定 WHISPER_TEST_AUDIO")
    # spawn 必須使用模組最上層可 pickle 的 Settings 類別。
    manager = JobManager(GpuSettings(root=tmp_path, disk_reserve_bytes=0), transcribe)
    meta = manager.create(
        JobRequest(
            source_name="speech.wav",
            expected_bytes=source.stat().st_size,
            device=device,
            compute_type=precision,
            language="en",
        )
    )
    manager.begin_upload(meta.job_id).write_bytes(source.read_bytes())
    manager.end_upload(meta.job_id)
    manager.start(meta.job_id)
    meta = wait_terminal(manager, meta.job_id, timeout=60)
    assert meta.status == Status.COMPLETED, (meta.error_code, meta.error)
    assert meta.actual_device == "cuda" and meta.actual_device_index == 0
    assert meta.fallback_reason is None and meta.segment_count > 0
    assert meta.compute_type == ("int8_float16" if precision == "auto" else precision)
    for fmt in ("txt", "md", "srt"):
        assert export(meta, manager.segments[meta.job_id], fmt).strip()
    restored = JobManager(manager.settings, check_model=False).get(meta.job_id)
    assert restored.actual_device == "cuda" and restored.compute_type == meta.compute_type


class GpuSettings(Settings):
    @property
    def models_dir(self):
        return ROOT / "models"


@pytest.mark.gpu
def test_real_gpu_pause_resume_and_cancel(tmp_path):
    import wave

    from app.schemas import ExecutionState
    from app.transcriber import decode_media, transcribe

    source = Path(os.environ.get("WHISPER_TEST_AUDIO", ROOT / "temp/fixtures/meeting.wav"))
    if not source.is_file():
        pytest.skip("缺少本機語音素材")
    # 重複固定短素材讓控制有足夠操作時間，不作文字品質評估。
    audio = decode_media(source, 30)
    long_source = tmp_path / "long.wav"
    with wave.open(str(long_source), "wb") as handle:
        handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        handle.writeframes((audio * 32767).astype("int16").tobytes() * 10)
    manager = JobManager(GpuSettings(root=tmp_path, disk_reserve_bytes=0), transcribe)
    meta = manager.create(
        JobRequest(
            source_name="long.wav",
            expected_bytes=long_source.stat().st_size,
            device="cuda",
            language="en",
        )
    )
    job_id = meta.job_id
    manager.begin_upload(job_id).write_bytes(long_source.read_bytes())
    manager.end_upload(job_id)
    manager.start(job_id)

    def await_state(predicate):
        deadline = time.monotonic() + 60
        while True:
            current = manager.get(job_id)
            if predicate(current):
                return current
            assert current.status not in {Status.COMPLETED, Status.FAILED}, current.error
            assert time.monotonic() < deadline
            time.sleep(0.01)

    try:
        await_state(lambda m: m.status == Status.TRANSCRIBING)
        for _ in range(2):
            requested = time.monotonic()
            manager.set_paused(job_id, True)
            paused = await_state(lambda m: m.execution_state == ExecutionState.PAUSED)
            latency = time.monotonic() - requested
            time.sleep(0.3)
            assert manager.get(job_id).segment_count == paused.segment_count
            print(f"GPU pause latency: {latency:.3f}s; saved segments: {paused.segment_count}")
            manager.set_paused(job_id, False)
            await_state(lambda m: m.execution_state == ExecutionState.RUNNING)
        await_state(lambda m: m.segment_count > 0)
        manager.set_paused(job_id, True)
        paused = await_state(lambda m: m.execution_state == ExecutionState.PAUSED)
        manager.cancel(job_id)
        result = wait_terminal(manager, job_id)
        assert result.status == Status.CANCELLED
        assert result.segment_count == paused.segment_count and result.segment_count > 0
        assert result.paused_sec >= 0.6 and result.actual_device == "cuda"
        restored = JobManager(manager.settings, check_model=False)
        assert restored.get(job_id).segment_count == result.segment_count
    finally:
        manager.close()
