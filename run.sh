#!/usr/bin/env bash
# Starter resmi SahamBuddy (tanpa Docker).
# Pakai: ./run.sh   — bot jalan di foreground, log ke layar.
#       nohup ./run.sh > bot.log 2>&1 &   (atau pakai systemd, lihat deploy.md)
#
# Fix permanen kendala #3: beberapa VPS/sandbox men-set NO_PROXY dengan IPv6
# berkurung ("[::1]") yang membuat httpx bengkak — kita set nilai yang sehat.
set -euo pipefail
cd "$(dirname "$0")"

export NO_PROXY="localhost,127.0.0.1,::1"
export no_proxy="$NO_PROXY"

# venv opsional: kalau ada .venv dipakai, kalau tidak pakai python sistem
PY="python3"
[ -x .venv/bin/python ] && PY=".venv/bin/python"

# cek token tanpa menampilkan isinya
if [ -z "${BOT_TOKEN:-}" ] && [ -f .env ]; then
    set -a; . ./.env; set +a
fi
if [ -z "${BOT_TOKEN:-}" ]; then
    echo "BOT_TOKEN belum di-set. Salin .env.example -> .env lalu isi token." >&2
    exit 1
fi

exec "$PY" bot.py
