import pytest
from pydantic import ValidationError

from app.config import Settings


def test_defaults_match_env_example():
    s = Settings(_env_file=None)
    assert s.fuzzy_threshold == 90
    assert s.max_claims == 6
    assert s.max_depth == 3
    assert s.max_concurrent_analyses == 2
    assert s.prompt_version == "v11"
    assert s.origin_check is True
    assert s.extract_votes == 5
    assert (s.extract_mode, s.summary_votes, s.summary_detail_slots) == ("summary", 3, 2)


def test_max_depth_is_bounded_1_to_5(monkeypatch):
    for ok in ("1", "5"):
        monkeypatch.setenv("MAX_DEPTH", ok)
        assert Settings(_env_file=None).max_depth == int(ok)
    for bad in ("0", "6"):
        monkeypatch.setenv("MAX_DEPTH", bad)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)


def test_env_override(monkeypatch):
    monkeypatch.setenv("FUZZY_THRESHOLD", "85")
    monkeypatch.setenv("PROMPT_VERSION", "v8")
    s = Settings(_env_file=None)
    assert s.fuzzy_threshold == 85
    assert s.prompt_version == "v8"
