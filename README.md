# ShaffofAI audit bot

A Telegram bot that sends the platform's audit trail to a chat:

- **on request** — `/loglar <range> [filters]` answers with a short summary and
  two files: an Excel workbook (one sheet per source) and a JSON file;
- **as it happens** — alerts for server errors, backend errors, repeated failed
  sign-ins, browser script errors, lost or deleted audit rows and tender-v2
  operator commands.

It reads two sources, both over HTTP on the platform's Docker network:

| Source | Table | API | Account |
|---|---|---|---|
| Platform (nine backends + site) | `audit_logs` | gateway `GET /admin/gateway/audit` (HANDOFF §18.4) | the bot's **own** government account with `is_admin` |
| tender-v2 | `sorov_jurnali` | `GET /jurnal` (HANDOFF §18.7) | tender-v2's read-only `JURNAL_LOGIN` / `JURNAL_PAROL` |

It only reads (GET). It never opens a port: it asks Telegram for new messages
(long polling). Its only connection outside the server is to `api.telegram.org`.

## Using it (Uzbek)

| Command | What it does |
|---|---|
| `/loglar bugun` | today, from 00:00 Tashkent time until now |
| `/loglar kecha` | all of yesterday |
| `/loglar 3soat` · `30daqiqa` · `2kun` · `1hafta` | the last 3 hours, 30 minutes, … |
| `/loglar 2026-10-01` (or `01.10.2026`, `01.10`) | that whole day |
| `/loglar 01.10 09:00 18:00` | that day, 09:00–18:00 |
| `/loglar 01.10 09:00 02.10 12:00` | from one moment to another |
| `/loglar 09:00 12:30` | today, between two times |
| `/holat` | the bot, both sources and the alert cursors |
| `/yordam` | help, with buttons for the common ranges |
| `/id` | the chat's id (answered in any chat — that is how a chat is added) |

Filters, after the range, in any order:

| Filter | Platform | tender-v2 |
|---|---|---|
| `xatolar` | `level=error` | `daraja=error` |
| `tanalar` | also request/response bodies, for the first `EXPORT_MAX_BODIES` rows | the same |
| `tur=sorov\|kirish\|brauzer\|log\|qolda` | `kind=request\|auth\|client\|log\|manual` | `tur=sorov\|kirish\|—\|log\|amal` |
| `daraja=xato\|ogohlantirish\|info` | `level` | `daraja` |
| `foydalanuvchi=<login>` | `username` | `login` |
| `yol=/api-v2/fasad` | path prefix | path prefix |
| `holat=500` | `status` | `holat_kodi` |
| `xizmat=fasad\|gateway\|…` | `source` (tender-v2 left out) | — |
| `xizmat=platforma` / `xizmat=tender-v2` | only that source | |

Example: `/loglar kecha xatolar`, `/loglar 2soat foydalanuvchi=ali@misol.uz tanalar`.

A range is `[start, end)`. A range that has not ended stops at "now", and the
summary then says that rows of requests still running may arrive later. Each
source gives at most `EXPORT_MAX_ROWS` rows: past that the export keeps the
**newest** rows of the range and the summary says it was cut — narrow the range. A JSON file over Telegram's 50 MB limit is sent
zipped; an Excel file over it is not sent and the bot says so.

Times in messages and in the Excel file are Tashkent time; the JSON file keeps
the APIs' own UTC timestamps.

## Alerts

Sent to `ALERT_CHAT_IDS`. The bot follows both tables by id (tail mode — never
by time, which would miss requests still running). On its first start it begins
at the newest row; afterwards it remembers where it stopped (`/state` volume),
so a restart neither repeats nor skips alerts. After a long outage (more than
`ALERT_CATCHUP_ROWS` new rows) it skips ahead and says so.

