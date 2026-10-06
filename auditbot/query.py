"""What ``/logs …`` asks for: a time range and filters. What a person types
is English; what the bot answers (these error messages too) is Uzbek.

    /logs today                         from 00:00 Tashkent until now
    /logs yesterday                     all of yesterday
    /logs 3h   /logs 30m                the last three hours / thirty minutes
    /logs 2d   /logs 1w                 the last two days / one week
                                        (also "3 hours", "30min", "2days", "1week")
    /logs 2026-10-01                    that whole day (also 01.10.2026, 01.10)
    /logs 2026-10-01 09:00 18:00        that day, 09:00 to 18:00
    /logs 01.10 09:00 02.10 12:00       from one moment to another
    /logs 09:00 12:30                   today, between two times

followed by any of

    errors                  only level=error
    bodies                  also fetch request/response bodies (first N rows)
    kind=request|auth|browser|log|manual
    level=error|warning|info
    user=<e-mail or username>
    path=/api-v2/fasad      path prefix
    status=500              HTTP status
    service=fasad|gateway|…|platform|tender-v2

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


_UNITS = {
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "week": 604800, "weeks": 604800,
}
_SPAN = re.compile(r"^(\d{1,4})(" + "|".join(sorted(_UNITS, key=len, reverse=True)) + r")$")

_KINDS = {   # what the user types -> (platform kind, tender-v2 tur or None)
    "request": ("request", "sorov"),
    "auth": ("auth", "kirish"),
    "browser": ("client", None),
    "log": ("log", "log"),
    "manual": ("manual", "amal"),
}
_LEVELS = ("error", "warning", "info")
_KEYS = ("kind", "level", "user", "path", "status", "service")


def _plain(word: str) -> str:
    return word.lower()


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
    if words == ["today"]:
        return at(today), now
    if words == ["yesterday"]:
        return at(today - timedelta(days=1)), at(today)
    # "3h" or "3 hours".
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
    """The arguments of ``/logs`` (without the command itself)."""
    tokens = text.split()
    range_tokens: list[str] = []
    platform: dict[str, Any] = {}
    tender: dict[str, Any] = {}
    use_platform = use_tender = True
    bodies = False
    labels: list[str] = []

    for token in tokens:
        word = _plain(token)
        if word == "errors":
            platform["level"] = tender["daraja"] = "error"
            labels.append("errors")
            continue
        if word == "bodies":
            bodies = True
            labels.append("bodies")
            continue
        if "=" not in token:
            range_tokens.append(token)
            continue
        raw_key, value = token.split("=", 1)
        key = _plain(raw_key)
        value = value.strip()
        if key not in _KEYS or not value:
            raise QueryError(f"Noma'lum filtr: {token} (bor filtrlar: {', '.join(_KEYS)})")
        plain_value = _plain(value)
        if key == "kind":
            if plain_value not in _KINDS:
                raise QueryError("kind= qiymatlari: " + ", ".join(_KINDS))
            kind, tur = _KINDS[plain_value]
            platform["kind"] = kind
            if tur:
                tender["tur"] = tur
            else:
                use_tender = False             # tender-v2 has no browser events
        elif key == "level":
            if plain_value not in _LEVELS:
                raise QueryError("level= qiymatlari: " + ", ".join(_LEVELS))
            platform["level"] = tender["daraja"] = plain_value
        elif key == "user":
            platform["username"] = value
            tender["login"] = value
        elif key == "path":
            if not value.startswith("/"):
                raise QueryError("path= / bilan boshlanishi kerak, masalan path=/api-v2/fasad")
            platform["path"] = tender["yol"] = value
        elif key == "status":
            if not value.isdigit():
                raise QueryError("status= raqam bo'lishi kerak, masalan status=500")
            platform["status"] = tender["holat_kodi"] = int(value)
        elif key == "service":
            if plain_value in ("tender-v2", "tenderv2", "tender_v2"):
                use_platform = False
            elif plain_value == "platform":
                use_tender = False
            else:
                platform["source"] = plain_value
                use_tender = False
        labels.append(f"{key}={value}")

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
