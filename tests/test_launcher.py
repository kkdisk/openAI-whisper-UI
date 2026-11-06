"""CMD 換行、編碼及含空白路徑的啟動回歸檢查。"""

import os
import shutil
import subprocess

import pytest

from app.config import ROOT


def test_batch_uses_ascii_crlf():
    data = (ROOT / "start.bat").read_bytes()
    assert data.isascii()
    assert not data.startswith(b"\xef\xbb\xbf")
    assert data.count(b"\n") == data.count(b"\r\n")


@pytest.mark.windows
@pytest.mark.skipif(os.name != "nt", reason="Windows CMD 專用")
def test_missing_environment_in_spaced_directory(tmp_path):
    directory = tmp_path / "啟動 test"
    directory.mkdir()
    script = directory / "start.bat"
    shutil.copyfile(ROOT / "start.bat", script)
    result = subprocess.run(
        f'cmd.exe /d /s /c ""{script}""',
        cwd=tmp_path,
        input=b"\r\n",
        capture_output=True,
        timeout=10,
    )
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode == 1
    assert "Python environment is missing" in output
    assert "不是內部或外部命令" not in output
