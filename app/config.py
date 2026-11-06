"""集中管理本機路徑與資源限制。"""

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
}


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    max_upload_bytes: int = 4 * 1024**3
    disk_reserve_bytes: int = 1024**3
    max_duration_sec: int = 120 * 60
    max_json_bytes: int = 64 * 1024
    upload_wait_sec: float = 60
    upload_idle_sec: float = 60
    upload_total_sec: float = 3600
    cancel_grace_sec: float = 2
    prompt_tokens: int = 200
    port: int = 8000

    @property
    def models_dir(self) -> Path:
        return self.root / "models"

    @property
    def jobs_dir(self) -> Path:
        return self.root / "data" / "jobs"
