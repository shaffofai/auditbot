import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from auditbot import sources
from auditbot.sources import Platform, SourceError, Tender
from fakes import T0, FakePlatform, FakeTender

RANGE = (T0 - timedelta(hours=1), T0 + timedelta(days=1))


def platform_for(fake: FakePlatform, password="secret-1") -> Platform:
    return Platform("http://gateway.test", "audit-bot", password,
                    httpx.AsyncClient(transport=fake.transport()))


def tender_for(fake: FakeTender) -> Tender:
    return Tender("http://tender.test", fake.user, fake.password,
                  httpx.AsyncClient(transport=fake.transport()))


def run(coro):
    return asyncio.run(coro)


def test_platform_range_pages_with_the_cursor_and_returns_oldest_first():
    fake = FakePlatform()
    for i in range(450):
        fake.add(source="fasad" if i % 2 else "gasn")
    rows, truncated = run(platform_for(fake).range(*RANGE, {}, 20000))
    assert [r["id"] for r in rows] == list(range(1, 451)) and not truncated
    pages = [c for c in fake.calls if c["path"] == "/admin/gateway/audit"]
    assert len(pages) == 3
    assert all(c["params"]["detail"] == "true" and c["params"]["limit"] == "200" for c in pages)
    assert "cursor" not in pages[0]["params"] and "cursor" in pages[1]["params"]
    assert pages[0]["params"]["from"].endswith("+00:00")
    assert "meta" in rows[0]                                  # detail fields came along


def test_platform_range_filters_and_truncation():
    fake = FakePlatform()
    for i in range(30):
        fake.add(level="error" if i < 25 else "info")
    rows, truncated = run(platform_for(fake).range(*RANGE, {"level": "error"}, 10))
    assert len(rows) == 10 and truncated
    assert fake.calls[0]["params"]["level"] == "error"


def test_platform_tail_and_head():
    fake = FakePlatform()
    for _ in range(5):
        fake.add()
    platform = platform_for(fake)
    assert run(platform.head()) == 5
    rows, nxt = run(platform.tail(2))
    assert [r["id"] for r in rows] == [3, 4, 5] and nxt == 5
    assert set(fake.calls[-1]["params"]) == {"after_id", "limit", "detail"}
    rows, nxt = run(platform.tail(5))
    assert rows == [] and nxt == 5


def test_platform_empty_table():
    assert run(platform_for(FakePlatform()).head()) == 0


def test_platform_row_and_missing_row():
    fake = FakePlatform()
    fake.add(request_body="{}")
    platform = platform_for(fake)
    assert run(platform.row(1))["request_body"] == "{}"
    assert run(platform.row(9)) is None


@pytest.mark.parametrize("status, text", [(403, "admin emas"), (429, "cheklangan")])
def test_refusals_are_auth_errors(status, text):
    fake = FakePlatform()
    fake.fail_with = status
    with pytest.raises(SourceError) as exc:
        run(platform_for(fake).head())
    assert exc.value.auth and text in exc.value.message
    assert len(fake.calls) == 1                               # never retried


def test_wrong_password():
    with pytest.raises(SourceError) as exc:
        run(platform_for(FakePlatform(), password="wrong").head())
    assert exc.value.auth and "401" in exc.value.message


def test_busy_is_retried_then_reported(monkeypatch):
    slept = []

    async def no_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(sources.asyncio, "sleep", no_sleep)
    fake = FakePlatform()
    fake.fail_with = 503
    with pytest.raises(SourceError) as exc:
        run(platform_for(fake).head())
    assert not exc.value.auth and "503" in exc.value.message and "fake failure" in exc.value.message
    assert len(fake.calls) == 3 and len(slept) == 2


def test_unreachable():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    platform = Platform("http://gateway.test", "u", "p",
                        httpx.AsyncClient(transport=httpx.MockTransport(refuse)))
    with pytest.raises(SourceError) as exc:
        run(platform.head())
    assert "ulanib bo'lmadi" in exc.value.message and "gateway.test" not in exc.value.message


def test_wrong_url_is_reported():
    fake = FakePlatform()
    platform = Platform("http://gateway.test/nope", "audit-bot", "secret-1",
                        httpx.AsyncClient(transport=fake.transport()))
    with pytest.raises(SourceError) as exc:
        run(platform.head())
    assert "404" in exc.value.message


def test_tender_range_pages_with_oldin_id():
    fake = FakeTender()
    for i in range(1200):
        fake.add(tur="log" if i % 3 == 0 else "sorov")
    rows, truncated = run(tender_for(fake).range(*RANGE, {}, 20000))
    assert [r["id"] for r in rows] == list(range(1, 1201)) and not truncated
    assert len(fake.calls) == 3
    assert fake.calls[1]["params"]["oldin_id"] == str(1200 - 500 + 1)
    rows, _ = run(tender_for(fake).range(*RANGE, {"tur": "log"}, 20000))
    assert len(rows) == 400


def test_tender_tail_head_row():
    fake = FakeTender()
    for _ in range(4):
        fake.add(sorov_tanasi="{}")
    tender = tender_for(fake)
    assert run(tender.head()) == 4
    rows, nxt = run(tender.tail(1))
    assert [r["id"] for r in rows] == [2, 3, 4] and nxt == 4
    assert "sorov_tanasi" not in rows[0]
    assert run(tender.row(2))["sorov_tanasi"] == "{}"


def test_tender_sends_only_known_parameters():
    """/jurnal answers 400 to any parameter it does not know."""
    fake = FakeTender()
    fake.add()
    tender = tender_for(fake)
    run(tender.range(*RANGE, {"daraja": "error", "login": "x", "yol": "/check", "holat_kodi": 500,
                              "tur": "sorov"}, 100))
    run(tender.tail(0))
    run(tender.head())
    for call in fake.calls:
        assert set(call["params"]) <= set(FakeTender.FILTERS)


def test_parse_time():
    assert sources.parse_time("2026-10-05T04:00:00Z") == T0
    assert sources.parse_time("2026-10-05T04:00:00") == T0
    assert sources.parse_time("2026-10-05T09:00:00+05:00") == T0
    assert sources.parse_time("nonsense") is None and sources.parse_time(None) is None
    assert sources.parse_time("2026-10-05T04:00:00.5Z") > sources.parse_time("2026-10-05T04:00:00Z")
    assert isinstance(sources.parse_time("2026-10-05T04:00:00Z"), datetime)
    assert sources.parse_time("2026-10-05T04:00:00Z").tzinfo is not None
    assert sources.EPOCH > datetime(1970, 1, 1, tzinfo=timezone.utc)
