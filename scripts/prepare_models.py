"""明確聯網下載固定 revision，再於禁止連網環境驗證載入與 VAD。"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import MODEL_REPOS, Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=MODEL_REPOS, default="small")
    parser.add_argument("--revision", help="指定固定 commit；省略時先解析遠端目前 commit 並記錄")
    args = parser.parse_args()
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    from huggingface_hub import HfApi, snapshot_download

    repo = MODEL_REPOS[args.model]
    revision = HfApi().model_info(repo, revision=args.revision).sha
    destination = Settings().models_dir / args.model
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "ready.json").unlink(missing_ok=True)
    print(f"準備模型 {args.model}，固定 revision：{revision}")
    snapshot_download(repo, revision=revision, local_dir=str(destination))
    code = """
import os, socket, sys
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
def reject(*a, **k):
    raise RuntimeError('離線驗證禁止連網')
socket.socket.connect = reject
socket.create_connection = reject
import numpy as np
from faster_whisper import WhisperModel
from faster_whisper.vad import get_speech_timestamps
model = WhisperModel(sys.argv[1], device='cpu', compute_type='int8', local_files_only=True)
model.hf_tokenizer.encode('本機離線測試 API')
get_speech_timestamps(np.zeros(16000, dtype=np.float32))
"""
    subprocess.run([sys.executable, "-c", code, str(destination.resolve())], check=True)
    (destination / "ready.json").write_text(
        json.dumps({"repo": repo, "revision": revision}, ensure_ascii=False), encoding="utf-8"
    )
    print("模型與 VAD 離線載入驗證完成。此步驟尚未驗證語音辨識品質。")


if __name__ == "__main__":
    main()