| Alert | Platform | tender-v2 |
|---|---|---|
| 🔴 Server error | a request answered 5xx (per source, method, path, status) | a request answered 5xx |
| 🔴 Backend error | a log record at level error | a log line at level error |
| 🟠 Failed sign-ins | one account failing `ALERT_FAILED_LOGINS` times in `ALERT_WINDOW_SECONDS` | one login refused as often |
| 🟡 Browser errors | `js.error`, `js.unhandledrejection`, `console.error` — one message per batch | — |
| ⚠️ Rows lost | `audit.dropped`, `log.dropped`, `client.dropped` | `jurnal_tashlandi` |
| 🗑 Rows deleted | `audit.delete`, `audit.purge` | — |
| ℹ️ Operator command | — | `amal` rows (`--requeue`, `--qayta-och`) |

The same alert within the window is counted, not repeated; the next one says how
many were held back. At most `ALERT_MAX_PER_MINUTE` messages leave per minute.
A source that stops answering is reported after three failed looks, and again
when it is back. Refused credentials stop the bot from asking for ten minutes
(every refused try is a failed sign-in for the throttle).

Failed sign-ins are not grouped by address: until `router/realip.conf` is filled
in, every visitor has the company edge's address (HANDOFF §5.3).

## Before you start: what this sends where

- **Logs leave the server.** Exports and alerts go to Telegram's servers and stay
  in the chat: e-mails, usernames, IP addresses, paths, error texts — and, with
  `tanalar`, request and response bodies (already redacted of passwords and
  tokens by the audit trail). Choose the chats, and who is in them, accordingly.
- **Everyone in an allowed group can export.** `ALLOWED_CHAT_IDS` lists chats, not
  people. Prefer a private chat or a small private group.
- **The bot's platform account is a full admin.** There is no read-only audit
  role (HANDOFF §18.9). The bot only reads, but its password is an admin
  password: keep `.env` at `chmod 600` and give the account to the bot only. To
  replace the password: sign in to the dashboard as `audit-bot`, change it on the
  Account tab, put the new one in `.env`, `sudo docker compose up -d --no-build`.
- Never paste the token or a password into a chat, an issue or a commit.

## Deploy (the owner runs these on the server)

**1. Create the Telegram bot.** In Telegram, talk to `@BotFather`: `/newbot`,
choose a name and a username. Keep the token it gives you for step 4. Leave
group privacy on (the default): in a group the bot then sees only commands.

**2. Create the bot's platform account.** Generate a password on the server:

```bash
openssl rand -hex 24
```

In the dashboard (Users tab): **Create user** — name `Audit bot`, username
`audit-bot`, that password. Then press **Make admin** on its row. Keep the
password for step 4.

