"""Konfigurasi bot — bisa dioverride lewat environment variables."""
import os

# Token bot dari @BotFather (wajib)
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Database SQLite untuk watchlist/alert/paper trading
STORE_DB = os.getenv("STORE_DB", "store.db")

# File JSON lama — hanya dipakai untuk migrasi otomatis sekali
STORE_FILE = os.getenv("STORE_FILE", "store.json")

# Folder chart sementara (file dihapus setelah dikirim ke Telegram)
TMP_DIR = os.getenv("TMP_DIR", "temp")

# File heartbeat (dibuat tiap 5 menit) — dipakai HEALTHCHECK Docker. Kosong = nonaktif
HEARTBEAT_FILE = os.getenv("HEARTBEAT_FILE", "heartbeat.txt")

# Sumber data: yfinance dengan suffix .JK (Bursa Efek Indonesia)
TICKER_SUFFIX = ".JK"

# Rentang data & interval untuk analisis teknikal
DEFAULT_PERIOD = "1y"

# Ukuran chart (inch)
CHART_SIZE = (12, 10)
CHART_DPI = 110

# Jam bursa Indonesia (WIB, UTC+7) — scanner aktif di jam ini
MARKET_OPEN = (9, 0)    # 09:00 WIB
MARKET_CLOSE = (16, 0)  # 16:00 WIB

# Scanner alert dijalankan tiap X menit
ALERT_SCAN_MINUTES = 30
