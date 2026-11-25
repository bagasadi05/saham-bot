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

# Pakai bundel sertifikat CA sistem bila tersedia (mencegah SSL error di VPS/proxy)
if [ -f /etc/ssl/certs/ca-certificates.crt ]; then
    export SSL_CERT_FILE="${SSL_CERT_FILE:-/etc/ssl/certs/ca-certificates.crt}"
    export REQUESTS_CA_BUNDLE="${REQUESTS_CA_BUNDLE:-/etc/ssl/certs/ca-certificates.crt}"
    export CURL_CA_BUNDLE="${CURL_CA_BUNDLE:-/etc/ssl/certs/ca-certificates.crt}"
fi

# venv opsional: kalau ada .venv dipakai, kalau tidak pakai python sistem
PY="python3"
[ -x .venv/bin/python ] && PY=".venv/bin/python"

# cek token tanpa mengeksekusi .env sebagai skrip shell
if [ -z "${BOT_TOKEN:-}" ] && [ -f .env ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%$'\r'}"
        case "$line" in
            ""|\#*) continue ;;
            *=*)
                key="${line%%=*}"
                val="${line#*=}"
                key="${key#"${key%%[![:space:]]*}"}"
                key="${key%"${key##*[![:space:]]}"}"
                case "$key" in
                    ''|*[!A-Za-z0-9_]*|[0-9]*) continue ;;
                esac
                val="${val#"${val%%[![:space:]]*}"}"
                val="${val%"${val##*[![:space:]]}"}"
                case "$val" in
                    \"*\") val="${val#\"}"; val="${val%\"}" ;;
                    \'*\') val="${val#\'}"; val="${val%\'}" ;;
                esac
                export "$key=$val"
                ;;
        esac
    done < .env
fi
if [ -z "${BOT_TOKEN:-}" ]; then
    echo "BOT_TOKEN belum di-set. Salin .env.example -> .env lalu isi token." >&2
    exit 1
fi

exec "$PY" bot.py
