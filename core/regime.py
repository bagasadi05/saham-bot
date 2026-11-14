"""Filter rezim pasar IHSG (^JKSE) berbasis tren kausal SMA50 vs SMA200.

Berdasarkan ablasi riset (`research/experiments.py`) atas prediksi OOS 20 hari:
saat `SMA50 < SMA200` pada IHSG (`^JKSE`), memangkas eksposur saham ke 50%
menurunkan max drawdown historis secara material sambil menjaga ketahanan Sharpe
tanpa membunuh alpha seperti keluar ke kas 100%.

Aturan kejujuran data (`AGENTS.md`):
- Indikator dihitung murni kausal (`bar <= t`).
- Jika data `^JKSE` tidak tersedia atau kurang dari 200 hari bursa, status
  dilaporkan jujur sebagai `"NA"` (`"tidak tersedia"`) dengan `exposure_scale=1.0`,
  tidak diisi nol dan tidak dikarang.
"""
from __future__ import annotations

import math
import threading
import time

import pandas as pd

from core import data

FAST_MA = 50
SLOW_MA = 200
MIN_BARS = SLOW_MA
BULL_SCALE = 1.0
DEFENSIVE_SCALE = 0.5

REGIME_MODES = ("none", "sma200_cash", "sma200_half", "sma50_200_half", "vol_target_14")

_REGIME_TTL = 6 * 3600
_FAIL_TTL = 5 * 60
_regime_cache: dict = {"ts": 0.0, "val": None}
_regime_lock = threading.Lock()


def clear_cache() -> None:
    """Bersihkan cache memori rezim IHSG (dipakai fixture tes)."""
    with _regime_lock:
        _regime_cache.update({"ts": 0.0, "val": None})


def ihsg_indicators(df: pd.DataFrame | None) -> pd.DataFrame:
    """Hitung indikator tren/volatilitas IHSG secara kausal (bar <= t)."""
    if df is None or df.empty or "close" not in df:
        return pd.DataFrame()
    dates = pd.to_datetime(df.index, errors="coerce").normalize()
    close = pd.to_numeric(df["close"], errors="coerce")
    out = pd.DataFrame({"date": dates, "close": close})
    out = out.dropna(subset=["date", "close"])
    out = out[out["close"] > 0]
    if out.empty:
        return pd.DataFrame()
    out = out.sort_values("date").drop_duplicates("date", keep="last").set_index("date")
    out["sma50"] = out["close"].rolling(FAST_MA).mean()
    out["sma200"] = out["close"].rolling(SLOW_MA).mean()
    out["vol20"] = out["close"].pct_change().rolling(20).std() * math.sqrt(252)
    return out


def exposure_from_indicators(
    close: float | None,
    sma50: float | None,
    sma200: float | None,
    vol20: float | None = None,
    mode: str = "sma50_200_half",
) -> float:
    """Tentukan fraksi modal saham `[0..1]` dari nilai indikator pada tanggal t."""
    if mode == "none":
        return BULL_SCALE
    if mode not in REGIME_MODES:
        raise ValueError(f"mode regime tidak dikenal: {mode}")
    if mode == "sma200_cash":
        if sma200 is None or pd.isna(sma200) or close is None or pd.isna(close):
            return BULL_SCALE
        return BULL_SCALE if float(close) >= float(sma200) else 0.0
    if mode == "sma200_half":
        if sma200 is None or pd.isna(sma200) or close is None or pd.isna(close):
            return BULL_SCALE
        return BULL_SCALE if float(close) >= float(sma200) else DEFENSIVE_SCALE
    if mode == "sma50_200_half":
        if sma200 is None or pd.isna(sma200) or sma50 is None or pd.isna(sma50):
            return BULL_SCALE
        return BULL_SCALE if float(sma50) >= float(sma200) else DEFENSIVE_SCALE
    if vol20 is not None and pd.notna(vol20) and float(vol20) > 0:
        return min(BULL_SCALE, 0.14 / float(vol20))
    return BULL_SCALE