**3. tender-v2 journal credentials** (skip if `JURNAL_LOGIN` / `JURNAL_PAROL` are
already set in `~/shaffofai-tender-v2/.env`; this is tender-v2's DEPLOY.md §6):

```bash
cd ~/shaffofai-tender-v2
grep -q '^JURNAL_LOGIN=' .env || printf '\nJURNAL_LOGIN=\nJURNAL_PAROL=\n' >> .env
sed -i -e "s/^JURNAL_LOGIN=$/JURNAL_LOGIN=jurnal/" -e "s/^JURNAL_PAROL=$/JURNAL_PAROL=$(openssl rand -hex 24)/" .env
sudo docker compose up -d --no-build api
```

**4. Install the bot.** First the repository must be on GitHub (a new private
repository, e.g. `shaffofai/auditbot`) with a green CI run, so the image exists.
Like the other private images, pulling it needs the server's `docker login ghcr.io`.

```bash
cd ~ && git clone git@github.com:shaffofai/auditbot.git shaffofai-auditbot
cd ~/shaffofai-auditbot
cp .env.example .env && chmod 600 .env
# Copy the journal pair from tender-v2 without showing it:
sed -i -e "s/^JURNAL_LOGIN=.*/JURNAL_LOGIN=$(sed -n 's/^JURNAL_LOGIN=//p' ~/shaffofai-tender-v2/.env)/" \
       -e "s/^JURNAL_PAROL=.*/JURNAL_PAROL=$(sed -n 's/^JURNAL_PAROL=//p' ~/shaffofai-tender-v2/.env)/" .env
nano .env        # TELEGRAM_BOT_TOKEN, PLATFORM_PASSWORD; leave ALLOWED_CHAT_IDS empty for now
sudo docker compose pull && sudo docker compose up -d --no-build
sudo docker logs --tail 20 shaffofai-v2-auditbot
```

The log should say `setup mode, only /id is answered` and `auditbot running`.

**5. Allow the chats.** Send `/id` to the bot in a private chat, and in the group
(add the bot to the group first). Put the ids in `.env` — `ALLOWED_CHAT_IDS=` for
everyone who may export, `ALERT_CHAT_IDS=` for the chats that get alerts (empty =
all allowed chats) — and restart:

```bash
cd ~/shaffofai-auditbot && nano .env && sudo docker compose up -d --no-build
```

**6. Check.** In the chat: `/holat` (both sources ✅), then `/loglar 1soat`.

```bash
sudo docker ps --filter name=shaffofai-v2-auditbot --format '{{.Status}}'      # (healthy)
sudo docker logs --tail 30 shaffofai-v2-auditbot
```

If Telegram cannot be reached (`getUpdates failed: network error`), test from
the container:

```bash
sudo docker exec shaffofai-v2-auditbot python -c "import httpx; print(httpx.get('https://api.telegram.org', timeout=10).status_code)"
```

A server that needs a proxy for the internet: `HTTPS_PROXY=` in `.env` (not
`HTTP_PROXY` — that would send the calls to the gateway through it too).

**Update / roll back.** CI publishes `sha-<commit>` and `latest` on every push to
`main`; its run summary prints the exact commands:

```bash
cd ~/shaffofai-auditbot && git pull --ff-only
sudo sed -i 's/^TAG=.*/TAG=sha-<commit>/' .env
sudo docker compose pull && sudo docker compose up -d --no-build
```

**Stop:** `sudo docker compose stop`. Nothing else depends on the bot.

## Settings

All in `.env` (see `.env.example` for every one, with its default).

| Setting | Default | |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | required |
| `ALLOWED_CHAT_IDS` | empty = setup mode | chats that may use the bot |
| `ALERT_CHAT_IDS` | = allowed | chats that get alerts; must be allowed |
| `PLATFORM_URL` | `http://gateway:8000` | |
| `PLATFORM_USERNAME` / `PLATFORM_PASSWORD` | — | required; an admin account |
| `TENDER_URL` | `http://tender-v2:8000` | |
| `JURNAL_LOGIN` / `JURNAL_PAROL` | empty = tender-v2 off | |
| `ALERT_INTERVAL_SECONDS` | 30 | 0 turns alerts off |
| `ALERT_TENDER_INTERVAL_SECONDS` | 60 | tender-v2 journals each look as a row (~1 400 a day) |
| `ALERT_WINDOW_SECONDS` | 600 | de-duplication and failed-sign-in window |
| `ALERT_FAILED_LOGINS` | 5 | |
| `ALERT_BROWSER_ERRORS` | 1 | 0 = browser errors only in exports |
| `ALERT_MAX_PER_MINUTE` | 15 | |
| `ALERT_CATCHUP_ROWS` | 5000 | |
| `EXPORT_MAX_ROWS` | 20000 | per source |
| `EXPORT_MAX_BODIES` | 300 | rows whose bodies `tanalar` fetches |
| `TZ_OFFSET_HOURS` | 5 | |

What the bot costs the platform: the gateway folds the bot's reads into one
audit row per 10 minutes; every request still checks the password (bcrypt), so
an export of 20 000 rows is about 100 requests. tender-v2 records each read as
a journal row.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q tests
```

`tests/fakes.py` stands in for the gateway's audit API, tender-v2's `/jurnal`
and Telegram, following their contracts (browse/tail paging, unknown `/jurnal`
parameters refused, 401 on a wrong password). Code layout:

| File | |
|---|---|
| `auditbot/config.py` | settings from the environment |
| `auditbot/telegram.py` | the Telegram Bot API calls (plain httpx, long polling) |
| `auditbot/sources.py` | the two APIs: range (browse), tail, head, one row |
| `auditbot/query.py` | `/loglar` arguments → range and filters |
| `auditbot/export.py` | summary text, Excel, JSON |
| `auditbot/alerts.py` | which new rows make a message; de-duplication; per-minute budget |
| `auditbot/bot.py` | commands, exports, the alert loop, state, heartbeat |
