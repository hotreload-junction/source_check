from app.config import Settings


def test_defaults_match_env_example():
    s = Settings(_env_file=None)
    assert s.fuzzy_threshold == 90
    assert s.max_claims == 6
    assert s.max_depth == 1
    assert s.max_concurrent_analyses == 2
    assert s.prompt_version == "v2"


def test_env_override(monkeypatch):
    monkeypatch.setenv("FUZZY_THRESHOLD", "85")
    monkeypatch.setenv("PROMPT_VERSION", "v7")
    s = Settings(_env_file=None)
    assert s.fuzzy_threshold == 85
    assert s.prompt_version == "v7"
