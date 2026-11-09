"""Membuat chart analisis teknikal: harga + MA + rentang 20 hari, volume, RSI, MACD.

Dibuat lewat `matplotlib.figure.Figure` (bukan pyplot) supaya aman dipanggil
dari beberapa thread sekaligus — bot memakai asyncio.to_thread per permintaan
dan pyplot menyimpan state global yang bisa saling menimpa.
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")  # tanpa GUI / display
import matplotlib.dates as mdates
import pandas as pd
from matplotlib.figure import Figure

from config import CHART_SIZE, CHART_DPI

BLUE = "#049CDB"
GOLD, GREEN, RED = "#ffb74d", "#81c784", "#e57373"
BG, GRID = "#101922", "#26364a"


def _style(fig, axes):
    fig.patch.set_facecolor(BG)
    for ax in axes:
        ax.set_facecolor(BG)
        ax.tick_params(colors="white", labelsize=9)
        for s in ax.spines.values():
            s.set_color("#2a3a4a")
        ax.grid(color=GRID, lw=.4, alpha=.6)


def build_chart(symbol: str, ind: pd.DataFrame, score_pct: float, out_path: str) -> str:
    df = ind.tail(180)  # sekitar 6 bulan bursa
    x = df.index

    fig = Figure(figsize=CHART_SIZE, dpi=CHART_DPI)
    ax1, ax2, ax3, ax4 = fig.subplots(
        4, 1, sharex=True,
        gridspec_kw={"height_ratios": [3, 1, 1, 1], "hspace": 0.12})
    _style(fig, (ax1, ax2, ax3, ax4))

    # Panel 1 — harga, MA, rentang 20 hari
    ax1.plot(x, df["close"], color=BLUE, lw=1.6, label="Close")
    ax1.plot(x, df["sma20"], color=GOLD, lw=1, label="MA20")
    ax1.plot(x, df["sma50"], color=GREEN, lw=1, label="MA50")
    ax1.plot(x, df["sma200"], color=RED, lw=1, label="MA200")
    if "bb_upper" in df.columns and not df["bb_upper"].isna().all():
        ax1.plot(x, df["bb_upper"], color="#4dd0e1", lw=.7, ls=":", label="BB atas")
        ax1.plot(x, df["bb_lower"], color="#4dd0e1", lw=.7, ls=":")
        ax1.fill_between(x, df["bb_lower"], df["bb_upper"], color="#16344a", alpha=.35)
    ax1.fill_between(x, df["low20"], df["high20"], color="#2b3a4d", alpha=.5,
                     label="Rentang 20 hari")
    ax1.set_title(f"{symbol} — skor momentum {score_pct:+.0f}%",
                  color="white", fontsize=12, loc="left")
    ax1.legend(loc="upper left", frameon=False, fontsize=9, labelcolor="white", ncols=5)
    ax1.set_ylabel("Harga (Rp)", color="white", fontsize=9)

    # Panel 2 — volume
    bar_c = ["#26a69a" if o <= c else "#ef5350"
             for o, c in zip(df["open"], df["close"], strict=True)]
    ax2.bar(x, df["volume"], color=bar_c, width=1.0, alpha=.8)
    ax2.plot(x, df["vol_avg20"], color="#546e7a", lw=1, label="Vol avg 20d")
    ax2.set_ylabel("Volume", color="white", fontsize=9)
    ax2.legend(loc="upper left", frameon=False, fontsize=8, labelcolor="white")

    # Panel 3 — RSI
    ax3.plot(x, df["rsi14"], color="#ce93d8", lw=1.4)
    for level in (70, 50, 30):
        ax3.axhline(level, color="#607d8b", lw=.8,
                    ls="--" if level in (70, 30) else ":")
    ax3.set_ylim(0, 100)
    ax3.set_ylabel("RSI 14", color="white", fontsize=9)

    # Panel 4 — MACD
    hist_c = ["#26a69a" if h >= 0 else "#ef5350" for h in df["macd_hist"]]
    ax4.bar(x, df["macd_hist"], color=hist_c, width=1.0, alpha=.85, label="Histogram")
    ax4.plot(x, df["macd"], color="#29b6f6", lw=1.1, label="MACD")
    ax4.plot(x, df["macd_signal"], color="#ff8a65", lw=1.1, label="Signal")
    ax4.axhline(0, color="#607d8b", lw=.7)
    ax4.set_ylabel("MACD", color="white", fontsize=9)
    ax4.set_xlabel("Tanggal", color="white", fontsize=9)
    ax4.legend(loc="upper left", frameon=False, fontsize=8, labelcolor="white")
    ax4.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))

    fig.savefig(out_path, bbox_inches="tight", facecolor=fig.get_facecolor())
    return out_path
