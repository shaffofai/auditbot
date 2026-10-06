"""The bot end to end against the three fakes: commands in, messages and files out;
alerts from new rows; the cursor kept across a restart."""

import asyncio
import io
import json
import logging
from datetime import timedelta

import httpx
from openpyxl import load_workbook

from auditbot.bot import Bot
from auditbot.sources import Platform, Tender
from auditbot.telegram import Telegram
from fakes import T0, FakePlatform, FakeTelegram, FakeTender

NOW = T0 + timedelta(hours=5)          # 14:00 in Tashkent; the fakes' rows start at 09:00


class Clock:
    def __init__(self):
        self.t = 5000.0

    def __call__(self):
        return self.t


class World:
    def __init__(self, settings, *, tender=True):
        self.settings = settings
        self.tg = FakeTelegram(settings.telegram_token)
        self.platform = FakePlatform(settings.platform_user, settings.platform_password)
        self.tender = FakeTender(settings.tender_user, settings.tender_password)
        self.clock = Clock()
        self.with_tender = tender

    def bot(self) -> Bot:
        s = self.settings
        return Bot(s,
                   Telegram(s.telegram_token, s.telegram_api,
                            httpx.AsyncClient(transport=self.tg.transport())),
                   Platform(s.platform_url, s.platform_user, s.platform_password,
                            httpx.AsyncClient(transport=self.platform.transport())),
                   Tender(s.tender_url, s.tender_user, s.tender_password,
                          httpx.AsyncClient(transport=self.tender.transport()))
                   if self.with_tender else None,
                   clock=self.clock, now=lambda: NOW)


def message(chat_id, text, update_id=1):
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, "text": text}}


async def settle(bot):
    """Let the export tasks the bot started finish."""
    for _ in range(50):
        await asyncio.sleep(0)
        if not bot.exporting:
            return
    while bot.exporting:
        await asyncio.sleep(0.01)


def run_commands(world, *texts, chat_id=111):
    async def go():
        bot = world.bot()
        for i, text in enumerate(texts):
            await bot.handle(message(chat_id, text, i))
            await settle(bot)
        return bot
    return asyncio.run(go())


def test_export_sends_summary_xlsx_and_json(make_settings):
    world = World(make_settings())
    world.platform.add(kind="request", status_code=500, source="gasn", username="gov",
                       path="/api-v2/gasn/back/ask")
    world.platform.add(kind="auth", event="login_failed", level="warning", username="bob")
    world.platform.add(kind="client", event="console.warn", level="warning", source="web")
    world.tender.add(tur="kirish", hodisa="kirish_rad", login="tender", holat_kodi=401)

    run_commands(world, "/loglar bugun")

    texts = world.tg.messages(111)
    assert texts[0].startswith("⏳ Tayyorlanmoqda: 05.10.2026 00:00 — 05.10.2026 14:00")
    summary = texts[1]
    assert "<b>Platforma</b>: 3 ta yozuv" in summary and "5xx: 1" in summary
    assert "<b>tender-v2</b>: 1 ta yozuv" in summary
    names = [d["filename"] for d in world.tg.documents]
    assert names == ["loglar_20261005-0000_20261005-1400.xlsx",
                     "loglar_20261005-0000_20261005-1400.json"]
    wb = load_workbook(io.BytesIO(world.tg.documents[0]["content"]))
    assert wb.sheetnames == ["Xulosa", "Platforma", "tender-v2"]
    assert wb["Platforma"].max_row == 4 and wb["tender-v2"].max_row == 2
    doc = json.loads(world.tg.documents[1]["content"])
    assert doc["platforma"]["yozuvlar_soni"] == 3 and doc["tender_v2"]["yozuvlar_soni"] == 1
    # The range reached both APIs in UTC.
    first = world.platform.calls[0]["params"]
    assert first["from"] == "2026-10-04T19:00:00+00:00" and first["to"] == "2026-10-05T09:00:00+00:00"
    assert world.tender.calls[0]["params"]["dan"] == "2026-10-04T19:00:00+00:00"


