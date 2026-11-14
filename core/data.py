"""Pengambilan data harga saham Indonesia dari yfinance.

Lapisan ketahanan:
  1. cache memori ber-TTL (default 5 menit) — /skor, /saran, dan /analisis
     sering memakai emiten yang sama, tak perlu menembak Yahoo berulang
  2. retry singkat (5, 10 detik) saat Yahoo error/429
  3. hook fallback opsional P25 berbasis env var (EODHD / Alpha Vantage)
     khusus emiten `.JK` jika Yahoo gagal; nonaktif secara default
  4. fallback cache disk (temp/cache/) dipisah per `interval` dan `period`
  5. penanda df.attrs supaya laporan bot jujur soal sumber & kesegaran data
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

from config import DEFAULT_PERIOD, TICKER_SUFFIX
from core import data_quality, marketcal

log = logging.getLogger("sahambuddy.data")

_CACHE_DIR = os.getenv("SAHAMBOT_CACHE", os.path.join("temp", "cache"))
_MAX_RETRIES = int(os.getenv("YF_RETRIES", "3"))
_TTL_SECONDS = int(os.getenv("SAHAMBOT_TTL", "300") or 0)
_EODHD_COOLDOWN_SEC = max(0, int(os.getenv("EODHD_COOLDOWN_SECONDS", "900") or 0))

# yfinance dipanggil dengan auto_adjust=True: OHLC sudah disesuaikan split
# dan dividen. Penanda ini disimpan bersama cache agar data tidak tercampur.
_ADJUSTED = True

_PERIOD_DAYS = {
    "1d": 1, "5d": 5, "1wk": 7, "1mo": 30, "3mo": 90,
    "6mo": 180, "ytd": 180, "1y": 365, "2y": 730,
    "5y": 1825, "10y": 3650, "max": 99999,
}

_mem: dict[tuple, tuple[float, pd.DataFrame]] = {}
_mem_lock = threading.Lock()
_stats = {"hit": 0, "miss": 0}
_fallback_cooldown_until: dict[str, float] = {}


def cache_stats() -> dict:
    with _mem_lock:
        return {"hit": _stats["hit"], "miss": _stats["miss"],
                "entries": len(_mem), "ttl_seconds": _TTL_SECONDS}


def clear_cache():
    with _mem_lock:
        _mem.clear()
        _stats.update({"hit": 0, "miss": 0})
        _fallback_cooldown_until.clear()


def supports_yf_repair() -> bool:
    """True bila `scipy` terpasang sehingga `yfinance(..., repair=True)` aman dipakai."""
    return importlib.util.find_spec("scipy") is not None


def download_yf_with_safe_repair(download_fn, *args, **kwargs):
    """Panggil fungsi unduh `yfinance` dengan `repair` adaptif terhadap ketersediaan `scipy`."""
    use_repair = supports_yf_repair()
    try:
        return download_fn(*args, repair=use_repair, **kwargs)
    except ModuleNotFoundError as exc:
        if use_repair and "scipy" in str(exc).lower():
            log.warning("scipy tidak tersedia; mengulang unduh yfinance tanpa repair")
            return download_fn(*args, repair=False, **kwargs)
        raise


def _is_fallback_cooled_down(provider: str) -> bool:
    with _mem_lock:
        until = _fallback_cooldown_until.get(provider, 0.0)
        return until > time.monotonic()


def _mark_fallback_cooldown(provider: str, seconds: int) -> None:
    if seconds <= 0:
        return
    with _mem_lock:
        _fallback_cooldown_until[provider] = time.monotonic() + seconds


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
    """Tanggal bar terakhir (ISO WIB) — dipakai laporan untuk bilang 'data per'."""
    try:
        idx = df.index[-1]
        if isinstance(idx, pd.Timestamp) and idx.tzinfo is not None:
            idx = idx.tz_convert(marketcal.WIB)
        elif isinstance(idx, datetime) and idx.tzinfo is not None:
            idx = idx.astimezone(marketcal.WIB)
        return (idx.date() if hasattr(idx, "date") else idx).isoformat()
    except Exception:
        return ""


def _period_days(period: str | None) -> int | None:
    if not period:
        return None
    p = str(period).strip().lower()
    if p in _PERIOD_DAYS:
        return _PERIOD_DAYS[p]
    m = re.fullmatch(r"(\d+)(d|wk|mo|y)", p)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    mult = {"d": 1, "wk": 7, "mo": 30, "y": 365}[unit]
    return n * mult


def _period_compatible(cached_period: str | None, requested_period: str | None) -> bool:
    """True bila cache punya rentang >= permintaan (mis. cache 2y cukup untuk 1y)."""
    if not requested_period or not cached_period:
        return True
    if str(cached_period).strip().lower() == str(requested_period).strip().lower():
        return True
    c_days = _period_days(cached_period)
    r_days = _period_days(requested_period)
    if c_days is None or r_days is None:
        return False
    return c_days >= r_days


def _period_tag(period: str | None) -> str:
    if not period:
        return ""
    clean = "".join(
        ch for ch in str(period).strip().lower() if ch.isalnum() or ch in ("-", "_")
    )
    return f"_{clean}" if clean else ""


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

def _cache_path(symbol: str, interval: str = "1d", period: str | None = None) -> str:
    iv = "" if interval == "1d" else f"_{interval}"
    per = _period_tag(period)
    return os.path.join(_CACHE_DIR, f"{symbol}{iv}{per}.csv")


def _meta_path(symbol: str, interval: str = "1d", period: str | None = None) -> str:
    """Sidecar JSON: penanda penyesuaian, penyedia, periode, dan waktu unduh."""
    return os.path.splitext(_cache_path(symbol, interval, period))[0] + ".json"


def _read_meta_file(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
        return meta if isinstance(meta, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _read_meta(symbol: str, interval: str = "1d", period: str | None = None) -> dict | None:
    return _read_meta_file(_meta_path(symbol, interval, period))


def _write_meta(symbol: str, df, interval: str = "1d", period: str | None = None):
    last_bar = df.attrs.get("last_bar_date") or _as_of(df)
    meta = {
        "symbol": symbol,
        "interval": interval,
        "period": period or df.attrs.get("period") or "",
        "provider": df.attrs.get("provider", "yfinance"),
        "adjusted": df.attrs.get("adjusted", _ADJUSTED),
        "repaired_count": int(df.attrs.get("repaired_count", 0) or 0),
        "fetched_at": df.attrs.get("fetched_at")
        or marketcal.now_wib().isoformat(timespec="seconds"),
        "rows": int(len(df)),
        "last_bar": last_bar,
    }
    path = _meta_path(symbol, interval, period)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    os.replace(tmp, path)


def _drop_repaired_column(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Hitung baris bertanda 'Repaired?' dari yfinance lalu buang kolomnya."""
    rep_cols = [c for c in df.columns if str(c).strip().lower() in ("repaired?", "repaired")]
    if not rep_cols:
        return df, 0
    repaired_count = 0
    for col in rep_cols:
        ser = df[col]
        if isinstance(ser, pd.DataFrame):
            ser = ser.iloc[:, 0]
        for val in ser.fillna(False):
            if isinstance(val, bool):
                if val:
                    repaired_count += 1
            elif isinstance(val, (int, float)):
                if not pd.isna(val) and val != 0:
                    repaired_count += 1
            elif str(val).strip().lower() in ("true", "1", "yes"):
                repaired_count += 1
    df = df.drop(columns=rep_cols)
    return df, repaired_count


