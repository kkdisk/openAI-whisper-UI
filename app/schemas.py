"""任務、片段與 API 資料契約。"""

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import ModelName


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Status(str, Enum):
    UPLOADING = "uploading"
    VALIDATING = "validating"
    LOADING_MODEL = "loading_model"
    DECODING = "decoding"
    TRANSCRIBING = "transcribing"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


TERMINAL = {Status.COMPLETED, Status.CANCELLED, Status.FAILED, Status.INTERRUPTED}


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_name: str = Field(min_length=1, max_length=255)
    expected_bytes: int = Field(gt=0, strict=True)
    title: str = Field(default="", max_length=300)
    meeting_date: str = Field(default="", max_length=100)
    participants: str = Field(default="", max_length=2000)
    terms: str = Field(default="", max_length=4000)
    model: ModelName = "small"
    language: Literal["zh", "auto", "en", "mixed"] = "zh"
    condition_on_previous_text: bool = True
    cpu_threads: Literal[4, 6, 8] = 4
    traditional: bool = True


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(ser_json_inf_nan="strings")
    segment_id: int
    start_sec: float
    end_sec: float
    raw_text: str
    text: str


class ErrorResponse(BaseModel):
    code: str
    message: str


class ProgressData(BaseModel):
    position_sec: float
    duration_sec: float | None


class TerminalData(BaseModel):
    status: Status
    message: str | None = None


class JobMeta(BaseModel):
    schema_version: int = 1
    job_id: str
    request: JobRequest
    status: Status = Status.UPLOADING
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    duration_sec: float | None = None
    last_position_sec: float = 0
    segment_count: int = 0
    uploaded_bytes: int = 0
    elapsed_sec: float = 0
    error: str | None = None
    error_code: str | None = None
    warnings: list[str] = Field(default_factory=list)
    versions: dict[str, str] = Field(default_factory=dict)
    model_revision: str | None = None
    conversion: str = "s2t"
    beam_size: int = 5
    vad_filter: bool = True
    compute_type: str = "int8"


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)
