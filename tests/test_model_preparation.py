"""下載後完整性與離線復原流程。"""

import hashlib
import json
from types import SimpleNamespace

import pytest

from app.config import MODEL_REPOS, Settings
from app.models import model_path
from scripts import prepare_models as preparation

REVISION = "a" * 40


def assets(settings):
    directory = settings.models_dir / "medium"
    metadata = directory / ".cache" / "huggingface" / "download"
    metadata.mkdir(parents=True)
    for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
        data = b"test model data" if name == "model.bin" else b"{}"
        (directory / name).write_bytes(data)
        digest = (
            hashlib.sha256(data).hexdigest()
            if name == "model.bin"
            else hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
        )
        (metadata / f"{name}.metadata").write_text(f"{REVISION}\n{digest}\n1.0\n", "utf-8")
    return directory


def test_verify_only_marks_ready_after_runtime(tmp_path, monkeypatch):
    settings = Settings(root=tmp_path)
    directory = assets(settings)
    calls = []

    def runtime(path):
        assert path == directory
        assert not (path / "ready.json").exists()
        calls.append(path)

    monkeypatch.setattr(preparation, "verify_runtime", runtime)
    preparation.prepare_model("medium", settings, verify_only=True)
    assert calls == [directory]
    assert model_path(settings, "medium") == directory
    marker = json.loads((directory / "ready.json").read_text("utf-8"))
    assert marker == {"repo": MODEL_REPOS["medium"], "revision": REVISION}


@pytest.mark.parametrize("failure", ["hash", "missing_metadata"])
def test_invalid_files_never_mark_ready(tmp_path, monkeypatch, failure):
    settings = Settings(root=tmp_path)
    directory = assets(settings)
    if failure == "hash":
        (directory / "model.bin").write_bytes(b"damaged")
    else:
        (directory / ".cache/huggingface/download/tokenizer.json.metadata").unlink()
    monkeypatch.setattr(preparation, "verify_runtime", lambda path: pytest.fail("不得開始載入"))
    with pytest.raises(ValueError):
        preparation.prepare_model("medium", settings, verify_only=True)
    assert not (directory / "ready.json").exists()
    assert (directory / "model.bin").exists()


def test_revision_mismatch(tmp_path):
    settings = Settings(root=tmp_path)
    directory = assets(settings)
    with pytest.raises(ValueError, match="revision"):
        preparation.verify_download_files(directory, "b" * 40)


def test_runtime_failure_removes_stale_ready(tmp_path, monkeypatch):
    settings = Settings(root=tmp_path)
    directory = assets(settings)
    (directory / "ready.json").write_text("{}", "utf-8")

    def fail(path):
        raise RuntimeError("模擬載入失敗")

    monkeypatch.setattr(preparation, "verify_runtime", fail)
    with pytest.raises(RuntimeError):
        preparation.prepare_model("medium", settings, verify_only=True)
    assert not (directory / "ready.json").exists()


def test_failed_download_preserves_files(tmp_path, monkeypatch):
    import huggingface_hub

    settings = Settings(root=tmp_path)
    directory = assets(settings)
    calls = []

    def failed_download(repo, **kwargs):
        calls.append(kwargs)
        raise OSError("模擬下載結束階段失敗")

    monkeypatch.setattr(
        huggingface_hub,
        "HfApi",
        lambda: SimpleNamespace(model_info=lambda *args, **kwargs: SimpleNamespace(sha=REVISION)),
    )
    monkeypatch.setattr(huggingface_hub, "snapshot_download", failed_download)
    with pytest.raises(OSError):
        preparation.prepare_model("medium", settings)
    assert calls[0]["max_workers"] == 1
    assert not (directory / "ready.json").exists()
    assert (directory / "model.bin").exists()
