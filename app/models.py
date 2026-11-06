"""只允許明確準備完成的本機模型。"""

import json
import os
from pathlib import Path

from app.config import MODEL_REPOS, Settings
from app.schemas import AppError


def offline_mode() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"


def validate_assets(path: Path) -> None:
    required = ("model.bin", "config.json", "tokenizer.json")
    if not path.is_dir() or any(
        not (path / name).is_file() or (path / name).stat().st_size == 0 for name in required
    ):
        raise AppError("model_missing", "本機模型不完整，請先執行模型準備。")
    if not any(p.is_file() and p.stat().st_size > 0 for p in path.glob("vocabulary.*")):
        raise AppError("model_missing", "本機模型缺少詞彙檔，請重新準備。")


def model_path(settings: Settings, name: str, require_ready: bool = True) -> Path:
    if name not in MODEL_REPOS:
        raise AppError("invalid_model", "模型不在允許清單中。")
    path = (settings.models_dir / name).resolve()
    if not path.is_relative_to(settings.models_dir.resolve()):
        raise AppError("invalid_model", "模型路徑不在工具目錄內。")
    validate_assets(path)
    if require_ready:
        try:
            ready = json.loads((path / "ready.json").read_text(encoding="utf-8"))
            if ready["repo"] != MODEL_REPOS[name] or not ready["revision"]:
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise AppError("model_not_ready", "模型尚未通過離線準備驗證。") from exc
    return path


def list_models(settings: Settings) -> list[dict]:
    rows = []
    for name in ("base", "small", "medium"):
        try:
            model_path(settings, name)
            rows.append({"name": name, "ready": True, "message": "已準備"})
        except AppError as exc:
            rows.append({"name": name, "ready": False, "message": exc.message})
    return rows
