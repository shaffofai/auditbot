from auditbot.alerts import Alerts
from conftest import TZ


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(**kw):
    clock = Clock()
    return Alerts(tz=TZ, window=600, failed_logins=3, clock=clock, **kw), clock


def req(i, status, path="/api-v2/fasad/front/report", source="fasad", **extra):
    return {"id": i, "kind": "request", "event": "http", "status_code": status, "path": path,
            "method": "POST", "source": source, "created_at": "2026-10-05T04:00:00Z",
            "username": "ali@misol.uz", **extra}


def test_5xx_alert_then_deduplicated_then_counted():
    alerts, clock = make()
    out = alerts.platform([req(1, 500, meta={"exception": "KeyError"}), req(2, 200), req(3, 500)])
    assert len(out) == 1
    assert "Server xatosi 500" in out[0] and "fasad" in out[0] and "KeyError" in out[0]
    assert "09:00:00" in out[0] and "#1" in out[0]
    assert alerts.platform([req(4, 500)]) == []
    clock.t += 601
    again = alerts.platform([req(5, 500)])
    assert len(again) == 1 and "yana 2 marta" in again[0]
    assert len(alerts.platform([req(6, 503)])) == 1             # another status: its own alert


def test_escaping_of_user_controlled_text():
    alerts, _ = make()
    out = alerts.platform([req(1, 500, path="/x<script>&", username="<b>me</b>")])
    assert "<script>" not in out[0] and "&lt;script&gt;&amp;" in out[0]
    assert "&lt;b&gt;me&lt;/b&gt;" in out[0]


def test_failed_logins_alert_once_at_threshold():
    alerts, clock = make()
    failed = {"kind": "auth", "event": "login_failed", "username": "bob", "user_side": "back",
              "created_at": "2026-10-05T04:00:00Z", "meta": {"account_exists": True}}
    assert alerts.platform([dict(failed, id=1), dict(failed, id=2)]) == []
    out = alerts.platform([dict(failed, id=3)])
    assert len(out) == 1 and "bob" in out[0] and "akkaunt mavjud" in out[0] and "3 marta" in out[0]
    assert alerts.platform([dict(failed, id=4)]) == []          # the 4th: no second alert
    clock.t += 700                                              # the window forgets them
    assert alerts.platform([dict(failed, id=5), dict(failed, id=6)]) == []
    assert len(alerts.platform([dict(failed, id=7)])) == 1


def test_browser_errors_are_one_message():
    alerts, _ = make()
    rows = [{"id": i, "kind": "client", "event": "js.error", "message": f"TypeError {i}\nstack",
             "username": "u", "path": "/dashboard"} for i in range(8)]
    rows.append({"id": 99, "kind": "client", "event": "console.warn", "message": "meh"})
    out = alerts.platform(rows)
    assert len(out) == 1
    assert "Brauzer xatolari</b>: 8 ta yangi" in out[0]
    assert "TypeError 0" in out[0] and "stack" not in out[0] and "va yana 3 ta" in out[0]
    assert alerts.platform(rows[:3]) == []                      # the same lines: deduplicated


def test_browser_errors_can_be_off():
    alerts, _ = make(browser_errors=False)
    assert alerts.platform([{"id": 1, "kind": "client", "event": "js.error", "message": "x"}]) == []


def test_backend_errors_drops_and_deletes():
    alerts, _ = make()
    out = alerts.platform([
        {"id": 1, "kind": "log", "event": "log", "level": "error", "source": "gasn",
         "message": "query failed\nTraceback", "meta": {"logger": "gasn.sql"}},
        {"id": 2, "kind": "log", "event": "log", "level": "warning", "message": "slow"},
        {"id": 3, "kind": "log", "event": "log.dropped", "level": "error", "source": "gateway",
         "message": "12 audit rows dropped"},
        {"id": 4, "kind": "manual", "event": "audit.purge", "username": "admin",
         "message": "purged 500 rows before 2026-09-01"},
    ])
    assert len(out) == 3
    assert "Backend xatosi" in out[0] and "gasn.sql" in out[0]
    assert "tashlandi" in out[1] and "12 audit rows" in out[1]
    assert "o'chirildi" in out[2] and "admin" in out[2]
    # The writer's own notice (level warning, kind manual) and a browser over its budget.
    out = alerts.platform([
        {"id": 5, "kind": "manual", "event": "audit.dropped", "level": "warning",
         "source": "fasad", "message": "3 audit row(s) were dropped"},
        {"id": 6, "kind": "client", "event": "client.dropped", "level": "warning",
         "source": "web", "message": "40 browser event(s) over the budget were not stored"},
        {"id": 7, "kind": "client", "event": "client.dropped", "level": "warning",
         "source": "web", "message": "41 browser event(s) over the budget were not stored"},
    ])
    assert len(out) == 2 and "fasad" in out[0] and "40 browser" in out[1]


def test_tender_rules():
    alerts, _ = make()
    kirish = {"tur": "kirish", "hodisa": "kirish_rad", "login": "tender",
              "qoshimcha": {"sabab": "parol noto'g'ri"}}
    rows = [
        {"id": 1, "tur": "sorov", "usul": "POST", "yol": "/check", "holat_kodi": 500,
         "qoshimcha": {"istisno": "OperationalError"}},
        {"id": 2, "tur": "sorov", "usul": "GET", "yol": "/jurnal", "holat_kodi": 200},
        {"id": 3, "tur": "log", "hodisa": "jurnal_tashlandi", "daraja": "error", "xabar": "40 ta"},
        {"id": 4, "tur": "log", "daraja": "error", "manba": "worker", "xabar": "fayl buzuq"},
        dict(kirish, id=5), dict(kirish, id=6), dict(kirish, id=7),
        {"id": 8, "tur": "amal", "hodisa": "requeue", "manba": "worker", "qoshimcha": {"n": 3}},
    ]
    out = alerts.tender(rows)
    assert len(out) == 5
    assert "tender-v2 server xatosi 500" in out[0] and "OperationalError" in out[0]
    assert "jurnal yozuvlari tashlandi" in out[1]
    assert "fayl buzuq" in out[2]
    assert "ko'p rad etilgan kirish" in out[3] and "parol noto'g'ri" in out[3]
    assert "operator amali" in out[4] and "requeue" in out[4]


def test_limit_per_minute():
    alerts, clock = make(max_per_minute=4)
    out = alerts.limit([f"m{i}" for i in range(10)])
    assert out[:3] == ["m0", "m1", "m2"] and len(out) == 4 and "Yana 7 ta" in out[3]
    assert alerts.limit(["late"]) == []                        # the minute is used up
    clock.t += 61
    out = alerts.limit(["m"])
    assert out[0] == "m" and "Yana 1 ta" in out[1]             # the held one is reported
    assert alerts.limit(["n"]) == ["n"]
