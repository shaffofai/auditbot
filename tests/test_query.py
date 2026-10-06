from datetime import datetime, timezone

import pytest

from auditbot import query
from conftest import TZ

# 2026-10-05 14:30 in Tashkent.
NOW = datetime(2026, 10, 5, 9, 30, tzinfo=timezone.utc)


def local(*args):
    return datetime(*args, tzinfo=TZ)


def parse(text):
    return query.parse(text, now=NOW, tz=TZ)


@pytest.mark.parametrize("text, start, end", [
    ("bugun", local(2026, 10, 5), NOW),
    ("kecha", local(2026, 10, 4), local(2026, 10, 5)),
    ("3soat", local(2026, 10, 5, 11, 30), NOW),
    ("3 soat", local(2026, 10, 5, 11, 30), NOW),
    ("30daqiqa", local(2026, 10, 5, 14, 0), NOW),
    ("2kun", local(2026, 10, 3, 14, 30), NOW),
    ("1hafta", local(2026, 9, 28, 14, 30), NOW),
    ("2026-10-01", local(2026, 10, 1), local(2026, 10, 2)),
    ("01.10.2026", local(2026, 10, 1), local(2026, 10, 2)),
    ("01.10", local(2026, 10, 1), local(2026, 10, 2)),
    ("2026-10-01 09:00 18:00", local(2026, 10, 1, 9), local(2026, 10, 1, 18)),
    ("01.10 09:00 02.10 12:00", local(2026, 10, 1, 9), local(2026, 10, 2, 12)),
    ("2026-10-01T09:00 2026-10-02T12:00", local(2026, 10, 1, 9), local(2026, 10, 2, 12)),
    ("01.10 02.10", local(2026, 10, 1), local(2026, 10, 3)),
    ("09:00 12:30", local(2026, 10, 5, 9), local(2026, 10, 5, 12, 30)),
    ("09:00", local(2026, 10, 5, 9), NOW),
    ("09:00 23:00", local(2026, 10, 5, 9), NOW),            # not over yet: stops at now
    ("2026-10-05", local(2026, 10, 5), NOW),
    ("01.10 09:00", local(2026, 10, 1, 9), NOW),
])
def test_ranges(text, start, end):
    q = parse(text)
    assert (q.start, q.end) == (start, end)
    assert q.use_platform and q.use_tender and not q.bodies


def test_filters_map_to_both_apis():
    q = parse("bugun xatolar foydalanuvchi=ali@misol.uz yol=/api-v2/fasad holat=500 tanalar")
    assert q.platform == {"level": "error", "username": "ali@misol.uz",
                          "path": "/api-v2/fasad", "status": 500}
    assert q.tender == {"daraja": "error", "login": "ali@misol.uz",
                        "yol": "/api-v2/fasad", "holat_kodi": 500}
    assert q.bodies
    assert "faqat xatolar" in q.filters_label and "tanalar bilan" in q.filters_label


def test_kinds():
    q = parse("bugun tur=kirish")
    assert q.platform == {"kind": "auth"} and q.tender == {"tur": "kirish"}
    q = parse("bugun tur=brauzer")
    assert q.platform == {"kind": "client"} and not q.use_tender
    q = parse("bugun tur=qo'lda")                 # apostrophes do not matter
    assert q.platform == {"kind": "manual"} and q.tender == {"tur": "amal"}


def test_level_words():
    assert parse("bugun daraja=ogohlantirish").platform == {"level": "warning"}
    assert parse("bugun daraja=xato").tender == {"daraja": "error"}


def test_service_choice():
    q = parse("bugun xizmat=tender-v2")
    assert not q.use_platform and q.use_tender
    q = parse("bugun xizmat=platforma")
    assert q.use_platform and not q.use_tender
    q = parse("bugun xizmat=fasad")
    assert q.platform == {"source": "fasad"} and not q.use_tender


@pytest.mark.parametrize("text", [
    "", "ertaga", "32.10", "25:00", "bugun tur=nimadir", "bugun holat=abc",
    "bugun yol=api", "bugun nomalum=1", "18:00 09:00", "0soat",
    "bugun tur=brauzer xizmat=tender-v2", "06.10", "15:00 16:00",
])
def test_errors_are_uzbek_messages(text):
    with pytest.raises(query.QueryError) as exc:
        parse(text)
    assert str(exc.value)


def test_day_without_a_year_is_the_most_recent_one():
    january = datetime(2027, 1, 2, 6, 0, tzinfo=timezone.utc)
    q = query.parse("28.12", now=january, tz=TZ)
    assert (q.start, q.end) == (local(2026, 12, 28), local(2026, 12, 29))
