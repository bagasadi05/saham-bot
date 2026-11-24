"""Walk-forward LightGBM + fitur interaksi & uji universe Syariah/Expanded.

Dirancang untuk dijalankan di Google Colab (atau mesin lokal dengan `lightgbm`),
membandingkan secara adil tanpa kebocoran data (purged split + embargo):
  1. Baseline Ridge 12 faktor (`research/evaluate.py`)
  2. LightGBM non-linear dengan fitur interaksi & z-score per sektor
  3. Blend 50/50 (Ridge + LightGBM)

Pemakaian:
  python research/train_lgbm.py --universe expanded --horizon 20 --embargo 20 --compare-syariah
  python research/train_lgbm.py --universe syariah --horizon 5 --embargo 5
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

from core import relatif, saran, syariah  # noqa: E402
from research import evaluate as ev  # noqa: E402

BASE_FEATS = [f"f_{k}" for k in relatif.WEIGHTS]
ENRICHED_EXTRA = [
    "f_mom_accel",
    "f_trend_align",
    "f_rsi_trend",
    "f_vol_mom",
    "f_risk_adj_mom",
    "f_sector_z_mom20",
    "f_sector_z_rsi",
]


def load_sector_map() -> dict[str, str]:
    """Muat peta ticker -> sektor IDX dari data/idx_sectors.json."""
    path = ROOT / "data" / "idx_sectors.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        sec = raw.get("sectors") if isinstance(raw, dict) else None
        if isinstance(sec, dict):
            return {syariah.normalize_code(k): str(v) for k, v in sec.items() if v}
    except Exception:
        return {}
    return {}


def select_universe(mode: str = "expanded", max_tickers: int = 85) -> list[str]:
    """Pilih daftar ticker BEI (format `.JK`) sesuai mode riset."""
    m = (mode or "expanded").strip().lower()
    lq45 = [f"{syariah.normalize_code(t)}.JK" for t in getattr(saran, "LQ45", [])]
    sy_seed = syariah.syariah_universe(60)
    if m == "lq45":
        return list(dict.fromkeys(lq45))
    if m == "syariah":
        sy_lq45 = [t for t in lq45 if syariah.is_syariah(t)]
        return list(dict.fromkeys(sy_seed + sy_lq45))[:max_tickers]
    # expanded: gabungkan LQ45 + seed syariah likuid (JII/ISSI)
    return list(dict.fromkeys(lq45 + sy_seed))[:max_tickers]


def add_enriched_features(
    df: pd.DataFrame,
    sector_map: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Tambahkan fitur interaksi & z-score per sektor tanpa look-ahead."""
    out = df.copy()
    f_mom20 = pd.to_numeric(out.get("f_mom20"), errors="coerce")
    f_mom1 = pd.to_numeric(out.get("f_mom1"), errors="coerce")
    f_c50 = pd.to_numeric(out.get("f_close_sma50"), errors="coerce")
    f_50_200 = pd.to_numeric(out.get("f_sma50_sma200"), errors="coerce")
    f_rsi = pd.to_numeric(out.get("f_rsi"), errors="coerce")
    f_vol = pd.to_numeric(out.get("f_volume"), errors="coerce")
    f_volat = pd.to_numeric(out.get("f_volat"), errors="coerce")

    out["f_mom_accel"] = f_mom20 - f_mom1
    out["f_trend_align"] = f_c50 * f_50_200
    out["f_rsi_trend"] = f_rsi * f_50_200
    out["f_vol_mom"] = f_vol * f_mom20
    out["f_risk_adj_mom"] = f_mom20 / (f_volat.abs() + 0.5)

    smap = sector_map if sector_map is not None else load_sector_map()
    codes = out["ticker"].map(syariah.normalize_code)
    out["_sector"] = codes.map(lambda c: smap.get(c, "OTHER"))

    for src_col, dst_col in (("f_mom20", "f_sector_z_mom20"), ("f_rsi", "f_sector_z_rsi")):
        grp = out.groupby(["date", "_sector"])[src_col]
        mu = grp.transform("mean")
        sd = grp.transform("std").replace(0.0, np.nan)
        out[dst_col] = ((out[src_col] - mu) / sd).clip(-3.0, 3.0).fillna(0.0)

    out["is_syariah"] = codes.map(syariah.is_syariah)
    return out


def _rank_norm_by_date(df: pd.DataFrame, col: str) -> pd.Series:
    """Normalisasi prediksi ke persentil [-1, 1] per tanggal agar blend adil."""
    return df.groupby("date")[col].transform(
        lambda s: s.rank(pct=True) * 2.0 - 1.0
    )


