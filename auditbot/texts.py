"""What the bot says. Uzbek (Latin); HTML parse mode, so every literal < > &
is escaped here."""

HELP = """🤖 <b>ShaffofAI audit boti</b>

<b>/logs</b> &lt;range&gt; [filters] — loglarni Excel va JSON fayl qilib yuboradi.

<b>Oraliq</b> (Toshkent vaqti):
  today · yesterday · 3h · 30m · 2d · 1w
  2026-10-01 — butun kun (01.10.2026 yoki 01.10 ham bo'ladi)
  01.10 09:00 18:00 — o'sha kun, 09:00 dan 18:00 gacha
  01.10 09:00 02.10 12:00 — bir paytdan boshqasigacha
  09:00 12:30 — bugun, ikki vaqt orasida

<b>Filtrlar</b>:
  errors — faqat xatolar
  bodies — so'rov va javob tanalari ham (birinchi {bodies} ta yozuv)
  kind=request | auth | browser | log | manual
  level=error | warning | info
  user=email@misol.uz
  path=/api-v2/fasad · status=500
  service=fasad | gateway | … | platform | tender-v2

<b>Misollar</b>:
  /logs today errors
  /logs yesterday user=ali@misol.uz
  /logs 2h service=tender-v2
  /logs 01.10 09:00 18:00 kind=auth

/status — bot va manbalar holati
/id — shu chatning ID raqami

Kim foydalana olishini administratorlar dashboard'dagi <b>Telegram bot</b>
bo'limida belgilaydi.

Ogohlantirishlar o'zi keladi: server xatolari (5xx), backend xatolari, ko'p
muvaffaqiyatsiz kirishlar, brauzer xatolari, tashlangan yoki o'chirilgan audit
yozuvlari, tender-v2 operator amallari."""

ASK_RANGE = "Qaysi oraliq? Tugmani bosing yoki yozing, masalan: <code>/logs 3h errors</code>"

QUICK = [
    [("Oxirgi 1 soat", "1h"), ("Bugun", "today")],
    [("Kecha", "yesterday"), ("Bugungi xatolar", "today errors")],
]

PREPARING = "⏳ Tayyorlanmoqda: {label}…"
BUSY = "⏳ Oldingi so'rov hali tayyorlanmoqda, biroz kuting."
NOTHING = "Bu oraliqda hech narsa topilmadi."
TOO_BIG = "⚠️ {name} juda katta ({mb} MB, Telegram chegarasi 50 MB) — oraliqni qisqartiring."
SOURCE_FAILED = "⚠️ {source}: o'qib bo'lmadi ({why})"
TENDER_OFF = "tender-v2 o'chirilgan (JURNAL_LOGIN / JURNAL_PAROL berilmagan)"
NOT_UNDERSTOOD = "❓ {why}\n\nYordam: /help"
CHAT_ID = "Shu chatning ID raqami: <code>{chat_id}</code>"
PENDING = ("⏳ Botdan foydalanish so'rovingiz administratorlarga yuborildi. "
           "Tasdiqlangach /help va /logs ishlaydi.\nChat ID: <code>{chat_id}</code>")
UNAVAILABLE = ("⚠️ Bot hozir platformaga ulana olmayapti, so'rovingiz yozilmadi. "
               "Birozdan keyin qayta urinib ko'ring.")
CHAT_KINDS = {"private": "shaxsiy chat", "group": "guruh", "supergroup": "guruh",
              "channel": "kanal", "unknown": "chat"}

ALERT_SOURCE_DOWN = "⚠️ <b>{source}</b> javob bermayapti ({why}). Ogohlantirishlar to'xtab turibdi."
ALERT_SOURCE_BACK = "✅ <b>{source}</b> yana javob bermoqda."
ALERT_AUTH = ("⚠️ <b>{source}</b>: bot kira olmadi — login yoki parol rad etildi. "
              "Bot {minutes} daqiqa kutib qayta urinadi (har urinish kirish cheklovchisiga yoziladi).")
ALERT_SKIPPED = ("⚠️ <b>{source}</b>: bot to'xtab turgan paytda {rows} tadan ko'p yozuv qo'shildi — "
                 "ular uchun ogohlantirish yuborilmadi. Ko'rish uchun: /logs")
