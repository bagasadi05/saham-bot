"""Uji Ablasi Fase 3a: Kontribusi Marjinal Faktor Baru (Momentum 12-1, RS 3m, Low-Vol, ADTV).

Menguji secara kausal apakah menambahkan faktor baru ke skor peringkat 12 faktor
beku (`core/relatif.WEIGHTS`) meningkatkan IC OOS atau alpha neto setelah biaya
0,40% BEI. Bobot `core/relatif.py` TIDAK diubah otomatis sampai lolos gerbang
DSR >= 0,95 pada dataset 10 tahun penuh.

Pakai:
    python research/phase3_ablation.py --offline
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research import evaluate as ev, phase1_audit as p1  # noqa: E402


def run_ablation(
    frames: dict[str, pd.DataFrame],
    *,
    horizon: int = 20,
    step: int = 20,
    hold: float = 0.6,
) -> dict[str, Any]:
    """Hitung kontribusi marjinal penambahan tiap kandidat faktor ke baseline 12F."""
    parts = [p1.build_benchmark_frame(t, df, horizon=horizon) for t, df in sorted(frames.items())]
    parts = [p for p in parts if p is not None and not p.empty]
    if not parts:
        return {"ok": False, "reason": "tidak ada frame valid"}
    full = pd.concat(parts, ignore_index=True)
    full["base_12f"] = p1.score_frozen_model(full)

    # Kandidat faktor baru (semua kausal per tanggal)
    full["log_adtv"] = np.log1p(full["adtv20"].clip(lower=0.0))
    z_new = ev.zscore_by_date(full, ["base_12f", "bench_mom_12_1", "bench_low_vol", "log_adtv"])

    variants = {
        "baseline_12f": z_new["base_12f"],
        "plus_mom_12_1_25pct": 0.75 * z_new["base_12f"] + 0.25 * z_new["bench_mom_12_1"],
        "plus_low_vol_25pct": 0.75 * z_new["base_12f"] + 0.25 * z_new["bench_low_vol"],
        "plus_liquidity_15pct": 0.85 * z_new["base_12f"] + 0.15 * z_new["log_adtv"],
        "combo_12f_mom_liq": 0.65 * z_new["base_12f"] + 0.25 * z_new["bench_mom_12_1"] + 0.10 * z_new["log_adtv"],
    }

    report: dict[str, Any] = {}
    base_alpha = None
    for name, ser in variants.items():
        full[name] = ser
        res = p1.evaluate_signal_with_costs(full, name, step=step, hold=hold, adtv_min=2e9)
        if name == "baseline_12f":
            base_alpha = res.get("alpha_net_mean_pct")
        delta = (
            round(res["alpha_net_mean_pct"] - base_alpha, 3)
            if base_alpha is not None and res.get("alpha_net_mean_pct") is not None
            else None
        )
        report[name] = {
            "mean_ic": (res.get("ic") or {}).get("mean_ic"),
            "t_hac": (res.get("ic") or {}).get("t_hac"),
            "alpha_net_mean_pct": res.get("alpha_net_mean_pct"),
            "delta_alpha_vs_base_pct": delta,
            "turnover_one_way_pct": res.get("avg_turnover_one_way_pct"),
            "phases_net_positive": res.get("phases_net_positive"),
        }

    return {
        "ok": True,
        "n_tickers": len(parts),
        "n_rows": int(len(full)),
        "adtv_filter": ">= Rp2 M/hari",
        "ablation": report,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Uji Ablasi Fase 3a: Faktor Baru vs Baseline 12F")
    ap.add_argument("--period", default="1y")
    ap.add_argument("--step", type=int, default=20)
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    frames = p1._load_cached_frames(args.period)
    if not frames:
        print(json.dumps({"ok": False, "reason": "Cache kosong."}, indent=2))
        return
    print(json.dumps(run_ablation(frames, horizon=args.step, step=args.step), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
