"""Backtest transaksi nyata pakai backtesting.py — biaya broker, fill bar
berikutnya, stop-loss & take-profit berbasis ATR, dan filter jebakan momentum
(dari temuan backtest statistik v1).

Entry   : rating BUY / STRONG BUY pada penutupan  ATAU price di rentang BB (optional)
Entry R : rating >= +20 , skip bila ret20d > TRAP_RET_PCT
Exit    : TP = entry + 2×ATR | SL = entry − 1×ATR | maksimal HOLD_MAX hari bursa
Sizing  : risiko 1% ekuitas per trade — sama dengan skema /saran
Biaya   : beli 0,15% / jual 0,25% — sama persis dengan skema /saran
Cash    : Rp100.000.000

Pakai:
    python backtest_real.py BBCA [TICKER...]
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd
from backtesting import Backtest, Strategy

from core import analysis, data
from core.saran import BUY_FEE, SELL_FEE

TRAP_RET_PCT = 15.0   # skip entry bila harga sudah naik >15% (20 hari)
HOLD_MAX = 20         # hari bursa maksimal tahan
COMMISSION = (BUY_FEE + SELL_FEE) / 2  # hanya untuk ringkasan
MIN_RATING_PCT = 20.0  # ambil rating BUY ke atas
RISK_PCT = 0.01       # 1% ekuitas per trade (sama dengan /saran)


def commission(order_size: int, price: float) -> float:
    """Biaya per transaksi identik dengan /saran (beli 0,15%, jual 0,25%)

    backtesting.py memanggil ini per order; order_size < 0 berarti jual.
    """
    return BUY_FEE if order_size > 0 else SELL_FEE


def build_statistics(ticker: str, period: str = "5y"):
    """Siapkan dataset + arrays sinyal untuk backtesting.py."""
    df = data.fetch_history(ticker, period=period)
    if df is None or len(df) < 280:
        raise ValueError(f"Data '{ticker}' terlalu pendek untuk backtest nyata.")
    ind = analysis.compute_indicators(df)

    score_pct = np.full(len(ind), np.nan)
    for i in range(2, len(ind)):
        try:
            score_pct[i] = analysis.score_from_rows(ind.iloc[i], ind.iloc[i - 1])["pct"]
        except Exception:
            score_pct[i] = np.nan

    trap_ok = ind["ret20d"].fillna(0).to_numpy() <= TRAP_RET_PCT
    atr = ind["atr14"].to_numpy()
    entries = (
        (score_pct >= MIN_RATING_PCT)
        & trap_ok
        & ~np.isnan(atr)
        & ~np.isnan(score_pct)
    )
    out = pd.DataFrame({
        "Open": ind["open"], "High": ind["high"], "Low": ind["low"],
        "Close": ind["close"], "Volume": ind["volume"],
        "signal": entries, "atr": atr,
    }, index=ind.index)
    return out.dropna(subset=["Close"])


class PowerStrategy(Strategy):
    """Entry saat sinyal BUY (bukan di jebakan momentum), exit via ATR SL/TP."""

    atr_mult_sl = 1.0
    atr_mult_tp = 2.0
    hold_max = HOLD_MAX

    def init(self):
        self.signal = self.I(lambda: self.data.df["signal"], name="sinyal BUY")
        self.atr = self.I(lambda: self.data.df["atr"], name="ATR 14")
        self._entry_bar = -1

    def next(self):
        i = len(self.data) - 1
        if self.position:
            if i - self._entry_bar >= self.hold_max:
                self.position.close()  # keluar paksa: maksimal tahan
            return
        if self.signal[-1]:
            price = self.data.Close[-1]
            atr = self.atr[-1]
            if pd.isna(atr) or atr <= 0:
                return
            sl = price - self.atr_mult_sl * atr
            tp = price + self.atr_mult_tp * atr
            if sl <= 0:
                return
            # sizing identik dengan /saran: risiko RISK_PCT ekuitas per trade,
            # bukan all-in — supaya simulasi memvalidasi skema yang dipakai bot.
            risk_per_share = price - sl
            size = min(0.99, (RISK_PCT * price) / risk_per_share)
            if size <= 0:
                return
            self.buy(size=size, sl=sl, tp=tp)
            self._entry_bar = i


def run_backtest(ticker: str) -> dict:
    df = build_statistics(ticker)
    bt = Backtest(df, PowerStrategy, cash=100_000_000,
                  commission=commission, exclusive_orders=True)
    stats = bt.run()
    return stats


def fmt_report(ticker: str, stats) -> str:
    """Laporan perbandingan statistik vs simulasi transaksi nyata."""
    ret_pct = stats["Return (Ann.) [%]"]
    bh_pct = stats["Buy & Hold Return [%]"]
    trades = stats["# Trades"]
    win = stats["Win Rate [%]"]
    pf = stats.get("Profit Factor", float("nan"))
    dd = stats["Max. Drawdown [%]"]
    dur = stats.get("Exposure Time [%]", float("nan"))
    return (
        f"\n=== {ticker} — simulasi transaksi nyata ===\n"
        f"  Return strategi : {ret_pct:+.1f}% per tahun\n"
        f"  Buy & hold      : {bh_pct:+.1f}% per tahun\n"
        f"  Jumlah transaksi: {trades:.0f}\n"
        f"  Win-rate        : {win:.1f}%\n"
        f"  Profit factor   : {pf if not pd.isna(pf) else float('nan'):.2f}\n"
        f"  Max drawdown    : {dd:.1f}%\n"
        f"  Waktu terinvestasi: {dur:.0f}%\n"
        f"  (biaya beli {BUY_FEE*100:.2f}% / jual {SELL_FEE*100:.2f}% seperti /saran,\n"
        f"   fill bar berikutnya,\n"
        f"   SL 1×ATR, TP 2×ATR, maksimal tahan {HOLD_MAX} hari, skip trap momentum,\n"
        f"   sizing risiko {RISK_PCT*100:.0f}% ekuitas per trade seperti /saran)"
    )


def run_cli(argv: list[str]):
    tickers = argv or ["BBCA"]
    for t in tickers:
        t = t.upper()
        try:
            stats = run_backtest(t)
            print(fmt_report(t, stats))
        except ValueError as e:
            print(f"  {e}")


if __name__ == "__main__":
    run_cli(sys.argv[1:])
