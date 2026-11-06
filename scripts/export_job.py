"""等待既有任務終止後匯出，不啟動服務或修改任務狀態。"""

import argparse
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.formatter import export, export_name
from app.schemas import TERMINAL, AppError, JobMeta, TranscriptSegment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("--output", type=Path, default=Settings().root / "data" / "exports")
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-f0-9]{32}", args.job_id):
        parser.error("任務 ID 格式錯誤")
    directory = Settings().jobs_dir / args.job_id
    if not directory.resolve().is_relative_to(Settings().jobs_dir.resolve()):
        parser.error("任務目錄無效")
    while True:
        meta = JobMeta.model_validate_json((directory / "metadata.json").read_text("utf-8"))
        if meta.status in TERMINAL:
            break
        if not args.wait:
            parser.error("任務仍在執行，請加 --wait 等待終止")
        time.sleep(2)
    path = directory / "segments.jsonl"
    rows = (
        [
            TranscriptSegment.model_validate_json(line)
            for line in path.read_text("utf-8").splitlines()
        ]
        if path.exists()
        else []
    )
    destination = args.output.resolve() / args.job_id
    destination.mkdir(parents=True, exist_ok=True)
    for fmt, raw in (("txt", False), ("txt", True), ("md", False), ("srt", False)):
        try:
            text = export(meta, rows, fmt, raw)
        except AppError as exc:
            print(f"{fmt} 匯出失敗：{exc.message}")
            continue
        target = destination / export_name(meta, fmt, raw)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(text.encode("utf-8"))
        os.replace(temporary, target)
    print(f"任務狀態：{meta.status.value}；匯出目錄：{destination}")


if __name__ == "__main__":
    main()
