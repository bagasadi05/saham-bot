"""Uji portofolio: apakah keunggulan skor relatif bertahan SETELAH biaya transaksi.

IC mengukur kemampuan memeringkat, bukan cuan. Selisih kuintil Q5-Q1 hanya
+0,67% per 20 hari; biaya pulang-pergi all-in 0,40% (pajak jual sudah
termasuk). Jadi pertanyaan yang
harus dijawab sebelum fitur ini dipakai: **masih untung, atau habis di biaya?**

Desain:
  - Memakai prediksi OOS yang sudah disimpan research/evaluate.py
    (temp/research/oos_predictions.pkl) — bukan model yang dilatih ulang.
  - Rebalance tiap 20 hari bursa (non-overlapping), bukan harian.
  - Pilih kuintil teratas prediksi, bobot sama, long-only.
  - Biaya IDX: beli 0,15%, jual 0,25% all-in (pajak 0,1% sudah termasuk).
    Biaya dihitung per sisi dari perubahan bobot: sum|w_baru - w_lama|.
  - Opsional hysteresis: nama lama ditahan selama masih di band tahan
    (--hold), bukan langsung dilepas saat keluar dari top-N.
  - Pembanding: rata-rata seluruh universe hari itu (equal weight), bruto.

PENTING — kenapa ada 20 fase:
  Rebalance tiap 20 hari punya 20 kemungkinan titik mulai. Menjalankan satu
  fase saja bisa menipu: pada data 2022–2026, fase 0 memberi alpha kotor
  +1,34%/periode, fase 6 hanya -0,16%. Skrip ini menjalankan SEMUA fase dan
  melaporkan sebarannya, supaya angka yang dipakai bukan hasil pemilihan fase
  yang kebetulan bagus.

Pakai:
    python research/portfolio.py
    python research/portfolio.py --quintile 0.2 --step 20
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

from statistics import NormalDist

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import IDX_BUY_FEE, IDX_SELL_FEE

BUY_COST = IDX_BUY_FEE
SELL_COST = IDX_SELL_FEE
# Pajak jual 0,10% SUDAH termasuk di SELL_COST (all-in 0,25%). Versi lama
# menambahkan SELL_TAX lagi -> 0,50%, dobel hitung yang membuat alpha net
# terlihat lebih buruk daripada kenyataan.
SELL_TAX = 0.0
ROUND_TRIP = BUY_COST + SELL_COST   # 0,40%


def summarize(series: np.ndarray) -> dict:
    n = len(series)
    if n == 0:
        return {"n": 0, "mean_pct": None, "median_pct": None, "t": None,
                "cum_pct": None, "win_rate": None}
    mean = float(series.mean())
    sd = float(series.std(ddof=1)) if n > 1 else 0.0
    cum = float(np.prod(1.0 + series) - 1.0)
    return {
        "n": n,
        "mean_pct": round(mean * 100, 3),
        "median_pct": round(float(np.median(series)) * 100, 3),
        "t": round(mean / sd * math.sqrt(n), 2) if sd > 0 else None,
        "cum_pct": round(cum * 100, 2),
        "win_rate": round(float((series > 0).mean()) * 100, 1),
    }


def sharpe_ratio(x: np.ndarray) -> float | None:
    """Sharpe per periode (bukan disetahunkan)."""
    x = np.asarray(x, dtype=float)
    if len(x) < 2:
        return None
    sd = float(x.std(ddof=1))
    return float(x.mean()) / sd if sd > 0 else None


def deflated_sharpe(nets: list[np.ndarray], n_trials: int | None = None) -> dict:
    """Deflated Sharpe Ratio (Bailey & Lopez de Prado) untuk banyak fase.

    Setiap fase dianggap satu percobaan. SR terbaik diuji terhadap SR maksimum
    yang wajar muncul karena keberuntungan (`sr0`), memakai skew/kurtosis
    sebaran return fase itu.
    """
    series = [np.asarray(x, dtype=float) for x in nets if len(x) > 2]
    srs = [s for s in (sharpe_ratio(x) for x in series) if s is not None]
    if len(srs) < 2:
        return {"n_trials": len(srs), "best_sr": None, "sr0": None, "dsr": None}
    trials = int(n_trials or len(srs))
    if trials < 2:
        trials = 2
    var = float(np.var(srs, ddof=1))
    gamma = 0.5772156649
    nd = NormalDist()
    z1 = nd.inv_cdf(1.0 - 1.0 / trials)
    z2 = nd.inv_cdf(1.0 - 1.0 / (trials * math.e))
    sr0 = math.sqrt(var) * ((1.0 - gamma) * z1 + gamma * z2)

    idx = int(np.argmax(srs))
    best = srs[idx]
    x = series[idx]
    m, sd = float(x.mean()), float(x.std(ddof=1))
    if sd <= 0:
        return {"n_trials": trials, "best_sr": round(best, 3),
                "sr0": round(sr0, 3), "dsr": None}
    skew = float(np.mean(((x - m) / sd) ** 3))
    kurt = float(np.mean(((x - m) / sd) ** 4))     # raw kurtosis (normal = 3)
    denom = math.sqrt(max(1e-12, 1.0 - skew * best + (kurt - 1.0) / 4.0 * best ** 2))
    stat = (best - sr0) * math.sqrt(len(x) - 1) / denom
    return {
        "n_trials": trials,
        "best_sr": round(best, 3),
        "sr0": round(sr0, 3),
        "dsr": round(float(nd.cdf(stat)), 3),
    }


def turnover_cost(prev: dict[str, float], cur: dict[str, float]) -> dict:
    """Biaya per sisi + turnover.

    `two_way` = sum|w_baru - w_lama| (2,0 untuk tukar seluruh isi portofolio);
    `turnover` = setengahnya (satu arah) agar sebanding dengan angka lama.
    Biaya dihitung per sisi: beli 0,15%, jual 0,25%.
    """
    tickers = set(prev) | set(cur)
    buys = sum(max(0.0, cur.get(t, 0.0) - prev.get(t, 0.0)) for t in tickers)
    sells = sum(max(0.0, prev.get(t, 0.0) - cur.get(t, 0.0)) for t in tickers)
    two_way = buys + sells
    return {
        "buy": buys, "sell": sells,
        "two_way": two_way,
        "turnover": two_way / 2.0,
        "cost": buys * BUY_COST + sells * SELL_COST,
    }


def _select(g: pd.DataFrame, prev_weights: dict[str, float],
            take_frac: float, hold_frac: float) -> list[str]:
    """Pilih nama: pemegang lama bertahan selama masih di dalam band tahan."""
    n_top = max(1, int(round(len(g) * take_frac)))
    ranked = g.sort_values("pred", ascending=False)
    pct = ranked["pred"].rank(pct=True)
    entry_pct = 1.0 - take_frac
    exit_pct = 1.0 - max(take_frac, hold_frac)

    held: list[str] = []
    entrants: list[str] = []
    for ticker, p in zip(ranked["ticker"], pct, strict=True):
        if ticker in prev_weights and p >= exit_pct:
            held.append(ticker)
        elif p >= entry_pct:
            entrants.append(ticker)
    return (held + entrants)[:n_top]


def one_phase(oos: pd.DataFrame, q: float, step: int, offset: int,
              hold: float | None = None) -> pd.DataFrame:
    """Satu fase rebalance. Return baris per periode (gross/net/bench/turnover)."""
    dates = np.array(sorted(oos["date"].unique()))
    periods = list(dates[offset::step])
    hold_frac = q if hold is None else max(q, hold)

    prev: dict[str, float] = {}
    rows = []
    for d in periods:
        g = oos[oos["date"] == d].dropna(subset=["pred", "fwd"])
        if len(g) < 5:
            continue
        names = _select(g, prev, q, hold_frac)
        if not names:
            prev = {}
            continue
        weight = 1.0 / len(names)
        cur = {t: weight for t in names}
        top = g[g["ticker"].isin(names)]
        gross = float(top["fwd"].mean())
        bench = float(g["fwd"].mean())          # universe equal weight
        tc = turnover_cost(prev, cur)
        ranked = g.sort_values("pred", ascending=False)
        bottom = float(ranked.tail(max(1, len(names)))["fwd"].mean())

        rows.append({
            "date": str(pd.Timestamp(d).date()),
            "n": int(len(g)),
            "gross": gross, "bench": bench,
            "net": gross - tc["cost"], "cost": tc["cost"],
            "buy": tc["buy"], "sell": tc["sell"],
            "turnover": tc["turnover"], "turnover_two_way": tc["two_way"],
            "bottom": bottom,
        })
        prev = cur
    return pd.DataFrame(rows)


def run(oos: pd.DataFrame, q: float, step: int,
        hold: float | None = None) -> dict:
    phases = []
    nets: list[np.ndarray] = []
    for off in range(step):
        df = one_phase(oos, q, step, off, hold)
        if df.empty:
            continue
        nets.append(df["net"].to_numpy())
        alpha_gross = (df["gross"] - df["bench"]).to_numpy()
        alpha_net = (df["net"] - df["bench"]).to_numpy()
        phases.append({
            "offset": off,
            "n_periods": int(len(df)),
            "start": df["date"].iloc[0], "end": df["date"].iloc[-1],
            "turnover_pct": round(float(df["turnover"].mean()) * 100, 1),
            "turnover_two_way_pct": round(
                float(df["turnover_two_way"].mean()) * 100, 1),
            "gross": summarize(df["gross"].to_numpy()),
            "net": summarize(df["net"].to_numpy()),
            "alpha_gross": summarize(alpha_gross),
            "alpha_net": summarize(alpha_net),
        })

    if not phases:
        return {"error": "prediksi OOS tidak cukup untuk uji portofolio"}

    g = np.array([p["alpha_gross"]["mean_pct"] for p in phases
                  if p["alpha_gross"]["mean_pct"] is not None])
    n = np.array([p["alpha_net"]["mean_pct"] for p in phases
                  if p["alpha_net"]["mean_pct"] is not None])
    ts = np.array([p["alpha_net"]["t"] for p in phases
                   if p["alpha_net"]["t"] is not None])
    # Cross-check: excess kuintil Q5 di SEMUA tanggal (bukan hanya fase terpilih)
    all_mean = oos.groupby("date")["fwd"].mean()
    q5_mean = oos[oos["decile"] == 4].groupby("date")["fwd"].mean()
    all_exc = (q5_mean - all_mean).dropna()

    return {
        "n_phases": len(phases),
        "holding_days": step,
        "quintile": q,
        "hold_quintile": q if hold is None else max(q, hold),
        "hysteresis": bool(hold is not None and hold > q),
        "cost_per_round_trip_pct": round(ROUND_TRIP * 100, 2),
        "phases": phases,
        "across_phases": {
            "alpha_gross_mean_pct": round(float(g.mean()), 3),
            "alpha_gross_median_pct": round(float(np.median(g)), 3),
            "alpha_gross_min_pct": round(float(g.min()), 3),
            "alpha_gross_max_pct": round(float(g.max()), 3),
            "alpha_net_mean_pct": round(float(n.mean()), 3),
            "alpha_net_median_pct": round(float(np.median(n)), 3),
            "alpha_net_min_pct": round(float(n.min()), 3),
            "alpha_net_max_pct": round(float(n.max()), 3),
            "phases_net_positive": int((n > 0).sum()),
            "phase_t_mean": round(float(ts.mean()), 2) if len(ts) else None,
        },
        "all_dates_q5_excess": summarize(all_exc.to_numpy()),
        "deflated_sharpe": deflated_sharpe(nets),
        "avg_turnover_pct": round(
            float(np.mean([p["turnover_pct"] for p in phases])), 1),
        "avg_turnover_two_way_pct": round(
            float(np.mean([p["turnover_two_way_pct"] for p in phases])), 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quintile", type=float, default=0.2,
                    help="fraksi universe yang dibeli (0.2 = kuintil teratas)")
    ap.add_argument("--step", type=int, default=20,
                    help="jarak rebalance dalam hari bursa (default 20)")
    ap.add_argument("--hold", type=float, default=None,
                    help="batas tahan (mis. 0.4 = tetap pegang selama masih "
                         "top 40%%); default sama dengan --quintile")
    ap.add_argument("--src", default=os.path.join("temp", "research",
                                                  "oos_predictions.pkl"))
    ap.add_argument("--out", default=os.path.join("temp", "research"))
    args = ap.parse_args()

    if not os.path.exists(args.src):
        raise SystemExit(
            f"{args.src} belum ada — jalankan research/evaluate.py dulu "
            "(sekarang menyimpan prediksi OOS).")
    oos = pd.read_pickle(args.src)
    res = run(oos, args.quintile, args.step, args.hold)
    if "error" in res:
        raise SystemExit(res["error"])

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "portfolio.json"), "w",
              encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    a = res["across_phases"]
    print(f"\nPortofolio kuintil teratas · rebalance tiap {args.step} hari bursa")
    print(f"{res['n_phases']} fase · turnover satu arah {res['avg_turnover_pct']}%"
          f" (dua arah {res['avg_turnover_two_way_pct']}%)"
          f" · biaya pulang-pergi {res['cost_per_round_trip_pct']}%")
    if res["hysteresis"]:
        print(f"hysteresis aktif: masuk top {args.quintile * 100:.0f}%, "
              f"tahan sampai top {res['hold_quintile'] * 100:.0f}%\n")
    print(f"alpha bruto antar-fase : rata-rata {a['alpha_gross_mean_pct']:+.3f}%"
          f" · median {a['alpha_gross_median_pct']:+.3f}%"
          f" · rentang [{a['alpha_gross_min_pct']:+.2f}, {a['alpha_gross_max_pct']:+.2f}]")
    print(f"alpha neto antar-fase  : rata-rata {a['alpha_net_mean_pct']:+.3f}%"
          f" · median {a['alpha_net_median_pct']:+.3f}%"
          f" · rentang [{a['alpha_net_min_pct']:+.2f}, {a['alpha_net_max_pct']:+.2f}]")
    print(f"fase dengan alpha neto > 0: {a['phases_net_positive']}/{res['n_phases']}"
          f" · rata-rata t = {a['phase_t_mean']}")
    dsr = res.get("deflated_sharpe") or {}
    if dsr.get("dsr") is not None:
        print(f"Deflated Sharpe: terbaik {dsr['best_sr']} · ambang keberuntungan "
              f"{dsr['sr0']} · DSR {dsr['dsr']} (n_trials={dsr['n_trials']})")
    print(f"cross-check (semua tanggal): excess Q5 "
          f"{res['all_dates_q5_excess']['mean_pct']:+.3f}%/periode"
          f" · median {res['all_dates_q5_excess']['median_pct']:+.3f}%")
    print(f"\nditulis: {os.path.join(args.out, 'portfolio.json')}")


if __name__ == "__main__":
    main()
