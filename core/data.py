"""Pengambilan data harga saham Indonesia dari yfinance."""
from __future__ import annotations

import yfinance as yf

from config import TICKER_SUFFIX, DEFAULT_PERIOD


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


def fetch_history(ticker: str, period: str = DEFAULT_PERIOD):
    """Ambil OHLCV history. Return DataFrame dengan kolom standar, atau None kalau gagal."""
    symbol = normalize_ticker(ticker)
    df = yf.download(symbol, period=period, interval="1d", auto_adjust=True, progress=False)
    if df is None or df.empty:
        return None

    # yfinance bisa mengembalikan kolom MultiIndex (Price, Ticker) — ratakan
    if isinstance(df.columns, __import__("pandas").MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.rename(columns=str.lower)
    needed = {"open", "high", "low", "close", "volume"}
    if not needed.issubset(df.columns):
        return None
    df = df.dropna(subset=["close"])
    return df


def fetch_quote_name(ticker: str) -> str:
    """Nama perusahaan (sekadar mempercantik output)."""
    try:
        symbol = normalize_ticker(ticker)
        info = yf.Ticker(symbol).info
        return info.get("longName") or info.get("shortName") or symbol.replace(".JK", "")
    except Exception:
        return symbol if "." in symbol else normalize_ticker(ticker)
