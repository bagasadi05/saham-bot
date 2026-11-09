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
                  commission=commission, exclusive_orders=True,
                  finalize_trades=True)
    stats = bt.run()
    return stats


def _clean_stat(val) -> float | None:
    if val is None or pd.isna(val):
        return None
    f = float(val)
    return f if np.isfinite(f) else None


def _annualize_return(total_pct: float | None, stats) -> float | None:
    """Konversi return kumulatif (%) -> return per tahun (CAGR %)."""
    if total_pct is None:
        return None
    dur = stats.get("Duration") if hasattr(stats, "get") else None
    days = getattr(dur, "days", None)
    if days is None and hasattr(stats, "get"):
        start, end = stats.get("Start"), stats.get("End")
        if start is not None and end is not None:
            try:
                days = (pd.Timestamp(end) - pd.Timestamp(start)).days
            except Exception:
                days = None
    if not days or days <= 0:
        return None
    years = days / 365.25
    growth = 1.0 + total_pct / 100.0
    if growth <= 0:
        return -100.0
    return (growth ** (1.0 / years) - 1.0) * 100.0


def summarize_stats(ticker: str, stats) -> dict:
    """Ekstrak metrik ringkas backtest dengan Buy & Hold disetahunkan & NaN aman."""
    bh_total = _clean_stat(stats.get("Buy & Hold Return [%]"))
    end_dt = stats.get("End") if hasattr(stats, "get") else None
    as_of = str(pd.Timestamp(end_dt).date()) if end_dt is not None else ""
    trades_raw = _clean_stat(stats.get("# Trades"))
    return {
        "ticker": ticker.upper().replace(".JK", ""),
        "as_of": as_of,
        "ret_ann": _clean_stat(stats.get("Return (Ann.) [%]")),
        "bh_ann": _annualize_return(bh_total, stats),
        "bh_total": bh_total,
        "trades": int(trades_raw) if trades_raw is not None else 0,
        "win_rate": _clean_stat(stats.get("Win Rate [%]")),
        "profit_factor": _clean_stat(stats.get("Profit Factor")),
        "max_dd": _clean_stat(stats.get("Max. Drawdown [%]")),
        "exposure": _clean_stat(stats.get("Exposure Time [%]")),
    }


def _fmt_num(val: float | None, tpl: str, suffix: str = "") -> str:
    return "—" if val is None else (tpl.format(val) + suffix)


def fmt_report(ticker: str, stats) -> str:
    """Laporan perbandingan statistik vs simulasi transaksi nyata."""
    s = summarize_stats(ticker, stats)
    bh_extra = (f" (total {s['bh_total']:+.1f}%)"
                if s["bh_total"] is not None else "")
    return (
        f"\n=== {ticker} — simulasi transaksi nyata ===\n"
        f"  Return strategi : {_fmt_num(s['ret_ann'], '{:+.1f}', '% per tahun')}\n"
        f"  Buy & hold      : {_fmt_num(s['bh_ann'], '{:+.1f}', '% per tahun')}{bh_extra}\n"
        f"  Jumlah transaksi: {s['trades']}\n"
        f"  Win-rate        : {_fmt_num(s['win_rate'], '{:.1f}', '%')}\n"
        f"  Profit factor   : {_fmt_num(s['profit_factor'], '{:.2f}')}\n"
        f"  Max drawdown    : {_fmt_num(s['max_dd'], '{:.1f}', '%')}\n"
        f"  Waktu terinvestasi: {_fmt_num(s['exposure'], '{:.0f}', '%')}\n"
        f"  (biaya beli {BUY_FEE*100:.2f}% / jual {SELL_FEE*100:.2f}% seperti /saran,\n"
        "   fill bar berikutnya,\n"
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
