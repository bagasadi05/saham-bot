"""Pengambilan data harga saham Indonesia dari yfinance.

Lapisan ketahanan:
  1. cache memori ber-TTL (default 5 menit) — /skor, /saran, dan /analisis
     sering memakai emiten yang sama, tak perlu menembak Yahoo berulang
  2. retry singkat (5, 10 detik) saat Yahoo error/429
  3. fallback cache disk (temp/cache/) berisi data terakhir yang sukses
  4. penanda df.attrs supaya laporan bot jujur soal kesegaran data
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

from config import DEFAULT_PERIOD, TICKER_SUFFIX

log = logging.getLogger("sahambuddy.data")

_CACHE_DIR = os.getenv("SAHAMBOT_CACHE", os.path.join("temp", "cache"))
_MAX_RETRIES = int(os.getenv("YF_RETRIES", "3"))
_TTL_SECONDS = int(os.getenv("SAHAMBOT_TTL", "300") or 0)

_mem: dict[tuple[str, str], tuple[float, pd.DataFrame]] = {}
_mem_lock = threading.Lock()
_stats = {"hit": 0, "miss": 0}


def cache_stats() -> dict:
    with _mem_lock:
        return {"hit": _stats["hit"], "miss": _stats["miss"],
                "entries": len(_mem), "ttl_seconds": _TTL_SECONDS}


def clear_cache():
    with _mem_lock:
        _mem.clear()
        _stats.update({"hit": 0, "miss": 0})


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


def _as_of(df) -> str:
    """Tanggal bar terakhir (ISO) — dipakai laporan untuk bilang 'data per'."""
    try:
        idx = df.index[-1]
        return (idx.date() if hasattr(idx, "date") else idx).isoformat()
    except Exception:
        return ""


# ------------------------------------------------------------ cache RAM -----

def _mem_get(key: tuple[str, str]):
    if _TTL_SECONDS <= 0:
        return None
    with _mem_lock:
        item = _mem.get(key)
        if item is None or time.time() - item[0] > _TTL_SECONDS:
            _stats["miss"] += 1
            return None
        _stats["hit"] += 1
        df = item[1]
    return df.copy()


def _mem_put(key: tuple[str, str], df):
    if _TTL_SECONDS <= 0:
        return
    with _mem_lock:
        _mem[key] = (time.time(), df.copy())


# ------------------------------------------------------------ cache disk ----

def _cache_path(symbol: str) -> str:
    return os.path.join(_CACHE_DIR, f"{symbol}.csv")


def _save_cache(symbol: str, df):
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        path = _cache_path(symbol)
        tmp = path + ".tmp"
        df.to_csv(tmp)
        os.replace(tmp, path)  # atomik: pembaca tak pernah lihat file setengah jadi
    except Exception:
        pass  # cache gagal menulis tak menggagalkan bot


def _load_cache(symbol: str):
    p = _cache_path(symbol)
    if not os.path.exists(p):
        return None
    try:
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        df.attrs["stale"] = True
        df.attrs["as_of"] = _as_of(df)
        df.attrs["stale_note"] = (
            f"⚠️ <i>Data LIVE gagal diambil (Yahoo 429/koneksi) — dipakai cache terakhir: "
            f"{datetime.fromtimestamp(os.path.getmtime(p), tz=timezone.utc):%d %b %H:%M} UTC. "
            "Angkanya bisa jadul. Coba lagi nanti untuk data segar.</i>\n")
        return df
    except Exception:
        return None


# -------------------------------------------------------------- validasi ----

def _validate(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Buang bar yang mustahil secara OHLC agar skor tak dihitung dari data rusak."""
    if df.empty:
        return df
    df = df[~df.index.duplicated(keep="last")].sort_index()
    bad = (df["high"] < df["low"]) | (df["close"] <= 0) | (df["volume"] < 0)
    n_bad = int(bad.sum())
    if n_bad:
        log.warning("%s: %d bar tidak valid dibuang (high<low / harga<=0)", symbol, n_bad)
        df = df[~bad]
    return df


def fetch_history(ticker: str, period: str = DEFAULT_PERIOD,
                  raw_symbol: bool = False):
    """Ambil OHLCV history. Return DataFrame (attrs STALE bisa true) atau None.

    raw_symbol=True dipakai untuk simbol non-emiten seperti indeks '^JKSE'.
    Hasil live dicache di memori selama SAHAMBOT_TTL detik.
    """
    symbol = ticker if raw_symbol else normalize_ticker(ticker)
    key = (symbol, period)
    cached_mem = _mem_get(key)
    if cached_mem is not None:
        return cached_mem

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
        if attempt < _MAX_RETRIES - 1:
            time.sleep(5 * (2 ** attempt))  # 5, 10 detik — tanpa tidur sia-sia di akhir

    if df is None or df.empty:
        cached = _load_cache(symbol)
        if cached is not None:
            return cached
        if last_exc:
            raise ValueError(f"gagal mengambil data {symbol}: {last_exc}")
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)
    needed = {"open", "high", "low", "close", "volume"}
    if not needed.issubset(df.columns):
        cached = _load_cache(symbol)
        return cached if cached is not None else None
    df = df.dropna(subset=["close"])
    df = _validate(df, symbol)
    if df.empty:
        return None
    df.attrs["stale"] = False
    df.attrs["as_of"] = _as_of(df)
    _save_cache(symbol, df)
    _mem_put(key, df)
    return df


_name_cache: dict[str, str] = {}
_name_lock = threading.Lock()


def fetch_quote_name(ticker: str) -> str:
    """Nama perusahaan (sekadar mempercantik output), dicache per emiten.

    Dipanggil oleh fetch_news tiap pemindaian; tanpa cache, satu /saran
    menambah 12 panggilan `.info` yang lambat ke Yahoo.
    """
    try:
        symbol = normalize_ticker(ticker)
    except ValueError:
        return str(ticker).strip().upper()
    with _name_lock:
        if symbol in _name_cache:
            return _name_cache[symbol]
    fallback = symbol.replace(".JK", "")
    name = None
    try:
        info = yf.Ticker(symbol).info or {}
        name = info.get("longName") or info.get("shortName")
    except Exception:
        name = None
    if not name:
        return fallback  # kegagalan tidak dicache: coba lagi lain waktu
    with _name_lock:
        _name_cache[symbol] = name
    return name
