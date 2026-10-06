# ShaffofAI audit bot — sends the platform's and tender-v2's logs to Telegram.
#
#     docker build -t shaffofai-auditbot .
#     docker compose up -d            # see docker-compose.yml and README.md
#
# Pinned to the Debian release as well as the Python version, like the other
# services: an unpinned python:3.12-slim once moved from bookworm to trixie.
FROM python:3.12-slim-trixie

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY auditbot/ ./auditbot/

# A missing module fails the build here, not on the server.
RUN python -c "import auditbot.__main__, auditbot.bot"

RUN useradd --system --uid 10001 --no-create-home auditbot \
 && mkdir -p /state \
 && chown auditbot:auditbot /state
USER auditbot

ENV STATE_PATH=/state/state.json \
    HEARTBEAT_PATH=/tmp/auditbot.heartbeat

# Healthy while the bot's loop writes its heartbeat (every 20 s).
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os,sys,time; p=os.environ['HEARTBEAT_PATH']; sys.exit(0 if time.time() - os.path.getmtime(p) < 120 else 1)"]

ENTRYPOINT ["python", "-m", "auditbot"]
