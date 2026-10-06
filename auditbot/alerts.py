"""Which new rows deserve a message in the chat, and how many messages a burst
may cost.

The bot follows both tables in tail mode (rows after the last id it saw) and
hands every new row to :class:`Alerts`. What makes a message:

  platform  a request answered 5xx; a backend log record at level error; a
            browser script error (if enabled); one account failing to sign in
            N times within the window; audit rows dropped (the trail itself
            losing data); audit rows deleted through the API (someone removing
            the trail);
  tender-v2 a request answered 5xx; a log line at level error; one login
            refused N times within the window; journal rows dropped; an
            operator command (--requeue, --qayta-och).

The same thing happening again within the window is counted, not repeated: the
next message about it, after the window, says how many times it was suppressed.
And whatever a burst produces, at most ``alert_max_per_minute`` messages leave
per minute; the rest are folded into one line pointing at /logs.

Not alerted: failed sign-ins by address. Until router/realip.conf is filled in
every visitor has the company edge's address (HANDOFF §5.3), so "many failures
from one address" would only ever mean "many failures".
"""

from __future__ import annotations

import html
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import tzinfo
from typing import Callable

from auditbot.sources import parse_time

BROWSER_ERRORS = ("js.error", "js.unhandledrejection", "console.error")
DROP_EVENTS = ("audit.dropped", "log.dropped", "client.dropped")
DELETE_EVENTS = ("audit.delete", "audit.purge")
BROWSER_SHOWN = 5


def _e(value) -> str:
    return html.escape(str(value), quote=False) if value not in (None, "") else "—"


def _clip(text, limit: int) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _first_line(text) -> str:
    return str(text or "").strip().split("\n", 1)[0]


@dataclass
class _Seen:
    first: float
    suppressed: int = 0