def test_export_with_filters_and_bodies(make_settings):
    world = World(make_settings(export_max_bodies=1))
    world.platform.add(level="error", request_body='{"q":1}', response_body="boom")
    world.platform.add(level="error", request_body='{"q":2}', response_body="boom2")
    world.platform.add(level="info")

    run_commands(world, "/loglar bugun xatolar xizmat=platforma tanalar")

    assert world.tender.calls == []
    summary = world.tg.messages(111)[1]
    assert "Filtr: faqat xatolar, xizmat=platforma, tanalar bilan" in summary
    assert "Tanalar birinchi 1 ta yozuv uchun olindi." in summary
    doc = json.loads(world.tg.documents[1]["content"])
    rows = doc["platforma"]["yozuvlar"]
    assert [r["id"] for r in rows] == [1, 2]
    assert rows[0]["request_body"] == '{"q":1}' and "request_body" not in rows[1]
    assert "tender_v2" not in doc
    wb = load_workbook(io.BytesIO(world.tg.documents[0]["content"]))
    header = [c.value for c in wb["Platforma"][1]]
    assert "So'rov tanasi" in header


def test_nothing_found(make_settings):
    world = World(make_settings())
    run_commands(world, "/loglar kecha")
    assert world.tg.messages(111)[-1] == "Bu oraliqda hech narsa topilmadi."
    assert world.tg.documents == []


def test_one_source_failing_does_not_stop_the_other(make_settings):
    world = World(make_settings())
    world.tender.password = "a-different-one-of-24-chars"
    world.platform.add()
    run_commands(world, "/loglar bugun")
    summary = world.tg.messages(111)[1]
    assert "tender-v2: o'qib bo'lmadi (login yoki parol rad etildi (401))" in summary
    assert len(world.tg.documents) == 2


def test_tender_disabled(make_settings):
    world = World(make_settings(), tender=False)
    world.platform.add()
    run_commands(world, "/loglar bugun")
    assert "tender-v2 o'chirilgan" in world.tg.messages(111)[1]
    assert load_workbook(io.BytesIO(world.tg.documents[0]["content"])).sheetnames == ["Xulosa", "Platforma"]


def test_bad_request_and_help(make_settings):
    world = World(make_settings())
    run_commands(world, "/loglar ertaga", "/yordam", "/loglar", "/start@ShaffofAuditBot")
    texts = world.tg.messages(111)
    assert texts[0].startswith("❓ Tushunilmadi: ertaga")
    assert "<b>/loglar</b>" in texts[1] and "birinchi 300 ta" in texts[1]
    assert texts[2].startswith("Qaysi oraliq?")
    assert "<b>/loglar</b>" in texts[3]
    keyboards = [m.get("reply_markup") for m in world.tg.sent]
    assert keyboards[1]["inline_keyboard"][0][0] == {"text": "Oxirgi 1 soat", "callback_data": "q:1soat"}


def test_quick_button_runs_an_export(make_settings):
    world = World(make_settings())
    world.platform.add()

    async def go():
        bot = world.bot()
        await bot.handle({"update_id": 1, "callback_query": {
            "id": "cb1", "data": "q:bugun", "message": {"chat": {"id": 111}}}})
        await settle(bot)
    asyncio.run(go())
    assert len(world.tg.documents) == 2


def test_strangers_are_ignored_but_may_ask_their_id(make_settings, caplog):
    world = World(make_settings())
    world.platform.add()
    caplog.set_level(logging.INFO)
    run_commands(world, "/loglar bugun", "/yordam", "/id", "/id", chat_id=999)
    assert world.tg.messages(999) == ["Shu chatning ID raqami: <code>999</code>"]   # /id once per 10 s
    assert world.tg.documents == [] and world.platform.calls == []
    assert "not in ALLOWED_CHAT_IDS" in caplog.text
    # A forged button press from a stranger does nothing either.
    async def go():
        bot = world.bot()
        await bot.handle({"update_id": 9, "callback_query": {
            "id": "cb", "data": "q:bugun", "message": {"chat": {"id": 999}}}})
        await settle(bot)
    asyncio.run(go())
    assert world.tg.documents == []


