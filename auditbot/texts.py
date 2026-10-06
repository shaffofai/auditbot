"""What the bot says. Uzbek (Latin); HTML parse mode, so every literal < > &
is escaped here."""

HELP = """🤖 <b>ShaffofAI audit boti</b>

<b>/loglar</b> &lt;oraliq&gt; [filtrlar] — loglarni Excel va JSON fayl qilib yuboradi.

<b>Oraliq</b> (Toshkent vaqti):
  bugun · kecha · 3soat · 30daqiqa · 2kun · 1hafta
  2026-10-01 — butun kun (01.10.2026 yoki 01.10 ham bo'ladi)
  01.10 09:00 18:00 — o'sha kun, 09:00 dan 18:00 gacha
  01.10 09:00 02.10 12:00 — bir paytdan boshqasigacha
  09:00 12:30 — bugun, ikki vaqt orasida

<b>Filtrlar</b>:
  xatolar — faqat xatolar
  tanalar — so'rov va javob tanalari ham (birinchi {bodies} ta yozuv)
  tur=sorov | kirish | brauzer | log | qolda
  daraja=xato | ogohlantirish | info
  foydalanuvchi=email@misol.uz
  yol=/api-v2/fasad · holat=500
  xizmat=fasad | gateway | … | platforma | tender-v2

<b>Misollar</b>:
  /loglar bugun xatolar
  /loglar kecha foydalanuvchi=ali@misol.uz
  /loglar 2soat xizmat=tender-v2
  /loglar 01.10 09:00 18:00 tur=kirish

/holat — bot va manbalar holati
/id — shu chatning ID raqami

Ogohlantirishlar o'zi keladi: server xatolari (5xx), backend xatolari, ko'p
muvaffaqiyatsiz kirishlar, brauzer xatolari, tashlangan yoki o'chirilgan audit
yozuvlari, tender-v2 operator amallari."""

ASK_RANGE = "Qaysi oraliq? Tugmani bosing yoki yozing, masalan: <code>/loglar 3soat xatolar</code>"

QUICK = [
    [("Oxirgi 1 soat", "1soat"), ("Bugun", "bugun")],
    [("Kecha", "kecha"), ("Bugungi xatolar", "bugun xatolar")],
]

PREPARING = "⏳ Tayyorlanmoqda: {label}…"
BUSY = "⏳ Oldingi so'rov hali tayyorlanmoqda, biroz kuting."
NOTHING = "Bu oraliqda hech narsa topilmadi."
TOO_BIG = "⚠️ {name} juda katta ({mb} MB, Telegram chegarasi 50 MB) — oraliqni qisqartiring."
SOURCE_FAILED = "⚠️ {source}: o'qib bo'lmadi ({why})"
TENDER_OFF = "tender-v2 o'chirilgan (JURNAL_LOGIN / JURNAL_PAROL berilmagan)"
NOT_UNDERSTOOD = "❓ {why}\n\nYordam: /yordam"
CHAT_ID = "Shu chatning ID raqami: <code>{chat_id}</code>"

ALERT_SOURCE_DOWN = "⚠️ <b>{source}</b> javob bermayapti ({why}). Ogohlantirishlar to'xtab turibdi."
ALERT_SOURCE_BACK = "✅ <b>{source}</b> yana javob bermoqda."
ALERT_AUTH = ("⚠️ <b>{source}</b>: bot kira olmadi — login yoki parol rad etildi. "
              "Bot {minutes} daqiqa kutib qayta urinadi (har urinish kirish cheklovchisiga yoziladi).")
ALERT_SKIPPED = ("⚠️ <b>{source}</b>: bot to'xtab turgan paytda {rows} tadan ko'p yozuv qo'shildi — "
                 "ular uchun ogohlantirish yuborilmadi. Ko'rish uchun: /loglar")
