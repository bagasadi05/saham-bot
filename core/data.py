"""Pengambilan data harga saham Indonesia dari yfinance.

Tiga lapis ketahanan (kendala Yahoo 429 di IP datacenter/proxy bersama):
  1. retry dengan rehat 5/15 detik
  2. fallback cache disk (temp/cache/) — data terakhir yang sukses
  3. penanda `df.attrs["stale"]` agar laporan bot memberi tahu user jujur
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import yfinance as yf

from config import TICKER_SUFFIX, DEFAULT_PERIOD

_CACHE_DIR = os.getenv("SAHAMBOT_CACHE", os.path.join("temp", "cache"))
_MAX_RETRIES = int(os.getenv("YF_RETRIES", "3"))


def normalize_ticker(ticker: str) -> str:
    """'bbca' -> 'BBCA.JK', 'BBCA.JK' -> 'BBCA.JK'."""
    t = ticker.strip().upper()
    if not t:
        raise ValueError("Ticker kosong")
    if "." in t:
        symbol, suffix = t.split(".", 1)
        if suffix != "JK":
            raise ValueError(
                f"Suffix '{suffix}' tidak didukung. Untuk saham BEI cukup tulis kode emiten, contoh: BBCA"
            )
    else:
        symbol = t
    if not symbol.isalnum():
        raise ValueError(f"Kode saham '{ticker}' tidak valid")
    return symbol + TICKER_SUFFIX


def _cache_path(symbol: str) -> str:
    return os.path.join(_CACHE_DIR, f"{symbol}.csv")


def _save_cache(symbol: str, df):
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        df.to_csv(_cache_path(symbol))
    except Exception:
        pass  # cache gagal menulis tak menggagalkan bot


def _load_cache(symbol: str):
    p = _cache_path(symbol)
    if not os.path.exists(p):
        return None
    try:
        import pandas as pd
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        df.attrs["stale"] = True
        df.attrs["stale_note"] = (
            f"⚠️ <i>Data LIVE gagal diambil (Yahoo 429/koneksi) — dipakai cache terakhir: "
            f"{datetime.fromtimestamp(os.path.getmtime(p), tz=timezone.utc):%d %b %H:%M} UTC. "
            "Angkanya bisa jadul. Coba lagi nanti untuk data segar.</i>\n")
        return df
    except Exception:
        return None


def fetch_history(ticker: str, period: str = DEFAULT_PERIOD):
    """Ambil OHLCV history. Return DataFrame (attrs STALE bisa true) atau None."""
    symbol = normalize_ticker(ticker)
    df = None
    last_exc = None
    for attempt in range(_MAX_RETRIES):
        try:
            df = yf.download(symbol, period=period, interval="1d",
                             auto_adjust=True, progress=False)
            if df is not None and not df.empty:
                last_exc = None
                break
        except Exception as e:  # jaringan/rate-limit
            last_exc = e
        wait = 5 * (2 ** attempt)  # 5, 10, 20 detik
        time.sleep(wait)
    if df is None or df.empty:
        cached = _load_cache(symbol)
        if cached is not None:
            return cached
        if last_exc:
            raise ValueError(f"gagal mengambil data {symbol}: {last_exc}")
        return None

    import pandas as pd
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)
    needed = {"open", "high", "low", "close", "volume"}
    if not needed.issubset(df.columns):
        cached = _load_cache(symbol)
        if cached is not None:
            return cached
        return None
    df = df.dropna(subset=["close"])
    df.attrs["stale"] = False
    _save_cache(symbol, df)
    return df


def fetch_quote_name(ticker: str) -> str:
    """Nama perusahaan (sekadar mempercantik output)."""
    try:
        symbol = normalize_ticker(ticker)
        info = yf.Ticker(symbol).info
        return info.get("longName") or info.get("shortName") or symbol.replace(".JK", "")
    except Exception:
        return symbol if "." in symbol else normalize_ticker(ticker)