@dataclass
class Alerts:
    tz: tzinfo
    window: int = 600
    failed_logins: int = 5
    browser_errors: bool = True
    max_per_minute: int = 15
    clock: Callable[[], float] = time.monotonic
    _seen: dict = field(default_factory=dict)
    _failures: dict = field(default_factory=dict)
    _sent: deque = field(default_factory=deque)
    _held: int = 0

    # ---------- shared ----------

    def _once(self, key) -> tuple[bool, int]:
        """(send now?, how many were suppressed before this one)."""
        now = self.clock()
        seen = self._seen.get(key)
        if seen and now - seen.first < self.window:
            seen.suppressed += 1
            return False, 0
        suppressed = seen.suppressed if seen else 0
        self._seen[key] = _Seen(first=now)
        if len(self._seen) > 5000:                       # forget the oldest keys
            for old in sorted(self._seen, key=lambda k: self._seen[k].first)[:1000]:
                del self._seen[old]
        return True, suppressed

    def _again(self, suppressed: int) -> str:
        if not suppressed:
            return ""
        return f"\n<i>(oldingi {self.window // 60} daqiqada yana {suppressed} marta)</i>"

    def _when(self, value) -> str:
        moment = parse_time(value)
        return moment.astimezone(self.tz).strftime("%d.%m %H:%M:%S") if moment else "—"

    def _failure(self, key, at: float) -> int:
        """Failures of one identity within the window, this one included."""
        times = self._failures.setdefault(key, deque())
        times.append(at)
        while times and at - times[0] > self.window:
            times.popleft()
        if len(self._failures) > 5000:
            for old in [k for k, v in self._failures.items() if not v or at - v[-1] > self.window]:
                del self._failures[old]
        return len(times)

    # ---------- platform ----------

    def platform(self, rows: list[dict]) -> list[str]:
        messages: list[str] = []
        browser: list[dict] = []
        now = self.clock()
        for row in rows:
            kind, event = row.get("kind"), row.get("event")
            status = row.get("status_code")
            if event in DROP_EVENTS:
                # First: the trail itself lost rows (writer queue full, a browser
                # over its budget) — whatever kind and level the notice carries.
                send, again = self._once(("pdrop", event, row.get("source")))
                if send:
                    messages.append(
                        f"⚠️ <b>Audit yozuvlari tashlandi</b> — {_e(row.get('source'))}\n"
                        f"{_e(_clip(row.get('message'), 300))}\n"
                        f"{self._when(row.get('created_at'))} · #{row.get('id')}"
                        + self._again(again))
            elif kind == "request" and isinstance(status, int) and status >= 500:
                route = (row.get("path") or "").split("?")[0]
                send, again = self._once(("p5xx", row.get("source"), row.get("method"), route, status))
                if send:
                    meta = row.get("meta") or {}
                    cause = f"\nsabab: {_e(meta.get('exception'))}" if meta.get("exception") else ""
                    messages.append(
                        f"🔴 <b>Server xatosi {status}</b> — {_e(row.get('source'))}\n"
                        f"{_e(row.get('method'))} {_e(_clip(row.get('path'), 200))}\n"
                        f"foydalanuvchi: {_e(row.get('username'))}{cause}\n"
                        f"{self._when(row.get('created_at'))} · #{row.get('id')}"
                        + self._again(again))
            elif kind == "log" and row.get("level") == "error":
                logger = (row.get("meta") or {}).get("logger")
                line = _first_line(row.get("message"))
                send, again = self._once(("plog", row.get("source"), logger, line[:120]))
                if send:
                    messages.append(
                        f"🔴 <b>Backend xatosi</b> — {_e(row.get('source'))}\n"
                        f"{_e(logger)}: {_e(_clip(row.get('message'), 500))}\n"
                        f"{self._when(row.get('created_at'))} · #{row.get('id')}"
                        + self._again(again))
            elif kind == "auth" and event == "login_failed":
                who = row.get("username") or "?"
                count = self._failure(("pfail", row.get("user_side"), who), now)
                if count == self.failed_logins:
                    exists = (row.get("meta") or {}).get("account_exists")
                    note = "" if exists is None else (" (akkaunt mavjud)" if exists
                                                     else " (bunday akkaunt yo'q)")
                    messages.append(
                        f"🟠 <b>Ko'p muvaffaqiyatsiz kirish</b>\n"
                        f"{_e(who)}{note} — {count} marta, oxirgi {self.window // 60} daqiqada\n"
                        f"{self._when(row.get('created_at'))} · #{row.get('id')}")
            elif kind == "client" and event in BROWSER_ERRORS and self.browser_errors:
                browser.append(row)
            elif kind == "manual" and event in DELETE_EVENTS:
                messages.append(f"🗑 <b>Audit yozuvlari o'chirildi</b>\n"
                                f"{_e(_clip(row.get('message'), 300))}\n"
                                f"kim: {_e(row.get('username'))} · "
                                f"{self._when(row.get('created_at'))} · #{row.get('id')}")
        if browser:
            fresh = []
            for row in browser:
                send, _ = self._once(("pjs", _first_line(row.get("message"))[:120]))
                if send:
                    fresh.append(row)
            if fresh:
                lines = [f"🟡 <b>Brauzer xatolari</b>: {len(fresh)} ta yangi"]
                for row in fresh[:BROWSER_SHOWN]:
                    lines.append(f"• {_e(_clip(_first_line(row.get('message')), 160))}\n"
                                 f"  {_e(row.get('username'))} · {_e(_clip(row.get('path'), 80))}")
                if len(fresh) > BROWSER_SHOWN:
                    lines.append(f"… va yana {len(fresh) - BROWSER_SHOWN} ta")
                messages.append("\n".join(lines))
        return messages

    # ---------- tender-v2 ----------

    def tender(self, rows: list[dict]) -> list[str]:
        messages: list[str] = []
        now = self.clock()
        for row in rows:
            tur, status = row.get("tur"), row.get("holat_kodi")
            if tur == "sorov" and isinstance(status, int) and status >= 500:
                route = (row.get("yol") or "")
                send, again = self._once(("t5xx", row.get("usul"), route, status))
                if send:
                    cause = (row.get("qoshimcha") or {}).get("istisno")
                    messages.append(
                        f"🔴 <b>tender-v2 server xatosi {status}</b>\n"
                        f"{_e(row.get('usul'))} {_e(_clip(route, 200))}"
                        + (f"\nsabab: {_e(cause)}" if cause else "")
                        + f"\n{self._when(row.get('yaratildi'))} · #{row.get('id')}"
                        + self._again(again))
            elif tur == "log" and row.get("hodisa") == "jurnal_tashlandi":
                messages.append(f"⚠️ <b>tender-v2 jurnal yozuvlari tashlandi</b>\n"
                                f"{_e(_clip(row.get('xabar'), 300))}\n"
                                f"{self._when(row.get('yaratildi'))} · #{row.get('id')}")
            elif tur == "log" and row.get("daraja") == "error":
                line = _first_line(row.get("xabar"))
                send, again = self._once(("tlog", row.get("manba"), line[:120]))
                if send:
                    messages.append(
                        f"🔴 <b>tender-v2 xatosi</b> — {_e(row.get('manba'))}\n"
                        f"{_e(_clip(row.get('xabar'), 500))}\n"
                        f"{self._when(row.get('yaratildi'))} · #{row.get('id')}"
                        + self._again(again))
            elif tur == "kirish":
                who = row.get("login") or "kalitsiz"
                count = self._failure(("tfail", who), now)
                if count == self.failed_logins:
                    reason = (row.get("qoshimcha") or {}).get("sabab")
                    messages.append(
                        f"🟠 <b>tender-v2: ko'p rad etilgan kirish</b>\n"
                        f"{_e(who)} — {count} marta, oxirgi {self.window // 60} daqiqada"
                        + (f"\nsabab: {_e(_clip(reason, 120))}" if reason else "")
                        + f"\n{self._when(row.get('yaratildi'))} · #{row.get('id')}")
            elif tur == "amal":
                messages.append(f"ℹ️ <b>tender-v2 operator amali</b>: {_e(row.get('hodisa'))}\n"
                                f"{_e(_clip(row.get('qoshimcha'), 200))}\n"
                                f"manba: {_e(row.get('manba'))} · "
                                f"{self._when(row.get('yaratildi'))} · #{row.get('id')}")
        return messages

    # ---------- sending budget ----------

    def limit(self, messages: list[str]) -> list[str]:
        """At most ``max_per_minute`` messages in any minute. What does not fit is
        counted, and the next message that does fit says how many were held back."""
        now = self.clock()
        while self._sent and now - self._sent[0] >= 60:
            self._sent.popleft()
        room = self.max_per_minute - len(self._sent)
        if room <= 0:
            self._held += len(messages)
            return []
        if len(messages) <= room and not self._held:
            out = list(messages)
        else:
            out = messages[:room - 1]                # one place for the notice
            self._held += len(messages) - len(out)
            out.append(f"⏸ Yana {self._held} ta ogohlantirish yuborilmadi (daqiqasiga "
                       f"{self.max_per_minute} tadan ko'p). Batafsil: /logs 10m errors")
            self._held = 0
        for _ in out:
            self._sent.append(now)
        return out