def _save_cache(symbol: str, df, interval: str = "1d", period: str | None = None):
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        clean, _ = _drop_repaired_column(df)
        path = _cache_path(symbol, interval, period)
        tmp = path + ".tmp"
        clean.to_csv(tmp)
        os.replace(tmp, path)  # atomik: pembaca tak pernah lihat file setengah jadi
        try:
            _write_meta(symbol, clean, interval, period)
        except Exception:
            log.warning("%s: sidecar cache gagal ditulis", symbol)
    except Exception:
        pass  # cache gagal menulis tak menggagalkan bot


def _load_cache_file(
    csv_path: str,
    meta_path: str,
    symbol: str,
    interval: str,
    adjusted: bool,
    requested_period: str | None,
) -> tuple[pd.DataFrame | None, bool]:
    """Baca satu file cache CSV+sidecar. Return (df, hard_rejected)."""
    if not os.path.exists(csv_path):
        return None, False
    meta = _read_meta_file(meta_path)
    if meta is not None and meta.get("adjusted") not in (None, adjusted):
        log.warning(
            "%s: cache adjusted=%s tidak cocok dengan permintaan adjusted=%s — diabaikan",
            symbol, meta.get("adjusted"), adjusted)
        return None, True
    if requested_period and meta is not None and meta.get("period"):
        if not _period_compatible(meta.get("period"), requested_period):
            return None, False
    try:
        df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        df, _ = _drop_repaired_column(df)
        df.attrs["stale"] = True
        as_of = _as_of(df)
        df.attrs["as_of"] = as_of
        df.attrs["last_bar_date"] = (meta or {}).get("last_bar") or as_of
        df.attrs["provider"] = (meta or {}).get("provider", "yfinance")
        df.attrs["adjusted"] = (meta or {}).get("adjusted", None)
        df.attrs["repaired_count"] = int((meta or {}).get("repaired_count", 0) or 0)
        df.attrs["fetched_at"] = (meta or {}).get("fetched_at", "")
        df.attrs["interval"] = (meta or {}).get("interval", interval)
        df.attrs["period"] = (meta or {}).get("period", requested_period or "")
        df.attrs["provenance"] = "cache" if meta else "cache-legacy"
        mtime_wib = datetime.fromtimestamp(os.path.getmtime(csv_path), tz=marketcal.WIB)
        df.attrs["stale_note"] = (
            f"⚠️ <i>Data LIVE gagal diambil (Yahoo 429/koneksi) — dipakai cache terakhir: "
            f"{mtime_wib:%d %b %H:%M} WIB. "
            "Angkanya bisa jadul. Coba lagi nanti untuk data segar.</i>\n")
        if meta is None:
            df.attrs["stale_note"] += (
                "⚠️ <i>Cache lama tanpa penanda penyesuaian — dianggap adjusted=True.</i>\n")
        df.attrs["quality"] = data_quality.check(df)
        return df, False
    except Exception:
        return None, False


