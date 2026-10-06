"""The bot itself: Telegram long polling for commands, a loop that follows both
logs for alerts, and a heartbeat file for the container healthcheck.

Who may use it: only the chats listed in ALLOWED_CHAT_IDS. Everything else is
ignored, except ``/id``, which tells any chat its own id (that is how a new
chat is added). Alerts go to ALERT_CHAT_IDS.
"""

from __future__ import annotations

import asyncio
import html
import io
import json
import logging
import os
import time
import zipfile
from datetime import datetime, timezone
from typing import Any, Callable

from auditbot import export, query, texts
from auditbot.alerts import Alerts
from auditbot.config import Settings
from auditbot.sources import Platform, SourceError, Tender
from auditbot.telegram import DOCUMENT_MAX_BYTES, Telegram, TelegramError

log = logging.getLogger("auditbot")

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
AUTH_BACKOFF = 600            # seconds to wait after a source refused the credentials
MAX_BACKOFF = 300
DOWN_AFTER = 3                # consecutive failures before the chat is told
FILE_LIMIT = DOCUMENT_MAX_BYTES - 1024 * 1024


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _zip(name: str, content: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr(name, content)
    return out.getvalue()


class State:
    """Where the alert loop stopped reading, kept across restarts."""

    def __init__(self, path: str):
        self.path = path
        self.data: dict[str, Any] = {}
        try:
            with open(path, encoding="utf-8") as fh:
                self.data = json.load(fh)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("state file unreadable (%s); starting from the newest rows",
                        type(exc).__name__)

    def get(self, key: str) -> int | None:
        value = self.data.get(key)
        return int(value) if isinstance(value, int) else None

    def set(self, key: str, value: int) -> None:
        self.data[key] = int(value)

    def save(self) -> None:
        tmp = self.path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh)
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("state file not written (%s)", type(exc).__name__)


class _Follower:
    """One source followed in tail mode, with its own pace and back-off."""

    def __init__(self, name: str, source, check: Callable[[list[dict]], list[str]], key: str,
                 interval: int):
        self.name, self.source, self.check, self.key = name, source, check, key
        self.interval = interval
        self.failures = 0
        self.next_at = 0.0
        self.down_told = False


