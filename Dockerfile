# SahamBuddy — bot analisis saham Indonesia (BEI)
# Build:  docker build -t saham-bot .
# Slim (tanpa ML, hemat ~1,5 GB & RAM): docker build --build-arg SKIP_MODEL=1 -t saham-bot-slim .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Jakarta \
    BACKUP_DIR=/app/data/backups \
    SAHAMBOT_LOCAL_MODEL=/app/models/indo-fin-roberta

# argumen build: SKIP_MODEL=1 → tanpa torch/transformers (sentimen pakai lexicon)
ARG SKIP_MODEL=0

WORKDIR /app

# dependensi inti (tanpa torch/transformers)
COPY requirements.txt requirements-ml.txt download_model.py ./
RUN pip install -r requirements.txt \
    && if [ "$SKIP_MODEL" = "1" ]; then \
         echo "SKIP_MODEL=1 → tanpa torch/transformers (lexicon fallback)"; \
       else \
         pip install -r requirements-ml.txt; \
       fi

# unduh model sentimen (~500 MB) saat build, kecuali SKIP_MODEL=1
RUN if [ "$SKIP_MODEL" = "1" ]; then \
        echo "SKIP_MODEL=1 → tanpa unduhan model (lexicon fallback)"; \
    else \
        python download_model.py; \
    fi

# kode aplikasi
COPY config.py bot.py backtest.py backtest_real.py ./
COPY core/ ./core/
COPY data/validation.json ./data/validation.json
COPY data/idx_universe.json ./data/idx_universe.json
COPY tools/ ./tools/

# user non-root & folder data untuk volume
RUN useradd -m -u 1000 appuser \
    && mkdir -p /app/data /app/models \
    && chown -R appuser:appuser /app
USER appuser

ENV STORE_DB=/app/data/store.db \
    STORE_FILE=/app/data/store.json \
    TMP_DIR=/app/data/temp \
    SAHAMBOT_CACHE=/app/data/cache \
    HEARTBEAT_FILE=/app/data/heartbeat.txt

# healthcheck: heartbeat ditulis job tiap 5 menit → dianggap mati kalau >30 menit basi
HEALTHCHECK --interval=5m --timeout=10s --start-period=3m --retries=3 \
    CMD python -c "import os,sys,time; p=os.environ.get('HEARTBEAT_FILE',''); sys.exit(0 if not p or (os.path.exists(p) and time.time()-os.path.getmtime(p)<1800) else 1)"

CMD ["python", "bot.py"]
