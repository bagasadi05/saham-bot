"""Konfigurasi bot — bisa dioverride lewat environment variables."""
import os

# Token bot dari @BotFather (wajib)
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# File penyimpanan watchlist & alert (JSON)
STORE_FILE = os.getenv("STORE_FILE", "store.json")

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
