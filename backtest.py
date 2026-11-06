"""Backtest skor momentum: berapa akurat sinyal bot secara historis?

Metode:
  Untuk tiap hari di masa lalu (dengan data HANYA sampai hari itu — tidak ada
  kebocoran masa depan), hitung skor bot, lalu ukur hasil harga H hari
  ber kemudian. Rekap win-rate & rata-rata imbal hasil per kategori rating.

Pakai:
    python backtest.py BBCA [BBCA ...]          # horizon default 20 hari
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from core import analysis, data

WARMUP = 210  # MA200 + palate


def backtest(ticker: str, horizon: int = 20, period: str = "5y") -> pd.DataFrame:
    """Return tabel berisi (tanggal, rating, pct, forward_ret%)."""
    df = data.fetch_history(ticker, period=period)
    if df is None or len(df) < WARMUP + horizon + 60:
        raise ValueError(
            f"Data '{ticker}' kurang panjang untuk backtest {period} "
            f"(butuh >= {WARMUP + horizon + 60} baris).")
    ind = analysis.compute_indicators(df)

    closes = ind["close"].to_numpy()
    n = len(ind)
    rows = []
    for i in range(WARMUP, n - horizon):
        s = analysis.score_from_rows(ind.iloc[i], ind.iloc[i - 1])
        fwd = (closes[i + horizon] / closes[i] - 1) * 100
        rows.append({
            "date": ind.index[i],
            "rating": s["rating"],
            "pct": s["pct"],
            "fwd_ret": fwd,
        })
    return pd.DataFrame(rows)


def summarize(bt: pd.DataFrame) -> str:
    base = bt["fwd_ret"].mean()
    out = [f"Baseline (tanpa filter): rata² {base:+.2f}% "
           f"per {len(bt)} observasi"]
    order = ["STRONG BUY", "BUY", "NETRAL", "SELL", "STRONG SELL"]
    lines = []
    for r in order:
        grp = bt[bt["rating"] == r]
        if grp.empty:
            continue
        win = (grp["fwd_ret"] > 0).mean() * 100
        lines.append(
            f"{r:<12} n={len(grp):4d} | win-rate {win:5.1f}% | "
            f"rata² {grp['fwd_ret'].mean():+5.2f}% | median {grp['fwd_ret'].median():+5.2f}%")
    return "\n".join(lines)


def run_cli(argv: list[str]):
    """CLI: python backtest.py TICKER [TICKER...]"""
    tickers = argv or ["BBCA"]
    for t in tickers:
        t = t.upper()
        print(f"\n=== Backtest {t} (horizon 20 hari bursa, data 5 tahun) ===")
        try:
            bt = backtest(t)
        except ValueError as e:
            print(f"  {e}")
            continue
        print(summarize(bt))
        sb = bt[bt["rating"].str.startswith("STRONG BUY")]
        if not sb.empty:
            last_sb = sb.iloc[-1]
            print(f"  Sinyal STRONG BUY terakhir: {last_sb['date'].date()} "
                  f"→ hasil {last_sb['fwd_ret']:+.2f}% dalam 20 hari")


if __name__ == "__main__":
    run_cli(sys.argv[1:])
