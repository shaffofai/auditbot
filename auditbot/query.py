"""What ``/loglar …`` asks for: a time range and filters, in Uzbek.

    /loglar bugun                          today, from 00:00 Tashkent until now
    /loglar kecha                          all of yesterday
    /loglar 3soat   /loglar 30daqiqa       the last three hours / thirty minutes
    /loglar 2kun    /loglar 1hafta         the last two days / one week
    /loglar 2026-10-01                     that whole day (also 01.10.2026, 01.10)
    /loglar 2026-10-01 09:00 18:00         that day, 09:00 to 18:00
    /loglar 01.10 09:00 02.10 12:00        from one moment to another
    /loglar 09:00 12:30                    today, between two times

followed by any of

    xatolar                 only level=error
    tanalar                 also fetch request/response bodies (first N rows)
    tur=sorov|kirish|brauzer|log|qolda
    daraja=xato|ogohlantirish|info
    foydalanuvchi=<e-mail or username>
    yol=/api-v2/fasad       path prefix
    holat=500               HTTP status
    xizmat=fasad|gateway|…|platforma|tender-v2

Times are Tashkent time (the offset comes from the settings); the range is
[start, end), as both APIs treat it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any


class QueryError(ValueError):
    """A request the bot cannot understand; the message is shown to the user (Uzbek)."""


@dataclass
class Query:
    start: datetime
    end: datetime
    platform: dict[str, Any] = field(default_factory=dict)   # audit API filters
    tender: dict[str, Any] = field(default_factory=dict)     # /jurnal filters
    use_platform: bool = True
    use_tender: bool = True
    bodies: bool = False
    filters_label: str = ""


_APOSTROPHES = str.maketrans("", "", "'ʻʼ‘’`´")

_UNITS = {
    "daqiqa": 60, "min": 60, "m": 60,
    "soat": 3600, "s": 3600, "h": 3600,
    "kun": 86400, "d": 86400,
    "hafta": 604800, "w": 604800,
}
_SPAN = re.compile(r"^(\d{1,4})(daqiqa|min|soat|kun|hafta|[mshdw])$")

_KINDS = {   # what the user types -> (platform kind, tender tur or None)
    "sorov": ("request", "sorov"), "request": ("request", "sorov"),
    "kirish": ("auth", "kirish"), "auth": ("auth", "kirish"),
    "brauzer": ("client", None), "client": ("client", None),
    "log": ("log", "log"),
    "qolda": ("manual", "amal"), "manual": ("manual", "amal"), "amal": ("manual", "amal"),
}
_LEVELS = {
    "xato": "error", "xatolar": "error", "error": "error",
    "ogohlantirish": "warning", "warning": "warning",
    "info": "info", "malumot": "info",
}
_KEYS = {
    "tur": "tur", "daraja": "daraja",
    "foydalanuvchi": "foydalanuvchi", "user": "foydalanuvchi", "login": "foydalanuvchi",
    "yol": "yol", "path": "yol",
    "holat": "holat", "status": "holat",
    "xizmat": "xizmat", "manba": "xizmat",
}


def _plain(word: str) -> str:
    return word.translate(_APOSTROPHES).lower()


def _parse_date(token: str, today: date) -> date | None:
    for pattern, order in ((r"^(\d{4})-(\d{1,2})-(\d{1,2})$", "ymd"),
                           (r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", "dmy"),
                           (r"^(\d{1,2})\.(\d{1,2})$", "dm")):
        m = re.match(pattern, token)
        if not m:
            continue
        parts = [int(p) for p in m.groups()]
        try:
            if order == "ymd":
                return date(parts[0], parts[1], parts[2])
            if order == "dmy":
                return date(parts[2], parts[1], parts[0])
            # Without a year: this year's, unless that is months away — "28.12"
            # typed in January means last December, "06.10" typed on 05.10 is
            # simply tomorrow (and refused as not started yet).
            day = date(today.year, parts[1], parts[0])
            if (day - today).days > 31:
                day = date(today.year - 1, parts[1], parts[0])
            return day
        except ValueError:
            raise QueryError(f"Sana noto'g'ri: {token}") from None
    return None


def _parse_time(token: str) -> time | None:
    m = re.match(r"^(\d{1,2}):(\d{2})$", token)
    if not m:
        return None
    try:
        return time(int(m.group(1)), int(m.group(2)))
    except ValueError:
        raise QueryError(f"Vaqt noto'g'ri: {token}") from None


def _range(tokens: list[str], now: datetime, tz: tzinfo) -> tuple[datetime, datetime]:
    local_now = now.astimezone(tz)
    today = local_now.date()

    def at(d: date, t: time = time(0, 0)) -> datetime:
        return datetime.combine(d, t, tzinfo=tz)

    words = [_plain(t) for t in tokens]
    if not words:
        raise QueryError("Oraliq ko'rsatilmagan.")
    if words == ["bugun"]:
        return at(today), now
    if words == ["kecha"]:
        return at(today - timedelta(days=1)), at(today)
    # "3soat" or "3 soat".
    span = None
    if len(words) == 1:
        span = _SPAN.match(words[0])
    elif len(words) == 2 and words[0].isdigit():
        span = _SPAN.match(words[0] + words[1])
    if span:
        seconds = int(span.group(1)) * _UNITS[span.group(2)]
        if seconds <= 0:
            raise QueryError("Oraliq noldan katta bo'lishi kerak.")
        return now - timedelta(seconds=seconds), now

    # ISO "2026-10-01T09:00" is a date and a time in one token.
    flat: list[str] = []
    for token in tokens:
        if "T" in token and re.match(r"^\d{4}-\d{2}-\d{2}T\d{1,2}:\d{2}", token):
            d, t = token.split("T", 1)
            flat += [d, t[:5]]
        else:
            flat.append(token)
    parsed: list[date | time] = []
    for token in flat:
        value = _parse_date(token, today) or _parse_time(token)
        if value is None:
            raise QueryError(f"Tushunilmadi: {token}")
        parsed.append(value)

    kinds = "".join("d" if isinstance(v, date) else "t" for v in parsed)
    v = parsed
    if kinds == "d":
        return at(v[0]), at(v[0] + timedelta(days=1))
    if kinds == "dt":
        return at(v[0], v[1]), now
    if kinds == "dtt":
        return at(v[0], v[1]), at(v[0], v[2])
    if kinds == "dd":
        return at(v[0]), at(v[1] + timedelta(days=1))
    if kinds == "dtdt":
        return at(v[0], v[1]), at(v[2], v[3])
    if kinds == "tt":
        return at(today, v[0]), at(today, v[1])
    if kinds == "t":
        return at(today, v[0]), now
    raise QueryError("Oraliq tushunilmadi.")


def parse(text: str, *, now: datetime, tz: tzinfo) -> Query:
    """The arguments of ``/loglar`` (without the command itself)."""
    tokens = text.split()
    range_tokens: list[str] = []
    platform: dict[str, Any] = {}
    tender: dict[str, Any] = {}
    use_platform = use_tender = True
    bodies = False
    labels: list[str] = []

    for token in tokens:
        word = _plain(token)
        if word in ("xatolar", "xato"):
            platform["level"] = tender["daraja"] = "error"
            labels.append("faqat xatolar")
            continue
        if word in ("tanalar", "tana"):
            bodies = True
            labels.append("tanalar bilan")
            continue
        if "=" not in token:
            range_tokens.append(token)
            continue
        raw_key, value = token.split("=", 1)
        key = _KEYS.get(_plain(raw_key))
        value = value.strip()
        if not key or not value:
            raise QueryError(f"Noma'lum filtr: {token}")
        plain_value = _plain(value)
        if key == "tur":
            if plain_value not in _KINDS:
                raise QueryError("tur= qiymatlari: sorov, kirish, brauzer, log, qolda")
            kind, tur = _KINDS[plain_value]
            platform["kind"] = kind
            if tur:
                tender["tur"] = tur
            else:
                use_tender = False             # tender-v2 has no browser events
        elif key == "daraja":
            if plain_value not in _LEVELS:
                raise QueryError("daraja= qiymatlari: xato, ogohlantirish, info")
            platform["level"] = tender["daraja"] = _LEVELS[plain_value]
        elif key == "foydalanuvchi":
            platform["username"] = value
            tender["login"] = value
        elif key == "yol":
            if not value.startswith("/"):
                raise QueryError("yol= / bilan boshlanishi kerak, masalan yol=/api-v2/fasad")
            platform["path"] = tender["yol"] = value
        elif key == "holat":
            if not value.isdigit():
                raise QueryError("holat= raqam bo'lishi kerak, masalan holat=500")
            platform["status"] = tender["holat_kodi"] = int(value)
        elif key == "xizmat":
            if plain_value in ("tender-v2", "tenderv2", "tender_v2"):
                use_platform = False
            elif plain_value == "platforma":
                use_tender = False
            else:
                platform["source"] = plain_value
                use_tender = False
        labels.append(f"{raw_key}={value}")

    if not use_platform and not use_tender:
        raise QueryError("Bu filtrlar bilan hech qaysi manba qolmadi.")
    start, end = _range(range_tokens, now, tz)
    if start >= now:
        raise QueryError("Bu oraliq hali boshlanmagan.")
    end = min(end, now)
    if end <= start:
        raise QueryError("Oraliq oxiri boshidan keyin bo'lishi kerak.")
    if start.astimezone(timezone.utc).year < 1971:
        raise QueryError("Sana juda erta.")
    return Query(start=start, end=end, platform=platform, tender=tender,
                 use_platform=use_platform, use_tender=use_tender, bodies=bodies,
                 filters_label=", ".join(labels))
