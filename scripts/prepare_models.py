"""明確聯網下載固定 revision，再於禁止連網環境驗證載入與 VAD。"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import MODEL_REPOS, Settings
from app.models import model_path, validate_assets


def verify_download_files(destination: Path, revision: str | None = None) -> str:
    """比對本機 HF 來源紀錄及檔案雜湊，不因進度達 100% 就標記完成。"""
    validate_assets(destination)
    names = ["model.bin", "config.json", "tokenizer.json"]
    names.extend(p.name for p in destination.glob("vocabulary.*") if p.is_file())
    if (destination / "preprocessor_config.json").is_file():
        names.append("preprocessor_config.json")
    for name in names:
        metadata = destination / ".cache" / "huggingface" / "download" / f"{name}.metadata"
        try:
            commit, etag, _ = metadata.read_text("utf-8").splitlines()
        except (OSError, ValueError) as exc:
            raise ValueError(f"{name} 缺少完整的下載來源紀錄，請重新執行下載準備。") from exc
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError(f"{name} 的來源 revision 格式不正確。")
        if revision is None:
            revision = commit
        if commit != revision:
            raise ValueError(f"{name} 的 revision 與其他資產不一致，請使用固定 revision 重新準備。")
        path = destination / name
        if re.fullmatch(r"[0-9a-f]{64}", etag):
            digest = hashlib.sha256()
        elif re.fullmatch(r"[0-9a-f]{40}", etag):
            digest = hashlib.sha1()
            digest.update(f"blob {path.stat().st_size}\0".encode())
        else:
            raise ValueError(f"{name} 的雜湊格式不支援，不能跳過完整性檢查。")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != etag:
            raise ValueError(f"{name} 雜湊不符；檔案可能未完成，請重新執行下載準備。")
    return revision


def verify_runtime(destination: Path):
    """獨立子程序實際載入模型、tokenizer 與 VAD，禁止建立網路連線。"""
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


def prepare_model(name: str, settings: Settings, revision=None, verify_only=False):
    if name not in MODEL_REPOS:
        raise ValueError("模型不在允許清單中。")
    destination = settings.models_dir / name
    if not destination.resolve().is_relative_to(settings.models_dir.resolve()):
        raise ValueError("模型目錄不在工具目錄內。")
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "ready.json").unlink(missing_ok=True)
    repo = MODEL_REPOS[name]
    if not verify_only:
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        from huggingface_hub import HfApi, snapshot_download

        revision = HfApi().model_info(repo, revision=revision).sha
        print(f"準備模型 {name}，固定 revision：{revision}", flush=True)
        # 多檔串行下載，保留 HF 既有續傳／本機檔案重用機制。
        snapshot_download(repo, revision=revision, local_dir=str(destination), max_workers=1)
    print("正在比對本機模型雜湊與來源紀錄（不下載）…", flush=True)
    revision = verify_download_files(destination, revision)
    print("正在驗證模型、tokenizer 與 VAD 離線載入…", flush=True)
    verify_runtime(destination)
    marker = destination / "ready.json"
    temporary = destination / "ready.tmp"
    temporary.write_text(
        json.dumps({"repo": repo, "revision": revision}, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary, marker)
    model_path(settings, name)
    print("模型與 VAD 離線載入驗證完成。此步驟尚未驗證語音辨識品質。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=MODEL_REPOS, default="small")
    parser.add_argument("--revision", help="指定固定 commit；省略時從遠端或本機來源紀錄取得")
    parser.add_argument("--verify-only", action="store_true", help="只驗證已下載資產，完全不連網")
    args = parser.parse_args()
    try:
        prepare_model(args.model, Settings(), args.revision, args.verify_only)
    except KeyboardInterrupt:
        parser.exit(130, "\n模型準備已取消；已下載檔案保留，尚未標記為可用。\n")
    except Exception as exc:
        parser.exit(
            1,
            f"\n模型準備未完成（{type(exc).__name__}）：{exc}\n"
            "已下載檔案保留；若資產齊全，可加 --verify-only 驗證而不重新下載。\n",
        )


if __name__ == "__main__":
    main()
