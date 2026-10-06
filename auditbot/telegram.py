"""The few Telegram Bot API calls the bot makes, over plain httpx.

No framework: long polling with ``getUpdates``, ``sendMessage``,
``sendDocument``, ``answerCallbackQuery``, ``setMyCommands``. The token is part
of every URL, so neither the URL nor an httpx exception text (which quotes it)
is ever logged — failures are reported by method name and Telegram's own
``description``.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from typing import Any

import httpx

log = logging.getLogger("auditbot.telegram")

# Telegram's own limits.
MESSAGE_MAX_CHARS = 4096
CAPTION_MAX_CHARS = 1024
DOCUMENT_MAX_BYTES = 50 * 1024 * 1024


class TelegramError(Exception):
    """A call Telegram refused. Carries the method and Telegram's description only."""

    def __init__(self, method: str, description: str, *, retry_after: float | None = None,
                 code: int | None = None):
        super().__init__(f"{method}: {description}")
        self.method = method
        self.description = description
        self.retry_after = retry_after
        self.code = code                       # Telegram's error_code: 403 = blocked / removed

    @property
    def gone(self) -> bool:
        """The chat cannot be written to any more: the person blocked the bot,
        the bot was removed from the group, or the chat no longer exists."""
        text = self.description.lower()
        return self.code == 403 or "chat not found" in text or "upgraded to a supergroup" in text


class Telegram:
    def __init__(self, token: str, api: str = "https://api.telegram.org",
                 client: httpx.AsyncClient | None = None):
        self._base = f"{api}/bot{token}/"
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(70.0, connect=10.0))

    async def close(self) -> None:
        await self._client.aclose()

    async def call(self, method: str, *, data: dict[str, Any] | None = None,
                   files: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        """One API call, retried once after Telegram's own ``retry_after`` on 429."""
        for attempt in (1, 2):
            try:
                if files:
                    response = await self._client.post(self._base + method, data=data, files=files,
                                                       timeout=timeout)
                else:
                    response = await self._client.post(self._base + method, json=data or {},
                                                       timeout=timeout)
            except httpx.HTTPError as exc:
                # The exception text names the URL, and the URL holds the token.
                raise TelegramError(method, f"network error ({type(exc).__name__})") from None
            try:
                body = response.json()
            except ValueError:
                raise TelegramError(method, f"HTTP {response.status_code}, not JSON") from None
            if body.get("ok"):
                return body.get("result")
            retry_after = (body.get("parameters") or {}).get("retry_after")
            if response.status_code == 429 and retry_after and attempt == 1:
                await asyncio.sleep(min(float(retry_after), 60.0))
                continue
            raise TelegramError(method, str(body.get("description") or response.status_code),
                                retry_after=retry_after, code=body.get("error_code"))
        raise AssertionError("unreachable")

    async def updates(self, offset: int | None, timeout: int = 50) -> list[dict]:
        data: dict[str, Any] = {"timeout": timeout,
                                # my_chat_member: the bot was added to or removed from a
                                # group, or a person blocked or unblocked it.
                                "allowed_updates": ["message", "callback_query", "my_chat_member"]}
        if offset is not None:
            data["offset"] = offset
        return await self.call("getUpdates", data=data, timeout=timeout + 15)

    async def send(self, chat_id: int, text: str, *, keyboard: list[list[dict]] | None = None) -> None:
        """An HTML message. Longer text than Telegram takes is cut, never split
        mid-tag: callers build their messages from escaped pieces."""
        if len(text) > MESSAGE_MAX_CHARS:
            text = text[:MESSAGE_MAX_CHARS - 20].rsplit("\n", 1)[0] + "\n…"
        data: dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                                "disable_web_page_preview": True}
        if keyboard:
            data["reply_markup"] = {"inline_keyboard": keyboard}
        try:
            await self.call("sendMessage", data=data)
        except TelegramError as exc:
            if "parse" not in exc.description.lower():
                raise
            # Markup Telegram refused (a tag cut by the length limit, say): the
            # words matter more than the bold, so the text goes again as plain text.
            data.pop("parse_mode")
            data["text"] = html.unescape(re.sub(r"</?[a-z]+[^>]*>", "", text))
            await self.call("sendMessage", data=data)

    async def document(self, chat_id: int, filename: str, content: bytes, mime: str,
                       caption: str = "") -> None:
        data = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption[:CAPTION_MAX_CHARS]
            data["parse_mode"] = "HTML"
        await self.call("sendDocument", data=data, files={"document": (filename, content, mime)},
                        timeout=300)

    async def typing(self, chat_id: int, action: str = "upload_document") -> None:
        try:
            await self.call("sendChatAction", data={"chat_id": chat_id, "action": action})
        except TelegramError:
            pass                                  # cosmetic only

    async def answer(self, callback_id: str, text: str = "") -> None:
        try:
            await self.call("answerCallbackQuery", data={"callback_query_id": callback_id,
                                                         "text": text})
        except TelegramError:
            pass                                  # the button just keeps spinning a moment

    async def commands(self, commands: list[tuple[str, str]]) -> None:
        await self.call("setMyCommands", data={
            "commands": [{"command": c, "description": d} for c, d in commands]})
