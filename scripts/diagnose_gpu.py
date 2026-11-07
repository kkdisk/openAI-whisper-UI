"""限時、離線 GPU 診斷；有語音樣本且產生文字才算推論通過。"""

import argparse
import hashlib
import json
import subprocess
import sys
import time
from importlib.metadata import version
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import TRANSCRIPTION_MODELS, Settings
from app.devices import cuda_error, select_device
from app.models import model_path, offline_mode
from app.schemas import AppError, JobRequest
from app.transcriber import decode_media, transcribe
from scripts.runtime_stats import RuntimeStats


def probe(args):
    offline_mode()
    result = {
        "versions": {p: version(p) for p in ("faster-whisper", "ctranslate2", "av", "onnxruntime")}
    }
    import ctranslate2

    result["cuda_count"] = ctranslate2.get_cuda_device_count()
    selection = select_device(args.device, args.compute_type, args.device_index)
    result["selection"] = selection.__dict__
    directory = model_path(Settings(), args.model)
    result["model"] = args.model
    result["revision"] = json.loads((directory / "ready.json").read_text("utf-8"))["revision"]
    start = time.perf_counter()
    if args.audio is None:
        from faster_whisper import WhisperModel

        model = WhisperModel(str(directory), **selection.model_kwargs(), local_files_only=True)
        result["actual_compute_type"] = model.model.compute_type
        result["inference"] = "not_tested_no_audio"
    else:
        audio = decode_media(args.audio, 30)
        if len(audio) > 30 * 16000:
            raise AppError("duration_limit", "診斷只接受 30 秒內語音。")
        result["audio_sha256"] = hashlib.sha256(args.audio.read_bytes()).hexdigest()
        result["duration_sec"] = len(audio) / 16000
        events = []
        transcribe(
            args.audio,
            JobRequest(
                source_name=args.audio.name,
                expected_bytes=args.audio.stat().st_size,
                model=args.model,
                device=args.device,
                compute_type=args.compute_type,
                device_index=args.device_index,
                language="auto",
            ),
            Settings(),
            lambda k, p: events.append((k, p)),
            lambda: False,
        )
        segments = [p for k, p in events if k == "segment" and p["raw_text"].strip()]
        if not segments:
            raise AppError("no_speech_output", "沒有非空語音片段，不能證明 GPU 推論成功。")
        result["inference"] = "passed"
        result["segments"] = len(segments)
        result["actual"] = [p for k, p in events if k == "device"][-1]
    result["elapsed_sec"] = time.perf_counter() - start
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=TRANSCRIPTION_MODELS, default="small")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument(
        "--compute-type", choices=("auto", "float16", "int8_float16"), default="auto"
    )
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.timeout < 1 or args.device_index < 0:
        parser.error("timeout 必須為正數，device-index 必須為非負數")
    if args.worker:
        try:
            with RuntimeStats(args.device, args.device_index) as stats:
                result = {"ok": True, **probe(args)}
            result["resources"] = stats.report()
        except Exception as exc:
            result = {
                "ok": False,
                "code": cuda_error(exc) or getattr(exc, "code", "probe_failed"),
                "message": str(exc),
                "exception": type(exc).__name__,
                "cause": str(exc.__cause__) if exc.__cause__ else None,
            }
        print(json.dumps(result, ensure_ascii=True))
        return
    result = {"python": sys.executable, "device_index_policy": "explicit index, default 0"}
    try:
        gpu = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,driver_version,memory.total,memory.free",
                "--format=csv",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        result["nvidia_smi"] = {
            "exit_code": gpu.returncode,
            "stdout": gpu.stdout,
            "stderr": gpu.stderr,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["nvidia_smi"] = {"error": type(exc).__name__}
    try:
        child = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], "--worker"],
            capture_output=True,
            text=True,
            timeout=args.timeout,
        )
        result["exit_code"] = child.returncode
        try:
            result["probe"] = json.loads(child.stdout)
        except ValueError:
            result["probe"] = {"ok": False, "code": "worker_crash", "stderr": child.stderr[-2000:]}
    except subprocess.TimeoutExpired:
        result["probe"] = {"ok": False, "code": "probe_timeout"}
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result["probe"]["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