def test_one_export_per_chat_at_a_time(make_settings):
    world = World(make_settings())
    world.platform.add()

    async def go():
        bot = world.bot()
        await bot.handle(message(111, "/loglar bugun", 1))
        await bot.handle(message(111, "/loglar kecha", 2))
        await settle(bot)
    asyncio.run(go())
    assert any("Oldingi so'rov hali tayyorlanmoqda" in t for t in world.tg.messages(111))
    assert len(world.tg.documents) == 2


def test_status(make_settings):
    world = World(make_settings())
    world.platform.add()
    run_commands(world, "/holat")
    text = world.tg.messages(111)[0]
    assert "Platforma: ✅ ishlayapti, oxirgi yozuv #1" in text
    assert "tender-v2: ✅ ishlayapti, oxirgi yozuv #0" in text
    assert "Ogohlantirishlar: yoqilgan" in text


def test_poll_loop_reads_updates_and_moves_the_offset(make_settings):
    world = World(make_settings())
    world.tg.updates = [message(111, "/id", 41), message(111, "/holat", 42)]

    async def go():
        bot = world.bot()
        task = asyncio.create_task(bot.poll())
        for _ in range(100):
            await asyncio.sleep(0.01)
            if len(world.tg.messages(111)) >= 2:
                break
        task.cancel()
        return bot
    bot = asyncio.run(go())
    assert bot.offset == 43
    assert world.tg.messages(111)[0].startswith("Shu chatning ID raqami")


# ---------- alerts ----------

def test_alerts_start_at_the_end_then_report_new_rows(make_settings):
    world = World(make_settings())
    world.platform.add(status_code=500)                  # before the bot: never alerted
    world.tender.add(tur="sorov", holat_kodi=500)

    async def go():
        bot = world.bot()
        for f in bot.followers:
            await bot.follow_one(f)
        assert world.tg.sent == []
        assert bot.state.get("platform_after_id") == 1 and bot.state.get("tender_after_id") == 1

        world.platform.add(status_code=502, source="fasad", path="/api-v2/fasad/front/report",
                           method="POST", meta={"exception": "TimeoutError"})
        world.platform.add(kind="client", event="js.error", source="web",
                           message="TypeError: x is undefined")
        world.tender.add(tur="sorov", usul="POST", yol="/check", holat_kodi=500,
                         qoshimcha={"istisno": "OperationalError"})
        for f in bot.followers:
            await bot.follow_one(f)
        return bot
    bot = asyncio.run(go())

    texts = world.tg.messages(-1002)
    assert len(texts) == 3 and world.tg.messages(111) == []     # alerts go to ALERT_CHAT_IDS only
    assert "Server xatosi 502" in texts[0] and "TimeoutError" in texts[0]
    assert "Brauzer xatolari" in texts[1]
    assert "tender-v2 server xatosi 500" in texts[2]

    saved = json.load(open(world.settings.state_path))
    assert saved == {"platform_after_id": 3, "tender_after_id": 2}


def test_alert_cursor_survives_a_restart(make_settings):
    world = World(make_settings())
    world.platform.add()

    async def first():
        bot = world.bot()
        await bot.follow_one(bot.followers[0])
    asyncio.run(first())
    world.platform.add(status_code=500)                  # while the bot was down

    async def second():
        bot = world.bot()
        await bot.follow_one(bot.followers[0])
    asyncio.run(second())
    assert len(world.tg.messages(-1002)) == 1 and "Server xatosi 500" in world.tg.messages(-1002)[0]


