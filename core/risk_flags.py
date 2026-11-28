"""Deteksi Saham Gorengan / Risiko Manipulasi BEI (`RISIKO_SPEKULATIF`) — Fase 3c.

Mendeteksi pola spekulatif ekstrem secara deterministik dari OHLCV + fundamental
tanpa mengarang data:
  1. Lonjakan volume abnormal (z-score volume > 3,0 terhadap 20-60 hari).
  2. Rentetan ARA / ARB berulang dalam 10 hari bursa terakhir (pakai ambang
     dinamis `core/pricing.ara_pct` & `arb_pct` BEI 2026).
  3. Harga melesat tajam (> 35% dalam 20 hari) tanpa dukungan fundamental
     (laba negatif / PER ekstrem > 60x).

Output: dict `{speculative_risk, level, score, flags, veto_reason}` yang dapat
langsung masuk peringatan/veto `core/cek.py`.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from core import pricing


def _sf(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def detect_speculative_risk(
    ind: pd.DataFrame | None,
    fund_raw: dict[str, Any] | None = None,
    *,
    board: str | None = None,
    window: int = 10,
) -> dict[str, Any]:
    """Audit risiko saham gorengan / manipulasi dari bar OHLCV & fundamental."""
    if ind is None or not isinstance(ind, pd.DataFrame) or len(ind) < 25:
        return {
            "available": False,
            "speculative_risk": False,
            "level": "TIDAK TERSEDIA",
            "score": 0,
            "vol_zscore": None,
            "ara_arb_streak": 0,
            "ret20d_pct": None,
            "flags": [],
            "veto_reason": None,
        }

    close_s = pd.to_numeric(ind["close"], errors="coerce").dropna()
    vol_s = pd.to_numeric(ind["volume"], errors="coerce").dropna() if "volume" in ind.columns else pd.Series(dtype=float)
    if len(close_s) < 25:
        return {
            "available": False,
            "speculative_risk": False,
            "level": "TIDAK TERSEDIA",
            "score": 0,
            "vol_zscore": None,
            "ara_arb_streak": 0,
            "ret20d_pct": None,
            "flags": [],
            "veto_reason": None,
        }

    flags: list[str] = []
    score = 0

    # 1. Lonjakan volume abnormal (z-score volume > 3.0)
    vol_z: float | None = None
    if len(vol_s) >= 25:
        hist_v = vol_s.iloc[-min(61, len(vol_s)) : -1]
        mu = float(hist_v.mean())
        sd = float(hist_v.std(ddof=1)) if len(hist_v) > 1 else 0.0
        cur_v = float(vol_s.iloc[-1])
        if sd > 0:
            vol_z = round((cur_v - mu) / sd, 2)
            if vol_z > 3.0:
                score += 25
                flags.append(f"Lonjakan volume abnormal (z-score {vol_z:.1f}σ > 3,0σ)")

    # 2. Rentetan ARA / ARB dalam `window` hari bursa terakhir
    n_chk = min(window, len(close_s) - 1)
    ara_hits = 0
    arb_hits = 0
    is_fca = "pemantauan" in str(board or "").lower()
    tail_c = close_s.iloc[-(n_chk + 1) :].to_numpy(dtype=float)
    for idx in range(1, len(tail_c)):
        prev_c, cur_c = tail_c[idx - 1], tail_c[idx]
        if prev_c <= 0:
            continue
        pct = (cur_c / prev_c) - 1.0
        if is_fca:
            ara_lim = 0.10
            arb_lim = 0.10
        else:
            ara_raw = pricing.auto_reject_pct(prev_c, None, allow_expired=True)
            ara_lim = (ara_raw / 100.0) if ara_raw else (0.35 if prev_c <= 200 else (0.25 if prev_c <= 5000 else 0.20))
            arb_raw = pricing.arb_pct(prev_c, board="regular")
            arb_lim = (arb_raw / 100.0) if (arb_raw and arb_raw > 1.0) else (arb_raw or 0.07)
        if ara_lim and pct >= ara_lim * 0.90:
            ara_hits += 1
        elif arb_lim and pct <= -arb_lim * 0.90:
            arb_hits += 1
    ara_arb_total = ara_hits + arb_hits
    if ara_arb_total >= 3:
        score += 40
        flags.append(f"Rentetan ekstrem ARA/ARB {ara_arb_total}x dalam {n_chk} hari (ARA {ara_hits}x, ARB {arb_hits}x)")
    elif ara_arb_total == 2:
        score += 20
        flags.append(f"Gejolak ARA/ARB {ara_arb_total}x dalam {n_chk} hari terakhir")

    # 3. Harga melesat tajam (>35% dalam 20h) tanpa dukungan fundamental
    ret20 = round((float(close_s.iloc[-1]) / float(close_s.iloc[-21]) - 1.0) * 100.0, 1) if len(close_s) >= 21 and float(close_s.iloc[-21]) > 0 else None
    per = _sf((fund_raw or {}).get("per"))
    roe = _sf((fund_raw or {}).get("roe"))
    weak_fund = (per is not None and (per < 0 or per > 60.0)) or (roe is not None and roe < 0)
    if ret20 is not None and ret20 >= 35.0:
        if weak_fund:
            score += 35
            flags.append(f"Reli ekstrem +{ret20:.0f}% (20h) tanpa dukungan fundamental (PER/ROE lemah)")
        else:
            score += 15
            flags.append(f"Reli parabolik +{ret20:.0f}% dalam 20 hari (rawan koreksi tajam)")

    spec_risk = score >= 50
    if score >= 50:
        level = "RISIKO_SPEKULATIF"
        veto_reason = f"RISIKO_SPEKULATIF ({'; '.join(flags[:2])})"
    elif score >= 25:
        level = "WASPADA"
        veto_reason = None
    else:
        level = "NORMAL"
        veto_reason = None

    return {
        "available": True,
        "speculative_risk": spec_risk,
        "level": level,
        "score": int(min(100, score)),
        "vol_zscore": vol_z,
        "ara_arb_streak": ara_arb_total,
        "ret20d_pct": ret20,
        "flags": flags,
        "veto_reason": veto_reason,
    }
