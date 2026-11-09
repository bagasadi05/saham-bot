"""Diagnostik horizon: apakah skor/momentum bekerja di 5, 10, 20, atau 60 hari?

Alasan: sinyal yang tampak "salah arah" di 20 hari bisa jadi benar di horizon
lain. Bot memakai SL 1xATR / TP 2xATR (hari–minggu), jadi 5–20 hari adalah
horizon yang relevan; 60 hari dipakai sebagai pembanding gaya investasi.

IC dihitung cross-sectional per tanggal (Spearman), lalu dirata-rata per tahun
supaya kelihatan apakah arahnya stabil atau cuma efek satu rezim pasar.

Pakai:
    python research/horizons.py                 # seluruh LQ45
    python research/horizons.py --limit 15
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import analysis, data, saran  # noqa: E402
from research.evaluate import WARMUP, ic_summary, xs_ic  # noqa: E402

HORIZONS = (5, 10, 20, 60)


def build(tickers: list[str], period: str) -> pd.DataFrame:
    rows = []
    for n, t in enumerate(tickers, 1):
        df = data.fetch_history(t, period=period)
        if df is None or len(df) < WARMUP + max(HORIZONS) + 20:
            print(f"  ! {t}: data kurang")
            continue
        ind = analysis.compute_indicators(df)
        close = ind["close"].to_numpy(float)
        fwd = {h: np.full(len(ind), np.nan) for h in HORIZONS}
        for h in HORIZONS:
            fwd[h][:-h] = close[h:] / close[:-h] - 1

        for i in range(WARMUP, len(ind) - max(HORIZONS)):
            r = ind.iloc[i]
            if pd.isna(r["sma200"]):
                continue
            sc = analysis.score_from_rows(r, ind.iloc[i - 1])
            row = {
                "date": ind.index[i].normalize(),
                "ticker": t,
                "score": float(sc["pct"]),
                "mom20": float(r["ret20d"]) / 100 if pd.notna(r["ret20d"]) else np.nan,
                "rsi": float(r["rsi14"]) if pd.notna(r["rsi14"]) else np.nan,
            }
            for h in HORIZONS:
                row[f"fwd{h}"] = float(fwd[h][i])
            rows.append(row)
        print(f"  [{n}/{len(tickers)}] {t}: {len(ind)} baris", flush=True)
        time.sleep(0.2)
    return pd.DataFrame(rows)


def table(df: pd.DataFrame) -> dict:
    out = {}
    for h in HORIZONS:
        col = f"fwd{h}"
        d = df.dropna(subset=[col]).copy()
        d["year"] = pd.to_datetime(d["date"]).dt.year
        row = {}
        for name, src, sign in [("score", "score", 1), ("mom20", "mom20", 1),
                                ("anti_mom20", "mom20", -1),
                                ("rsi", "rsi", 1), ("anti_rsi", "rsi", -1)]:
            d["_sig"] = sign * d[src]
            ic = xs_ic(d, "_sig", ret=col, min_n=8)
            s = ic_summary(ic)
            per_year = {}
            for y, g in d.groupby("year"):
                iy = xs_ic(g, "_sig", ret=col, min_n=8)
                per_year[int(y)] = round(float(iy.mean()), 4) if len(iy) else None
            row[name] = {**s, "by_year": per_year}
        out[h] = row
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="10y")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=os.path.join("temp", "research"))
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    tickers = list(dict.fromkeys(saran.LQ45))
    if args.limit:
        tickers = tickers[: args.limit]
    os.makedirs(args.out, exist_ok=True)
    cache = os.path.join(args.out, f"horizons_{args.period}_{len(tickers)}.pkl")
    if os.path.exists(cache) and not args.refresh:
        df = pd.read_pickle(cache)
    else:
        df = build(tickers, args.period)
        df.to_pickle(cache)

    res = table(df)
    with open(os.path.join(args.out, "horizons.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)

    print(f"\n{len(df):,} baris, {df['ticker'].nunique()} emiten\n")
    print(f"{'sinyal':12}" + "".join(f"{h:>10}d" for h in HORIZONS))
    for name in ["score", "mom20", "anti_mom20", "rsi", "anti_rsi"]:
        cells = []
        for h in HORIZONS:
            v = res[h][name]["mean_ic"]
            cells.append(f"{v:>11}" if v is not None else f"{'—':>11}")
        print(f"{name:12}" + "".join(cells))

    print("\nIC per tahun (horizon 20d):")
    for name in ["score", "anti_mom20", "anti_rsi"]:
        by = res[20][name]["by_year"]
        print(f"  {name:11} " + " ".join(
            f"{y}:{v:+.3f}" if v is not None else f"{y}:  —" for y, v in by.items()))


if __name__ == "__main__":
    main()
