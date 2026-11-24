"""Evaluasi detektor rezim pasar (Volatilitas + Breadth BEI + Drawdown) anti-drawdown.

Membandingkan 4 aturan skala eksposur pada sinyal peringkat relatif:
  1. always_100        — tanpa rem eksposur (baseline 100%)
  2. sma50_200_half    — aturan lama (50% saat IHSG di bawah SMA50/200)
  3. vol_breadth_3tier — 3 tingkat (100% / 50% / 25%) berbasis volatilitas 20h + breadth MA50
  4. adaptive_dd_guard — vol_breadth_3tier + rem darurat 25% saat drawdown IHSG 60h <= -8%

Pemakaian:
  python research/regime_eval.py --out data/regime_eval.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import relatif, saran  # noqa: E402
from research import evaluate as ev  # noqa: E402


def compute_market_features(df: pd.DataFrame) -> pd.DataFrame:
    """Hitung breadth (% emiten di atas SMA50), volatilitas pasar, & proxy drawdown 60h."""
    g = df.groupby("date", sort=True)
    # Proxy equal-weight market return 1d dari f_mom1
    mkt_ret1d = g["f_mom1"].mean().fillna(0.0)
    mkt_idx = (1.0 + mkt_ret1d).cumprod()
    sma50 = mkt_idx.rolling(50).mean()
    sma200 = mkt_idx.rolling(200).mean()
    vol20_ann = mkt_ret1d.rolling(20).std() * np.sqrt(252.0)
    roll_peak60 = mkt_idx.rolling(60, min_periods=1).max()
    dd60 = mkt_idx / roll_peak60 - 1.0
    breadth_ma50 = g["f_close_sma50"].apply(lambda s: float((s > 0).mean()))

    return pd.DataFrame({
        "mkt_ret1d": mkt_ret1d,
        "mkt_idx": mkt_idx,
        "sma50": sma50,
        "sma200": sma200,
        "vol20_ann": vol20_ann,
        "dd60": dd60,
        "breadth_ma50": breadth_ma50,
    })


def exposure_series(mkt: pd.DataFrame, rule: str) -> pd.Series:
    """Kembalikan seri skala eksposur (0.25, 0.50, atau 1.00) per tanggal."""
    idx = mkt.index
    if rule == "always_100":
        return pd.Series(1.0, index=idx)
    if rule == "sma50_200_half":
        bear = (mkt["mkt_idx"] < mkt["sma50"]) & (mkt["sma50"] < mkt["sma200"])
        return pd.Series(np.where(bear, 0.5, 1.0), index=idx)
    if rule in ("vol_breadth_3tier", "adaptive_dd_guard"):
        high_vol = mkt["vol20_ann"] > 0.22
        weak_breadth = mkt["breadth_ma50"] < 0.40
        very_weak = (mkt["breadth_ma50"] < 0.25) | (mkt["vol20_ann"] > 0.30)
        exp = np.where(very_weak, 0.25, np.where(high_vol | weak_breadth, 0.50, 1.0))
        if rule == "adaptive_dd_guard":
            exp = np.where(mkt["dd60"] <= -0.08, 0.25, exp)
        return pd.Series(exp, index=idx)
    raise ValueError(f"Aturan rezim tidak dikenal: {rule}")


def evaluate_regime_rules(df: pd.DataFrame, horizon: int = 20) -> dict:
    """Bandingkan 4 aturan rezim pada portofolio top-quintile 20 hari (net biaya 0,40%)."""
    feats = [f"f_{k}" for k in relatif.WEIGHTS]
    z = ev.zscore_by_date(df, feats)
    w = np.array([relatif.WEIGHTS[k] for k in relatif.WEIGHTS], dtype=float)
    scored = df[["date", "ticker", "fwd_net", "f_mom1", "f_close_sma50"]].copy()
    scored["pred"] = z[feats].fillna(0.0).to_numpy(dtype=float) @ w

    mkt = compute_market_features(scored)
    dates = sorted(scored["date"].unique())
    # Ambil tanggal non-overlapping setiap `horizon` hari bursa
    rebal_dates = dates[::horizon]
    sub = scored[scored["date"].isin(rebal_dates)]

    top_rets: dict[pd.Timestamp, float] = {}
    for d, grp in sub.groupby("date"):
        if len(grp) < 10:
            continue
        k = max(1, int(len(grp) * 0.2))
        top_rets[d] = float(grp.nlargest(k, "pred")["fwd_net"].mean())

    ret_s = pd.Series(top_rets).sort_index()
    results: dict[str, dict] = {}
    for rule in ("always_100", "sma50_200_half", "vol_breadth_3tier", "adaptive_dd_guard"):
        exp_s = exposure_series(mkt, rule).reindex(ret_s.index).fillna(1.0)
        adj = ret_s * exp_s
        eq = (1.0 + adj).cumprod()
        peak = eq.cummax()
        mdd = float((eq / peak - 1.0).min()) if len(eq) else 0.0
        mean_r = float(adj.mean()) if len(adj) else 0.0
        std_r = float(adj.std(ddof=1)) if len(adj) > 1 else 0.0
        ann_ret = float((eq.iloc[-1] ** (252.0 / (len(adj) * horizon)) - 1.0)) if len(eq) else 0.0
        sharpe = (mean_r / std_r * np.sqrt(252.0 / horizon)) if std_r > 0 else 0.0
        calmar = (ann_ret / abs(mdd)) if mdd < 0 else 0.0
        results[rule] = {
            "n_periods": int(len(adj)),
            "avg_exposure_pct": round(float(exp_s.mean() * 100.0), 1),
            "mean_period_ret_pct": round(mean_r * 100.0, 3),
            "ann_return_pct": round(ann_ret * 100.0, 2),
            "max_drawdown_pct": round(mdd * 100.0, 2),
            "sharpe": round(float(sharpe), 3),
            "calmar": round(float(calmar), 3),
        }

    best_calmar = max(results.items(), key=lambda kv: kv[1]["calmar"])[0]
    return {
        "horizon": int(horizon),
        "n_rebalance_periods": int(len(ret_s)),
        "recommended_rule_by_calmar": best_calmar,
        "rules": results,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Evaluasi aturan rezim anti-drawdown BEI.")
    ap.add_argument("--period", default="10y")
    ap.add_argument("--horizon", type=int, default=20)
    ap.add_argument("--out", default="data/regime_eval.json")
    args = ap.parse_args(argv)

    tickers = list(dict.fromkeys(saran.LQ45))
    df = ev.build_dataset(tickers, args.period, args.horizon)
    report = evaluate_regime_rules(df, horizon=args.horizon)

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print("\n=== PERBANDINGAN ATURAN REZIM ANTI-DRAWDOWN ===")
    for rule, m in report["rules"].items():
        print(
            f"  {rule:18s} | Exp={m['avg_exposure_pct']:5.1f}% | "
            f"Ann={m['ann_return_pct']:+6.2f}% | MDD={m['max_drawdown_pct']:+6.2f}% | "
            f"Sharpe={m['sharpe']:+.3f} | Calmar={m['calmar']:+.3f}"
        )
    print(f"Rekomendasi terbaik (Calmar): {report['recommended_rule_by_calmar']}")
    print(f"Ditulis ke {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
