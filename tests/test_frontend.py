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
        [node, "--test", str(Path(__file__).with_name("frontend.test.cjs"))],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
