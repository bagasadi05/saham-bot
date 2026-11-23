"""Membuat chart analisis teknikal: harga + MA + rentang 20 hari, volume, RSI, MACD.

Dibuat lewat `matplotlib.figure.Figure` (bukan pyplot) supaya aman dipanggil
dari beberapa thread sekaligus — bot memakai asyncio.to_thread per permintaan
dan pyplot menyimpan state global yang bisa saling menimpa.
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")  # tanpa GUI / display
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
from matplotlib.figure import Figure

from config import CHART_SIZE, CHART_DPI

BLUE = "#29b6f6"
GOLD, GREEN, RED = "#f5b041", "#26a69a", "#ef5350"
CANVAS_BG, BG, GRID = "#0d1117", "#131922", "#21262d"


def _style(fig, axes):
    fig.patch.set_facecolor(CANVAS_BG)
    for ax in axes:
        ax.set_facecolor(BG)
        ax.yaxis.tick_right()
        ax.yaxis.set_label_position("right")
        ax.tick_params(colors="#c9d1d9", labelsize=8.5, length=3, color="#30363d")
        ax.spines["top"].set_visible(False)
        ax.spines["left"].set_visible(False)
        ax.spines["right"].set_color("#30363d")
        ax.spines["bottom"].set_color("#30363d")
        ax.grid(color=GRID, lw=0.6, ls="--", alpha=0.8)


def build_chart(
    symbol: str,
    ind: pd.DataFrame,
    score_pct: float,
    out_path: str,
    *,
    levels: dict | None = None,
) -> str:
    df = ind.tail(180)  # sekitar 6-9 bulan bursa
    # Pakai indeks integer berurutan agar celah akhir pekan / libur panjang tidak melebar
    x = np.arange(len(df))
    dates = [
        idx.strftime("%d %b %y") if hasattr(idx, "strftime") else str(idx)[:10]
        for idx in df.index
    ]

    fig = Figure(figsize=CHART_SIZE, dpi=CHART_DPI)
    ax1, ax2, ax3, ax4 = fig.subplots(
        4, 1, sharex=True,
        gridspec_kw={"height_ratios": [3, 1, 1, 1], "hspace": 0.12})
    _style(fig, (ax1, ax2, ax3, ax4))

    # Panel 1 — candlestick OHLC, MA, Bollinger, S/R pivot, dan opsional SL/TP
    if all(col in df.columns for col in ("open", "high", "low", "close")):
        opens = df["open"].to_numpy(dtype=float)
        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)
        closes = df["close"].to_numpy(dtype=float)
        up = closes >= opens
        colors = np.where(up, "#26a69a", "#ef5350")
        ax1.vlines(x, lows, highs, color=colors, lw=0.7, alpha=0.85, zorder=2)
        body_bottom = np.minimum(opens, closes)
        body_height = np.maximum(np.abs(closes - opens), np.maximum(closes * 0.001, 0.5))
        ax1.bar(x, body_height, bottom=body_bottom, width=0.62, color=colors, alpha=0.9, zorder=3)
    else:
        ax1.plot(x, df["close"], color=BLUE, lw=1.6, label="Close")
    ax1.plot(x, df["sma20"], color=GOLD, lw=1, label="MA20", zorder=4)
    ax1.plot(x, df["sma50"], color=GREEN, lw=1, label="MA50", zorder=4)
    ax1.plot(x, df["sma200"], color=RED, lw=1, label="MA200", zorder=4)
    if "bb_upper" in df.columns and not df["bb_upper"].isna().all():
        ax1.plot(x, df["bb_upper"], color="#4dd0e1", lw=.7, ls=":", label="BB")
        ax1.plot(x, df["bb_lower"], color="#4dd0e1", lw=.7, ls=":")
        ax1.fill_between(x, df["bb_lower"], df["bb_upper"], color="#16344a", alpha=.30)
    if "low" in df.columns and "high" in df.columns and len(df) >= 5:
        from core import analysis as _ana
        sr = _ana.support_resistance(df)
        sup_lvl = sr.get("swing_support") or sr.get("support")
        res_lvl = sr.get("swing_resistance") or sr.get("resistance")
        if sup_lvl:
            ax1.axhline(sup_lvl, color="#26a69a", lw=0.8, ls="--", alpha=0.75, label="Support")
        if res_lvl:
            ax1.axhline(res_lvl, color="#ef5350", lw=0.8, ls="--", alpha=0.75, label="Resistance")
        poc_info = _ana.volume_profile_poc(df)
        if poc_info and poc_info.get("poc"):
            ax1.axhline(poc_info["poc"], color="#ffd54f", lw=0.85, ls=":", alpha=0.85, label="POC 60h")
    if isinstance(levels, dict):
        for k, col, lbl in (("sl", "#ff5252", "SL"), ("tp", "#69f0ae", "TP"), ("entry", "#ffd740", "Entry")):
            v = levels.get(k)
            if v is not None and float(v) > 0:
                ax1.axhline(float(v), color=col, lw=0.9, ls="-.", alpha=0.85, label=lbl)
    if len(df) and not pd.isna(df["close"].iloc[-1]):
        last_px = float(df["close"].iloc[-1])
        ax1.annotate(
            f" {last_px:,.0f}",
            xy=(x[-1], last_px),
            xytext=(4, 0),
            textcoords="offset points",
            color="white",
            fontsize=8,
            va="center",
            bbox={"boxstyle": "round,pad=0.2", "fc": BLUE, "ec": "none", "alpha": 0.9},
        )
    c_vals = df["close"].dropna()
    if len(c_vals) and float(c_vals.min()) == float(c_vals.max()):
        c0 = float(c_vals.iloc[0])
        pad = max(abs(c0) * 0.02, 1.0)
        ax1.set_ylim(c0 - pad, c0 + pad)
    last_c = float(df["close"].iloc[-1]) if len(df) and not pd.isna(df["close"].iloc[-1]) else 0.0
    prev_c = float(df["close"].iloc[-2]) if len(df) >= 2 and not pd.isna(df["close"].iloc[-2]) else last_c
    chg_1d = ((last_c / prev_c) - 1.0) * 100.0 if prev_c else 0.0
    ax1.set_title(
        f"{symbol}  ·  Rp{last_c:,.0f} ({chg_1d:+.2f}%)  ·  Skor {score_pct:+.0f}%",
        color="#f0f6fc", fontsize=11.5, fontweight="bold", loc="left", pad=8,
    )
    ax1.legend(loc="upper left", frameon=False, fontsize=9, labelcolor="white", ncols=5)
    ax1.set_ylabel("Harga (Rp)", color="white", fontsize=9)

    # Panel 2 — volume
    vols = df["volume"].fillna(0.0)
    bar_c = ["#26a69a" if o <= c else "#ef5350"
             for o, c in zip(df["open"], df["close"], strict=True)]
    ax2.bar(x, vols, color=bar_c, width=0.75, alpha=.8)
    ax2.plot(x, df["vol_avg20"], color="#546e7a", lw=1, label="Vol avg 20d")
    ax2.set_ylabel("Volume", color="white", fontsize=9)
    ax2.legend(loc="upper left", frameon=False, fontsize=8, labelcolor="white")

    # Panel 3 — RSI
    ax3.axhspan(70, 100, color="#ef5350", alpha=0.07)
    ax3.axhspan(0, 30, color="#26a69a", alpha=0.07)
    ax3.plot(x, df["rsi14"], color="#c792ea", lw=1.4)
    for level in (70, 50, 30):
        ax3.axhline(level, color="#484f58", lw=0.8,
                    ls="--" if level in (70, 30) else ":")
    ax3.set_ylim(0, 100)
    ax3.set_ylabel("RSI 14", color="white", fontsize=9)

    # Panel 4 — MACD
    hist = df["macd_hist"].fillna(0.0)
    hist_c = ["#26a69a" if h >= 0 else "#ef5350" for h in hist]
    ax4.bar(x, hist, color=hist_c, width=0.75, alpha=.85, label="Histogram")
    ax4.plot(x, df["macd"], color="#29b6f6", lw=1.1, label="MACD")
    ax4.plot(x, df["macd_signal"], color="#ff8a65", lw=1.1, label="Signal")
    ax4.axhline(0, color="#607d8b", lw=.7)
    ax4.set_ylabel("MACD", color="white", fontsize=9)
    ax4.set_xlabel("Tanggal", color="white", fontsize=9)
    ax4.legend(loc="upper left", frameon=False, fontsize=8, labelcolor="white")

    def _fmt_date(val, _pos=None):
        idx = int(round(val))
        return dates[idx] if 0 <= idx < len(dates) else ""

    ax4.xaxis.set_major_locator(mticker.MaxNLocator(nbins=7, integer=True))
    ax4.xaxis.set_major_formatter(mticker.FuncFormatter(_fmt_date))
    if len(df) > 0:
        ax4.set_xlim(-0.5, len(df) - 0.5)

    fig.savefig(out_path, bbox_inches="tight", facecolor=fig.get_facecolor())
    return out_path