class Bot:
    def __init__(self, settings: Settings, telegram: Telegram, platform: Platform,
                 tender: Tender | None, *, clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] = _utcnow):
        self.s = settings
        self.tg = telegram
        self.platform = platform
        self.tender = tender
        self.clock = clock
        self.now = now
        self.state = State(settings.state_path)
        self.alerts = Alerts(tz=settings.tz, window=settings.alert_window,
                             failed_logins=settings.alert_failed_logins,
                             browser_errors=settings.alert_browser_errors,
                             max_per_minute=settings.alert_max_per_minute, clock=clock)
        self.exporting: set[int] = set()
        # One export at a time: twenty thousand detailed rows of each source, as
        # dicts, an Excel file and a JSON file, all in memory at once.
        self.export_slots = asyncio.Semaphore(1)
        self.id_replied: dict[int, float] = {}
        self.started = clock()
        self.offset: int | None = None
        # tender-v2 journals every /jurnal read as a row of its own (the platform
        # folds the bot's reads into one row per 10 minutes), so it is asked less
        # often: ~1 400 small rows a day at 60 s.
        self.followers = [_Follower("Platforma", platform, self.alerts.platform,
                                    "platform_after_id", settings.alert_interval)]
        if tender is not None:
            self.followers.append(_Follower("tender-v2", tender, self.alerts.tender,
                                            "tender_after_id",
                                            max(settings.alert_interval,
                                                settings.alert_tender_interval)))

    # ---------- run ----------

    async def run(self) -> None:
        try:
            await self.tg.commands([("loglar", "Loglarni Excel va JSON qilib olish"),
                                    ("holat", "Bot va manbalar holati"),
                                    ("yordam", "Qanday ishlatiladi"),
                                    ("id", "Shu chatning ID raqami")])
        except TelegramError as exc:
            log.warning("setMyCommands failed: %s", exc.description)
        tasks = [asyncio.create_task(self.poll(), name="poll"),
                 asyncio.create_task(self.heartbeat(), name="heartbeat")]
        if self.s.alerts_enabled:
            tasks.append(asyncio.create_task(self.follow(), name="alerts"))
        if not self.s.allowed_chats:
            log.warning("ALLOWED_CHAT_IDS is empty: setup mode, only /id is answered. "
                        "Send /id to the bot from each chat, put the ids in .env, start again.")
        log.info("auditbot running: %d allowed chat(s), alerts %s, tender-v2 %s",
                 len(self.s.allowed_chats), "on" if self.s.alerts_enabled else "off",
                 "on" if self.tender is not None else "off")
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in pending:
            task.cancel()
        for task in done:
            task.result()                      # re-raise what stopped it

    async def heartbeat(self) -> None:
        while True:
            try:
                with open(self.s.heartbeat_path, "w") as fh:
                    fh.write(str(int(time.time())))
            except OSError:
                pass
            await asyncio.sleep(20)

    # ---------- commands ----------

    async def poll(self) -> None:
        delay = 1.0
        while True:
            try:
                updates = await self.tg.updates(self.offset)
                delay = 1.0
                await asyncio.sleep(0)          # an instant empty answer must not starve the loop
            except TelegramError as exc:
                log.warning("getUpdates failed: %s", exc.description)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue
            for update in updates:
                self.offset = int(update["update_id"]) + 1
                try:
                    await self.handle(update)
                except Exception:                              # noqa: BLE001
                    log.exception("update %s failed", update.get("update_id"))

    async def handle(self, update: dict) -> None:
        if "callback_query" in update:
            cq = update["callback_query"]
            chat_id = ((cq.get("message") or {}).get("chat") or {}).get("id")
            await self.tg.answer(cq.get("id", ""))
            data = cq.get("data") or ""
            if chat_id in self.s.allowed_chats and data.startswith("q:"):
                self.start_export(chat_id, data[2:])
            return
        message = update.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        text = (message.get("text") or "").strip()
        if chat_id is None or not text.startswith("/"):
            return
        command, _, args = text.partition(" ")
        command = command[1:].split("@", 1)[0].lower()
        if command == "id":
            await self.reply_id(chat_id)
            return
        if chat_id not in self.s.allowed_chats:
            log.info("ignored /%s from chat %s (not in ALLOWED_CHAT_IDS)", command[:32], chat_id)
            return
        if command in ("start", "yordam", "help"):
            await self.tg.send(chat_id, texts.HELP.format(bodies=self.s.export_max_bodies),
                               keyboard=self.quick())
        elif command == "loglar":
            if args.strip():
                self.start_export(chat_id, args)
            else:
                await self.tg.send(chat_id, texts.ASK_RANGE, keyboard=self.quick())
        elif command == "holat":
            await self.tg.send(chat_id, await self.status())

    def quick(self) -> list[list[dict]]:
        return [[{"text": label, "callback_data": "q:" + value} for label, value in row]
                for row in texts.QUICK]

    async def reply_id(self, chat_id: int) -> None:
        now = self.clock()
        if now - self.id_replied.get(chat_id, -1e9) < 10:
            return
        self.id_replied[chat_id] = now
        if len(self.id_replied) > 1000:
            self.id_replied = {c: t for c, t in self.id_replied.items() if now - t < 10}
        await self.tg.send(chat_id, texts.CHAT_ID.format(chat_id=chat_id))

    async def status(self) -> str:
        lines = ["🩺 <b>Holat</b>"]
        up = int(self.clock() - self.started)
        lines.append(f"Ishlamoqda: {up // 3600} soat {up % 3600 // 60} daqiqa")
        for name, source in (("Platforma", self.platform), ("tender-v2", self.tender)):
            if source is None:
                lines.append(f"{name}: {texts.TENDER_OFF}")
                continue
            try:
                head = await source.head()
                lines.append(f"{name}: ✅ ishlayapti, oxirgi yozuv #{head}")
            except SourceError as exc:
                lines.append(f"{name}: ❌ {html.escape(exc.message, quote=False)}")
        if self.s.alerts_enabled:
            cursors = ", ".join(f"{f.name} #{self.state.get(f.key)}" for f in self.followers)
            lines.append(f"Ogohlantirishlar: yoqilgan, har {self.s.alert_interval} soniyada "
                         f"({cursors})")
        else:
            lines.append("Ogohlantirishlar: o'chirilgan")
        return "\n".join(lines)

    # ---------- export ----------

    def start_export(self, chat_id: int, args: str) -> None:
        if chat_id in self.exporting:
            asyncio.create_task(self._send_quietly(chat_id, texts.BUSY))
            return
        self.exporting.add(chat_id)
        task = asyncio.create_task(self.export(chat_id, args))
        task.add_done_callback(lambda _t, c=chat_id: self.exporting.discard(c))

    async def _send_quietly(self, chat_id: int, text: str) -> None:
        try:
            await self.tg.send(chat_id, text)
        except TelegramError as exc:
            log.warning("sendMessage failed: %s", exc.description)

    async def export(self, chat_id: int, args: str) -> None:
        try:
            q = query.parse(args, now=self.now(), tz=self.s.tz)
        except query.QueryError as exc:
            await self._send_quietly(chat_id, texts.NOT_UNDERSTOOD.format(
                why=html.escape(str(exc), quote=False)))
            return
        label = f"{export.local_label(q.start, self.s.tz)} — {export.local_label(q.end, self.s.tz)}"
        await self._send_quietly(chat_id, texts.PREPARING.format(label=label))
        try:
            async with self.export_slots:
                await self.tg.typing(chat_id)
                await self._export(chat_id, q)
        except Exception:                                            # noqa: BLE001
            log.exception("export failed")
            await self._send_quietly(chat_id, "⚠️ Tayyorlab bo'lmadi — bot loglarida sabab bor.")

    async def _fetch(self, source, name: str, q: query.Query, filters: dict,
                     notes: list[str]) -> tuple[list[dict], bool] | None:
        try:
            rows, truncated = await source.range(q.start, q.end, filters, self.s.export_max_rows)
        except SourceError as exc:
            notes.append(texts.SOURCE_FAILED.format(source=name, why=exc.message))
            return None
        if q.bodies and rows:
            await self._bodies(source, rows)
        return rows, truncated

    async def _bodies(self, source, rows: list[dict]) -> None:
        """Bodies are not in a list page; fetch them row by row, a few at a time,
        for the first EXPORT_MAX_BODIES rows."""
        gate = asyncio.Semaphore(4)
        keys = ("request_body", "response_body", "sorov_tanasi", "javob_tanasi")

        async def one(row: dict) -> None:
            async with gate:
                try:
                    full = await source.row(row["id"])
                except SourceError:
                    return
            if full:
                for key in keys:
                    if key in full:
                        row[key] = full[key]

        await asyncio.gather(*(one(r) for r in rows[: self.s.export_max_bodies]))

    async def _export(self, chat_id: int, q: query.Query) -> None:
        notes: list[str] = []
        jobs = []
        if q.use_platform:
            jobs.append(self._fetch(self.platform, "Platforma", q, q.platform, notes))
        if q.use_tender:
            if self.tender is None:
                notes.append(texts.TENDER_OFF)
            else:
                jobs.append(self._fetch(self.tender, "tender-v2", q, q.tender, notes))
        results = await asyncio.gather(*jobs)
        platform = results[0] if q.use_platform else None
        tender = results[-1] if (q.use_tender and self.tender is not None) else None
        if q.bodies:
            notes.append(f"Tanalar birinchi {self.s.export_max_bodies} ta yozuv uchun olindi.")

        text = export.summary(start=q.start, end=q.end, tz=self.s.tz,
                              filters_label=q.filters_label, platform=platform, tender=tender,
                              limit=self.s.export_max_rows, now=self.now(), notes=notes)
        await self.tg.send(chat_id, text)
        if not any(r and r[0] for r in (platform, tender)):
            if platform is not None or tender is not None:
                await self.tg.send(chat_id, texts.NOTHING)
            return

        await self.tg.typing(chat_id)
        stem = export.file_stem(q.start, q.end, self.s.tz)
        xlsx, js = await asyncio.to_thread(self._build, text, q, platform, tender)
        files = [(stem + ".xlsx", xlsx, XLSX), (stem + ".json", js, "application/json")]
        if len(js) > FILE_LIMIT:
            # JSON shrinks about tenfold; Excel is a zip already.
            files[1] = (stem + ".json.zip", await asyncio.to_thread(_zip, stem + ".json", js),
                        "application/zip")
        for name, content, mime in files:
            if len(content) > FILE_LIMIT:
                await self.tg.send(chat_id, texts.TOO_BIG.format(
                    name=name, mb=round(len(content) / 1024 / 1024)))
                continue
            await self.tg.document(chat_id, name, content, mime)

    def _build(self, text: str, q: query.Query, platform, tender) -> tuple[bytes, bytes]:
        xlsx = export.workbook(summary_text=text, tz=self.s.tz,
                               platform=platform[0] if platform else None,
                               tender=tender[0] if tender else None, bodies=q.bodies)
        js = export.json_file(start=q.start, end=q.end, tz=self.s.tz,
                              filters_label=q.filters_label, platform=platform, tender=tender)
        return xlsx, js

    # ---------- alerts ----------

    async def follow(self) -> None:
        while True:
            for follower in self.followers:
                if self.clock() >= follower.next_at:
                    follower.next_at = self.clock() + follower.interval
                    try:
                        await self.follow_one(follower)
                    except Exception:                                  # noqa: BLE001
                        # An answer of an unexpected shape must not stop the alerts for good.
                        log.exception("alerts: %s check failed", follower.name)
            await asyncio.sleep(min(f.interval for f in self.followers) / 2)

    async def _to_end(self, source, after: int) -> int:
        """Move ``after`` to the end of the table without alerting on what is passed.
        ``head()`` is close to the end (it is the newest row by time, not by id),
        and the short tail from there covers the rest."""
        after = max(after, await source.head())
        while True:
            rows, nxt = await source.tail(after)
            after = max(after, nxt)
            if len(rows) < source.PAGE:
                return after

    async def follow_one(self, f: _Follower) -> None:
        messages: list[str] = []
        try:
            after = self.state.get(f.key)
            if after is None:
                # First start: begin at the newest row, not at the beginning of time.
                after = await self._to_end(f.source, 0)
            else:
                read = 0
                while True:
                    rows, nxt = await f.source.tail(after)
                    read += len(rows)
                    messages += f.check(rows)
                    after = max(after, nxt)
                    if len(rows) < f.source.PAGE:
                        break
                    if read >= self.s.alert_catchup_rows:
                        # A long outage: skip to the end instead of replaying all of it.
                        after = await self._to_end(f.source, after)
                        messages.append(texts.ALERT_SKIPPED.format(
                            source=f.name, rows=self.s.alert_catchup_rows))
                        break
            if after != self.state.get(f.key):
                self.state.set(f.key, after)
                self.state.save()
        except SourceError as exc:
            await self._source_failed(f, exc)
            return
        if f.down_told:
            messages.insert(0, texts.ALERT_SOURCE_BACK.format(source=f.name))
        f.failures, f.down_told = 0, False
        await self.broadcast(messages)

    async def _source_failed(self, f: _Follower, exc: SourceError) -> None:
        f.failures += 1
        log.warning("alerts: %s failed (%s)", f.name, exc.message)
        if exc.auth:
            f.next_at = self.clock() + AUTH_BACKOFF
            if not f.down_told:
                f.down_told = True
                await self.broadcast([texts.ALERT_AUTH.format(source=f.name,
                                                              minutes=AUTH_BACKOFF // 60)])
            return
        f.next_at = self.clock() + min(f.interval * 2 ** f.failures, MAX_BACKOFF)
        if f.failures >= DOWN_AFTER and not f.down_told:
            f.down_told = True
            await self.broadcast([texts.ALERT_SOURCE_DOWN.format(
                source=f.name, why=html.escape(exc.message, quote=False))])

    async def broadcast(self, messages: list[str]) -> None:
        if not messages:
            return
        for text in self.alerts.limit(messages):
            for chat_id in sorted(self.s.alert_chats):
                await self._send_quietly(chat_id, text)