def effective_scale(reg: dict | None) -> float:
    """Faktor pengali eksposur/risiko dari dict rezim (`1.0` jika normal/NA)."""
    if not reg or reg.get("status") != "DEFENSIVE":
        return BULL_SCALE
    try:
        val = float(reg.get("exposure_scale", DEFENSIVE_SCALE))
    except (TypeError, ValueError):
        return DEFENSIVE_SCALE
    if math.isnan(val):
        return DEFENSIVE_SCALE
    return min(BULL_SCALE, max(0.1, val))


def evaluate_ihsg(df: pd.DataFrame | None) -> dict:
    """Nilai rezim IHSG terkini (`BULL`, `DEFENSIVE`, atau `NA` yang jujur)."""
    stale = bool(getattr(df, "attrs", {}).get("stale", False)) if df is not None else False
    ind = ihsg_indicators(df)
    if ind.empty:
        return {
            "status": "NA",
            "label": "tidak tersedia",
            "exposure_scale": BULL_SCALE,
            "close": None,
            "sma50": None,
            "sma200": None,
            "gap_pct": None,
            "as_of": None,
            "bars": 0,
            "stale": stale,
            "reason": "data ^JKSE tidak tersedia",
        }

    last = ind.iloc[-1]
    as_of = ind.index[-1].strftime("%Y-%m-%d")
    raw_close = float(last["close"])
    close = round(raw_close, 2)
    raw_sma50 = float(last["sma50"]) if pd.notna(last["sma50"]) else None
    raw_sma200 = float(last["sma200"]) if pd.notna(last["sma200"]) else None
    sma50 = round(raw_sma50, 2) if raw_sma50 is not None else None
    sma200 = round(raw_sma200, 2) if raw_sma200 is not None else None
    n_bars = int(len(ind))

    if n_bars < MIN_BARS or raw_sma50 is None or raw_sma200 is None or raw_sma200 <= 0:
        return {
            "status": "NA",
            "label": "tidak tersedia",
            "exposure_scale": BULL_SCALE,
            "close": close,
            "sma50": sma50,
            "sma200": None,
            "gap_pct": None,
            "as_of": as_of,
            "bars": n_bars,
            "stale": stale,
            "reason": f"data ^JKSE belum cukup ({n_bars}/{MIN_BARS} hari bursa)",
        }

    scale = exposure_from_indicators(
        raw_close, raw_sma50, raw_sma200, mode="sma50_200_half"
    )
    gap_pct = round((raw_sma50 / raw_sma200 - 1.0) * 100.0, 2)
    if scale < BULL_SCALE:
        return {
            "status": "DEFENSIVE",
            "label": "defensif (SMA50 < SMA200)",
            "exposure_scale": scale,
            "close": close,
            "sma50": sma50,
            "sma200": sma200,
            "gap_pct": gap_pct,
            "as_of": as_of,
            "bars": n_bars,
            "stale": stale,
            "reason": "IHSG defensif (SMA50 < SMA200) — porsi & risiko dipangkas ke 50%",
        }
    return {
        "status": "BULL",
        "label": "konstruktif (SMA50 >= SMA200)",
        "exposure_scale": BULL_SCALE,
        "close": close,
        "sma50": sma50,
        "sma200": sma200,
        "gap_pct": gap_pct,
        "as_of": as_of,
        "bars": n_bars,
        "stale": stale,
        "reason": "IHSG fase tren naik (SMA50 >= SMA200) — eksposur normal 100%",
    }


def fetch_ihsg_regime(period: str = "2y", *, offline_only: bool = False) -> dict:
    """Ambil rezim `^JKSE` terbaru dengan cache 6 jam (retry fail 5 menit)."""
    if offline_only:
        df = data._load_cache("^JKSE", "1d")
        return evaluate_ihsg(df)

    now = time.time()
    with _regime_lock:
        cached = _regime_cache["val"]
        age = now - _regime_cache["ts"]
        if cached is not None:
            ttl = _FAIL_TTL if cached.get("status") == "NA" else _REGIME_TTL
            if age < ttl:
                return dict(cached)

    df = None
    for _ in range(2):
        try:
            df = data.fetch_history("^JKSE", period, raw_symbol=True)
            if df is not None and len(df) >= MIN_BARS:
                break
        except Exception:
            df = None

    res = evaluate_ihsg(df)
    with _regime_lock:
        _regime_cache["ts"] = time.time()
        _regime_cache["val"] = dict(res)
    return res
