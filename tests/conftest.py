"""一般測試不連網，真實模型必須明確啟用。"""

import ipaddress
import socket

import pytest


def pytest_addoption(parser):
    parser.addoption("--real-model", action="store_true", help="執行本機真實模型測試")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--real-model"):
        for item in items:
            if "real_model" in item.keywords:
                item.add_marker(pytest.mark.skip(reason="需 --real-model 及本機模型／語音素材"))


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    original = socket.socket.connect

    def local_only(sock, address):
        if isinstance(address, tuple):
            try:
                if ipaddress.ip_address(address[0]).is_loopback:
                    return original(sock, address)
            except ValueError:
                pass
        raise AssertionError("測試不得建立外部網路連線")

    monkeypatch.setattr(socket.socket, "connect", local_only)