def _load_cache(
    symbol: str,
    interval: str = "1d",
    adjusted: bool = _ADJUSTED,
    period: str | None = None,
):
    """Baca cache disk yang cocok dengan `interval`, `period`, dan `adjusted`.

    Urutan:
      1. Cache spesifik `(symbol, interval, period)`
      2. Cache legacy `{symbol}{_interval}.csv` bila period-nya kompatibel
         (atau tanpa sidecar)
      3. Jika `period` tidak ditentukan (mis. offline `_load_cache("^JKSE", "1d")`),
         cari cache berperiode terpanjang yang tersedia di `_CACHE_DIR`.
    """
    primary_csv = _cache_path(symbol, interval, period)
    primary_meta = _meta_path(symbol, interval, period)
    df, hard_rejected = _load_cache_file(
        primary_csv, primary_meta, symbol, interval, adjusted, period)
    if df is not None or hard_rejected:
        return df

    if period is not None:
        legacy_csv = _cache_path(symbol, interval, None)
        legacy_meta = _meta_path(symbol, interval, None)
        df, hard_rejected = _load_cache_file(
            legacy_csv, legacy_meta, symbol, interval, adjusted, period)
        if df is not None or hard_rejected:
            return df

    # Jika pemanggil tidak menyebut period (mis. regime offline), cari cache
    # perioded yang ada (pilih rentang terpanjang, mis. 10y/2y > 1mo).
    if period is None and os.path.isdir(_CACHE_DIR):
        iv = "" if interval == "1d" else f"_{interval}"
        prefix = f"{symbol}{iv}_"
        candidates: list[tuple[int, str]] = []
        try:
            for fname in os.listdir(_CACHE_DIR):
                if fname.startswith(prefix) and fname.endswith(".csv"):
                    ptag = fname[len(prefix):-4]
                    days = _period_days(ptag) or 0
                    candidates.append((days, os.path.join(_CACHE_DIR, fname)))
        except OSError:
            candidates = []
        candidates.sort(key=lambda item: -item[0])
        for _, csv_p in candidates:
            meta_p = os.path.splitext(csv_p)[0] + ".json"
            df, hard_rejected = _load_cache_file(
                csv_p, meta_p, symbol, interval, adjusted, None)
            if df is not None:
                return df
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


def _finalize_live_df(
    df: pd.DataFrame,
    symbol: str,
    period: str,
    interval: str,
    *,
    provider: str = "yfinance",
    provenance: str = "live",
    adjusted: bool = _ADJUSTED,
    repaired_count: int = 0,
) -> pd.DataFrame:
    as_of = _as_of(df)
    df.attrs["stale"] = False
    df.attrs["provider"] = provider
    df.attrs["adjusted"] = adjusted
    df.attrs["repaired_count"] = int(repaired_count)
    df.attrs["interval"] = interval
    df.attrs["period"] = period
    df.attrs["fetched_at"] = marketcal.now_wib().isoformat(timespec="seconds")
    df.attrs["provenance"] = provenance
    df.attrs["as_of"] = as_of
    df.attrs["last_bar_date"] = as_of
    df.attrs["quality"] = data_quality.check(df)
    return df


# ------------------------------------------- P25: fallback provider opt-in --

