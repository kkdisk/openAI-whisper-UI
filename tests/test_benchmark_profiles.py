"""提示比較只能改提示，其他辨識參數須固定。"""

import pytest

from app.schemas import JobRequest
from app.transcriber import transcription_options
from scripts.benchmark_transcription import profile_options


def test_prompt_comparison_preserves_other_options():
    options = transcription_options(
        JobRequest(source_name="test.mp3", expected_bytes=1, language="auto")
    )
    original = options.copy()
    profiles = profile_options(["baseline", "no_prompt", "terms_only"], options, " API GLT-123 ")
    assert profiles["baseline"] == original
    assert profiles["no_prompt"]["initial_prompt"] is None
    assert profiles["terms_only"]["initial_prompt"] == "API GLT-123"
    for profile in profiles.values():
        assert {k: v for k, v in profile.items() if k != "initial_prompt"} == {
            k: v for k, v in original.items() if k != "initial_prompt"
        }
    assert options == original


def test_empty_terms_rejected():
    with pytest.raises(ValueError, match="terms_only"):
        profile_options(["terms_only"], {}, " ")
