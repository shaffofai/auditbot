"""In-memory stand-ins for the three servers the bot talks to, as httpx
transports: the gateway's audit API, tender-v2's /jurnal and Telegram.

They follow the real contracts closely enough to catch a wrong parameter name
or a paging mistake: browse mode is newest first with a cursor, tail mode is
ascending with a horizon, unknown /jurnal parameters are a 400, a wrong
password is a 401.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx

T0 = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)     # 09:00 in Tashkent


def at(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def _when(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _basic(request: httpx.Request) -> tuple[str, str] | None:
    header = request.headers.get("authorization", "")
    if not header.startswith("Basic "):
        return None
    user, _, password = base64.b64decode(header[6:]).decode().partition(":")
    return user, password


class FakePlatform:
    """GET /admin/gateway/audit (browse + tail), GET /admin/gateway/audit/{id}."""

    LIST_KEYS = ("id", "created_at", "kind", "event", "level", "source", "service", "request_id",
                 "session_id", "user_side", "user_id", "username", "is_admin", "authenticated",
                 "ip", "method", "path", "query", "status_code", "duration_ms", "completed_at",
                 "request_bytes", "response_bytes", "has_request_body", "has_response_body",
                 "files", "message", "note", "repeat_count", "last_seen_at")
    DETAIL_KEYS = ("user_agent", "request_headers", "response_headers", "request_files",
                   "response_files", "meta")
    FILTERS = ("kind", "event", "level", "source", "service", "user_side", "username", "method",
               "status", "request_id", "session_id")

    def __init__(self, user="audit-bot", password="secret-1"):
        self.user, self.password = user, password
        self.rows: list[dict] = []
        self.calls: list[dict] = []
        self.fail_with: int | None = None

    def add(self, **row) -> dict:
        row.setdefault("id", len(self.rows) + 1)
        row.setdefault("created_at", at(len(self.rows)))
        row.setdefault("kind", "request")
        row.setdefault("event", "http")
        row.setdefault("level", "info")
        row.setdefault("source", "gateway")
        row.setdefault("repeat_count", 1)
        self.rows.append(row)
        return row

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        params = {k: v[-1] for k, v in parse_qs(request.url.query.decode(), keep_blank_values=True).items()}
        self.calls.append({"path": request.url.path, "params": params})
        if _basic(request) != (self.user, self.password):
            return httpx.Response(401, json={"detail": "Invalid credentials"})
        if self.fail_with:
            return httpx.Response(self.fail_with, json={"detail": "fake failure"})
        path = request.url.path
        if path.startswith("/admin/gateway/audit/"):
            row_id = int(path.rsplit("/", 1)[1])
            for row in self.rows:
                if row["id"] == row_id:
                    return httpx.Response(200, json=row)
            return httpx.Response(404, json={"detail": "No audit row with that id"})
        if path != "/admin/gateway/audit":
            return httpx.Response(404, json={"detail": "Not Found"})

        limit = int(params.get("limit", 100))
        detail = params.get("detail") == "true"
        if detail and limit > 200:
            return httpx.Response(422, json={"detail": "limit: at most 200 with detail=true"})
        rows = self.rows
        for key in self.FILTERS:
            if key in params:
                want = params[key]
                rows = [r for r in rows if str(r.get("status_code" if key == "status" else key)) == want]
        if "path" in params:
            rows = [r for r in rows if (r.get("path") or "").startswith(params["path"])]
        keys = self.LIST_KEYS + (self.DETAIL_KEYS if detail else ())

        def shape(row):
            return {k: row.get(k) for k in keys}

        if "after_id" in params:
            if {"from", "to", "cursor"} & params.keys():
                return httpx.Response(422, json={"detail": "after_id cannot be combined"})
            after = int(params["after_id"])
            horizon = max([r["id"] for r in self.rows] + [after])
            page = sorted((r for r in rows if after < r["id"] <= horizon), key=lambda r: r["id"])
            more = len(page) > limit
            page = page[:limit]
            return httpx.Response(200, json={
                "items": [shape(r) for r in page], "from": None, "to": None,
                "next_after_id": page[-1]["id"] if more else horizon})

        end = _when(params["to"]) if "to" in params else datetime.now(timezone.utc)
        start = _when(params["from"]) if "from" in params else end - timedelta(hours=24)
        rows = [r for r in rows if start <= _when(r["created_at"]) < end]
        rows.sort(key=lambda r: (_when(r["created_at"]), r["id"]), reverse=True)
        if "cursor" in params:
            c_time, c_id = params["cursor"].split("|")
            rows = [r for r in rows if (_when(r["created_at"]), r["id"]) < (_when(c_time), int(c_id))]
        more = len(rows) > limit
        page = rows[:limit]
        return httpx.Response(200, json={
            "items": [shape(r) for r in page], "from": start.isoformat(), "to": end.isoformat(),
            "next_cursor": f"{page[-1]['created_at']}|{page[-1]['id']}" if more else None})


class FakeTender:
    """GET /jurnal (browse + tail), GET /jurnal/{id}."""

    FILTERS = ("dan", "gacha", "tur", "manba", "daraja", "holat_kodi", "login", "sorov_id",
               "yol", "limit", "oldin_id", "keyin_id")
    LIST_KEYS = ("id", "yaratildi", "tur", "hodisa", "daraja", "manba", "sorov_id", "usul", "yol",
                 "sorov_qatori", "holat_kodi", "davomiylik_ms", "ip", "login", "tasdiqlangan",
                 "xabar", "sorov_tanasi_uzunligi", "javob_tanasi_uzunligi", "qoshimcha")

    def __init__(self, user="jurnal-oquvchi", password="x" * 24):
        self.user, self.password = user, password
        self.rows: list[dict] = []
        self.calls: list[dict] = []

    def add(self, **row) -> dict:
        row.setdefault("id", len(self.rows) + 1)
        row.setdefault("yaratildi", at(len(self.rows)))
        row.setdefault("tur", "sorov")
        row.setdefault("hodisa", "http")
        row.setdefault("daraja", "info")
        row.setdefault("manba", "api")
        self.rows.append(row)
        return row

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        params = {k: v[-1] for k, v in parse_qs(request.url.query.decode(), keep_blank_values=True).items()}
        self.calls.append({"path": request.url.path, "params": params})
        if _basic(request) != (self.user, self.password):
            return httpx.Response(401, json={"xato": "login yoki parol noto'g'ri"})
        path = request.url.path
        if path.startswith("/jurnal/"):
            jid = int(path.rsplit("/", 1)[1])
            for row in self.rows:
                if row["id"] == jid:
                    return httpx.Response(200, json=row)
            return httpx.Response(404, json={"xato": "topilmadi"})
        if path != "/jurnal":
            return httpx.Response(404, json={"xato": "topilmadi"})
        unknown = sorted(set(params) - set(self.FILTERS))
        if unknown:
            return httpx.Response(400, json={"xato": f"noma'lum parametr: {', '.join(unknown)}"})
        limit = int(params.get("limit", 100))
        if not 1 <= limit <= 500:
            return httpx.Response(400, json={"xato": "limit: 1..500"})
        rows = self.rows
        for key in ("tur", "manba", "daraja", "login"):
            if key in params:
                rows = [r for r in rows if r.get(key) == params[key]]
        if "holat_kodi" in params:
            rows = [r for r in rows if r.get("holat_kodi") == int(params["holat_kodi"])]

        def shape(row):
            return {k: row.get(k) for k in self.LIST_KEYS}

        if "keyin_id" in params:
            after = int(params["keyin_id"])
            horizon = max([r["id"] for r in self.rows] + [after])
            page = sorted((r for r in rows if after < r["id"] <= horizon), key=lambda r: r["id"])[:limit]
            return httpx.Response(200, json={
                "qatorlar": [shape(r) for r in page],
                "keyingi_keyin_id": page[-1]["id"] if len(page) == limit else horizon})
        end = _when(params["gacha"]) if "gacha" in params else None
        start = _when(params["dan"]) if "dan" in params else (end or datetime.now(timezone.utc)) - timedelta(hours=24)
        rows = [r for r in rows if _when(r["yaratildi"]) >= start
                and (end is None or _when(r["yaratildi"]) < end)]
        if "oldin_id" in params:
            rows = [r for r in rows if r["id"] < int(params["oldin_id"])]
        rows = sorted(rows, key=lambda r: r["id"], reverse=True)[:limit]
        return httpx.Response(200, json={
            "qatorlar": [shape(r) for r in rows], "dan": start.isoformat(),
            "gacha": end.isoformat() if end else None,
            "keyingi_oldin_id": rows[-1]["id"] if len(rows) == limit else None})


class FakeTelegram:
    """Records what the bot sends; ``updates`` are handed out by getUpdates."""

    def __init__(self, token="123:test"):
        self.token = token
        self.sent: list[dict] = []
        self.documents: list[dict] = []
        self.updates: list[dict] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def messages(self, chat_id=None) -> list[str]:
        return [m["text"] for m in self.sent if chat_id is None or m["chat_id"] == chat_id]

    def handle(self, request: httpx.Request) -> httpx.Response:
        prefix = f"/bot{self.token}/"
        if not request.url.path.startswith(prefix):
            return httpx.Response(404, json={"ok": False, "description": "Not Found"})
        method = request.url.path[len(prefix):]
        if method == "sendDocument":
            body = request.read()
            ctype = request.headers["content-type"]
            boundary = ctype.split("boundary=")[1].encode()
            parts = {}
            for part in body.split(b"--" + boundary):
                if b"\r\n\r\n" not in part:
                    continue
                head, _, content = part.partition(b"\r\n\r\n")
                content = content.rstrip(b"\r\n")
                head_text = head.decode("utf-8", "replace")
                name = head_text.split('name="')[1].split('"')[0]
                filename = head_text.split('filename="')[1].split('"')[0] if 'filename="' in head_text else None
                parts[name] = (filename, content)
            self.documents.append({"chat_id": int(parts["chat_id"][1]),
                                   "filename": parts["document"][0],
                                   "content": parts["document"][1]})
            return httpx.Response(200, json={"ok": True, "result": {}})
        data = json.loads(request.read() or b"{}")
        if method == "sendMessage":
            if data.get("parse_mode") == "HTML" and "<x>" in data["text"]:
                return httpx.Response(400, json={"ok": False, "description":
                                                 "Bad Request: can't parse entities: unsupported tag"})
            if len(data["text"]) > 4096:
                return httpx.Response(400, json={"ok": False, "description": "message is too long"})
            self.sent.append(data)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.sent)}})
        if method == "getUpdates":
            out, self.updates = self.updates, []
            return httpx.Response(200, json={"ok": True, "result": out})
        return httpx.Response(200, json={"ok": True, "result": True})