def _http_get_json(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers={"User-Agent": "SahamBuddy/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _clip_to_period(df: pd.DataFrame, period: str) -> pd.DataFrame:
    days = _period_days(period)
    if days is None or days >= 90000 or df.empty:
        return df
    cutoff = pd.Timestamp(marketcal.now_wib().date() - timedelta(days=days))
    sub = df[df.index >= cutoff]
    return sub if not sub.empty else df


def _fetch_eodhd(symbol: str, period: str, api_key: str) -> pd.DataFrame | None:
    days = _period_days(period) or 365
    start = (marketcal.now_wib().date() - timedelta(days=days + 14)).isoformat()
    qs = urllib.parse.urlencode({
        "api_token": api_key,
        "fmt": "json",
        "period": "d",
        "from": start,
    })
    url = f"https://eodhd.com/api/eod/{urllib.parse.quote(symbol)}?{qs}"
    payload = _http_get_json(url)
    if not isinstance(payload, list) or not payload:
        return None
    rows: list[dict] = []
    dates: list[pd.Timestamp] = []
    has_adj = False
    for r in payload:
        if not isinstance(r, dict) or not r.get("date"):
            continue
        try:
            o = float(r["open"])
            h = float(r["high"])
            low = float(r["low"])
            c = float(r["close"])
            v = float(r.get("volume", 0) or 0)
        except (KeyError, TypeError, ValueError):
            continue
        adj = r.get("adjusted_close")
        if adj is not None and c > 0:
            try:
                adj_f = float(adj)
                if adj_f > 0:
                    fac = adj_f / c
                    o, h, low, c = o * fac, h * fac, low * fac, adj_f
                    has_adj = True
            except (TypeError, ValueError):
                pass
        dates.append(pd.Timestamp(r["date"]))
        rows.append({"open": o, "high": h, "low": low, "close": c, "volume": v})
    if not rows:
        return None
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(dates, name="Date"))
    df = _validate(df, symbol)
    df = _clip_to_period(df, period)
    if df.empty:
        return None
    return _finalize_live_df(
        df, symbol, period, "1d",
        provider="eodhd", provenance="fallback", adjusted=has_adj, repaired_count=0,
    )


def _fetch_alphavantage(symbol: str, period: str, api_key: str) -> pd.DataFrame | None:
    days = _period_days(period) or 365
    outputsize = "full" if days > 120 else "compact"
    qs = urllib.parse.urlencode({
        "function": "TIME_SERIES_DAILY_ADJUSTED",
        "symbol": symbol,
        "outputsize": outputsize,
        "apikey": api_key,
    })
    url = f"https://www.alphavantage.co/query?{qs}"
    payload = _http_get_json(url)
    if not isinstance(payload, dict):
        return None
    series = payload.get("Time Series (Daily)")
    if not isinstance(series, dict) or not series:
        return None
    rows: list[dict] = []
    dates: list[pd.Timestamp] = []
    has_adj = False
    for day_str, bar in series.items():
        if not isinstance(bar, dict):
            continue
        try:
            o = float(bar["1. open"])
            h = float(bar["2. high"])
            low = float(bar["3. low"])
            c = float(bar["4. close"])
            vol_raw = bar.get("6. volume", bar.get("5. volume", 0))
            v = float(vol_raw or 0)
        except (KeyError, TypeError, ValueError):
            continue
        adj = bar.get("5. adjusted close")
        if adj is not None and c > 0:
            try:
                adj_f = float(adj)
                if adj_f > 0:
                    fac = adj_f / c
                    o, h, low, c = o * fac, h * fac, low * fac, adj_f
                    has_adj = True
            except (TypeError, ValueError):
                pass
        dates.append(pd.Timestamp(day_str))
        rows.append({"open": o, "high": h, "low": low, "close": c, "volume": v})
    if not rows:
        return None
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(dates, name="Date"))
    df = _validate(df, symbol)
    df = _clip_to_period(df, period)
    if df.empty:
        return None
    return _finalize_live_df(
        df, symbol, period, "1d",
        provider="alphavantage", provenance="fallback", adjusted=has_adj, repaired_count=0,
    )


