import os

from megan.config import Settings


def test_defaults():
    s = Settings(_env_file=None)
    assert s.megan_name == "Megan"
    assert s.max_open_asks == 4  # the spec's hard cap
    assert s.megan_classify_model == "claude-haiku-4-5"
    assert s.megan_reasoning_model == "claude-opus-4-8"


def test_configured_flags():
    s = Settings(_env_file=None)
    assert s.anthropic_configured is False
    assert s.telegram_configured is False

    s2 = Settings(_env_file=None, anthropic_api_key="x", telegram_api_id=1, telegram_api_hash="h")
    assert s2.anthropic_configured is True
    assert s2.telegram_configured is True


def test_env_override(monkeypatch):
    monkeypatch.setenv("MAX_OPEN_ASKS", "3")
    s = Settings(_env_file=None)
    assert s.max_open_asks == 3
    os.environ.pop("MAX_OPEN_ASKS", None)
