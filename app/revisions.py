"""獨立保存人工修訂；呼叫者須持有 JobManager.lock。"""

import math
import os
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.schemas import AppError, TranscriptSegment


class RevisionRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(max_length=40)
    anchor: int = Field(default=0, ge=0)
    text: str = Field(max_length=12000)
    deleted: bool = False
    start_sec: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    end_sec: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class RevisionState(BaseModel):
    version: int = Field(default=0, ge=0)
    rows: list[RevisionRow] = Field(default_factory=list)


class RevisionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=0, strict=True)
    row: RevisionRow


def read_revisions(directory: Path) -> RevisionState:
    path = directory / "revisions.json"
    if not path.exists():
        return RevisionState()
    try:
        return RevisionState.model_validate_json(path.read_text("utf-8"))
    except (ValueError, OSError, ValidationError) as exc:
        raise AppError("revision_corrupt", "修訂檔無法讀取，請保留檔案並檢查備份。", 500) from exc


def write_revision(directory: Path, originals: list[TranscriptSegment], change: RevisionWrite):
    state = read_revisions(directory)
    if state.version != change.version:
        raise AppError(
            "revision_conflict", "其他分頁已修改此稿。請先複製未儲存文字，再重新整理。", 409
        )
    row = change.row.model_copy(deep=True)
    if row.key == "new":
        row.key = "u:" + uuid4().hex
    existing = next((i for i, item in enumerate(state.rows) if item.key == row.key), None)
    if row.key.startswith("s:"):
        if row.key not in {f"s:{s.segment_id}" for s in originals} or row.anchor != 0:
            raise AppError("invalid_revision", "找不到要修訂的原始片段。", 422)
        if row.start_sec is not None or row.end_sec is not None:
            raise AppError("invalid_revision", "原始片段的時間戳保留不變。", 422)
    elif row.key.startswith("u:"):
        if change.row.key != "new" and existing is None:
            raise AppError("invalid_revision", "找不到新增語句。", 422)
        if row.anchor > len(originals):
            raise AppError("invalid_revision", "新增位置尚未轉錄。", 422)
        if existing is not None and state.rows[existing].anchor != row.anchor:
            raise AppError("invalid_revision", "新增位置不可變更。", 422)
    else:
        raise AppError("invalid_revision", "片段識別無效。", 422)
    if (row.start_sec is None) != (row.end_sec is None) or (
        row.start_sec is not None and row.end_sec <= row.start_sec
    ):
        raise AppError("invalid_timestamp", "請同時填寫起訖秒數，結束須大於開始；或兩者留空。", 422)
    if existing is None:
        state.rows.append(row)
    else:
        state.rows[existing] = row
    state.version += 1
    temp = directory / "revisions.tmp"
    with temp.open("w", encoding="utf-8") as handle:
        handle.write(state.model_dump_json())
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, directory / "revisions.json")
    return state


def revised_segments(originals: list[TranscriptSegment], state: RevisionState):
    overrides = {row.key: row for row in state.rows}
    inserted: dict[int, list[RevisionRow]] = {}
    for row in state.rows:
        if row.key.startswith("u:") and not row.deleted:
            inserted.setdefault(row.anchor, []).append(row)
    result = []

    def add_inserted(anchor):
        for row in inserted.get(anchor, []):
            result.append(
                TranscriptSegment(
                    segment_id=0,
                    start_sec=row.start_sec if row.start_sec is not None else math.nan,
                    end_sec=row.end_sec if row.end_sec is not None else math.nan,
                    text=row.text,
                    raw_text=row.text,
                )
            )

    add_inserted(0)
    for original in originals:
        row = overrides.get(f"s:{original.segment_id}")
        if row is None or not row.deleted:
            result.append(original.model_copy(update={"text": row.text}) if row else original)
        add_inserted(original.segment_id)
    return result
