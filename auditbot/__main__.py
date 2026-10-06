"""python -m auditbot — start the bot. Settings come from the environment
(see .env.example); a missing one stops it with one line saying which."""

from __future__ import annotations

import asyncio
import logging
import sys

from auditbot import config
from auditbot.bot import Bot
from auditbot.sources import Platform, Tender
from auditbot.telegram import Telegram


def main() -> None:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    # httpx logs every request URL at INFO — and the Telegram URL holds the token.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    settings = config.load()
    for name in config.obsolete_settings():
        logging.getLogger("auditbot").warning(
            "%s in .env is ignored: chats are managed in the dashboard (Telegram bot tab)", name)
    asyncio.run(_run(settings))


async def _run(settings: config.Settings) -> None:
    telegram = Telegram(settings.telegram_token, settings.telegram_api)
    platform = Platform(settings.platform_url, settings.platform_user, settings.platform_password)
    tender = (Tender(settings.tender_url, settings.tender_user, settings.tender_password)
              if settings.tender_enabled else None)
    try:
        await Bot(settings, telegram, platform, tender).run()
    finally:
        await telegram.close()
        await platform.close()
        if tender is not None:
            await tender.close()


if __name__ == "__main__":
    main()
