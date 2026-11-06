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


def test_interrupt_closes_manager_and_socket(monkeypatch, capsys):
    from types import SimpleNamespace
    from unittest.mock import Mock

    import uvicorn

    import app.main as entry

    sock = Mock()
    manager = Mock()
    server = Mock()
    server.run.side_effect = KeyboardInterrupt
    monkeypatch.setattr(entry.socket, "socket", Mock(return_value=sock))
    monkeypatch.setattr(
        entry,
        "create_app",
        lambda settings: SimpleNamespace(state=SimpleNamespace(manager=manager)),
    )
    monkeypatch.setattr(uvicorn, "Server", lambda config: server)
    monkeypatch.setattr("threading.Thread", Mock())
    entry.main()
    manager.close.assert_called_once()
    sock.close.assert_called_once()
    output = capsys.readouterr()
    assert "服務已停止" in output.out
    assert "Traceback" not in output.err


@pytest.mark.windows
@pytest.mark.skipif(os.name != "nt", reason="Windows SIGINT／Uvicorn 整合")
def test_uvicorn_sigint_exits_without_traceback(tmp_path):
    import sys

    code = f"""
import signal, threading
from pathlib import Path
import app.main as entry
from app.config import Settings
entry.Settings = lambda: Settings(root=Path({str(tmp_path)!r}), port=0)
entry.webbrowser.open = lambda url: True
timer = threading.Timer(1.5, lambda: signal.raise_signal(signal.SIGINT))
timer.daemon = True
timer.start()
entry.main()
print("SHUTDOWN_RETURNED")
"""
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code],
        cwd=ROOT,
        capture_output=True,
        timeout=15,
    )
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode == 0, output
    assert "SHUTDOWN_RETURNED" in output
    assert "KeyboardInterrupt" not in output
    assert "Traceback" not in output
