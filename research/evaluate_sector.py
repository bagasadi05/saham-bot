"""Evaluasi Out-of-Sample (OOS) skor relatif lintas-pasar vs per-sektor.

Menguji apakah normalisasi z-score per sektor (`data/idx_sectors.json`)
menambah IC atau spread kuintil OOS dibanding z-score global lintas-pasar.

Pakai:
    python research/evaluate_sector.py --offline
    python research/evaluate_sector.py --period 10y
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import relatif, saran  # noqa: E402
from research import evaluate  # noqa: E402

DEFAULT_SECTORS = os.path.join("data", "idx_sectors.json")


def load_sector_map(path: str = DEFAULT_SECTORS) -> dict[str, str]:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    secs = raw.get("sectors") if isinstance(raw, dict) else raw
    if not isinstance(secs, dict):
        return {}
    return {str(k).upper().replace(".JK", ""): str(v) for k, v in secs.items() if v}


def _compute_zscore_column(
    df: pd.DataFrame,
    weights: dict[str, float],
    sector_map: dict[str, str] | None = None,
    min_sector_n: int = 3,
) -> pd.Series:
    """Hitung z-score gabungan per tanggal (global atau per sektor dengan fallback global)."""
    feats = [f"f_{k}" for k in weights if f"f_{k}" in df.columns]
    w_arr = np.array([weights[f.removeprefix("f_")] for f in feats], dtype=float)
    out = pd.Series(np.nan, index=df.index, dtype=float)

    for _dt_val, grp in df.groupby("date"):
        mat = grp[feats].to_numpy(dtype=float)
        g_mu = np.nanmean(mat, axis=0)
        g_sd = np.nanstd(mat, axis=0, ddof=1)
        g_sd = np.where((g_sd > 1e-12) & np.isfinite(g_sd), g_sd, 1.0)
        z_global = np.clip(np.nan_to_num((mat - g_mu) / g_sd, nan=0.0), -3.0, 3.0) @ w_arr

        if not sector_map:
            out.loc[grp.index] = z_global
            continue

        secs = grp["ticker"].astype(str).str.upper().map(lambda t: sector_map.get(t, ""))
        z_final = z_global.copy()
        for sec_name, s_idx in secs.groupby(secs).groups.items():
            if not sec_name or len(s_idx) < min_sector_n:
                continue
            pos = grp.index.get_indexer(s_idx)
            s_mat = mat[pos]
            s_mu = np.nanmean(s_mat, axis=0)
            s_sd = np.nanstd(s_mat, axis=0, ddof=1)
            s_sd = np.where((s_sd > 1e-12) & np.isfinite(s_sd), s_sd, g_sd)
            z_sec = np.clip(np.nan_to_num((s_mat - s_mu) / s_sd, nan=0.0), -3.0, 3.0) @ w_arr
            z_final[pos] = z_sec
        out.loc[grp.index] = z_final
    return out


def compare_global_vs_sector(
    df: pd.DataFrame,
    sector_map: dict[str, str],
    weights: dict[str, float] | None = None,
    horizon: int = 20,
) -> dict:
    """Bandingkan IC dan spread Q5-Q1 antara z-score global vs per-sektor."""
    w = weights or dict(relatif.WEIGHTS)
    work = df.copy()
    work["z_global"] = _compute_zscore_column(work, w, sector_map=None)
    work["z_sector"] = _compute_zscore_column(work, w, sector_map=sector_map)

    ic_g = evaluate.ic_summary(evaluate.xs_ic(work, "z_global"), horizon)
    ic_s = evaluate.ic_summary(evaluate.xs_ic(work, "z_sector"), horizon)

    def _q_spread(col: str) -> float | None:
        sub = work.dropna(subset=[col, "fwd"]).copy()
        if sub.empty:
            return None
        q5_rets, q1_rets = [], []
        for _d, grp in sub.groupby("date"):
            if len(grp) < 10:
                continue
            ranked = grp.sort_values(col)
            n5 = max(1, len(ranked) // 5)
            q1_rets.append(float(ranked.head(n5)["fwd"].mean()))
            q5_rets.append(float(ranked.tail(n5)["fwd"].mean()))
        if not q5_rets:
            return None
        return round((float(np.mean(q5_rets)) - float(np.mean(q1_rets))) * 100.0, 4)

    spread_g = _q_spread("z_global")
    spread_s = _q_spread("z_sector")
    diff_ic = (
        round(float(ic_s["mean_ic"]) - float(ic_g["mean_ic"]), 4)
        if ic_s.get("mean_ic") is not None and ic_g.get("mean_ic") is not None
        else None
    )
    diff_spread = (
        round(spread_s - spread_g, 4)
        if spread_s is not None and spread_g is not None
        else None
    )
    return {
        "n_rows": int(len(work)),
        "n_tickers": int(work["ticker"].nunique()),
        "global": {
            "mean_ic": ic_g.get("mean_ic"),
            "t_hac": ic_g.get("t_hac"),
            "q5_q1_spread_pct": spread_g,
        },
        "sector": {
            "mean_ic": ic_s.get("mean_ic"),
            "t_hac": ic_s.get("t_hac"),
            "q5_q1_spread_pct": spread_s,
        },
        "delta_sector_minus_global": {
            "ic_diff": diff_ic,
            "spread_diff_pct": diff_spread,
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--period", default="10y")
    ap.add_argument("--horizon", type=int, default=20)
    ap.add_argument("--offline", action="store_true", help="pakai cache dataset jika tersedia")
    ap.add_argument("--out", default=os.path.join("temp", "research", "sector_eval.json"))
    args = ap.parse_args(argv)

    tickers = list(dict.fromkeys(saran.LQ45))
    cache = os.path.join("temp", "research", f"dataset_{args.period}_{len(tickers)}.pkl")
    if os.path.exists(cache):
        df = pd.read_pickle(cache)
    elif args.offline:
        print(f"Cache {cache} belum ada untuk mode --offline.", file=sys.stderr)
        return 2
    else:
        df = evaluate.build_dataset(tickers, args.period, args.horizon)
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        df.to_pickle(cache)

    sector_map = load_sector_map()
    report = compare_global_vs_sector(df, sector_map, horizon=args.horizon)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
