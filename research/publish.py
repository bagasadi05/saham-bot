"""Ringkas hasil riset menjadi `data/validation.json` yang ikut di-commit.

Bot memakai file ini untuk perintah /validasi — jadi angka yang dilihat
pengguna adalah hasil uji yang benar-benar dijalankan, bukan klaim di README.
Jalankan ulang setelah research/evaluate.py (dan evaluate_kronos.py) selesai.

Pakai:
    python research/publish.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import marketcal  # noqa: E402
from research import evaluate  # noqa: E402

EVAL = os.path.join("temp", "research", "evaluation.json")
KRONOS = os.path.join("temp", "research", "kronos_eval.json")
PORTFOLIO = os.path.join("temp", "research", "portfolio.json")
EXPERIMENTS = os.path.join("temp", "research", "experiments.json")
OOS = os.path.join("temp", "research", "oos_predictions.pkl")
OUT = os.path.join("data", "validation.json")


def _enrich_quintile_bands(quintiles: list[dict], oos_df=None) -> list[dict]:
    """Lengkapi pita empiris P25-P75 (IQR) per kuintil jika tersedia."""
    rows = [dict(q) for q in quintiles]
    needs_band = not rows or any("iqr_pct" not in q for q in rows)
    if needs_band and oos_df is not None:
        bands = evaluate.summarize_quintile_bands(oos_df)
        by_q = {int(b["quintile"]): b for b in bands}
        if not rows:
            return bands
        for row in rows:
            b = by_q.get(int(row["quintile"]))
            if not b:
                continue
            for key in ("median_pct", "p25_pct", "p75_pct", "iqr_pct", "win_rate"):
                if row.get(key) is None and b.get(key) is not None:
                    row[key] = b[key]
    return rows


def build_validation_payload(
    ev: dict,
    p: dict | None = None,
    k: dict | None = None,
    exp: dict | None = None,
    oos_df=None,
) -> dict:
    """Bangun payload `data/validation.json` dari output riset lengkap + metadata jujur."""
    meta = dict(ev.get("meta") or {})
    meta["published_at"] = marketcal.now_wib().isoformat(timespec="seconds")
    meta.setdefault("weights_synced_to", "core/relatif.py")

    wfs = ev.get("walk_forward_summary") or {}
    quintiles = _enrich_quintile_bands(ev.get("oos_quintiles") or [], oos_df=oos_df)

    slim = {
        "meta": meta,
        "score": {
            "ic": ev["ic_score"],
            "ic_timeseries": ev["ic_timeseries"],
            "by_year": [{"year": r["year"], "ic": r["ic"]} for r in ev["years"]],
        },
        "ratings": [
            {"rating": r["rating"], "n": r["n"], "excess_pct": r["excess_pct"],
             "excess_win_rate": r["excess_win_rate"],
             "ci": r["ci95_excess_daily_pct"]}
            for r in ev["ratings"]
        ],
        "walk_forward": [
            {"year": r["year"], "ic_model": r.get("ic_model"),
             "ic_score": r.get("ic_score"), "ic_eq": r.get("ic_eq"),
             "lam_rel": r.get("lam_rel"), "ic_blend": r.get("ic_blend")}
            for r in ev["walk_forward"]
        ],
        "walk_forward_summary": wfs,
        "quintiles": quintiles,
        "weights": ev.get("production_weights", {}),
    }

    if p is not None:
        a = p.get("across_phases", {})
        dsr_info = p.get("deflated_sharpe") or {}
        psr_info = p.get("probabilistic_sharpe") or {}
        psr_val = psr_info.get("psr") if psr_info.get("psr") is not None else p.get("psr")
        psr_mean_val = (
            psr_info.get("psr_mean") if psr_info.get("psr_mean") is not None
            else a.get("psr_mean")
        )
        cli_params = p.get("cli_params") or {
            "quintile": p.get("quintile"),
            "step": p.get("holding_days"),
            "hold": p.get("hold_quintile"),
            "min_hold": p.get("min_hold"),
            "regime": p.get("regime"),
        }
        exp_trials = {
            "portfolio_phases": p.get("n_phases"),
            "cv_trials": wfs.get("cv_trials"),
        }
        if exp is not None:
            exp_trials.update({
                "ablation_configs": exp.get("n_configs"),
                "ablation_phase_trials": exp.get("total_trials"),
                "total_trials_with_horizons": exp.get(
                    "total_trials_with_horizons", exp.get("total_trials")
                ),
                "dsr_gate": exp.get("dsr_gate"),
            })
        slim["portfolio"] = {
            "portfolio_generated": p.get("portfolio_generated") or meta["published_at"],
            "cli_params": cli_params,
            "cli_command": p.get("cli_command"),
            "n_phases": p.get("n_phases"),
            "holding_days": p.get("holding_days"),
            "cost_per_round_trip_pct": p.get("cost_per_round_trip_pct"),
            "avg_turnover_pct": p.get("avg_turnover_pct"),
            "avg_turnover_two_way_pct": p.get("avg_turnover_two_way_pct"),
            "quintile": p.get("quintile"),
            "hold_quintile": p.get("hold_quintile"),
            "hysteresis": p.get("hysteresis"),
            "min_hold": p.get("min_hold"),
            "regime": p.get("regime"),
            "avg_exposure_pct": a.get("avg_exposure_pct"),
            "max_drawdown_mean_pct": a.get("max_drawdown_mean_pct"),
            "alpha_active_gross_mean_pct": a.get("alpha_active_gross_mean_pct"),
            "deflated_sharpe": dsr_info,
            "psr": psr_val,
            "psr_mean": psr_mean_val,
            "experiments_trials": exp_trials,
            "alpha_gross_mean_pct": a.get("alpha_gross_mean_pct"),
            "alpha_net_mean_pct": a.get("alpha_net_mean_pct"),
            "alpha_net_median_pct": a.get("alpha_net_median_pct"),
            "phases_net_positive": a.get("phases_net_positive"),
            "ci95_alpha_net": p.get("ci95_alpha_net"),
            "alpha_vs_ihsg_mean_pct": a.get("alpha_vs_ihsg_mean_pct"),
            "phases_vs_ihsg_positive": a.get("phases_vs_ihsg_positive"),
        }

    if k is not None:
        slim["kronos"] = {
            "n_obs": k.get("n_obs"), "n_tickers": k.get("n_tickers"),
            "n_dates": k.get("n_dates"), "start": k.get("start"), "end": k.get("end"),
            "ic": {c: k.get(c) for c in ("kronos", "score", "mom20", "anti_mom")},
        }
    return slim


def main():
    if not os.path.exists(EVAL):
        raise SystemExit(f"{EVAL} belum ada — jalankan research/evaluate.py dulu")
    with open(EVAL, encoding="utf-8") as f:
        ev = json.load(f)

    p = None
    if os.path.exists(PORTFOLIO):
        with open(PORTFOLIO, encoding="utf-8") as f:
            p = json.load(f)

    k = None
    if os.path.exists(KRONOS):
        with open(KRONOS, encoding="utf-8") as f:
            k = json.load(f)

    exp = None
    if os.path.exists(EXPERIMENTS):
        with open(EXPERIMENTS, encoding="utf-8") as f:
            exp = json.load(f)

    oos_df = None
    quintiles = ev.get("oos_quintiles") or []
    if (not quintiles or any("iqr_pct" not in q for q in quintiles)) and os.path.exists(OOS):
        import pandas as pd
        oos_df = pd.read_pickle(OOS)

    slim = build_validation_payload(ev, p=p, k=k, exp=exp, oos_df=oos_df)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, indent=1)
    print(f"ditulis: {OUT} ({os.path.getsize(OUT):,} byte)")


if __name__ == "__main__":
    main()
