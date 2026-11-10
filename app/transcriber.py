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


def transcribe(
    path: Path, request: JobRequest, settings: Settings, emit, cancelled, resume: dict | None = None
):
    """resume 為 H50a 續跑資訊：from_sec、first_segment_id、context_text、expected_duration_sec。"""
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
        _transcribe(path, request, settings, emit, cancelled, selection, resume)
    except Exception as exc:
        code = cuda_error(exc) if selection.device == "cuda" else None
        if code:
            raise AppError(code, CUDA_ERRORS[code]) from exc
        raise


def _transcribe(path, request, settings, emit, cancelled, selection, resume=None):
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
    if settings.segmentation == "rolling":
        _transcribe_rolling(model, directory, path, request, settings, emit, cancelled, resume)
        return
    offset_sec, first_id = 0.0, 1
    if resume is None:
        audio = decode_media(path, settings.max_duration_sec, cancelled)
        total_samples = len(audio)
    else:
        from app.rolling import context_prompt, decode_from

        offset_sec, first_id = float(resume["from_sec"]), int(resume["first_segment_id"])
        audio, total_samples = decode_from(path, settings.max_duration_sec, offset_sec, cancelled)
        expected = resume.get("expected_duration_sec")
        if expected is not None and abs(total_samples / 16000 - expected) > 1.0:
            raise AppError("source_mismatch", SOURCE_MISMATCH)
        if request.condition_on_previous_text and resume.get("context_text"):
            options["initial_prompt"] = context_prompt(
                prompt, resume["context_text"], token_encoder(model)
            )
    emit("info", run_info(directory, total_samples / 16000))
    if cancelled():
        return
    emit("status", {"status": "transcribing"})
    if len(audio) == 0:
        return  # 已保存位置即為音軌結尾，沒有剩餘音訊。
    segments, _ = model.transcribe(audio, **options)
    for i, segment in enumerate(controlled_segments(segments, cancelled), first_id):
        if cancelled():
            return
        emit(
            "segment",
            segment_payload(
                i, offset_sec + segment.start, offset_sec + segment.end, segment.text, request
            ),
        )


SOURCE_MISMATCH = "選擇的檔案與原任務的音軌長度不同，請確認是同一個原始檔。"


def token_encoder(model):
    return lambda text: model.hf_tokenizer.encode(text, add_special_tokens=False).ids


def run_info(directory, duration_sec):
    manifest = json.loads((directory / "ready.json").read_text(encoding="utf-8"))
    return {
        "duration_sec": duration_sec,
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
    }


def segment_payload(segment_id, start, end, text, request, outside_audio=False):
    return TranscriptSegment(
        segment_id=segment_id,
        start_sec=start,
        end_sec=end,
        raw_text=text.strip(),
        text=convert_text(text.strip(), request.traditional),
        outside_audio=outside_audio,
    ).model_dump()


def _transcribe_rolling(model, directory, path, request, settings, emit, cancelled, resume):
    """R2 分段辨識：逐塊推論，峰值記憶體由區塊長度而非總長決定。"""
    from app.rolling import (
        LanguageLock,
        adjust_timestamps,
        context_prompt,
        declared_duration,
        iter_audio_blocks,
        with_last,
    )

    options = transcription_options(request)
    base_prompt = options["initial_prompt"]
    encode = token_encoder(model)
    start_sec = float(resume["from_sec"]) if resume else 0.0
    next_id = int(resume["first_segment_id"]) if resume else 1
    expected = resume.get("expected_duration_sec") if resume else None
    declared = declared_duration(path)
    if declared is not None and declared > settings.effective_max_duration_sec:
        raise AppError("duration_limit", "音軌超過允許的時長。")
    if expected is not None and declared is not None and abs(declared - expected) > 1.0:
        raise AppError("source_mismatch", SOURCE_MISMATCH)
    emit("info", run_info(directory, declared))
    lock = LanguageLock(request.language, settings.language_lock_min_speech_sec)
    previous_text = (resume.get("context_text") or "") if resume else ""
    stats = {}
    blocks = iter_audio_blocks(
        path,
        settings.block_sec,
        settings.boundary_search_sec,
        settings.effective_max_duration_sec,
        cancelled,
        min_gap_sec=settings.min_gap_sec,
        start_sec=start_sec,
        stats=stats,
    )
    checkpoint = getattr(cancelled, "checkpoint", None)
    started = False
    for index, (block, last) in enumerate(with_last(blocks)):
        if cancelled():
            return
        if not started:
            emit("status", {"status": "transcribing"})
            started = True
        elif checkpoint is not None and not checkpoint():
            return  # 區塊之間是乾淨的暫停點；checkpoint 回 False 代表取消。
        opts = {**options, "language": lock.language}
        if request.condition_on_previous_text and previous_text:
            opts["initial_prompt"] = context_prompt(
                base_prompt, previous_text, encode, settings.prev_context_tokens
            )
        block_length = len(block.audio) / 16000
        next_offset = None if last else block.offset_sec + block_length
        segments, info = model.transcribe(block.audio, **opts)
        texts = []
        for segment in controlled_segments(segments, cancelled):
            if cancelled():
                return
            start, end, adjusted = adjust_timestamps(
                segment.start, segment.end, block.offset_sec, next_offset
            )
            if adjusted:
                emit(
                    "warning",
                    {"code": f"timestamp_adjusted@{next_id}:{segment.start:.2f}-{segment.end:.2f}"},
                )
            outside = bool(segment.start >= block_length)
            emit(
                "segment",
                segment_payload(next_id, start, end, segment.text, request, outside),
            )
            texts.append(segment.text.strip())
            next_id += 1
        if cancelled():
            return
        was_locked = lock.language
        lock.observe(info.language, info.duration_after_vad)
        if texts:
            previous_text = " ".join(texts)[-2000:]
        emit(
            "block",
            {
                "index": index,
                "offset_sec": block.offset_sec,
                "duration_sec": len(block.audio) / 16000,
                "forced_cut": block.forced_cut,
                "locked_language": lock.language if lock.language != was_locked else None,
            },
        )
    actual = stats.get("total_samples", 0) / 16000
    if expected is not None and abs(actual - expected) > 1.0:
        emit("warning", {"code": f"source_duration_mismatch:{actual:.1f}/{expected:.1f}"})
    emit("info", run_info(directory, actual))
