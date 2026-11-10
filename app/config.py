"""集中管理本機路徑與資源限制。"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, get_args

ROOT = Path(__file__).resolve().parent.parent
ModelName = Literal["base", "small", "medium", "large-v3-turbo"]
TRANSCRIPTION_MODELS = get_args(ModelName)
SEGMENTATION_MODES = ("whole", "rolling")
MODEL_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}


def _segmentation_default() -> str:
    # R2 期間預設整段；開發驗證可設 WHISPER_SEGMENTATION=rolling 啟用分段辨識。
    return os.environ.get("WHISPER_SEGMENTATION", "whole")


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    max_upload_bytes: int = 4 * 1024**3
    disk_reserve_bytes: int = 1024**3
    max_duration_sec: int = 180 * 60
    max_json_bytes: int = 64 * 1024
    upload_wait_sec: float = 60
    upload_idle_sec: float = 60
    upload_total_sec: float = 3600
    cancel_grace_sec: float = 2
    prompt_tokens: int = 200
    port: int = 8000
    # 分段（rolling）辨識；參數待 R1 實驗定案，見 tasks/rolling-transcription.md。
    segmentation: str = field(default_factory=_segmentation_default)
    block_sec: float = 600
    boundary_search_sec: float = 30
    min_gap_sec: float = 0.3
    language_lock_min_speech_sec: float = 2.0
    prev_context_tokens: int | None = None
    rolling_max_duration_sec: int = 12 * 3600

    def __post_init__(self):
        if self.segmentation not in SEGMENTATION_MODES:
            raise ValueError(f"segmentation 須為 {SEGMENTATION_MODES} 之一")
        if not 0 < self.boundary_search_sec < self.block_sec:
            raise ValueError("boundary_search_sec 須大於 0 且小於 block_sec")

    @property
    def effective_max_duration_sec(self) -> int:
        """依辨識模式選擇時長上限：整段受記憶體限制，分段只需安全上限。"""
        if self.segmentation == "rolling":
            return self.rolling_max_duration_sec
        return self.max_duration_sec

    @property
    def models_dir(self) -> Path:
        return self.root / "models"

    @property
    def jobs_dir(self) -> Path:
        return self.root / "data" / "jobs"
