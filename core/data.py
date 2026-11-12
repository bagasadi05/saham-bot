"""Pengambilan data harga saham Indonesia dari yfinance.

Lapisan ketahanan:
  1. cache memori ber-TTL (default 5 menit) — /skor, /saran, dan /analisis
     sering memakai emiten yang sama, tak perlu menembak Yahoo berulang
  2. retry singkat (5, 10 detik) saat Yahoo error/429
  3. fallback cache disk (temp/cache/) berisi data terakhir yang sukses
  4. penanda df.attrs supaya laporan bot jujur soal kesegaran data
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

from config import DEFAULT_PERIOD, TICKER_SUFFIX
from core import data_quality

log = logging.getLogger("sahambuddy.data")

_CACHE_DIR = os.getenv("SAHAMBOT_CACHE", os.path.join("temp", "cache"))
_MAX_RETRIES = int(os.getenv("YF_RETRIES", "3"))
_TTL_SECONDS = int(os.getenv("SAHAMBOT_TTL", "300") or 0)

# yfinance dipanggil dengan auto_adjust=True: OHLC sudah disesuaikan split
# dan dividen. Penanda ini disimpan bersama cache agar data tidak tercampur.
_ADJUSTED = True

_mem: dict[tuple, tuple[float, pd.DataFrame]] = {}
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

def _mem_get(key: tuple):
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


def _mem_put(key: tuple, df):
    if _TTL_SECONDS <= 0:
        return
    with _mem_lock:
        _mem[key] = (time.time(), df.copy())


# ------------------------------------------------------------ cache disk ----

def _cache_path(symbol: str, interval: str = "1d") -> str:
    suffix = "" if interval == "1d" else f"_{interval}"
    return os.path.join(_CACHE_DIR, f"{symbol}{suffix}.csv")


def _meta_path(symbol: str, interval: str = "1d") -> str:
    """Sidecar JSON: penanda penyesuaian, penyedia, dan waktu unduh."""
    return os.path.splitext(_cache_path(symbol, interval))[0] + ".json"


def _read_meta(symbol: str, interval: str = "1d") -> dict | None:
    try:
        with open(_meta_path(symbol, interval), encoding="utf-8") as f:
            meta = json.load(f)
        return meta if isinstance(meta, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_meta(symbol: str, df, interval: str = "1d"):
    meta = {
        "symbol": symbol,
        "interval": interval,
        "provider": "yfinance",
        "adjusted": _ADJUSTED,
        "fetched_at": df.attrs.get("fetched_at")
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rows": int(len(df)),
        "last_bar": _as_of(df),
    }
    path = _meta_path(symbol, interval)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    os.replace(tmp, path)


def _save_cache(symbol: str, df, interval: str = "1d"):
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        path = _cache_path(symbol, interval)
        tmp = path + ".tmp"
        df.to_csv(tmp)
        os.replace(tmp, path)  # atomik: pembaca tak pernah lihat file setengah jadi
        try:
            _write_meta(symbol, df, interval)
        except Exception:
            log.warning("%s: sidecar cache gagal ditulis", symbol)
    except Exception:
        pass  # cache gagal menulis tak menggagalkan bot


def _load_cache(symbol: str, interval: str = "1d", adjusted: bool = _ADJUSTED):
    """Baca cache disk hanya bila penanda penyesuaiannya cocok.

    Cache lama tanpa sidecar tetap diterima (dulu data.py juga memakai
    auto_adjust=True), tetapi ditandai `provenance = cache-legacy` supaya
    ketidakpastiannya terlihat oleh pemanggil.
    """
    p = _cache_path(symbol, interval)
    if not os.path.exists(p):
        return None
    meta = _read_meta(symbol, interval)
    if meta is not None and meta.get("adjusted") not in (None, adjusted):
        log.warning(
            "%s: cache adjusted=%s tidak cocok dengan permintaan adjusted=%s — diabaikan",
            symbol, meta.get("adjusted"), adjusted)
        return None
    try:
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        df.attrs["stale"] = True
        df.attrs["as_of"] = _as_of(df)
        df.attrs["provider"] = (meta or {}).get("provider", "yfinance")
        df.attrs["adjusted"] = (meta or {}).get("adjusted", None)
        df.attrs["fetched_at"] = (meta or {}).get("fetched_at", "")
        df.attrs["provenance"] = "cache" if meta else "cache-legacy"
        df.attrs["stale_note"] = (
            f"⚠️ <i>Data LIVE gagal diambil (Yahoo 429/koneksi) — dipakai cache terakhir: "
            f"{datetime.fromtimestamp(os.path.getmtime(p), tz=timezone.utc):%d %b %H:%M} UTC. "
            "Angkanya bisa jadul. Coba lagi nanti untuk data segar.</i>\n")
        if meta is None:
            df.attrs["stale_note"] += (
                "⚠️ <i>Cache lama tanpa penanda penyesuaian — dianggap adjusted=True.</i>\n")
        df.attrs["quality"] = data_quality.check(df)
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


def _fetch_bars(ticker: str, period: str = DEFAULT_PERIOD,
                interval: str = "1d", raw_symbol: bool = False):
    """Ambil OHLCV dengan cache memori, retry, dan fallback cache disk."""
    symbol = ticker if raw_symbol else normalize_ticker(ticker)
    key = (symbol, period) if interval == "1d" else (symbol, period, interval)
    cached_mem = _mem_get(key)
    if cached_mem is not None:
        return cached_mem

    df = None
    last_exc = None
    for attempt in range(_MAX_RETRIES):
        try:
            df = yf.download(symbol, period=period, interval=interval,
                             auto_adjust=True, progress=False)
            if df is not None and not df.empty:
                last_exc = None
                break
        except Exception as e:  # jaringan/rate-limit
            last_exc = e
        if attempt < _MAX_RETRIES - 1:
            time.sleep(5 * (2 ** attempt))  # 5, 10 detik — tanpa tidur sia-sia di akhir

    if df is None or df.empty:
        cached = _load_cache(symbol, interval)
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
        cached = _load_cache(symbol, interval)
        return cached if cached is not None else None
    df = df.dropna(subset=["close"])
    df = _validate(df, symbol)
    if df.empty:
        return None
    df.attrs["stale"] = False
    df.attrs["provider"] = "yfinance"
    df.attrs["adjusted"] = _ADJUSTED
    df.attrs["interval"] = interval
    df.attrs["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    df.attrs["provenance"] = "live"
    df.attrs["quality"] = data_quality.check(df)
    df.attrs["as_of"] = _as_of(df)
    _save_cache(symbol, df, interval)
    _mem_put(key, df)
    return df


def fetch_history(ticker: str, period: str = DEFAULT_PERIOD,
                  raw_symbol: bool = False):
    """Ambil OHLCV harian. Return DataFrame (attrs STALE bisa true) atau None.

    raw_symbol=True dipakai untuk simbol non-emiten seperti indeks '^JKSE'.
    Hasil live dicache di memori selama SAHAMBOT_TTL detik.
    """
    return _fetch_bars(ticker, period=period, interval="1d", raw_symbol=raw_symbol)


def fetch_weekly(ticker: str, period: str = "2y", raw_symbol: bool = False):
    """[P9] Ambil candle mingguan (`interval=1wk`) untuk konfirmasi tren."""
    try:
        return _fetch_bars(ticker, period=period, interval="1wk", raw_symbol=raw_symbol)
    except Exception:
        return None


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