def _fetch_fallback_provider(symbol: str, period: str, interval: str) -> pd.DataFrame | None:
    """Hook fallback OHLCV opsional (P25) khusus emiten `.JK`, strictly opt-in via env."""
    if interval != "1d" or not symbol.endswith(".JK"):
        return None
    eodhd_key = os.getenv("EODHD_API_KEY", "").strip()
    if eodhd_key and not _is_fallback_cooled_down("eodhd"):
        try:
            df = _fetch_eodhd(symbol, period, eodhd_key)
            if df is not None and not df.empty:
                return df
        except urllib.error.HTTPError as exc:
            if exc.code in (402, 429) and _EODHD_COOLDOWN_SEC > 0:
                _mark_fallback_cooldown("eodhd", _EODHD_COOLDOWN_SEC)
                log.warning(
                    "%s: EODHD kuota/rate-limit (HTTP %s) — jeda %ds sebelum fallback EODHD lagi",
                    symbol, exc.code, _EODHD_COOLDOWN_SEC,
                )
            else:
                log.warning("%s: fallback EODHD gagal (%s)", symbol, exc)
        except Exception as exc:
            log.warning("%s: fallback EODHD gagal (%s)", symbol, exc)
    av_key = os.getenv("ALPHAVANTAGE_API_KEY", "").strip()
    if av_key:
        try:
            df = _fetch_alphavantage(symbol, period, av_key)
            if df is not None and not df.empty:
                return df
        except Exception as exc:
            log.warning("%s: fallback Alpha Vantage gagal (%s)", symbol, exc)
    return None


def _fetch_bars(ticker: str, period: str = DEFAULT_PERIOD,
                interval: str = "1d", raw_symbol: bool = False):
    """Ambil OHLCV dengan cache memori, retry, fallback opsional, dan fallback cache disk."""
    symbol = ticker if raw_symbol else normalize_ticker(ticker)
    key = (symbol, period) if interval == "1d" else (symbol, period, interval)
    cached_mem = _mem_get(key)
    if cached_mem is not None:
        return cached_mem

    df = None
    last_exc = None
    for attempt in range(_MAX_RETRIES):
        try:
            df = download_yf_with_safe_repair(
                yf.download, symbol, period=period, interval=interval,
                auto_adjust=True, progress=False,
            )
            if df is not None and not df.empty:
                last_exc = None
                break
        except Exception as e:  # jaringan/rate-limit
            last_exc = e
        if attempt < _MAX_RETRIES - 1:
            time.sleep(5 * (2 ** attempt))  # 5, 10 detik — tanpa tidur sia-sia di akhir

    if df is None or df.empty:
        fb = _fetch_fallback_provider(symbol, period, interval)
        if fb is not None:
            _save_cache(symbol, fb, interval, period=period)
            _mem_put(key, fb)
            return fb
        cached = _load_cache(symbol, interval, period=period)
        if cached is not None:
            return cached
        if last_exc:
            raise ValueError(f"gagal mengambil data {symbol}: {last_exc}")
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)
    df, repaired_count = _drop_repaired_column(df)
    needed = {"open", "high", "low", "close", "volume"}
    if not needed.issubset(df.columns):
        fb = _fetch_fallback_provider(symbol, period, interval)
        if fb is not None:
            _save_cache(symbol, fb, interval, period=period)
            _mem_put(key, fb)
            return fb
        cached = _load_cache(symbol, interval, period=period)
        return cached if cached is not None else None
    df = df.dropna(subset=["close"])
    df = _validate(df, symbol)
    if df.empty:
        return None
    df = _finalize_live_df(
        df, symbol, period, interval,
        provider="yfinance", provenance="live",
        adjusted=_ADJUSTED, repaired_count=repaired_count,
    )
    _save_cache(symbol, df, interval, period=period)
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


def _pick_name(source) -> str | None:
    """Ambil longName/shortName/displayName dari mapping atau objek metadata."""
    if source is None:
        return None
    for key in ("longName", "shortName", "displayName", "name"):
        val = None
        if isinstance(source, dict) or hasattr(source, "get"):
            try:
                val = source.get(key)
            except Exception:
                val = None
        if not val:
            val = getattr(source, key, None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def fetch_quote_name(ticker: str) -> str:
    """Nama perusahaan (sekadar mempercantik output), dicache per emiten.

    Mengutamakan `.fast_info` / `.history_metadata` yang lebih ringan daripada
    `.info` (yang sering memicu Yahoo 429), baru fallback ke `.info` bila perlu.
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
        tk = yf.Ticker(symbol)
        try:
            name = _pick_name(getattr(tk, "fast_info", None))
        except Exception:
            name = None
        if not name:
            try:
                meta = (tk.get_history_metadata()
                        if hasattr(tk, "get_history_metadata")
                        else getattr(tk, "history_metadata", None))
                name = _pick_name(meta)
            except Exception:
                name = None
        if not name:
            try:
                name = _pick_name(getattr(tk, "info", None))
            except Exception:
                name = None
    except Exception:
        name = None
    if not name:
        return fallback  # kegagalan tidak dicache: coba lagi lain waktu
    with _name_lock:
        _name_cache[symbol] = name
    return name
