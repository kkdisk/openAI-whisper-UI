"""分段（rolling）辨識的串流解碼與切點選擇；R1 實驗用，尚未接入正式流程。

正式路徑仍為 app.transcriber.decode_media 整段解碼。本模組的錯誤碼與訊息
刻意與 decode_media 一致，供 R2 整合時直接替換。
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.schemas import AppError

SAMPLE_RATE = 16000
ENERGY_FRAME = 320  # 20 ms
# 邊界搜尋專用；辨識用 VAD 預設 min_silence 2000 ms／pad 400 ms 會吞掉短間隙。
BOUNDARY_VAD = {
    "threshold": 0.5,
    "min_silence_duration_ms": 150,
    "speech_pad_ms": 0,
    "min_speech_duration_ms": 0,
}

SpeechFn = Callable[[np.ndarray], list[dict]]


@dataclass(frozen=True)
class Cut:
    index: int  # 視窗內樣本索引
    forced: bool


@dataclass(frozen=True)
class AudioBlock:
    offset_samples: int
    audio: np.ndarray  # float32, 16 kHz mono
    forced_cut: bool  # 本塊結尾是強制切點；最後一塊恆為 False

    @property
    def offset_sec(self) -> float:
        return self.offset_samples / SAMPLE_RATE


def silero_speech(window: np.ndarray, options: dict | None = None) -> list[dict]:
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    return get_speech_timestamps(
        window, VadOptions(**(BOUNDARY_VAD if options is None else options))
    )


def find_cut(
    window: np.ndarray,
    min_gap_sec: float = 0.3,
    speech_fn: SpeechFn = silero_speech,
) -> Cut:
    """在視窗內取最長且達門檻的非語音間隙中點；找不到則切在最低能量的 20 ms。"""
    length = len(window)
    if length == 0:
        raise ValueError("搜尋視窗不可為空")
    edges = [0]
    for chunk in sorted(speech_fn(window), key=lambda c: c["start"]):
        edges += [max(0, min(length, chunk["start"])), max(0, min(length, chunk["end"]))]
    edges.append(length)
    best = None
    for start, end in zip(edges[::2], edges[1::2], strict=True):
        if end - start >= min_gap_sec * SAMPLE_RATE and (
            best is None or end - start > best[1] - best[0]
        ):
            best = (start, end)
    if best is not None:
        return Cut(index=max(1, (best[0] + best[1]) // 2), forced=False)
    frames = length // ENERGY_FRAME
    if frames == 0:
        return Cut(index=max(1, length // 2), forced=True)
    energy = np.square(window[: frames * ENERGY_FRAME].astype(np.float64)).reshape(frames, -1)
    quietest = int(np.argmin(energy.sum(axis=1)))
    return Cut(index=max(1, quietest * ENERGY_FRAME + ENERGY_FRAME // 2), forced=True)


def iter_pcm(path: Path, max_duration_sec: int, cancelled=lambda: False) -> Iterator[np.ndarray]:
    """逐 frame 產出 16 kHz mono float32；檢查與 decode_media 相同。"""
    import av

    count = 0
    try:
        with av.open(str(path)) as container:
            if not container.streams.audio:
                raise AppError("no_audio", "檔案沒有可辨識的音軌。")
            stream = container.streams.audio[0]
            if stream.duration and stream.time_base:
                if float(stream.duration * stream.time_base) > max_duration_sec:
                    raise AppError("duration_limit", "音軌超過允許的時長。")
            # resampler 只在檔案結尾 flush；中途 flush 會遺失內部緩衝樣本。
            resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
            for frame in _with_flush(container.decode(stream)):
                if frame is not None and cancelled():
                    raise AppError("cancelled", "已取消。")
                for out in resampler.resample(frame):
                    array = out.to_ndarray().reshape(-1)
                    count += array.size
                    if count > max_duration_sec * SAMPLE_RATE:
                        raise AppError("duration_limit", "音軌超過允許的時長。")
                    yield array.astype("float32") / 32768.0
    except AppError:
        raise
    except Exception as exc:
        raise AppError("decode_failed", "無法解碼音軌，可能損毀或編碼不支援。") from exc
    if count == 0:
        raise AppError("empty_audio", "音軌沒有有效音訊。")


def _with_flush(frames):
    yield from frames
    yield None


def iter_audio_blocks(
    path: Path,
    block_sec: float,
    search_sec: float,
    max_duration_sec: int,
    cancelled=lambda: False,
    min_gap_sec: float = 0.3,
    speech_fn: SpeechFn = silero_speech,
) -> Iterator[AudioBlock]:
    """串流切塊；緩衝上限約為 block_sec 加上一塊的殘餘，與總長無關。"""
    block = int(block_sec * SAMPLE_RATE)
    search = int(search_sec * SAMPLE_RATE)
    if not 0 < search < block:
        raise ValueError("search_sec 須大於 0 且小於 block_sec")
    pending: list[np.ndarray] = []
    buffered = 0
    offset = 0
    for array in iter_pcm(path, max_duration_sec, cancelled):
        pending.append(array)
        buffered += array.size
        while buffered >= block:
            buffer = np.concatenate(pending)
            window_start = block - search
            cut = find_cut(buffer[window_start:block], min_gap_sec, speech_fn)
            split = window_start + cut.index
            yield AudioBlock(offset, buffer[:split], cut.forced)
            offset += split
            pending, buffered = [buffer[split:]], buffer.size - split
    if buffered:
        yield AudioBlock(offset, np.concatenate(pending), False)


# --- 每塊辨識的規則（計畫書 3.4）；純函式，R2 整合時沿用 --------------------

# faster-whisper get_prompt 只保留前文最後 max_length // 2 - 1 = 223 個 token。
PROMPT_TOKEN_LIMIT = 223
PROMPT_SAFETY_TOKENS = 8
LANGUAGE_LOCK_MIN_SPEECH_SEC = 2.0


def adjust_timestamps(
    start_rel: float, end_rel: float, offset_sec: float, next_offset_sec: float | None
) -> tuple[float, float, bool]:
    """塊內時間換算為原檔時間；只在越過本塊範圍時調整，不刪文字。

    next_offset_sec 為下一塊起點；最後一塊傳 None，與整段路徑相同不裁切結尾。
    非有限值原樣換算（與整段路徑相同），回傳 adjusted=True 以便記錄。
    """
    start, end = offset_sec + start_rel, offset_sec + end_rel
    if not (np.isfinite(start) and np.isfinite(end)):
        return start, end, True
    adjusted = False
    if start < offset_sec:
        start, adjusted = offset_sec, True
    if end < offset_sec:
        end, adjusted = offset_sec, True
    if next_offset_sec is not None:
        if end > next_offset_sec:
            end, adjusted = next_offset_sec, True
        if start > next_offset_sec:
            start, adjusted = next_offset_sec, True
    return start, end, adjusted


def context_prompt(
    base_prompt: str,
    previous_text: str,
    encode: Callable[[str], list[int]],
    max_tail_tokens: int | None = None,
) -> list[int]:
    """引導句＋術語在前、上一塊尾端原文在後；總長不超過 get_prompt 保留上限。

    超過上限時 get_prompt 會從開頭截斷，術語會先被丟掉，因此只縮短尾端原文。
    """
    base = encode(" " + base_prompt.strip()) if base_prompt.strip() else []
    budget = PROMPT_TOKEN_LIMIT - PROMPT_SAFETY_TOKENS - len(base)
    if max_tail_tokens is not None:
        budget = min(budget, max_tail_tokens)
    if budget <= 0 or not previous_text.strip():
        return base
    tail = encode(" " + previous_text.strip())
    return base + tail[-budget:]


class LanguageLock:
    """auto 在第一個有效語音量達門檻的區塊才鎖定；zh／en 固定，mixed 逐窗偵測。"""

    def __init__(self, language: str, min_speech_sec: float = LANGUAGE_LOCK_MIN_SPEECH_SEC):
        self.mode = language
        self.min_speech_sec = min_speech_sec
        self.locked: str | None = language if language in {"zh", "en"} else None

    @property
    def language(self) -> str | None:
        """傳給本塊 model.transcribe 的 language。"""
        return self.locked

    def observe(self, detected: str, duration_after_vad: float) -> None:
        if self.mode == "auto" and self.locked is None:
            if duration_after_vad >= self.min_speech_sec:
                self.locked = detected
