"""整段與分段（rolling）辨識對照實驗（T100.2）；每組在獨立子程序執行，不下載模型。

用法：
  python scripts/benchmark_rolling.py run temp\\a.m4a --output temp\\rolling-cmp
  python scripts/benchmark_rolling.py run temp\\a.m4a --output temp\\x --runs whole rolling:600
  python scripts/benchmark_rolling.py repeat temp\\a.m4a --times 4 --output temp\\a-x4.wav

輸出含逐字稿內容，請放在 temp/ 等不提交的目錄。請先完成或取消 UI 中的轉錄，
避免同時執行影響耗時與記憶體。
"""

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import threading
import time
import wave
from difflib import SequenceMatcher
from importlib.metadata import version
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import TRANSCRIPTION_MODELS, Settings
from app.formatter import timestamp
from app.rolling import (
    SAMPLE_RATE,
    LanguageLock,
    adjust_timestamps,
    context_prompt,
    iter_audio_blocks,
    iter_pcm,
    with_last,
)
from app.schemas import JobRequest
from scripts.runtime_stats import memory_snapshot

DEFAULT_RUNS = ("whole", "rolling:300", "rolling:600", "rolling:1200")
MAX_DURATION_SEC = 12 * 3600
MB = 1024**2


# --- 共用小工具（有單元測試） ----------------------------------------------


def parse_run(spec: str) -> dict:
    if spec == "whole":
        return {"name": "whole", "mode": "whole", "block_sec": None}
    mode, _, value = spec.partition(":")
    if mode != "rolling" or not value:
        raise ValueError(f"無法辨識的 run：{spec}（應為 whole 或 rolling:<秒>）")
    block = float(value)
    if block <= 0:
        raise ValueError("block 秒數須大於 0")
    return {"name": f"rolling-{value}", "mode": "rolling", "block_sec": block}


def char_count(rows) -> int:
    return sum(len("".join(r["text"].split())) for r in rows)


def consecutive_repeats(rows) -> int:
    texts = [r["text"].strip() for r in rows]
    return sum(1 for a, b in zip(texts, texts[1:], strict=False) if a and a == b)


def text_between(rows, start, end) -> str:
    return "".join(r["text"].strip() for r in rows if start <= r["start_sec"] < end)


def worst_windows(base_rows, other_rows, duration, window=60.0, top=10):
    """以固定時間窗比較兩份稿，回傳相似度最低者；避免對整份長稿做二次方比對。"""
    scores = []
    start = 0.0
    while start < duration:
        a = text_between(base_rows, start, start + window)
        b = text_between(other_rows, start, start + window)
        if a or b:
            ratio = SequenceMatcher(None, a, b, autojunk=False).ratio()
            scores.append({"start_sec": start, "ratio": ratio, "chars": (len(a), len(b))})
        start += window
    return sorted(scores, key=lambda s: s["ratio"])[:top]


def linear_slope(values) -> float | None:
    if len(values) < 3:
        return None
    x = np.arange(len(values), dtype=float)
    return float(np.polyfit(x, np.asarray(values, dtype=float), 1)[0])


