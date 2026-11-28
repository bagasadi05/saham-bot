"""Evaluasi Fase 2: Backtest Historis PowerScore, Kalibrasi Verdict Ceklist & Uji Veto.

Membandingkan:
  1. Skor Momentum 9 Sinyal (`core/analysis.score_from_rows`)
  2. Peringkat Relatif 12 Faktor Tervalidasi (`core/relatif.WEIGHTS`)
  3. Kalibrasi Verdict Ceklist (`LAYAK` / `TUNGGU` / `HINDARI`) — hit-rate,
     rata-rata return 20 hari, max drawdown intra-horizon, dan efektivitas Veto.

Catatan kejujuran: fundamental & berita historis point-in-time tidak tersedia
di cache OHLCV, sehingga uji historis ini memvalidasi komponen teknikal,
peringkat relatif, likuiditas ADTV, dan aturan veto harga/likuiditas.

Pakai:
    python research/evaluate_powerscore.py --offline
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import analysis, cek, data, relatif  # noqa: E402
from research import evaluate as ev  # noqa: E402


def _horizon_max_dd(closes: np.ndarray, lows: np.ndarray, start: int, horizon: int) -> float | None:
    entry = float(closes[start])
    if entry <= 0 or start + horizon >= len(lows):
        return None
    min_low = float(np.nanmin(lows[start + 1 : start + 1 + horizon]))
    if not np.isfinite(min_low):
        return None
    return round((min_low / entry - 1.0) * 100.0, 2)


def evaluate_powerscore_and_cek(
    frames: dict[str, pd.DataFrame],
    *,
    horizon: int = 20,
    stride: int = 5,
) -> dict[str, Any]:
    """Evaluasi historis perbandingan skor teknikal vs peringkat 12F & kalibrasi verdict /cek."""
    records: list[dict[str, Any]] = []

    for sym, df in sorted(frames.items()):
        if df is None or len(df) < 65:
            continue
        ind = analysis.compute_indicators(df)
        n = len(ind)
        closes = ind["close"].to_numpy(dtype=float)
        lows = ind["low"].to_numpy(dtype=float)
        vols = ind["volume"].to_numpy(dtype=float)
        adtv20 = pd.Series(closes * vols).rolling(20, min_periods=10).mean().to_numpy(dtype=float)
        volat20 = ind["ret1d"].rolling(20).std().to_numpy(dtype=float) / 100.0

        warmup = min(210, max(45, n // 3))
        recs = ind.to_dict("records")

        for i in range(warmup, n - horizon, max(1, int(stride))):
            c_now = closes[i]
            c_fwd = closes[i + horizon]
            if not (np.isfinite(c_now) and np.isfinite(c_fwd) and c_now > 0):
                continue
            fwd_pct = (c_fwd / c_now - 1.0) * 100.0
            dd_pct = _horizon_max_dd(closes, lows, i, horizon)
            sc = analysis.score_from_rows(recs[i], recs[i - 1])
            snap_i = {
                "close": c_now,
                "rsi": recs[i].get("rsi14"),
                "sma20": recs[i].get("sma20"),
                "sma50": recs[i].get("sma50"),
                "sma200": recs[i].get("sma200"),
                "ret1d": recs[i].get("ret1d"),
                "ret20d": recs[i].get("ret20d"),
                "atr14": recs[i].get("atr14"),
            }
            sub_ind = ind.iloc[max(0, i - 60) : i + 1]
            res_i = {
                "ticker": sym,
                "ind": sub_ind,
                "score": sc,
                "snap": snap_i,
                "weekly": {"direction": "naik" if (snap_i["sma50"] and c_now > snap_i["sma50"]) else "turun"},
            }
            val20 = float(adtv20[i]) if np.isfinite(adtv20[i]) else None
            chk = cek.build(
                res=res_i, fund=None, fund_raw=None, nsent=None,
                bandar_row=None, agenda=None, agenda_days=None,
                peers=None, value_avg20=val20,
            )
            sma50 = recs[i].get("sma50") or np.nan
            sma200 = recs[i].get("sma200") or np.nan
            sma20 = recs[i].get("sma20") or np.nan
            high20 = recs[i].get("high20") or np.nan
            vol_avg20 = recs[i].get("vol_avg20") or np.nan
            records.append({
                "date": ind.index[i].normalize(),
                "ticker": sym,
                "fwd": fwd_pct / 100.0,
                "fwd_pct": fwd_pct,
                "max_dd_pct": dd_pct,
                "tech_9sig": float(sc["pct"]),
                "verdict": chk.get("verdict") or "TIDAK CUKUP DATA",
                "vetoed": bool(chk.get("vetoes")),
                "f_close_sma50": (c_now / sma50 - 1.0) if np.isfinite(sma50) and sma50 > 0 else np.nan,
                "f_close_sma200": (c_now / sma200 - 1.0) if np.isfinite(sma200) and sma200 > 0 else np.nan,
                "f_sma20_sma50": (sma20 / sma50 - 1.0) if np.isfinite(sma20) and np.isfinite(sma50) and sma50 > 0 else np.nan,
                "f_sma50_sma200": (sma50 / sma200 - 1.0) if np.isfinite(sma50) and np.isfinite(sma200) and sma200 > 0 else np.nan,
                "f_macd": (recs[i].get("macd_hist") or np.nan) / c_now,
                "f_rsi": ((recs[i].get("rsi14") or np.nan) - 60.0) / 20.0,
                "f_breakout": (c_now / high20 - 1.0) if np.isfinite(high20) and high20 > 0 else np.nan,
                "f_volume": (vols[i] / vol_avg20 - 1.0) if np.isfinite(vol_avg20) and vol_avg20 > 0 else np.nan,
                "f_mom20": (recs[i].get("ret20d") or np.nan) / 100.0,
                "f_mom1": (recs[i].get("ret1d") or np.nan) / 100.0,
                "f_volat": volat20[i] if np.isfinite(volat20[i]) else np.nan,
                "f_atr": (recs[i].get("atr14") or np.nan) / c_now,
            })

    if not records:
        return {"ok": False, "reason": "tidak ada baris evaluasi valid"}

    df_ev = pd.DataFrame(records)
    pairs = [(k, f"f_{k}") for k in relatif.WEIGHTS]
    cols = [col for _, col in pairs]
    z = ev.zscore_by_date(df_ev, cols)
    w = np.array([relatif.WEIGHTS[k] for k, _ in pairs], dtype=float)
    df_ev["rel_12f"] = z[cols].to_numpy(dtype=float) @ w
    # Hybrid: 50% peringkat relatif tervalidasi + 50% skor 9-sinyal
    df_ev["rel_pct_scaled"] = (df_ev.groupby("date")["rel_12f"].rank(pct=True) - 0.5) * 200.0
    df_ev["hybrid_50_50"] = 0.5 * df_ev["rel_pct_scaled"] + 0.5 * df_ev["tech_9sig"]

    ic_comp = {
        "tech_9sig_momentum": ev.ic_summary(ev.xs_ic(df_ev, "tech_9sig", ret="fwd", min_n=5), nlags=max(1, horizon // stride)),
        "relatif_12f_validated": ev.ic_summary(ev.xs_ic(df_ev, "rel_12f", ret="fwd", min_n=5), nlags=max(1, horizon // stride)),
        "hybrid_rel_plus_9sig": ev.ic_summary(ev.xs_ic(df_ev, "hybrid_50_50", ret="fwd", min_n=5), nlags=max(1, horizon // stride)),
    }

    verdicts: dict[str, Any] = {}
    for v_name in ("LAYAK", "TUNGGU", "HINDARI", "TIDAK CUKUP DATA"):
        g = df_ev[df_ev["verdict"] == v_name]
        if g.empty:
            continue
        fwds = g["fwd_pct"].to_numpy(dtype=float)
        dds = g["max_dd_pct"].dropna().to_numpy(dtype=float)
        lo, hi = ev.bootstrap_ci(fwds / 100.0) if len(fwds) >= 5 else (None, None)
        verdicts[v_name] = {
            "n": int(len(g)),
            "hit_rate_pct": round(float((fwds > 0).mean() * 100.0), 1),
            "mean_fwd_pct": round(float(fwds.mean()), 2),
            "median_fwd_pct": round(float(np.median(fwds)), 2),
            "mean_max_dd_pct": round(float(dds.mean()), 2) if len(dds) else None,
            "ci95_mean_pct": [lo, hi],
        }

    veto_audit: dict[str, Any] = {}
    for flag, label in ((True, "vetoed"), (False, "non_vetoed")):
        g = df_ev[df_ev["vetoed"] == flag]
        if g.empty:
            veto_audit[label] = {"n": 0}
            continue
        fwds = g["fwd_pct"].to_numpy(dtype=float)
        dds = g["max_dd_pct"].dropna().to_numpy(dtype=float)
        veto_audit[label] = {
            "n": int(len(g)),
            "hit_rate_pct": round(float((fwds > 0).mean() * 100.0), 1),
            "mean_fwd_pct": round(float(fwds.mean()), 2),
            "mean_max_dd_pct": round(float(dds.mean()), 2) if len(dds) else None,
        }

    return {
        "ok": True,
        "n_rows": int(len(df_ev)),
        "n_tickers": int(df_ev["ticker"].nunique()),
        "horizon": horizon,
        "ic_comparison": ic_comp,
        "verdict_calibration": verdicts,
        "veto_audit": veto_audit,
    }


def _load_cached(period: str = "1y") -> dict[str, pd.DataFrame]:
    cache_dir = Path("temp/cache")
    if not cache_dir.is_dir():
        return {}
    out: dict[str, pd.DataFrame] = {}
    for fp in sorted(cache_dir.glob(f"*_{period}.csv")):
        stem = fp.stem[: -(len(period) + 1)]
        if stem.startswith("^"):
            continue
        df = data.fetch_cached_history(stem, period)
        if df is not None and len(df) >= 65:
            out[stem.replace(".JK", "")] = df
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Audit Fase 2: PowerScore & Kalibrasi Verdict Ceklist")
    ap.add_argument("--period", default="1y")
    ap.add_argument("--horizon", type=int, default=20)
    ap.add_argument("--stride", type=int, default=5)
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    frames = _load_cached(args.period)
    if not frames:
        print(json.dumps({"ok": False, "reason": "Cache kosong."}, indent=2))
        return
    res = evaluate_powerscore_and_cek(frames, horizon=args.horizon, stride=args.stride)
    print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