def test_long_outage_skips_ahead(make_settings):
    world = World(make_settings(alert_catchup_rows=400))
    world.platform.add()

    async def go():
        bot = world.bot()
        await bot.follow_one(bot.followers[0])
        for _ in range(1000):
            world.platform.add(status_code=500, path=f"/p{_ % 3}")
        await bot.follow_one(bot.followers[0])
        return bot
    bot = asyncio.run(go())
    texts = world.tg.messages(-1002)
    assert any("tadan ko'p yozuv qo'shildi" in t for t in texts)
    assert bot.state.get("platform_after_id") == 1001
    tail_calls = [c for c in world.platform.calls if "after_id" in c["params"]]
    assert len(tail_calls) < 10


def test_source_down_and_back(make_settings):
    world = World(make_settings())
    world.platform.add()

    async def go():
        bot = world.bot()
        f = bot.followers[0]
        await bot.follow_one(f)
        world.platform.fail_with = 500
        for _ in range(3):
            await bot.follow_one(f)
        assert f.next_at > world.clock.t                 # backing off
        world.platform.fail_with = None
        await bot.follow_one(f)
    asyncio.run(go())
    texts = world.tg.messages(-1002)
    assert len(texts) == 2
    assert "Platforma</b> javob bermayapti" in texts[0] and "HTTP 500" in texts[0]
    assert "yana javob bermoqda" in texts[1]


def test_refused_credentials_wait_ten_minutes(make_settings):
    world = World(make_settings(platform_password="wrong-one"))
    world.platform.password = "right-one"

    async def go():
        bot = world.bot()
        f = bot.followers[0]
        await bot.follow_one(f)
        return f
    f = asyncio.run(go())
    assert f.next_at - world.clock.t == 600
    assert len(world.platform.calls) == 1
    assert "bot kira olmadi" in world.tg.messages(-1002)[0]


def test_token_never_logged(make_settings, caplog):
    caplog.set_level(logging.DEBUG)
    world = World(make_settings())

    def broken(request):
        raise httpx.ConnectError("boom", request=request)

    async def go():
        bot = world.bot()
        bot.tg = Telegram(world.settings.telegram_token, world.settings.telegram_api,
                          httpx.AsyncClient(transport=httpx.MockTransport(broken)))
        task = asyncio.create_task(bot.poll())
        await asyncio.sleep(0.05)
        task.cancel()
        await bot._send_quietly(111, "x")
    asyncio.run(go())
    assert "getUpdates failed: network error (ConnectError)" in caplog.text
    assert world.settings.telegram_token not in caplog.text


def test_user_text_is_escaped_in_replies(make_settings):
    world = World(make_settings())
    run_commands(world, "/loglar <b>")
    assert world.tg.messages(111) == ["❓ Tushunilmadi: &lt;b&gt;\n\nYordam: /yordam"]


def test_markup_telegram_refuses_goes_again_as_plain_text(make_settings):
    world = World(make_settings())

    async def go():
        await world.bot().tg.send(111, "<b>qalin</b> &amp; <x>")
    asyncio.run(go())
    assert world.tg.sent[-1]["text"] == "qalin & " and "parse_mode" not in world.tg.sent[-1]


def test_big_json_is_zipped_and_too_big_files_explained(make_settings, monkeypatch):
    import auditbot.bot as botmodule
    monkeypatch.setattr(botmodule, "FILE_LIMIT", 6000)
    world = World(make_settings())
    for i in range(200):
        world.platform.add(message="the same words again " * 3, username=f"user{i % 5}@misol.uz")
    run_commands(world, "/loglar bugun xizmat=platforma")
    names = [d["filename"] for d in world.tg.documents]
    assert names == ["loglar_20261005-0000_20261005-1400.json.zip"]
    import zipfile
    with zipfile.ZipFile(io.BytesIO(world.tg.documents[0]["content"])) as zf:
        doc = json.loads(zf.read("loglar_20261005-0000_20261005-1400.json"))
    assert doc["platforma"]["yozuvlar_soni"] == 200
    assert any("juda katta" in t and ".xlsx" in t for t in world.tg.messages(111))
