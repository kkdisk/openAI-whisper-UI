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


class ExecutionState(str, Enum):
    RUNNING = "running"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    RESUME_REQUESTED = "resume_requested"


TERMINAL = {Status.COMPLETED, Status.CANCELLED, Status.FAILED, Status.INTERRUPTED}
CONTINUABLE = {Status.CANCELLED, Status.FAILED, Status.INTERRUPTED}


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_name: str = Field(min_length=1, max_length=255)
    expected_bytes: int = Field(gt=0, strict=True)
    title: str = Field(default="", max_length=300)
    meeting_date: str = Field(default="", max_length=100)
    participants: str = Field(default="", max_length=2000)
    terms: str = Field(default="", max_length=4000)
    model: ModelName = "small"
    device: Literal["auto", "cpu", "cuda"] = "cpu"
    compute_type: Literal["auto", "int8_float16", "float16"] = "auto"
    device_index: int = Field(default=0, ge=0, le=31, strict=True)
    language: Literal["zh", "auto", "en", "mixed"] = "zh"
    condition_on_previous_text: bool = True
    cpu_threads: Literal[4, 6, 8] = 4
    traditional: bool = True


class ContinueRequest(BaseModel):
    """從已保存位置續跑；未提供的設定沿用原任務。"""

    model_config = ConfigDict(extra="forbid")
    expected_bytes: int = Field(gt=0, strict=True)
    model: ModelName | None = None
    device: Literal["auto", "cpu", "cuda"] | None = None
    compute_type: Literal["auto", "int8_float16", "float16"] | None = None
    device_index: int | None = Field(default=None, ge=0, le=31, strict=True)
    cpu_threads: Literal[4, 6, 8] | None = None


class Continuation(BaseModel):
    """一次續跑的紀錄；接縫在 from_sec 附近，需人工回聽。"""

    from_sec: float
    first_segment_id: int
    model: str
    device: str
    cpu_threads: int
    created_at: str = Field(default_factory=now_iso)
    previous_status: str
    prior_elapsed_sec: float = 0
    prior_active_elapsed_sec: float = 0
    prior_paused_sec: float = 0
    prior_actual_device: str | None = None
    source_check: Literal["pending", "sha256", "size_duration"] = "pending"
    segmentation: Literal["whole", "rolling"] = "whole"


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(ser_json_inf_nan="strings")
    segment_id: int
    start_sec: float
    end_sec: float
    raw_text: str
    text: str
    # 分段辨識時，片段起點已超出本塊音訊：多為模型在塊尾補白處自行生成。
    # 原稿保留；修訂稿預設排除，使用者可復原。
    outside_audio: bool = False


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
    active_elapsed_sec: float = 0
    paused_sec: float = 0
    execution_state: ExecutionState = ExecutionState.RUNNING
    control_seq: int = 0
    control_wait_sec: float = 0
    error: str | None = None
    error_code: str | None = None
    warnings: list[str] = Field(default_factory=list)
    versions: dict[str, str] = Field(default_factory=dict)
    model_revision: str | None = None
    conversion: str = "s2t"
    beam_size: int = 5
    vad_filter: bool = True
    compute_type: str = "int8"
    actual_device: str | None = None
    actual_device_index: int | None = None
    fallback_reason: str | None = None
    source_sha256: str | None = None
    continuations: list[Continuation] = Field(default_factory=list)
    # 舊任務沒有這些欄位時視為整段辨識。
    segmentation: Literal["whole", "rolling"] = "whole"
    block_sec: float | None = None
    boundaries_sec: list[float] = Field(default_factory=list)
    detected_language: str | None = None


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)
