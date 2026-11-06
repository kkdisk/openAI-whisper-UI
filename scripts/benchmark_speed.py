"""以保留音高的 atempo 比較語速，保存原檔時間軸；不改動產品預設。"""

import argparse
import hashlib
import json
import sys
import time
import wave
from fractions import Fraction
from importlib.metadata import version
from pathlib import Path

import av
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import TRANSCRIPTION_MODELS, Settings
from app.formatter import timestamp
from app.models import model_path, offline_mode
from app.schemas import JobRequest
from app.transcriber import decode_media, transcription_options
from scripts.benchmark_transcription import PROFILES


def change_tempo(audio: np.ndarray, speed: float) -> np.ndarray:
    if not 0.5 <= speed <= 2:
        raise ValueError("speed must be between 0.5 and 2")
    if speed == 1:
        return audio.copy()
    graph = av.filter.Graph()
    source = graph.add_abuffer(
        sample_rate=16000, format="flt", layout="mono", time_base=Fraction(1, 16000)
    )
    tempo = graph.add("atempo", str(speed))
    sink = graph.add("abuffersink")
    source.link_to(tempo)
    tempo.link_to(sink)
    graph.configure()
    blocks = []

    def drain():
        while True:
            try:
                blocks.append(sink.pull().to_ndarray().reshape(-1).copy())
            except (av.error.BlockingIOError, av.error.EOFError):
                return

    for offset in range(0, len(audio), 4096):
        frame = av.AudioFrame.from_ndarray(audio[offset : offset + 4096][None, :], "flt", "mono")
        frame.sample_rate = 16000
        frame.pts = offset
        frame.time_base = Fraction(1, 16000)
        source.push(frame)
        drain()
    source.push(None)
    drain()
    if not blocks:
        raise ValueError("atempo produced no audio")
    return np.concatenate(blocks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--duration", type=float, default=70)
    parser.add_argument("--model", choices=TRANSCRIPTION_MODELS, default="large-v3-turbo")
    parser.add_argument("--threads", type=int, choices=(4, 6, 8), default=8)
    parser.add_argument("--profile", choices=PROFILES, default="baseline")
    args = parser.parse_args()
    if args.start < 0 or args.duration <= 0:
        parser.error("start must be nonnegative and duration must be positive")
    directory = model_path(Settings(), args.model)
    offline_mode()
    audio = decode_media(args.audio, Settings().max_duration_sec)
    clip = audio[round(args.start * 16000) : round((args.start + args.duration) * 16000)]
    if not len(clip):
        parser.error("empty audio selection")
    args.output.mkdir(parents=True, exist_ok=False)
    options = (
        transcription_options(
            JobRequest(source_name=args.audio.name, expected_bytes=1, language="auto")
        )
        | PROFILES[args.profile]
    )
    manifest = {
        "model": args.model,
        "revision": json.loads((directory / "ready.json").read_text("utf-8"))["revision"],
        "device": "cpu",
        "compute_type": "int8",
        "threads": args.threads,
        "source_clip_start_sec": args.start,
        "source_clip_duration_sec": len(clip) / 16000,
        "source_clip_pcm_sha256": hashlib.sha256(clip.tobytes()).hexdigest(),
        "sample_rate": 16000,
        "seed": 42,
        "options": options,
        "versions": {name: version(name) for name in ("faster-whisper", "ctranslate2", "av")},
        "time_mapping": "source_sec = clip_start_sec + processed_sec * speed",
        "notice": "每組一次；atempo 保留音高；推論耗時不含音訊處理與模型載入。",
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    from ctranslate2 import set_random_seed
    from faster_whisper import WhisperModel

    model = WhisperModel(
        str(directory),
        device="cpu",
        compute_type="int8",
        cpu_threads=args.threads,
        local_files_only=True,
    )
    for speed in (1.0, 0.9, 0.85):
        name = f"speed-{speed:g}"
        processed = change_tempo(clip, speed)
        pcm = np.clip(np.rint(processed * 32768), -32768, 32767).astype("<i2")
        with wave.open(str(args.output / f"{name}.wav"), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(pcm.tobytes())
        set_random_seed(manifest["seed"])
        started = time.perf_counter()
        print(f"START {name}", flush=True)
        segments, _ = model.transcribe(pcm.astype(np.float32) / 32768, **options)
        rows = []
        with (args.output / f"{name}.jsonl").open("x", encoding="utf-8") as handle:
            for segment in segments:
                row = {
                    "processed_start_sec": segment.start,
                    "processed_end_sec": segment.end,
                    "source_start_sec": args.start + segment.start * speed,
                    "source_end_sec": args.start + segment.end * speed,
                    "text": segment.text.strip(),
                }
                rows.append(row)
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                print(f"{name}: source {row['source_end_sec']:.1f}s", flush=True)
        result = {
            "speed": speed,
            "elapsed_sec": time.perf_counter() - started,
            "processed_duration_sec": len(pcm) / 16000,
            "segments": len(rows),
            "completed": True,
            "pcm_sha256": hashlib.sha256(pcm.tobytes()).hexdigest(),
            "clipped_samples": int(np.count_nonzero(np.abs(processed) >= 1)),
        }
        (args.output / f"{name}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (args.output / f"{name}.txt").write_text(
            "時間戳已換算回原檔位置\n"
            + "\n".join(f"[{timestamp(r['source_start_sec'])}] {r['text']}" for r in rows)
            + "\n",
            encoding="utf-8",
        )
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
