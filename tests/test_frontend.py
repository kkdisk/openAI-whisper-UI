"""以 Node 執行前端非同步任務切換回歸測試。"""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_frontend_task_tracking():
    node = shutil.which("node")
    if node is None:
        pytest.skip("前端回歸測試需要 Node.js")
    result = subprocess.run(
        [
            node,
            "--test",
            str(Path(__file__).with_name("frontend.test.cjs")),
            str(Path(__file__).with_name("theme.test.cjs")),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_hidden_controls_stay_hidden():
    """通用 input 樣式不可蓋過 hidden；續跑用的選檔欄位曾因此外露。"""
    import re

    root = Path(__file__).resolve().parents[1] / "app" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    css = (root / "style.css").read_text(encoding="utf-8")
    tags = {m.group(1) for m in re.finditer(r"<(\w+)[^>]*\shidden[\s>]", html)}
    assert tags, "預期至少有一個 hidden 元素"
    for tag in tags:
        assert re.search(rf"{tag}\[hidden\][^{{]*\{{[^}}]*display:\s*none", css), tag
