import pytest

from auditbot import config

REQUIRED = {"TELEGRAM_BOT_TOKEN": "123:abc", "PLATFORM_USERNAME": "audit-bot",
            "PLATFORM_PASSWORD": "secret-1"}


@pytest.fixture
def env(monkeypatch):
    for name in ("ALLOWED_CHAT_IDS", "ALERT_CHAT_IDS", "JURNAL_LOGIN", "JURNAL_PAROL",
                 "ALERT_INTERVAL_SECONDS", "TENDER_URL", "TELEGRAM_API_URL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def test_defaults(env):
    env.setenv("ALLOWED_CHAT_IDS", "111, -1002")
    env.setenv("ALERT_CHAT_IDS", "")                     # `NAME=` in .env: the default
    s = config.load()
    assert s.allowed_chats == {111, -1002} and s.alert_chats == {111, -1002}
    assert s.platform_url == "http://gateway:8000" and s.tender_url == "http://tender-v2:8000"
    assert not s.tender_enabled and s.alerts_enabled
    assert s.alert_interval == 30 and s.alert_tender_interval == 60
    assert s.tz.utcoffset(None).total_seconds() == 5 * 3600
    assert "secret-1" not in repr(s) and "123:abc" not in repr(s)


def test_setup_mode_without_chats(env):
    s = config.load()
    assert s.allowed_chats == frozenset() and not s.alerts_enabled


def test_alert_chats_must_be_allowed(env):
    env.setenv("ALLOWED_CHAT_IDS", "111")
    env.setenv("ALERT_CHAT_IDS", "222")
    with pytest.raises(config.ConfigError, match="subset"):
        config.load()


def test_tender_needs_both_credentials(env):
    env.setenv("ALLOWED_CHAT_IDS", "1")
    env.setenv("JURNAL_LOGIN", "j")
    assert not config.load().tender_enabled
    env.setenv("JURNAL_PAROL", "p" * 24)
    assert config.load().tender_enabled


@pytest.mark.parametrize("name, value", [("ALLOWED_CHAT_IDS", "abc"),
                                         ("ALERT_INTERVAL_SECONDS", "soon"),
                                         ("ALERT_WINDOW_SECONDS", "5")])
def test_bad_values_stop_with_one_line(env, name, value):
    env.setenv(name, value)
    with pytest.raises(config.ConfigError) as exc:
        config.load()
    assert name in str(exc.value)


def test_missing_required(env):
    env.delenv("PLATFORM_PASSWORD")
    with pytest.raises(config.ConfigError, match="PLATFORM_PASSWORD"):
        config.load()
