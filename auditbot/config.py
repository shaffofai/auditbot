"""Settings, read once from the environment at start.

The container sees only what docker-compose.yml passes it (``env_file: .env``).
Secrets — the Telegram token and the two passwords — are never printed: the
repr of :class:`Settings` hides them, and nothing logs a URL that carries the
token (httpx's own request log is silenced in ``__main__``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta, timezone


class ConfigError(SystemExit):
    """A missing or malformed setting: the process stops with one clear line."""


def _text(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        if required:
            raise ConfigError(f"auditbot: {name} is not set (see .env.example)")
        return default or ""
    return value


def _number(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"auditbot: {name} must be a whole number, got {raw!r}") from None
    if value < minimum:
        raise ConfigError(f"auditbot: {name} must be at least {minimum}")
    return value


def _ids(name: str, default: str = "") -> frozenset[int]:
    raw = os.environ.get(name, "").strip() or default     # `NAME=` in .env means the default
    out = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            raise ConfigError(f"auditbot: {name} holds a value that is not a chat id: {part!r}") from None
    return frozenset(out)


@dataclass(frozen=True)
class Settings:
    telegram_token: str = field(repr=False)
    telegram_api: str
    allowed_chats: frozenset[int]
    alert_chats: frozenset[int]

    platform_url: str
    platform_user: str
    platform_password: str = field(repr=False)

    tender_url: str
    tender_user: str
    tender_password: str = field(repr=False)

    tz: timezone
    state_path: str
    heartbeat_path: str

    alert_interval: int          # seconds between two looks at the platform's tail
    alert_tender_interval: int   # the same for tender-v2 (each look is a journal row there)
    alert_window: int            # seconds: failed-login counting window, de-duplication window
    alert_failed_logins: int     # failed sign-ins in the window that make an alert
    alert_browser_errors: bool
    alert_max_per_minute: int
    alert_catchup_rows: int      # after a long outage, skip ahead instead of replaying more than this

    export_max_rows: int         # per source
    export_max_bodies: int       # rows whose bodies are fetched one by one when `tanalar` is asked

    @property
    def tender_enabled(self) -> bool:
        return bool(self.tender_url and self.tender_user and self.tender_password)

    @property
    def alerts_enabled(self) -> bool:
        return bool(self.alert_chats) and self.alert_interval > 0


def load() -> Settings:
    # Empty is allowed on purpose: the first start, when nobody knows a chat id
    # yet. The bot then answers /id only (setup mode) and sends no alerts.
    allowed = _ids("ALLOWED_CHAT_IDS")
    alerts = _ids("ALERT_CHAT_IDS", ",".join(str(i) for i in sorted(allowed)))
    stray = alerts - allowed
    if stray:
        raise ConfigError("auditbot: ALERT_CHAT_IDS must be a subset of ALLOWED_CHAT_IDS "
                          f"(not allowed: {', '.join(str(i) for i in sorted(stray))})")
    return Settings(
        telegram_token=_text("TELEGRAM_BOT_TOKEN", required=True),
        telegram_api=_text("TELEGRAM_API_URL", "https://api.telegram.org").rstrip("/"),
        allowed_chats=allowed,
        alert_chats=alerts,
        platform_url=_text("PLATFORM_URL", "http://gateway:8000").rstrip("/"),
        platform_user=_text("PLATFORM_USERNAME", required=True),
        platform_password=_text("PLATFORM_PASSWORD", required=True),
        tender_url=_text("TENDER_URL", "http://tender-v2:8000").rstrip("/"),
        tender_user=_text("JURNAL_LOGIN"),
        tender_password=_text("JURNAL_PAROL"),
        tz=timezone(timedelta(hours=_number("TZ_OFFSET_HOURS", 5)), "Toshkent"),
        state_path=_text("STATE_PATH", "/state/state.json"),
        heartbeat_path=_text("HEARTBEAT_PATH", "/tmp/auditbot.heartbeat"),
        alert_interval=_number("ALERT_INTERVAL_SECONDS", 30),
        alert_tender_interval=_number("ALERT_TENDER_INTERVAL_SECONDS", 60),
        alert_window=_number("ALERT_WINDOW_SECONDS", 600, minimum=60),
        alert_failed_logins=_number("ALERT_FAILED_LOGINS", 5, minimum=1),
        alert_browser_errors=_text("ALERT_BROWSER_ERRORS", "1") not in ("0", "false", "no", "off"),
        alert_max_per_minute=_number("ALERT_MAX_PER_MINUTE", 15, minimum=1),
        alert_catchup_rows=_number("ALERT_CATCHUP_ROWS", 5000, minimum=100),
        export_max_rows=_number("EXPORT_MAX_ROWS", 20000, minimum=100),
        export_max_bodies=_number("EXPORT_MAX_BODIES", 300),
    )
