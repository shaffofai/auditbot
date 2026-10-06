"""Which chats may use the bot — read from the platform, decided in the dashboard.

The list lives in the platform database (``telegram_chats``, gateway
``/admin/gateway/telegram/chats``). An admin approves or blocks a chat in the
dashboard, and decides which approved chats get the alerts. The bot only
reports what it sees (a chat wrote to it, it was added to or removed from a
group) and reads the decisions back.

The bot keeps a copy, refreshed when it is needed and kept in the state file:

* a command from a chat the copy does not know as approved refreshes it at
  once (at most every few seconds), so an approval works on the next try;
* a command from an approved chat refreshes it when older than a minute, so a
  revoked chat stops within a minute;
* alerts refresh it when older than a minute, and a "someone asked for
  access" notice always does (a just-approved alert group must not miss it);
* when the gateway cannot be reached the last copy is used — so alerts about
  the platform being down still reach their chats, even after a restart.

Each refresh is a request to the gateway, and each request an audit row: that
is why the copy is not refreshed on a timer.
"""

from __future__ import annotations

import html
import logging
from typing import Any, Callable

from auditbot.sources import Platform, SourceError

log = logging.getLogger("auditbot.chats")

STALE_FOR_COMMANDS = 60       # seconds
STALE_FOR_ALERTS = 60
RETRY_UNKNOWN = 5             # an unknown chat refreshes the copy at most this often
REPORT_AGAIN = 3600           # an unchanged chat is reported again after an hour


def describe(chat: dict) -> tuple[str, str | None]:
    """(title, username) of a Telegram chat object: a group's title, or a
    person's full name."""
    title = chat.get("title") or " ".join(
        part for part in (chat.get("first_name"), chat.get("last_name")) if part)
    return (title or None), chat.get("username")


class Chats:
    def __init__(self, platform: Platform, state, clock: Callable[[], float]):
        self.platform = platform
        self.state = state
        self.clock = clock
        # chat_id -> {"status", "alerts", "bot_member", "chat_type", "title"}
        self.known: dict[int, dict[str, Any]] = {}
        for key, value in (state.data.get("chats") or {}).items():
            try:
                self.known[int(key)] = dict(value)
            except (TypeError, ValueError):
                continue
        self.loaded_at: float | None = None      # never this run: the copy is from disk
        self.last_try = -1e9
        self.reported: dict[int, tuple[float, tuple]] = {}

    # ---------- reading ----------

    async def refresh(self) -> bool:
        """Read the list from the gateway. False (and the old copy kept) when
        it cannot be read."""
        self.last_try = self.clock()
        try:
            rows = await self.platform.chats()
        except SourceError as exc:
            log.warning("chat list not read (%s); using the last copy", exc.message)
            return False
        self.known = {int(r["chat_id"]): {k: r.get(k) for k in
                                          ("status", "alerts", "bot_member", "chat_type", "title")}
                      for r in rows}
        self.loaded_at = self.clock()
        self.state.data["chats"] = {str(k): v for k, v in self.known.items()}
        self.state.save()
        return True

    def _age(self) -> float:
        return float("inf") if self.loaded_at is None else self.clock() - self.loaded_at

    async def status(self, chat_id: int) -> str | None:
        """approved | pending | blocked | None (never seen), fresh enough to act on."""
        current = (self.known.get(chat_id) or {}).get("status")
        if current == "approved":
            if self._age() > STALE_FOR_COMMANDS and self.clock() - self.last_try > RETRY_UNKNOWN:
                await self.refresh()
        elif self.clock() - self.last_try > RETRY_UNKNOWN:
            await self.refresh()
        return (self.known.get(chat_id) or {}).get("status")

    async def alert_targets(self, *, fresh: bool = False) -> list[int]:
        limit = 0 if fresh else STALE_FOR_ALERTS
        if self._age() > limit and self.clock() - self.last_try > RETRY_UNKNOWN:
            await self.refresh()
        return sorted(chat_id for chat_id, c in self.known.items()
                      if c.get("status") == "approved" and c.get("alerts")
                      and c.get("bot_member", True))

    def counts(self) -> dict[str, int]:
        out = {"approved": 0, "pending": 0, "blocked": 0, "alerts": 0}
        for c in self.known.values():
            out[c.get("status") or "pending"] = out.get(c.get("status") or "pending", 0) + 1
            if c.get("status") == "approved" and c.get("alerts") and c.get("bot_member", True):
                out["alerts"] += 1
        return out

    # ---------- reporting ----------

    async def report(self, chat: dict, *, bot_member: bool = True,
                     migrated_from: int | None = None, force: bool = False) -> dict | None:
        """Tell the gateway about a chat. Skipped when the same chat, unchanged,
        was reported within the hour. Answers the gateway's ``{"chat", "created"}``,
        or None when nothing was sent or it could not be."""
        chat_id = chat.get("id")
        chat_type = chat.get("type")
        if not isinstance(chat_id, int) or chat_type not in ("private", "group", "supergroup",
                                                              "channel"):
            return None
        title, username = describe(chat)
        fingerprint = (chat_type, title, username, bot_member, migrated_from)
        last = self.reported.get(chat_id)
        now = self.clock()
        if (not force and last and last[1] == fingerprint and now - last[0] < REPORT_AGAIN
                and chat_id in self.known):
            return None
        body: dict[str, Any] = {"chat_id": chat_id, "chat_type": chat_type,
                                "title": (title or "")[:255] or None,
                                "username": (username or "")[:64] or None,
                                "bot_member": bot_member}
        if migrated_from is not None:
            body["migrated_from"] = migrated_from
        try:
            answer = await self.platform.chat_seen(body)
        except SourceError as exc:
            log.warning("chat %s not reported (%s)", chat_id, exc.message)
            return None
        self.reported[chat_id] = (now, fingerprint)
        if len(self.reported) > 5000:
            self.reported = {k: v for k, v in self.reported.items() if now - v[0] < REPORT_AGAIN}
        row = answer.get("chat") or {}
        self.known[chat_id] = {k: row.get(k) for k in
                               ("status", "alerts", "bot_member", "chat_type", "title")}
        if migrated_from is not None:
            self.known.pop(migrated_from, None)
        return answer

    async def gone(self, chat_id: int) -> None:
        """Sending to a chat failed for good (blocked, removed): mark it, so
        alerts stop going there until it writes again."""
        known = self.known.get(chat_id)
        if not known or known.get("bot_member") is False:
            return
        chat_type = known.get("chat_type")
        if chat_type not in ("private", "group", "supergroup", "channel"):
            chat_type = "private" if chat_id > 0 else "group"
        await self.report({"id": chat_id, "type": chat_type, "title": known.get("title")},
                          bot_member=False, force=True)


def request_notice(chat: dict, kind_labels: dict[str, str]) -> str:
    """The alert telling the admins someone asked for access."""
    title = html.escape(chat.get("title") or "nomsiz", quote=False)
    kind = kind_labels.get(chat.get("chat_type") or "", chat.get("chat_type") or "")
    handle = f" · @{html.escape(chat['username'], quote=False)}" if chat.get("username") else ""
    return (f"🆕 <b>Botdan foydalanish so'rovi</b>\n{title} — {kind}{handle}\n"
            f"ID: <code>{chat.get('chat_id')}</code>\n"
            "Tasdiqlash: dashboard → <b>Telegram bot</b> bo'limi")