def walk_forward_compare(
    df: pd.DataFrame,
    horizon: int = 20,
    embargo_days: int = 20,
    min_train_years: int = 4,
) -> dict:
    """Jalankan walk-forward purged CV: Ridge vs LightGBM vs Blend 50/50."""
    try:
        import lightgbm as lgb
    except ImportError as exc:
        raise RuntimeError(
            "lightgbm belum terpasang. Jalankan `pip install lightgbm` (otomatis di Colab)."
        ) from exc

    all_feats = [f for f in (BASE_FEATS + ENRICHED_EXTRA) if f in df.columns]
    base_feats = [f for f in BASE_FEATS if f in df.columns]
    years = sorted(pd.to_datetime(df["date"]).dt.year.unique())
    if len(years) <= min_train_years:
        raise ValueError(f"Tahun data terlalu sedikit ({years}); butuh > {min_train_years} tahun.")

    test_years = years[min_train_years:]
    oos_frames: list[pd.DataFrame] = []
    per_year: list[dict] = []
    last_model = None

    for yr in test_years:
        tr, te = ev.purged_split(df, int(yr), horizon, embargo_days=embargo_days)
        if len(tr) < 500 or len(te) < 200:
            continue

        # 1. Baseline Ridge 12 faktor (z-score per tanggal)
        tr_z = ev.zscore_by_date(tr, base_feats)
        te_z = ev.zscore_by_date(te, base_feats)
        y_tr_rank = ev.rank_target(tr).to_numpy(dtype=float)
        lam = ev.choose_lam_cv(tr, base_feats, horizon, embargo_days=embargo_days)
        w_ridge = ev.ridge_fit(tr_z[base_feats].fillna(0.0).to_numpy(dtype=float), y_tr_rank, lam)
        pred_ridge = te_z[base_feats].fillna(0.0).to_numpy(dtype=float) @ w_ridge

        # 2. LightGBM regularisasi ketat (pohon dangkal anti-overfit)
        tr_all_z = ev.zscore_by_date(tr, all_feats)
        te_all_z = ev.zscore_by_date(te, all_feats)
        model = lgb.LGBMRegressor(
            objective="regression",
            n_estimators=220,
            learning_rate=0.03,
            num_leaves=15,
            max_depth=4,
            min_child_samples=max(40, len(tr) // 250),
            subsample=0.8,
            subsample_freq=1,
            colsample_bytree=0.7,
            reg_alpha=1.0,
            reg_lambda=5.0,
            random_state=42 + int(yr),
            n_jobs=-1,
            verbose=-1,
        )
        model.fit(tr_all_z[all_feats].fillna(0.0), y_tr_rank)
        pred_lgb = model.predict(te_all_z[all_feats].fillna(0.0))
        last_model = model

        fold = te[["date", "ticker", "fwd", "is_syariah"]].copy()
        fold["pred_ridge"] = pred_ridge
        fold["pred_lgb"] = pred_lgb
        fold["pred_blend"] = (
            0.5 * _rank_norm_by_date(fold, "pred_ridge")
            + 0.5 * _rank_norm_by_date(fold, "pred_lgb")
        )
        oos_frames.append(fold)

        per_year.append({
            "year": int(yr),
            "n_test": int(len(fold)),
            "ic_ridge": round(float(ev.xs_ic(fold, "pred_ridge").mean()), 4),
            "ic_lgb": round(float(ev.xs_ic(fold, "pred_lgb").mean()), 4),
            "ic_blend": round(float(ev.xs_ic(fold, "pred_blend").mean()), 4),
        })

    if not oos_frames:
        raise RuntimeError("Tidak ada fold OOS yang cukup datanya untuk dievaluasi.")

    oos = pd.concat(oos_frames, ignore_index=True)

    def _summarize(sub_df: pd.DataFrame) -> dict:
        out: dict[str, dict] = {}
        for name, col in (
            ("ridge_12f", "pred_ridge"),
            ("lgbm_enriched", "pred_lgb"),
            ("blend_50_50", "pred_blend"),
        ):
            ic_s = ev.xs_ic(sub_df, col)
            summ = ev.ic_summary(ic_s, nlags=max(1, horizon // 2))
            q_df = sub_df.assign(pred=sub_df[col])
            q_tbl = ev.summarize_quintile_bands(q_df)
            q1 = next((x["excess_pct"] for x in q_tbl if x["quintile"] == 1), None)
            q5 = next((x["excess_pct"] for x in q_tbl if x["quintile"] == 5), None)
            spread = round(q5 - q1, 3) if (q1 is not None and q5 is not None) else None
            mono = relatif.check_quintile_monotonicity(
                {x["quintile"]: {"excess_pct": x["excess_pct"]} for x in q_tbl}
            )
            out[name] = {
                "ic_mean": summ.get("mean"),
                "t_hac": summ.get("t_hac"),
                "n_days": summ.get("n_days"),
                "q5_minus_q1_pct": spread,
                "monotonic": mono.get("monotonic"),
                "quintiles": q_tbl,
            }
        return out

    summary_all = _summarize(oos)
    sy_df = oos[oos["is_syariah"].astype(bool)]
    non_sy_df = oos[~oos["is_syariah"].astype(bool)]
    by_segment = {
        "syariah_only": _summarize(sy_df) if len(sy_df) >= 200 else None,
        "non_syariah": _summarize(non_sy_df) if len(non_sy_df) >= 200 else None,
    }

    feat_imp = {}
    if last_model is not None:
        imp = last_model.feature_importances_
        feat_imp = dict(
            sorted(
                ((f, int(v)) for f, v in zip(all_feats, imp, strict=False)),
                key=lambda kv: -kv[1],
            )
        )

    return {
        "horizon": int(horizon),
        "embargo_days": int(embargo_days),
        "n_oos_rows": int(len(oos)),
        "n_tickers": int(oos["ticker"].nunique()),
        "n_syariah_tickers": int(sy_df["ticker"].nunique()) if len(sy_df) else 0,
        "features": all_feats,
        "per_year": per_year,
        "overall": summary_all,
        "by_segment": by_segment,
        "feature_importance": feat_imp,
        "_last_model": last_model,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Walk-forward LightGBM vs Ridge (Colab GPU/CPU).")
    ap.add_argument("--universe", default="expanded", choices=("lq45", "syariah", "expanded"))
    ap.add_argument("--period", default="10y")
    ap.add_argument("--horizon", type=int, default=20)
    ap.add_argument("--embargo", type=int, default=20)
    ap.add_argument("--max-tickers", type=int, default=85)
    ap.add_argument("--out", default="temp/research/lgbm_report.json")
    ap.add_argument("--save-model", default="", help="Opsional: path simpan model LightGBM (.txt)")
    args = ap.parse_args(argv)

    tickers = select_universe(args.universe, max_tickers=args.max_tickers)
    print(f"[train_lgbm] universe={args.universe} ({len(tickers)} emiten), horizon={args.horizon}h, embargo={args.embargo}h")
    raw_df = ev.build_dataset(tickers, args.period, args.horizon)
    if raw_df is None or raw_df.empty:
        print("ERROR: dataset kosong.", file=sys.stderr)
        return 1

    df = add_enriched_features(raw_df)
    res = walk_forward_compare(df, horizon=args.horizon, embargo_days=args.embargo)
    model = res.pop("_last_model", None)

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")

    print("\n=== RINGKASAN OOS (ALL) ===")
    for name, m in res["overall"].items():
        print(
            f"  {name:15s} | IC={m['ic_mean']:+.4f} | t_HAC={m['t_hac']} | "
            f"Q5-Q1={m['q5_minus_q1_pct']}% | mono={m['monotonic']}"
        )
    sy = res["by_segment"].get("syariah_only")
    if sy:
        print(f"\n=== RINGKASAN OOS (SYARIAH ONLY: {res['n_syariah_tickers']} emiten) ===")
        for name, m in sy.items():
            print(
                f"  {name:15s} | IC={m['ic_mean']:+.4f} | t_HAC={m['t_hac']} | "
                f"Q5-Q1={m['q5_minus_q1_pct']}% | mono={m['monotonic']}"
            )
    print("\nTop 8 fitur paling berpengaruh (LightGBM):", list(res["feature_importance"].items())[:8])

    if args.save_model and model is not None:
        ic_r = res["overall"]["ridge_12f"]["ic_mean"] or 0.0
        ic_l = res["overall"]["lgbm_enriched"]["ic_mean"] or 0.0
        if ic_l > ic_r:
            mpath = ROOT / args.save_model
            mpath.parent.mkdir(parents=True, exist_ok=True)
            model.booster_.save_model(str(mpath))
            print(f"[OK] Model LightGBM mengalahkan Ridge ({ic_l:+.4f} > {ic_r:+.4f}), disimpan ke {mpath}")
        else:
            print(f"[SKIP] LightGBM ({ic_l:+.4f}) belum mengalahkan Ridge ({ic_r:+.4f}); model tidak diekspor.")

    print(f"\nLaporan disimpan ke {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
