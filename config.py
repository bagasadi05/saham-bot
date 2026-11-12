"""Konfigurasi bot — bisa dioverride lewat environment variables."""
import os
from pathlib import Path


def load_dotenv(path: str = ".env") -> None:
    """Muat file .env sederhana (KEY=VALUE) tanpa dependensi tambahan.

    Variabel yang SUDAH ada di environment tidak ditimpa — nilai eksplisit
    dari Docker/systemd selalu menang. File yang tidak ada diabaikan.
    """
    p = Path(path)
    if not p.is_file():
        return
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, val)


load_dotenv()


def clean_token(value: str | None) -> str:
    """Token kosong/placeholder dari .env.example dianggap belum di-set."""
    v = (value or "").strip()
    return "" if v.lower().startswith("tempel") else v


# Token bot dari @BotFather (wajib)
BOT_TOKEN = clean_token(os.getenv("BOT_TOKEN", ""))

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

# Screener seluruh pasar setelah bursa tutup (16:15 WIB, hari bursa).
# Matikan dengan FULL_SCAN_AUTO=off kalau VPS terlalu kecil / kuota Yahoo ketat.
FULL_SCAN_AUTO = os.getenv("FULL_SCAN_AUTO", "on").strip().lower() not in (
    "0", "off", "false", "no", "")

# Cadangan SQLite otomatis (job harian 23:30 WIB)
BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")
BACKUP_KEEP = max(1, int(os.getenv("BACKUP_KEEP", "7") or 7))
BACKUP_AUTO = os.getenv("BACKUP_AUTO", "on").strip().lower() not in (
    "0", "off", "false", "no", "")

# ------------------------------------------------------- biaya transaksi BEI
# Asumsi ritel all-in yang dipakai konsisten oleh /saran, /rencana, backtest,
# dan riset portofolio. Beli 0,15% (broker). Jual 0,25% SUDAH termasuk pajak
# penjualan 0,10% — jangan menambahkan pajak lagi di lapisan lain.
IDX_BUY_FEE = 0.0015
IDX_SELL_FEE = 0.0025
IDX_ROUND_TRIP = IDX_BUY_FEE + IDX_SELL_FEE   # 0,40%
