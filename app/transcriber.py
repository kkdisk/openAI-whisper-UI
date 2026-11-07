"""在工作子程序內進行離線解碼與辨識。"""

import json
from importlib.metadata import version
from pathlib import Path

from app.config import Settings
from app.devices import CUDA_ERRORS, cuda_error, select_device
from app.formatter import convert_text
from app.models import model_path, offline_mode
from app.schemas import AppError, JobRequest, TranscriptSegment


def transcription_options(request: JobRequest) -> dict:
    return {
        "language": None if request.language in {"auto", "mixed"} else request.language,
        "task": "transcribe",
        "beam_size": 5,
        "initial_prompt": "這是一場中文為主、包含英文專有名詞的會議。 " + request.terms.strip(),
        "vad_filter": True,
        "multilingual": request.language == "mixed",
        "condition_on_previous_text": request.condition_on_previous_text,
    }


def decode_media(path: Path, max_duration_sec: int, cancelled=lambda: False):
    import av
    import numpy as np

    chunks = []
    count = 0
    try:
        with av.open(str(path)) as container:
            if not container.streams.audio:
                raise AppError("no_audio", "檔案沒有可辨識的音軌。")
            stream = container.streams.audio[0]
            if stream.duration and stream.time_base:
                if float(stream.duration * stream.time_base) > max_duration_sec:
                    raise AppError("duration_limit", "音軌超過允許的時長。")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            for frame in container.decode(stream):
                if cancelled():
                    raise AppError("cancelled", "已取消。")
                for out in resampler.resample(frame):
                    array = out.to_ndarray().reshape(-1)
                    count += array.size
                    if count > max_duration_sec * 16000:
                        raise AppError("duration_limit", "音軌超過允許的時長。")
                    chunks.append(array.copy())
            for out in resampler.resample(None):
                array = out.to_ndarray().reshape(-1)
                count += array.size
                chunks.append(array.copy())
    except AppError:
        raise
    except Exception as exc:
        raise AppError("decode_failed", "無法解碼音軌，可能損毀或編碼不支援。") from exc
    if count == 0:
        raise AppError("empty_audio", "音軌沒有有效音訊。")
    if count > max_duration_sec * 16000:
        raise AppError("duration_limit", "音軌超過允許的時長。")
    return np.concatenate(chunks).astype("float32") / 32768.0


def controlled_segments(segments, cancelled):
    """取下一筆前暫停；不丟棄已由原生推論產生的片段。"""
    iterator = iter(segments)
    while not cancelled():
        checkpoint = getattr(cancelled, "checkpoint", None)
        if checkpoint is not None and not checkpoint():
            return
        try:
            segment = next(iterator)
        except StopIteration:
            return
        if cancelled():
            return
        yield segment


def transcribe(path: Path, request: JobRequest, settings: Settings, emit, cancelled):
    selection = select_device(request.device, request.compute_type, request.device_index)
    emit(
        "device",
        {
            "actual_device": selection.device,
            "actual_device_index": selection.device_index if selection.device == "cuda" else None,
            "compute_type": selection.compute_type,
            "fallback_reason": selection.fallback_reason,
        },
    )
    try:
        _transcribe(path, request, settings, emit, cancelled, selection)
    except Exception as exc:
        code = cuda_error(exc) if selection.device == "cuda" else None
        if code:
            raise AppError(code, CUDA_ERRORS[code]) from exc
        raise


def _transcribe(path, request, settings, emit, cancelled, selection):
    offline_mode()
    emit("status", {"status": "loading_model"})
    from faster_whisper import WhisperModel
    from faster_whisper.vad import get_vad_model

    try:
        get_vad_model()
    except Exception as exc:
        raise AppError(
            "vad_unavailable", "VAD 資產或 onnxruntime 無法載入，請重新準備套件環境。"
        ) from exc

    directory = model_path(settings, request.model)
    model = WhisperModel(
        str(directory),
        **selection.model_kwargs(),
        cpu_threads=request.cpu_threads,
        local_files_only=True,
    )
    options = transcription_options(request)
    underlying = getattr(model, "model", None)
    if underlying is not None:
        emit(
            "device",
            {
                "actual_device": selection.device,
                "actual_device_index": selection.device_index
                if selection.device == "cuda"
                else None,
                "compute_type": str(underlying.compute_type),
                "fallback_reason": selection.fallback_reason,
            },
        )
    prompt = options["initial_prompt"]
    tokens = model.hf_tokenizer.encode(" " + prompt.strip()).ids
    if len(tokens) > settings.prompt_tokens:
        raise AppError("prompt_limit", "專有名詞提示過長，請縮短至完整提示 200 tokens 以內。")
    emit("status", {"status": "decoding"})
    audio = decode_media(path, settings.max_duration_sec, cancelled)
    manifest = json.loads((directory / "ready.json").read_text(encoding="utf-8"))
    emit(
        "info",
        {
            "duration_sec": len(audio) / 16000,
            "model_revision": manifest["revision"],
            "versions": {
                name: version(name)
                for name in (
                    "faster-whisper",
                    "ctranslate2",
                    "av",
                    "onnxruntime",
                    "opencc-python-reimplemented",
                )
            },
        },
    )
    if cancelled():
        return
    emit("status", {"status": "transcribing"})
    segments, _ = model.transcribe(audio, **options)
    for i, segment in enumerate(controlled_segments(segments, cancelled), 1):
        if cancelled():
            return
        result = TranscriptSegment(
            segment_id=i,
            start_sec=segment.start,
            end_sec=segment.end,
            raw_text=segment.text.strip(),
            text=convert_text(segment.text.strip(), request.traditional),
        )
        emit("segment", result.model_dump())
