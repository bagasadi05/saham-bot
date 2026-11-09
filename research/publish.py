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

EVAL = os.path.join("temp", "research", "evaluation.json")
KRONOS = os.path.join("temp", "research", "kronos_eval.json")
PORTFOLIO = os.path.join("temp", "research", "portfolio.json")
OUT = os.path.join("data", "validation.json")


def main():
    if not os.path.exists(EVAL):
        raise SystemExit(f"{EVAL} belum ada — jalankan research/evaluate.py dulu")
    with open(EVAL, encoding="utf-8") as f:
        ev = json.load(f)

    slim = {
        "meta": ev["meta"],
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
             "ic_score": r.get("ic_score"), "ic_blend": r.get("ic_blend")}
            for r in ev["walk_forward"]
        ],
        "quintiles": ev.get("oos_quintiles", []),
        "weights": ev.get("production_weights", {}),
    }

    if os.path.exists(PORTFOLIO):
        with open(PORTFOLIO, encoding="utf-8") as f:
            p = json.load(f)
        a = p.get("across_phases", {})
        slim["portfolio"] = {
            "n_phases": p.get("n_phases"),
            "holding_days": p.get("holding_days"),
            "cost_per_round_trip_pct": p.get("cost_per_round_trip_pct"),
            "avg_turnover_pct": p.get("avg_turnover_pct"),
            "alpha_gross_mean_pct": a.get("alpha_gross_mean_pct"),
            "alpha_net_mean_pct": a.get("alpha_net_mean_pct"),
            "alpha_net_median_pct": a.get("alpha_net_median_pct"),
            "phases_net_positive": a.get("phases_net_positive"),
        }

    if os.path.exists(KRONOS):
        with open(KRONOS, encoding="utf-8") as f:
            k = json.load(f)
        slim["kronos"] = {
            "n_obs": k.get("n_obs"), "n_tickers": k.get("n_tickers"),
            "n_dates": k.get("n_dates"), "start": k.get("start"), "end": k.get("end"),
            "ic": {c: k.get(c) for c in ("kronos", "score", "mom20", "anti_mom")},
        }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, indent=1)
    print(f"ditulis: {OUT} ({os.path.getsize(OUT):,} byte)")


if __name__ == "__main__":
    main()
