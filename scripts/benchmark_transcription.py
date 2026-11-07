"""串行比較本機轉錄參數，保存原文、設定與耗時；不下載模型。"""

import argparse
import hashlib
import json
import platform
import sys
import time
from importlib.metadata import version
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import TRANSCRIPTION_MODELS, Settings
from app.devices import select_device
from app.formatter import timestamp
from app.models import model_path, offline_mode
from app.schemas import JobRequest
from app.transcriber import decode_media, transcription_options

PROFILES = {
    "baseline": {"multilingual": False, "condition_on_previous_text": True},
    "multilingual": {"multilingual": True, "condition_on_previous_text": True},
    "independent": {"multilingual": True, "condition_on_previous_text": False},
}
PROMPT_PROFILES = ("no_prompt", "terms_only")


def profile_options(names, options, terms=""):
    profiles = {}
    for name in names:
        if name == "no_prompt":
            profiles[name] = {**options, "initial_prompt": None}
        elif name == "terms_only":
            if not terms.strip():
                raise ValueError("terms_only 需要 --terms 術語清單")
            profiles[name] = {**options, "initial_prompt": terms.strip()}
        else:
            profiles[name] = {**options, **PROFILES[name]}
    return profiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="新的結果目錄，不覆蓋既有資料")
    parser.add_argument("--model", choices=TRANSCRIPTION_MODELS, default="large-v3-turbo")
    parser.add_argument("--threads", type=int, choices=(4, 6, 8), default=8)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument(
        "--compute-type", choices=("auto", "int8_float16", "float16"), default="auto"
    )
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument(
        "--profiles", nargs="+", choices=[*PROFILES, *PROMPT_PROFILES], default=list(PROFILES)
    )
    parser.add_argument("--terms", default="", help="只用於 terms_only 的簡短術語清單")
    parser.add_argument("--seed", type=int, help="每組開始前重設相同 CT2 隨機種子")
    args = parser.parse_args()
    directory = model_path(Settings(), args.model)
    if not args.audio.is_file():
        parser.error("找不到音訊檔案")
    args.output.mkdir(parents=True, exist_ok=False)
    offline_mode()
    from faster_whisper import WhisperModel

    load_start = time.perf_counter()
    selection = select_device(args.device, args.compute_type, args.device_index)
    model = WhisperModel(
        str(directory),
        **selection.model_kwargs(),
        cpu_threads=args.threads,
        local_files_only=True,
    )
    load_sec = time.perf_counter() - load_start
    decode_start = time.perf_counter()
    audio = decode_media(args.audio, Settings().max_duration_sec)
    decode_sec = time.perf_counter() - decode_start
    options = transcription_options(
        JobRequest(
            source_name=args.audio.name, expected_bytes=args.audio.stat().st_size, language="auto"
        )
    )
    try:
        profiles = profile_options(args.profiles, options, args.terms)
    except ValueError as exc:
        parser.error(str(exc))
    for profile in profiles.values():
        prompt = profile["initial_prompt"]
        if (
            prompt
            and len(model.hf_tokenizer.encode(" " + prompt.strip()).ids) > Settings().prompt_tokens
        ):
            parser.error("完整提示超過 200 tokens，請縮短術語清單")
    digest = hashlib.sha256()
    with args.audio.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    manifest = {
        "audio_sha256": digest.hexdigest(),
        "duration_sec": len(audio) / 16000,
        "model": args.model,
        "revision": json.loads((directory / "ready.json").read_text("utf-8"))["revision"],
        "device": selection.device,
        "device_index": selection.device_index,
        "compute_type": model.model.compute_type,
        "requested_compute_type": args.compute_type,
        "threads": args.threads,
        "seed": args.seed,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "versions": {name: version(name) for name in ("faster-whisper", "ctranslate2")},
        "load_sec": load_sec,
        "decode_sec": decode_sec,
        "notice": "依序共用已載入模型及音訊；耗時不含載入／解碼，非準確率或真實會議驗收。",
        "profiles": profiles,
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for name in args.profiles:
        if args.seed is not None:
            from ctranslate2 import set_random_seed

            set_random_seed(args.seed)
        print(f"START {name}", flush=True)
        started = time.perf_counter()
        rows = []
        with (args.output / f"{name}.jsonl").open("x", encoding="utf-8") as handle:
            segments, info = model.transcribe(audio, **manifest["profiles"][name])
            for segment in segments:
                row = {"start_sec": segment.start, "end_sec": segment.end, "text": segment.text}
                rows.append(row)
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                print(f"{name}: {segment.end:.1f}s / {manifest['duration_sec']:.1f}s", flush=True)
        result = {
            "profile": name,
            "elapsed_sec": time.perf_counter() - started,
            "initial_language": info.language,
            "segments": len(rows),
            "completed": True,
        }
        (args.output / f"{name}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (args.output / f"{name}.txt").write_text(
            f"參數組：{name}\n"
            + json.dumps(manifest["profiles"][name], ensure_ascii=False)
            + "\n\n"
            + "\n".join(f"[{timestamp(row['start_sec'])}] {row['text']}" for row in rows)
            + "\n",
            encoding="utf-8",
        )
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
