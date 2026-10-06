"""The two places logs come from.

``Platform`` reads ``audit_logs`` through the gateway's audit API
(``GET /admin/gateway/audit``, HANDOFF §18.4) with the bot's own admin account;
``Tender`` reads tender-v2's ``sorov_jurnali`` through ``GET /jurnal`` with its
read-only journal credentials (HANDOFF §18.7). Both are reached on the Docker
network by name (``gateway:8000``, ``tender-v2:8000``), never through the edge.

Both offer the same three things: the rows of a time range (browse mode, newest
first, paged), the rows after an id (tail mode, for the alerts), and one row in
full.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

log = logging.getLogger("auditbot.sources")

EPOCH = datetime(1970, 1, 2, tzinfo=timezone.utc)   # the audit API refuses dates before 1970


class SourceError(Exception):
    """A source refused or could not answer. ``auth`` is set when the credentials
    were refused, so the caller can stop asking instead of feeding the login
    throttle."""

    def __init__(self, source: str, message: str, *, auth: bool = False):
        super().__init__(f"{source}: {message}")
        self.source = source
        self.message = message
        self.auth = auth


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


class _Http:
    name = "?"

    def __init__(self, base: str, user: str, password: str, client: httpx.AsyncClient | None):
        self._base = base
        self._auth = _basic(user, password)
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                       body: dict[str, Any] | None = None) -> Any:
        """One call, retried twice when the server says it is busy (503). A 404
        is ``None``. The messages of the errors are shown in the chat, so they
        are Uzbek."""
        for attempt in range(3):
            try:
                response = await self._client.request(method, self._base + path, params=params,
                                                      json=body,
                                                      headers={"Authorization": self._auth})
            except httpx.HTTPError as exc:
                raise SourceError(self.name, f"ulanib bo'lmadi ({type(exc).__name__})") from None
            status = response.status_code
            if status == 401:
                raise SourceError(self.name, "login yoki parol rad etildi (401)", auth=True)
            if status == 403:
                raise SourceError(self.name, "akkaunt admin emas (403)", auth=True)
            if status == 429:
                # Only the login throttle answers a read with 429: retrying would
                # only lengthen it.
                raise SourceError(self.name, "kirish vaqtincha cheklangan (429)", auth=True)
            if status == 503 and attempt < 2:
                wait = response.headers.get("retry-after", "5")
                await asyncio.sleep(min(float(wait) if wait.isdigit() else 5.0, 30.0))
                continue
            if status == 404:
                return None
            if status >= 400:
                raise SourceError(self.name, f"HTTP {status}: {_short(response)}")
            try:
                return response.json()
            except ValueError:
                raise SourceError(self.name, "javob JSON emas") from None
        raise AssertionError("unreachable")

    async def _page(self, path: str, params: dict[str, Any]) -> dict:
        """A list page; a 404 here means a wrong URL, not a missing row."""
        page = await self._get(path, params)
        if not isinstance(page, dict):
            raise SourceError(self.name, f"{path} topilmadi (404) — manzil to'g'rimi?")
        return page


def _short(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    detail = body.get("detail", body.get("xato", body)) if isinstance(body, dict) else body
    return str(detail)[:300]


class Platform(_Http):
    """``audit_logs`` through ``/admin/gateway/audit``."""

    name = "platforma"
    PAGE = 200                     # the API's maximum with detail=true

    def __init__(self, url: str, user: str, password: str, client: httpx.AsyncClient | None = None):
        super().__init__(url, user, password, client)

    async def range(self, start: datetime, end: datetime, filters: dict[str, Any],
                    max_rows: int) -> tuple[list[dict], bool]:
        """Every row with start <= created_at < end, oldest first. Returns
        (rows, truncated)."""
        params: dict[str, Any] = {"from": _iso(start), "to": _iso(end), "limit": self.PAGE,
                                  "detail": "true", **filters}
        rows: list[dict] = []
        while True:
            page = await self._page("/admin/gateway/audit", params)
            rows.extend(page["items"])
            if len(rows) >= max_rows:
                return sorted(rows[:max_rows], key=_platform_order), True
            cursor = page.get("next_cursor")
            if not cursor:
                return sorted(rows, key=_platform_order), False
            params["cursor"] = cursor

    async def tail(self, after_id: int) -> tuple[list[dict], int]:
        """Rows written after ``after_id``, in write order; and the id to ask
        after next time (it advances even when nothing new matched)."""
        page = await self._page("/admin/gateway/audit",
                               {"after_id": after_id, "limit": self.PAGE, "detail": "true"})
        return page["items"], int(page.get("next_after_id") or after_id)

    async def head(self) -> int:
        """The newest id in the table (0 when empty)."""
        page = await self._page("/admin/gateway/audit", {"from": _iso(EPOCH), "limit": 1})
        return int(page["items"][0]["id"]) if page["items"] else 0

    async def row(self, row_id: int) -> dict | None:
        return await self._get(f"/admin/gateway/audit/{int(row_id)}")

    # The bot's chat list (telegram_chats, managed in the dashboard).

    CHATS = "/admin/gateway/telegram/chats"

    async def chats(self) -> list[dict]:
        page = await self._page(self.CHATS, {})
        return page["chats"]

    async def chat_seen(self, report: dict[str, Any]) -> dict:
        """Report a chat that wrote to the bot, or that it joined or left.
        Answers ``{"chat": {...}, "created": bool}``."""
        answer = await self._request("POST", self.CHATS + "/seen", body=report)
        if not isinstance(answer, dict):
            raise SourceError(self.name, f"{self.CHATS} topilmadi (404) — gateway eskimi?")
        return answer


class Tender(_Http):
    """``sorov_jurnali`` through tender-v2's ``/jurnal``."""

    name = "tender-v2"
    PAGE = 500                     # the route's maximum

    def __init__(self, url: str, user: str, password: str, client: httpx.AsyncClient | None = None):
        super().__init__(url, user, password, client)

    async def range(self, start: datetime, end: datetime, filters: dict[str, Any],
                    max_rows: int) -> tuple[list[dict], bool]:
        params: dict[str, Any] = {"dan": _iso(start), "gacha": _iso(end), "limit": self.PAGE,
                                  **filters}
        rows: list[dict] = []
        while True:
            page = await self._page("/jurnal", params)
            rows.extend(page["qatorlar"])
            if len(rows) >= max_rows:
                return sorted(rows[:max_rows], key=_tender_order), True
            before = page.get("keyingi_oldin_id")
            if not before:
                return sorted(rows, key=_tender_order), False
            params["oldin_id"] = before

    async def tail(self, after_id: int) -> tuple[list[dict], int]:
        page = await self._page("/jurnal", {"keyin_id": after_id, "limit": self.PAGE})
        return page["qatorlar"], int(page.get("keyingi_keyin_id") or after_id)

    async def head(self) -> int:
        page = await self._page("/jurnal", {"dan": _iso(EPOCH), "limit": 1})
        return int(page["qatorlar"][0]["id"]) if page["qatorlar"] else 0

    async def row(self, row_id: int) -> dict | None:
        return await self._get(f"/jurnal/{int(row_id)}")


def parse_time(value: Any) -> datetime | None:
    """An API timestamp as an aware datetime (None when absent or unreadable).
    Compared as datetimes, not strings: '…:32Z' and '…:32.04Z' sort wrongly as text."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _platform_order(row: dict) -> tuple:
    return (parse_time(row.get("created_at")) or EPOCH, row.get("id") or 0)


def _tender_order(row: dict) -> tuple:
    return (parse_time(row.get("yaratildi")) or EPOCH, row.get("id") or 0)
