"""Audit Fase 1: Biaya Transaksi, Likuiditas ADTV, Kapasitas & Benchmark Naif.

Membandingkan peringkat 12 faktor (`core/relatif.py` beku) terhadap tiga
benchmark naif:
  1. Momentum 12-1 (return 252h tanpa 21h terakhir — hindari short-term reversal)
  2. Low-Volatility (volatilitas 20h terendah)
  3. Equal-Weight / Acak (rata-rata lintas emiten)

Semua strategi diuji dengan:
  - Biaya transaksi BEI all-in: beli 0,15% + jual 0,25% = 0,40% pulang-pergi.
  - Filter likuiditas ADTV20 (mis. >= Rp2 miliar dan >= Rp5 miliar per hari).
  - Estimasi kapasitas posisi maksimum (5% dan 10% dari median ADTV kandidat Q5).
  - Evaluasi lintas seluruh fase rebalance (0..step-1) + moving-block bootstrap CI.

Pakai:
    python research/phase1_audit.py --offline
    python research/phase1_audit.py --adtv-min 2e9 --step 20 --hold 0.6
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import IDX_BUY_FEE, IDX_SELL_FEE  # noqa: E402
from core import analysis, data, relatif  # noqa: E402
from research import evaluate as ev, portfolio as port  # noqa: E402

BUY_COST = IDX_BUY_FEE
SELL_COST = IDX_SELL_FEE


def build_benchmark_frame(ticker: str, df: pd.DataFrame, horizon: int = 20) -> pd.DataFrame | None:
    """Bangun fitur model 12 faktor + benchmark naif (Mom 12-1, Low-Vol) + ADTV20 kausal."""
    if df is None or len(df) < 65:
        return None
    ind = analysis.compute_indicators(df)
    n = len(ind)
    close = ind["close"].to_numpy(dtype=float)
    vol = ind["volume"].to_numpy(dtype=float)
    val_daily = close * vol
    adtv20 = pd.Series(val_daily).rolling(20, min_periods=10).mean().to_numpy(dtype=float)
    volat20 = ind["ret1d"].rolling(20).std().to_numpy(dtype=float) / 100.0

    # Momentum 12-1: pakai 252h-21h bila cukup panjang, fallback 126h-21h untuk riwayat 1y
    long_lb = 252 if n > 260 else min(126, max(45, n - horizon - 5))
    mom_skip = 21
    mom_12_1 = np.full(n, np.nan, dtype=float)
    for i in range(long_lb, n):
        c_prev = close[i - mom_skip]
        c_base = close[i - long_lb]
        if c_base > 0 and c_prev > 0:
            mom_12_1[i] = c_prev / c_base - 1.0

    fwd = np.full(n, np.nan, dtype=float)
    if n > horizon:
        fwd[:-horizon] = close[horizon:] / close[:-horizon] - 1.0

    warmup = min(210, max(50, long_lb))
    valid = np.arange(warmup, n)
    valid = valid[np.isfinite(fwd[valid])]
    if len(valid) == 0:
        return None

    sma20 = ind["sma20"].to_numpy(dtype=float)[valid]
    sma50 = ind["sma50"].to_numpy(dtype=float)[valid]
    sma200 = ind["sma200"].to_numpy(dtype=float)[valid]
    macd_h = ind["macd_hist"].to_numpy(dtype=float)[valid]
    rsi14 = ind["rsi14"].to_numpy(dtype=float)[valid]
    high20 = ind["high20"].to_numpy(dtype=float)[valid]
    vol_v = vol[valid]
    vol_avg20 = ind["vol_avg20"].to_numpy(dtype=float)[valid]
    ret20d = ind["ret20d"].to_numpy(dtype=float)[valid]
    ret1d = ind["ret1d"].to_numpy(dtype=float)[valid]
    atr14 = ind["atr14"].to_numpy(dtype=float)[valid]
    c_v = close[valid]

    with np.errstate(divide="ignore", invalid="ignore"):
        f_vol = np.where(np.isfinite(vol_avg20) & (vol_avg20 != 0), vol_v / vol_avg20 - 1.0, np.nan)
        out = pd.DataFrame({
            "date": ind.index[valid].normalize(),
            "ticker": ticker,
            "close": c_v,
            "adtv20": adtv20[valid],
            "fwd": fwd[valid],
            "f_close_sma50": c_v / sma50 - 1.0,
            "f_close_sma200": c_v / sma200 - 1.0,
            "f_sma20_sma50": sma20 / sma50 - 1.0,
            "f_sma50_sma200": sma50 / sma200 - 1.0,
            "f_macd": macd_h / c_v,
            "f_rsi": (rsi14 - 60.0) / 20.0,
            "f_breakout": c_v / high20 - 1.0,
            "f_volume": f_vol,
            "f_mom20": ret20d / 100.0,
            "f_mom1": ret1d / 100.0,
            "f_volat": volat20[valid],
            "f_atr": atr14 / c_v,
            "bench_mom_12_1": mom_12_1[valid],
            "bench_low_vol": -volat20[valid],
        })
    return out


def score_frozen_model(df: pd.DataFrame) -> pd.Series:
    """Hitung skor model 12 faktor memakai bobot beku `core/relatif.WEIGHTS` (per tanggal)."""
    pairs = [(k, k if k in df.columns else f"f_{k}") for k in relatif.WEIGHTS]
    cols = [col for _, col in pairs]
    z = ev.zscore_by_date(df, cols)
    w = np.array([relatif.WEIGHTS[k] for k, _ in pairs], dtype=float)
    return pd.Series(z[cols].to_numpy(dtype=float) @ w, index=df.index, dtype=float)


def evaluate_signal_with_costs(
    df: pd.DataFrame,
    score_col: str,
    *,
    quintile: float = 0.2,
    step: int = 20,
    hold: float = 0.6,
    adtv_min: float = 0.0,
) -> dict[str, Any]:
    """Uji net spread, turnover, kapasitas ADTV, & IC HAC untuk satu kolom sinyal."""
    sub = df.copy()
    if adtv_min > 0:
        sub = sub[sub["adtv20"].fillna(0.0) >= adtv_min].copy()
    sub = sub.dropna(subset=[score_col, "fwd"])
    if sub.empty:
        return {"ok": False, "n_rows": 0, "reason": "data kosong setelah filter ADTV"}

    ic_s = ev.xs_ic(sub, score_col, ret="fwd", min_n=5)
    ic_sum = ev.ic_summary(ic_s, nlags=step)

    dates = sorted(sub["date"].unique())
    if len(dates) < step + 2:
        return {"ok": False, "n_rows": len(sub), "ic": ic_sum, "reason": "jumlah tanggal terlalu pendek"}

    phase_net_alphas: list[float] = []
    phase_gross_alphas: list[float] = []
    phase_turnovers: list[float] = []
    q5_adtvs: list[float] = []

    n_phases = min(step, max(1, len(dates) // 2))
    for phase in range(n_phases):
        rebal_dates = dates[phase::step]
        if len(rebal_dates) < 2:
            continue
        prev_w: dict[str, float] = {}
        nets: list[float] = []
        grosses: list[float] = []
        turns: list[float] = []
        for d in rebal_dates:
            g = sub[sub["date"] == d].sort_values(score_col, ascending=False)
            if len(g) < 5:
                continue
            k_top = max(1, int(math.ceil(len(g) * quintile)))
            k_hold = max(k_top, int(math.ceil(len(g) * hold)))
            top_names = list(g["ticker"].iloc[:k_top])
            hold_band = set(g["ticker"].iloc[:k_hold])

            # Hysteresis: pertahankan nama lama yang masih masuk band hold
            chosen = [t for t in prev_w if t in hold_band][:k_top]
            for t in top_names:
                if len(chosen) >= k_top:
                    break
                if t not in chosen:
                    chosen.append(t)
            new_w = {t: 1.0 / len(chosen) for t in chosen} if chosen else {}

            all_names = set(prev_w) | set(new_w)
            buy_turn = sum(max(0.0, new_w.get(t, 0.0) - prev_w.get(t, 0.0)) for t in all_names)
            sell_turn = sum(max(0.0, prev_w.get(t, 0.0) - new_w.get(t, 0.0)) for t in all_names)
            cost = buy_turn * BUY_COST + sell_turn * SELL_COST

            ret_map = dict(zip(g["ticker"], g["fwd"], strict=True))
            adtv_map = dict(zip(g["ticker"], g["adtv20"], strict=True))
            port_gross = sum(w * ret_map.get(t, 0.0) for t, w in new_w.items())
            ew_gross = float(g["fwd"].mean())
            grosses.append(port_gross - ew_gross)
            nets.append((port_gross - cost) - ew_gross)
            turns.append(0.5 * (buy_turn + sell_turn))
            for t in new_w:
                av = adtv_map.get(t)
                if av is not None and math.isfinite(av) and av > 0:
                    q5_adtvs.append(float(av))
            prev_w = new_w

        if nets:
            phase_gross_alphas.append(float(np.mean(grosses)) * 100.0)
            phase_net_alphas.append(float(np.mean(nets)) * 100.0)
            phase_turnovers.append(float(np.mean(turns)) * 100.0)

    med_adtv = float(np.median(q5_adtvs)) if q5_adtvs else 0.0
    ci = port.moving_block_bootstrap_ci(phase_net_alphas, block=min(4, max(1, len(phase_net_alphas)))) if len(phase_net_alphas) >= 4 else None
    return {
        "ok": bool(phase_net_alphas),
        "n_rows": int(len(sub)),
        "n_dates": int(len(dates)),
        "n_phases": int(len(phase_net_alphas)),
        "ic": ic_sum,
        "alpha_gross_mean_pct": round(float(np.mean(phase_gross_alphas)), 3) if phase_gross_alphas else None,
        "alpha_net_mean_pct": round(float(np.mean(phase_net_alphas)), 3) if phase_net_alphas else None,
        "avg_turnover_one_way_pct": round(float(np.mean(phase_turnovers)), 1) if phase_turnovers else None,
        "phases_net_positive": int(sum(1 for x in phase_net_alphas if x > 0)),
        "ci95_alpha_net": ci,
        "median_q5_adtv_rp": round(med_adtv),
        "max_pos_capacity_5pct_adtv_rp": round(med_adtv * 0.05),
        "max_pos_capacity_10pct_adtv_rp": round(med_adtv * 0.10),
    }


def run_benchmark_comparison(
    frames: dict[str, pd.DataFrame],
    *,
    horizon: int = 20,
    step: int = 20,
    hold: float = 0.6,
    adtv_thresholds: tuple[float, ...] = (0.0, 2e9, 5e9),
) -> dict[str, Any]:
    """Bandingkan Model 12 Faktor vs Momentum 12-1, Low-Vol, & Acak pada tiap batas ADTV."""
    parts = [build_benchmark_frame(t, df, horizon=horizon) for t, df in sorted(frames.items())]
    parts = [p for p in parts if p is not None and not p.empty]
    if not parts:
        return {"ok": False, "reason": "tidak ada frame valid"}
    full = pd.concat(parts, ignore_index=True)
    full["model_12f"] = score_frozen_model(full)
    rng = np.random.default_rng(42)
    full["bench_random"] = rng.normal(0.0, 1.0, size=len(full))

    results: dict[str, Any] = {}
    for thr in adtv_thresholds:
        key = f"adtv_ge_{int(thr // 1e9)}m" if thr > 0 else "all_universe"
        results[key] = {
            "model_12f": evaluate_signal_with_costs(full, "model_12f", step=step, hold=hold, adtv_min=thr),
            "bench_mom_12_1": evaluate_signal_with_costs(full, "bench_mom_12_1", step=step, hold=hold, adtv_min=thr),
            "bench_low_vol": evaluate_signal_with_costs(full, "bench_low_vol", step=step, hold=hold, adtv_min=thr),
            "bench_random": evaluate_signal_with_costs(full, "bench_random", step=step, hold=hold, adtv_min=thr),
        }
    return {
        "ok": True,
        "n_tickers": len(parts),
        "n_rows": int(len(full)),
        "horizon": horizon,
        "step": step,
        "hold": hold,
        "benchmarks": results,
    }


def _load_cached_frames(period: str = "1y") -> dict[str, pd.DataFrame]:
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
    ap = argparse.ArgumentParser(description="Audit Fase 1: Biaya, Likuiditas ADTV & Benchmark Naif")
    ap.add_argument("--period", default="1y")
    ap.add_argument("--step", type=int, default=20)
    ap.add_argument("--hold", type=float, default=0.6)
    ap.add_argument("--offline", action="store_true", help="Gunakan hanya cache OHLCV lokal di temp/cache")
    args = ap.parse_args()
    frames = _load_cached_frames(args.period)
    if not frames:
        print(json.dumps({"ok": False, "reason": f"Cache temp/cache/*_{args.period}.csv kosong."}, indent=2))
        return
    res = run_benchmark_comparison(frames, horizon=args.step, step=args.step, hold=args.hold)
    print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
