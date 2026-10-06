import sys
from datetime import timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from auditbot.config import Settings  # noqa: E402

TZ = timezone(timedelta(hours=5), "Toshkent")


@pytest.fixture
def make_settings(tmp_path):
    def make(**over) -> Settings:
        values = dict(
            telegram_token="123:test", telegram_api="https://tg.test",
            platform_url="http://gateway.test", platform_user="audit-bot",
            platform_password="secret-1",
            tender_url="http://tender.test", tender_user="jurnal-oquvchi", tender_password="x" * 24,
            tz=TZ, state_path=str(tmp_path / "state" / "state.json"),
            heartbeat_path=str(tmp_path / "heartbeat"),
            alert_interval=30, alert_tender_interval=60, alert_window=600,
            alert_failed_logins=3, alert_browser_errors=True, alert_max_per_minute=15,
            alert_catchup_rows=1000, export_max_rows=20000, export_max_bodies=300,
        )
        values.update(over)
        return Settings(**values)
    return make