def read_rows(path: Path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# --- 子程序：單組辨識 -------------------------------------------------------


class MemorySampler:
    def __init__(self, interval):
        self.interval, self.samples = interval, []
        self.stop = threading.Event()
        self.started = time.perf_counter()
        self.thread = threading.Thread(target=self.poll, daemon=True)

    def sample(self):
        snapshot = memory_snapshot()
        if snapshot:
            self.samples.append(
                [
                    round(time.perf_counter() - self.started, 1),
                    round(snapshot["working_set_bytes"] / MB),
                    round(snapshot["private_bytes"] / MB),
                ]
            )

    def poll(self):
        self.sample()
        while not self.stop.wait(self.interval):
            self.sample()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(5)
        self.sample()


def mib(snapshot, key):
    return round(snapshot[key] / MB) if snapshot else None


def child(config_path: Path):
    from app.devices import select_device
    from app.models import model_path, offline_mode
    from app.transcriber import decode_media, transcription_options

    config = json.loads(config_path.read_text(encoding="utf-8"))
    out = config_path.parent
    name = config["name"]
    offline_mode()
    from faster_whisper import WhisperModel
    from faster_whisper.vad import get_vad_model

    result = {"name": name, "mode": config["mode"], "block_sec": config["block_sec"]}
    with MemorySampler(config["sample_sec"]) as sampler:
        started = time.perf_counter()
        selection = select_device(config["device"], config["compute_type"], config["device_index"])
        model = WhisperModel(
            str(model_path(Settings(), config["model"])),
            **selection.model_kwargs(),
            cpu_threads=config["threads"],
            local_files_only=True,
        )
        get_vad_model()
        result["load_sec"] = time.perf_counter() - started
        result["compute_type"] = str(model.model.compute_type)
        request = JobRequest(
            source_name=Path(config["audio"]).name,
            expected_bytes=Path(config["audio"]).stat().st_size,
            language=config["language"],
            condition_on_previous_text=config["condition_on_previous_text"],
            terms=config["terms"],
        )
        options = transcription_options(request)
        base_prompt = options["initial_prompt"]
        if len(model.hf_tokenizer.encode(" " + base_prompt.strip()).ids) > Settings().prompt_tokens:
            raise SystemExit("完整提示超過 200 tokens，請縮短術語清單")
        if config["seed"] is not None:
            from ctranslate2 import set_random_seed

            set_random_seed(config["seed"])
        path = Path(config["audio"])
        blocks, adjusted = [], []
        segment_count = 0
        transcribe_started = time.perf_counter()
        with (out / f"{name}.jsonl").open("x", encoding="utf-8") as handle:

            def write(start, end, text, block):
                nonlocal segment_count
                segment_count += 1
                row = {"start_sec": start, "end_sec": end, "text": text, "block": block}
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()

            if config["mode"] == "whole":
                decode_started = time.perf_counter()
                audio = decode_media(path, MAX_DURATION_SEC)
                result["decode_sec"] = time.perf_counter() - decode_started
                result["duration_sec"] = len(audio) / SAMPLE_RATE
                segments, info = model.transcribe(audio, **options)
                for segment in segments:
                    write(segment.start, segment.end, segment.text.strip(), 0)
                    print(f"{name}: {segment.end:.1f}s", flush=True)
                result["detected_language"] = info.language
                blocks.append({"index": 0, "offset_sec": 0.0, "language": info.language})
            else:
                encode = model.hf_tokenizer.encode
                lock = LanguageLock(config["language"])
                previous_text = ""
                total_samples = 0
                source = iter_audio_blocks(
                    path,
                    config["block_sec"],
                    config["search_sec"],
                    MAX_DURATION_SEC,
                    min_gap_sec=config["min_gap_sec"],
                )
                for index, (block, last) in enumerate(with_last(source)):
                    block_started = time.perf_counter()
                    total_samples += len(block.audio)
                    next_offset = (
                        None if last else block.offset_sec + len(block.audio) / SAMPLE_RATE
                    )
                    opts = {**options, "language": lock.language}
                    if config["condition_on_previous_text"] and previous_text:
                        opts["initial_prompt"] = context_prompt(
                            base_prompt,
                            previous_text,
                            lambda t: encode(t, add_special_tokens=False).ids,
                            config["prev_context_tokens"],
                        )
                    segments, info = model.transcribe(block.audio, **opts)
                    texts = []
                    for segment in segments:
                        start, end, changed = adjust_timestamps(
                            segment.start, segment.end, block.offset_sec, next_offset
                        )
                        if changed:
                            adjusted.append({"block": index, "rel": [segment.start, segment.end]})
                        text = segment.text.strip()
                        texts.append(text)
                        write(start, end, text, index)
                        print(f"{name}: {end:.1f}s (block {index})", flush=True)
                    lock.observe(info.language, info.duration_after_vad)
                    if texts:
                        previous_text = " ".join(texts)[-2000:]
                    snapshot = memory_snapshot()
                    blocks.append(
                        {
                            "index": index,
                            "offset_sec": block.offset_sec,
                            "duration_sec": len(block.audio) / SAMPLE_RATE,
                            "forced_cut": block.forced_cut,
                            "passed_language": opts["language"],
                            "detected_language": info.language,
                            "duration_after_vad": info.duration_after_vad,
                            "segments": len(texts),
                            "elapsed_sec": time.perf_counter() - block_started,
                            "working_set_mib": mib(snapshot, "working_set_bytes"),
                            "private_mib": mib(snapshot, "private_bytes"),
                        }
                    )
                result["duration_sec"] = total_samples / SAMPLE_RATE
                result["locked_language"] = lock.language
        result["transcribe_sec"] = time.perf_counter() - transcribe_started
        result["total_sec"] = time.perf_counter() - started
    final = memory_snapshot()
    result.update(
        {
            "segments": segment_count,
            "blocks": blocks,
            "timestamp_adjustments": adjusted,
            "peak_working_set_mib": mib(final, "peak_working_set_bytes"),
            "peak_private_mib": mib(final, "peak_private_bytes"),
            "memory_samples": sampler.samples,
            "memory_samples_columns": ["elapsed_sec", "working_set_mib", "private_mib"],
        }
    )
    (out / f"{name}.result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# --- 主程序：依序執行並產生報告 --------------------------------------------


def spawn(command) -> int:
    return subprocess.run(command).returncode


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * MB), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(args):
    runs = [parse_run(spec) for spec in args.runs]
    if len({r["name"] for r in runs}) != len(runs):
        raise SystemExit("--runs 不可重複")
    if not args.audio.is_file():
        raise SystemExit("找不到音訊檔案")
    if args.search_sec <= 0 or any(
        r["block_sec"] is not None and r["block_sec"] <= args.search_sec for r in runs
    ):
        raise SystemExit("block 秒數須大於 --search-sec")
    args.output.mkdir(parents=True, exist_ok=False)
    common = {
        "audio": str(args.audio.resolve()),
        "model": args.model,
        "threads": args.threads,
        "device": args.device,
        "compute_type": args.compute_type,
        "device_index": args.device_index,
        "language": args.language,
        "condition_on_previous_text": not args.no_previous_text,
        "terms": args.terms,
        "seed": args.seed,
        "search_sec": args.search_sec,
        "min_gap_sec": args.min_gap_sec,
        "prev_context_tokens": args.prev_context_tokens,
        "sample_sec": args.sample_sec,
    }
    manifest = {
        **common,
        "audio_sha256": sha256(args.audio),
        "runs": runs,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "versions": {
            name: version(name) for name in ("faster-whisper", "ctranslate2", "av", "onnxruntime")
        },
        "notice": "每組獨立子程序；RAM 為該子程序生命週期峰值，含模型載入。",
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for spec in runs:
        config_path = args.output / f"{spec['name']}.config.json"
        config_path.write_text(
            json.dumps({**common, **spec}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"START {spec['name']}", flush=True)
        code = spawn([sys.executable, str(Path(__file__).resolve()), "_child", str(config_path)])
        print(f"END {spec['name']} exit={code}", flush=True)
        if code:
            (args.output / f"{spec['name']}.failed").write_text(str(code), encoding="utf-8")
    report = build_report(args.output, runs, args.context_sec)
    (args.output / "report.md").write_text(report, encoding="utf-8")
    print(f"REPORT {args.output / 'report.md'}", flush=True)


def build_report(out: Path, runs, context_sec=30.0) -> str:
    results, rows = {}, {}
    for spec in runs:
        path = out / f"{spec['name']}.result.json"
        if path.exists():
            results[spec["name"]] = json.loads(path.read_text(encoding="utf-8"))
        rows[spec["name"]] = read_rows(out / f"{spec['name']}.jsonl")
    lines = [
        "# 整段／分段辨識對照報告",
        "",
        "含逐字稿內容，僅供本機回聽比對；不提交 Git。相似度與字數只作篩選回聽位置，不等於準確率。",
        "",
        "| run | 狀態 | 總耗時 | 辨識耗時 | 峰值工作集 MiB | 峰值 private MiB | 片段 | 字數 | "
        "強制切點 | 時間調整 | 連續重複 | 最後結束 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for spec in runs:
        name, r, data = spec["name"], results.get(spec["name"]), rows[spec["name"]]
        if r is None:
            lines.append(
                f"| {name} | 失敗或未完成 | | | | | {len(data)} | {char_count(data)} |||||"
            )
            continue
        forced = sum(1 for b in r["blocks"] if b.get("forced_cut"))
        last_end = max((row["end_sec"] for row in data), default=0)
        lines.append(
            f"| {name} | 完成 | {timestamp(r['total_sec'])} | {timestamp(r['transcribe_sec'])} | "
            f"{r['peak_working_set_mib']} | {r['peak_private_mib']} | {r['segments']} | "
            f"{char_count(data)} | {forced} | {len(r['timestamp_adjustments'])} | "
            f"{consecutive_repeats(data)} | {timestamp(last_end)} / "
            f"{timestamp(r['duration_sec'])} |"
        )
    base = rows.get("whole") if "whole" in results else None
    for spec in runs:
        r = results.get(spec["name"])
        if spec["mode"] != "rolling" or r is None:
            continue
        data = rows[spec["name"]]
        lines += ["", f"## {spec['name']}", ""]
        private = [b["private_mib"] for b in r["blocks"] if b.get("private_mib") is not None]
        slope = linear_slope(private[1:])
        lines.append(
            f"- 區塊數 {len(r['blocks'])}；鎖定語言 {r.get('locked_language')}；"
            f"逐塊 private MiB（第二塊起）斜率 "
            f"{'—' if slope is None else f'{slope:.1f} MiB／塊'}"
        )
        lines.append(f"- 逐塊 private MiB：{private}")
        if base is not None:
            whole_chars, chars = char_count(base), char_count(data)
            ratio = (chars - whole_chars) / whole_chars * 100 if whole_chars else 0
            lines.append(f"- 字數相對整段：{chars} vs {whole_chars}（{ratio:+.2f}%）")
            lines += ["", "相似度最低的 60 秒窗（建議優先回聽）：", ""]
            for w in worst_windows(base, data, r["duration_sec"]):
                lines.append(
                    f"- {timestamp(w['start_sec'])} 相似度 {w['ratio']:.2f}，"
                    f"字數 整段 {w['chars'][0]}／分段 {w['chars'][1]}"
                )
        lines += ["", "### 切點對照", ""]
        for b in r["blocks"][1:]:
            cut = b["offset_sec"]
            previous = r["blocks"][b["index"] - 1]
            flag = "強制切點" if previous.get("forced_cut") else "靜音切點"
            lines += [f"#### {timestamp(cut)}（{flag}）", ""]
            for label, source in (("整段", base), ("分段", data)):
                if source is None:
                    continue
                lines.append(f"**{label}**")
                lines.append("")
                for row in source:
                    if cut - context_sec <= row["start_sec"] < cut + context_sec:
                        mark = " ←切點後" if label == "分段" and row["start_sec"] >= cut else ""
                        lines.append(
                            f"- [{timestamp(row['start_sec'])}–{timestamp(row['end_sec'])}] "
                            f"{row['text']}{mark}"
                        )
                lines.append("")
    return "\n".join(lines) + "\n"


def repeat(args):
    if args.times < 1:
        raise SystemExit("--times 須至少為 1")
    if args.output.exists():
        raise SystemExit("輸出檔已存在，不覆蓋")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as raw, wave.open(raw, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        for _ in range(args.times):
            for array in iter_pcm(args.audio, MAX_DURATION_SEC):
                pcm = np.rint(array * 32768.0).astype("<i2")
                handle.writeframes(pcm.tobytes())
    print(f"WROTE {args.output}", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="依序執行整段與分段對照")
    r.add_argument("audio", type=Path)
    r.add_argument("--output", type=Path, required=True, help="新的結果目錄，不覆蓋既有資料")
    r.add_argument("--runs", nargs="+", default=list(DEFAULT_RUNS))
    r.add_argument("--model", choices=TRANSCRIPTION_MODELS, default="small")
    r.add_argument("--threads", type=int, choices=(4, 6, 8), default=4)
    r.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    r.add_argument("--compute-type", choices=("auto", "int8_float16", "float16"), default="auto")
    r.add_argument("--device-index", type=int, default=0)
    r.add_argument("--language", choices=("zh", "auto", "en", "mixed"), default="zh")
    r.add_argument("--no-previous-text", action="store_true", help="關閉參考前文")
    r.add_argument("--terms", default="")
    r.add_argument("--seed", type=int, default=42)
    r.add_argument("--search-sec", type=float, default=30)
    r.add_argument("--min-gap-sec", type=float, default=0.3)
    r.add_argument("--prev-context-tokens", type=int, default=None)
    r.add_argument("--context-sec", type=float, default=30, help="切點對照前後秒數")
    r.add_argument("--sample-sec", type=float, default=5, help="記憶體取樣間隔")
    p = sub.add_parser("repeat", help="將來源首尾串接 N 次成 16 kHz WAV（記憶體實驗用）")
    p.add_argument("audio", type=Path)
    p.add_argument("--times", type=int, required=True)
    p.add_argument("--output", type=Path, required=True)
    c = sub.add_parser("_child")
    c.add_argument("config", type=Path)
    args = parser.parse_args(argv)
    {"run": run, "repeat": repeat, "_child": lambda a: child(a.config)}[args.command](args)


if __name__ == "__main__":
    main()
