"""Kalender bursa BEI (XIDX) + libur tambahan yang bisa dikonfigurasi.

Sumber utama: paket `exchange_calendars` (kalender XIDX). Paket itu memuat
libur nasional & cuti bersama yang sudah dirilis; untuk tahun berjalan kadang
SKB cuti bersama belum masuk rilis paket. Karena itu libur tambahan bisa diisi:

  env : MARKET_HOLIDAYS="2026-03-19,2026-03-20"
  file: market_holidays.json  →  ["2026-03-19", "2026-03-20"]
        (atau {"holidays": [...]})
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from functools import lru_cache
from zoneinfo import ZoneInfo

log = logging.getLogger("sahambuddy.marketcal")

WIB = ZoneInfo("Asia/Jakarta")
OPEN_MINUTE = 9 * 60     # 09:00 WIB
CLOSE_MINUTE = 16 * 60   # 16:00 WIB


@lru_cache(maxsize=1)
def extra_holidays() -> frozenset[dt.date]:
    """Libur di luar kalender paket (dari env / file)."""
    days: set[dt.date] = set()
    for part in (os.getenv("MARKET_HOLIDAYS", "") or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            days.add(dt.date.fromisoformat(part))
        except ValueError:
            log.warning("MARKET_HOLIDAYS: '%s' bukan tanggal ISO (YYYY-MM-DD)", part)

    path = os.getenv("MARKET_HOLIDAYS_FILE", "market_holidays.json")
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError):
            log.exception("gagal membaca %s — diabaikan", path)
            raw = []
        items = raw.get("holidays", []) if isinstance(raw, dict) else raw
        for item in items or []:
            try:
                days.add(dt.date.fromisoformat(str(item)))
            except ValueError:
                log.warning("%s: '%s' bukan tanggal ISO", path, item)
    return frozenset(days)


@lru_cache(maxsize=1)
def _calendar():
    try:
        import exchange_calendars as xcals
        return xcals.get_calendar("XIDX")
    except Exception:
        log.warning("exchange_calendars tidak tersedia — fallback Senin–Jumat "
                    "+ MARKET_HOLIDAYS")
        return None


def is_trading_day(day: dt.date) -> bool:
    if day.weekday() >= 5:
        return False
    if day in extra_holidays():
        return False
    cal = _calendar()
    if cal is None:
        return True
    try:
        return bool(cal.is_session(day.isoformat()))
    except Exception:  # tanggal di luar rentang kalender → anggap hari bursa
        return True


def last_trading_day(day: dt.date | None = None) -> dt.date:
    """Hari bursa terakhir (hari ini kalau bursa buka, kalau tidak mundur)."""
    d = day or now_wib().date()
    for _ in range(15):
        if is_trading_day(d):
            return d
        d -= dt.timedelta(days=1)
    return d


def trading_days_between(start: dt.date, end: dt.date) -> int:
    """Jumlah hari bursa pada (start, end]."""
    if end <= start:
        return 0
    n, d = 0, start
    while d < end:
        d += dt.timedelta(days=1)
        if is_trading_day(d):
            n += 1
    return n


def market_open(now: dt.datetime | None = None) -> bool:
    """Apakah sekarang jam bursa (hari bursa, 09:00–16:00 WIB)."""
    now = now or now_wib()
    if not is_trading_day(now.date()):
        return False
    minutes = now.hour * 60 + now.minute
    return OPEN_MINUTE <= minutes <= CLOSE_MINUTE


def now_wib() -> dt.datetime:
    return dt.datetime.now(WIB)


def _coerce_date(val) -> dt.date | None:
    if val is None:
        return None
    if isinstance(val, (list, tuple)):
        for item in val:
            parsed = _coerce_date(item)
            if parsed is not None:
                return parsed
        return None
    if isinstance(val, dt.datetime):
        return val.date()
    if isinstance(val, dt.date):
        return val
    if hasattr(val, "date") and callable(val.date):
        try:
            return val.date()
        except Exception:
            return None
    try:
        return dt.date.fromisoformat(str(val)[:10])
    except ValueError:
        return None


def upcoming_dividend_exdate(ticker: str,
                             today: dt.date | None = None) -> dict | None:
    """[P10] Ambil ex-date dividen mendatang dari Yahoo calendar jika ada."""
    from core import data as datamod
    import yfinance as yf

    today = today or now_wib().date()
    try:
        sym = datamod.normalize_ticker(ticker)
        tk = yf.Ticker(sym)
        cal = tk.calendar or {}
    except Exception:
        return None
    if isinstance(cal, list) and cal:
        cal = cal[0] if isinstance(cal[0], dict) else {}
    if not isinstance(cal, dict):
        return None
    ex_date = _coerce_date(cal.get("Ex-Dividend Date"))
    if ex_date is None or ex_date < today:
        return None
    amt_raw = cal.get("Dividend Rate") or cal.get("Dividend")
    amount = None
    if amt_raw is not None:
        try:
            f = float(amt_raw)
            if f > 0:
                amount = f
        except (TypeError, ValueError):
            amount = None
    return {"ex_date": ex_date, "amount": amount}
