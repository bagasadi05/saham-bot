# SahamBuddy — bot analisis saham Indonesia (BEI)
# Build:  docker build -t saham-bot .
# Slim (tanpa ML): docker build --build-arg SKIP_MODEL=1 -t saham-bot-slim .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SAHAMBOT_LOCAL_MODEL=/app/models/indo-fin-roberta

# argumen build: SKIP_MODEL=1 → tanpa model sentimen (lexicon fallback)
ARG SKIP_MODEL=0

WORKDIR /app

# dependensi python (torch+transformers ikut, sesuai requirements)
COPY requirements.txt download_model.py ./
RUN pip install -r requirements.txt

# unduh model sentimen (~500 MB) saat build, kecuali SKIP_MODEL=1
RUN if [ "$SKIP_MODEL" = "1" ]; then \
        echo "SKIP_MODEL=1 → tanpa unduhan model (lexicon fallback)"; \
    else \
        python download_model.py; \
    fi

# kode aplikasi
COPY config.py bot.py backtest.py backtest_real.py ./
COPY core/ ./core/

# user non-root & folder data untuk volume
RUN useradd -m -u 1000 appuser \
    && mkdir -p /app/data /app/models \
    && chown -R appuser:appuser /app
USER appuser

ENV STORE_FILE=/app/data/store.json \
    TMP_DIR=/app/data/temp

CMD ["python", "bot.py"]
