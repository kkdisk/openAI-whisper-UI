"""UI 模型清單、請求及準備來源的一致性。"""

import json

import pytest
from pydantic import ValidationError

from app.config import MODEL_REPOS, TRANSCRIPTION_MODELS, Settings
from app.models import list_models, model_path
from app.schemas import JobRequest


def test_unprepared_turbo_is_listed(tmp_path):
    rows = list_models(Settings(root=tmp_path))
    assert [row["name"] for row in rows] == list(TRANSCRIPTION_MODELS)
    assert all(name in MODEL_REPOS for name in TRANSCRIPTION_MODELS)
    assert not next(row for row in rows if row["name"] == "large-v3-turbo")["ready"]
    assert "tiny" not in {row["name"] for row in rows}


def test_ready_turbo_and_request(tmp_path):
    settings = Settings(root=tmp_path)
    directory = settings.models_dir / "large-v3-turbo"
    directory.mkdir(parents=True)
    for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
        (directory / name).write_text("{}", encoding="utf-8")
    (directory / "ready.json").write_text(
        json.dumps(
            {
                "repo": MODEL_REPOS["large-v3-turbo"],
                "revision": "a" * 40,
            }
        ),
        encoding="utf-8",
    )
    assert model_path(settings, "large-v3-turbo") == directory
    assert next(row for row in list_models(settings) if row["name"] == "large-v3-turbo")["ready"]
    request = JobRequest(source_name="test.mp4", expected_bytes=1, model="large-v3-turbo")
    assert request.model == "large-v3-turbo"


def test_model_alias_or_remote_path_not_accepted():
    for model in ("turbo", "../../model", "https://example.test/model"):
        with pytest.raises(ValidationError):
            JobRequest(source_name="test.mp4", expected_bytes=1, model=model)
