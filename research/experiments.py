"""Uji ablasi hipotesis alpha & penurunan turnover (offline pada prediksi OOS).

Tujuan: menguji hipotesis peningkatan kualitas hasil portofolio secara jujur
dan tanpa look-ahead SEBELUM menyentuh bobot atau aturan di `core/`, dengan
menghitung seluruh percobaan (`n_trials`) dalam Deflated Sharpe Ratio.

Hipotesis yang diuji:
  1. Hysteresis lebih lebar (`hold=0.5..0.7`) dan masa tahan minimum (`min_hold=2`)
     untuk menekan biaya transaksi IDX (`ROUND_TRIP = 0,40%` + sensitivitas slippage).
  2. Penghalusan sinyal peringkat harian (`pred_ema5`, `pred_ema10`) yang kausal.
  3. Pengukuran ukuran kuintil (`q=0.10, 0.15, 0.20`) dan bobot invers volatilitas.
  4. Filter rezim pasar IHSG (`^JKSE`, murni kausal `<= tanggal`): kas penuh,
     setengah eksposur (`SMA200` / `SMA50>=SMA200`), dan target volatilitas.

Setiap varian regime wajib melaporkan **alpha_net** (terhadap rata-rata universe)
dan **alpha_net_active** (pada periode aktif) secara terpisah dari total `net`
return / max drawdown agar efek kurangi-beta kas tidak diklaim sebagai skill
pemilihan saham.

Pakai:
    python research/experiments.py --offline
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import regime  # noqa: E402
from research import evaluate, portfolio  # noqa: E402

DSR_GATE = 0.95
REGIME_MODES = regime.REGIME_MODES
select_names = portfolio.select_names
max_drawdown_pct = portfolio.max_drawdown_pct

DEFAULT_SPECS: tuple[dict, ...] = (
    {"name": "0_base_q20_h40", "q": 0.2, "hold": 0.4},
    {"name": "1_hyst_q20_h50", "q": 0.2, "hold": 0.5},
    {"name": "2_hyst_q20_h60", "q": 0.2, "hold": 0.6},
    {"name": "3_hyst_q20_h70", "q": 0.2, "hold": 0.7},
    {"name": "4_minhold2_h40", "q": 0.2, "hold": 0.4, "min_hold": 2},
    {"name": "5_minhold2_h60", "q": 0.2, "hold": 0.6, "min_hold": 2},
    {"name": "6_ema5_h40", "q": 0.2, "hold": 0.4, "score_col": "pred_ema5"},
    {"name": "7_ema10_h40", "q": 0.2, "hold": 0.4, "score_col": "pred_ema10"},
    {"name": "8_invvol_q20_h50", "q": 0.2, "hold": 0.5, "inv_vol": True},
    {"name": "9_q15_h50", "q": 0.15, "hold": 0.5},
    {"name": "10_q10_h40", "q": 0.10, "hold": 0.4},
    {"name": "11_reg_sma200_cash_h50", "q": 0.2, "hold": 0.5, "regime": "sma200_cash"},
    {"name": "12_reg_sma200_half_h50", "q": 0.2, "hold": 0.5, "regime": "sma200_half"},
    {"name": "13_reg_sma50_200_half_h50", "q": 0.2, "hold": 0.5, "regime": "sma50_200_half"},
    {"name": "14_reg_voltgt14_h50", "q": 0.2, "hold": 0.5, "regime": "vol_target_14"},
    {"name": "15_m2_h60_sma50_200_half", "q": 0.2, "hold": 0.6, "min_hold": 2,
     "regime": "sma50_200_half"},
)

def load_ihsg_frame(step: int = 20, period: str = "10y",
                    offline: bool = False) -> pd.DataFrame:
    """Ambil OHLCV `^JKSE` dan susun fitur rezim kausal + pembanding horizon."""
    raw = portfolio.load_ihsg_raw(period=period, offline=offline)
    return ihsg_regime_frame(raw, step=step)


def ihsg_regime_frame(df: pd.DataFrame | None, step: int = 20) -> pd.DataFrame:
    """Indikator rezim kausal `core.regime` + return forward pembanding `step` hari."""
    out = regime.ihsg_indicators(df)
    if out.empty:
        return out
    out["ihsg_fwd"] = out["close"].shift(-step) / out["close"] - 1.0
    return out


def exposure_for_row(row: pd.Series | None, mode: str) -> float:
    """Tentukan fraksi modal saham `[0..1]` dari baris rezim pada tanggal rebalance."""
    if row is None:
        if mode not in REGIME_MODES:
            raise ValueError(f"mode regime tidak dikenal: {mode}")
        return 1.0
    return regime.exposure_from_indicators(
        row.get("close"),
        row.get("sma50"),
        row.get("sma200"),
        row.get("vol20"),
        mode=mode,
    )


def add_smoothed_scores(oos: pd.DataFrame) -> pd.DataFrame:
    """Tambah peringkat persentil & EMA kausal per emiten (`<= tanggal`)."""
    d = oos.copy()
    d["date"] = pd.to_datetime(d["date"]).dt.normalize()
    d = d.sort_values(["ticker", "date"])
    d["pred_pct"] = d.groupby("date")["pred"].rank(pct=True)
    d["pred_ema5"] = d.groupby("ticker")["pred_pct"].transform(
        lambda s: s.ewm(span=5, adjust=False).mean()
    )
    d["pred_ema10"] = d.groupby("ticker")["pred_pct"].transform(
        lambda s: s.ewm(span=10, adjust=False).mean()
    )
    return d


def _build_weights(
    sub: pd.DataFrame, exposure: float, inv_vol: bool
) -> dict[str, float]:
    if sub.empty or exposure <= 0.0:
        return {}
    if inv_vol and "volat20" in sub.columns:
        valid_vol = pd.to_numeric(sub["volat20"], errors="coerce")
        if valid_vol.notna().any():
            inv = (1.0 / valid_vol.clip(lower=0.005)).fillna(1.0)
            norm = inv / inv.sum()
            return {t: float(w) * exposure
                    for t, w in zip(sub["ticker"], norm, strict=True)}
    w = exposure / len(sub)
    return {t: w for t in sub["ticker"]}


def _prepare_groups(
    oos: pd.DataFrame, score_col: str
) -> tuple[np.ndarray, dict[pd.Timestamp, pd.DataFrame]]:
    clean = oos.dropna(subset=[score_col, "fwd"]).copy()
    clean["date"] = pd.to_datetime(clean["date"]).dt.normalize()
    groups = {pd.Timestamp(d): g for d, g in clean.groupby("date", sort=True)}
    dates = np.array(sorted(groups))
    return dates, groups


def simulate_variant(
    oos: pd.DataFrame,
    ihsg_df: pd.DataFrame | None = None,
    *,
    name: str,
    q: float = 0.2,
    hold: float = 0.4,
    step: int = 20,
    min_hold: int = 1,
    score_col: str = "pred",
    regime: str = "none",
    inv_vol: bool = False,
    extra_rt_bps: float = 0.0,
    _precomputed: tuple[np.ndarray, dict[pd.Timestamp, pd.DataFrame]] | None = None,
) -> dict:
    """Jalankan semua `step` fase untuk satu konfigurasi strategi."""
    dates, groups = _precomputed if _precomputed is not None else _prepare_groups(oos, score_col)
    if len(dates) == 0:
        return {"name": name, "error": "prediksi OOS kosong"}

    nets: list[np.ndarray] = []
    alphas_net: list[float] = []
    alphas_gross: list[float] = []
    active_alphas: list[float] = []
    vs_ihsg_means: list[float] = []
    turnovers: list[float] = []
    exposures: list[float] = []
    mdds: list[float] = []
    yearly_rows: list[dict] = []
    has_ihsg = ihsg_df is not None and not ihsg_df.empty

    for off in range(step):
        periods = dates[off::step]
        prev_w: dict[str, float] = {}
        prev_age: dict[str, int] = {}
        p_gross, p_net, p_bench, p_ihsg, p_turn, p_exp = [], [], [], [], [], []
        for d in periods:
            g = groups[pd.Timestamp(d)]
            if len(g) < 5:
                continue
            key = pd.Timestamp(d).normalize()
            irow = ihsg_df.loc[key] if (has_ihsg and key in ihsg_df.index) else None
            exp = exposure_for_row(irow, regime)

            if exp <= 0.0:
                cur_w: dict[str, float] = {}
                new_age: dict[str, int] = {}
                gross = 0.0
                active_alpha = np.nan
            else:
                names = select_names(
                    g, prev_age, q, hold, min_hold=min_hold, score_col=score_col
                )
                sub = g[g["ticker"].isin(names)]
                cur_w = _build_weights(sub, exp, inv_vol)
                new_age = {t: prev_age.get(t, 0) + 1 for t in cur_w}
                gross = sum(
                    cur_w[t] * float(f)
                    for t, f in zip(sub["ticker"], sub["fwd"], strict=True)
                )
                active_alpha = (gross / exp) - float(g["fwd"].mean())

            tc = portfolio.turnover_cost(prev_w, cur_w)
            slip_cost = tc["turnover"] * (extra_rt_bps / 10_000.0)
            total_cost = tc["cost"] + slip_cost
            bench = float(g["fwd"].mean())
            net = gross - total_cost
            r_ihsg = (
                float(irow["ihsg_fwd"])
                if (irow is not None and pd.notna(irow.get("ihsg_fwd")))
                else np.nan
            )

            p_gross.append(gross)
            p_net.append(net)
            p_bench.append(bench)
            p_ihsg.append(r_ihsg)
            p_turn.append(tc["turnover"])
            p_exp.append(exp)
            if not math.isnan(active_alpha):
                active_alphas.append(active_alpha)
            yearly_rows.append({"year": key.year, "alpha_net": net - bench,
                                "net": net, "turn": tc["turnover"]})
            prev_w, prev_age = cur_w, new_age

        if not p_net:
            continue
        arr_net = np.asarray(p_net, dtype=float)
        arr_gross = np.asarray(p_gross, dtype=float)
        arr_bench = np.asarray(p_bench, dtype=float)
        arr_ihsg = np.asarray(p_ihsg, dtype=float)
        nets.append(arr_net)
        alphas_gross.append(float((arr_gross - arr_bench).mean()) * 100.0)
        alphas_net.append(float((arr_net - arr_bench).mean()) * 100.0)
        valid_ih = ~np.isnan(arr_ihsg)
        if valid_ih.any():
            vs_ihsg_means.append(float((arr_net[valid_ih] - arr_ihsg[valid_ih]).mean()) * 100.0)
        turnovers.append(float(np.mean(p_turn)) * 100.0)
        exposures.append(float(np.mean(p_exp)) * 100.0)
        mdds.append(max_drawdown_pct(arr_net))

    if not nets:
        return {"name": name, "error": "tidak ada periode valid"}

    dsr_single = portfolio.deflated_sharpe(nets)
    srs = [s for s in (portfolio.sharpe_ratio(x) for x in nets) if s is not None]
    ci = portfolio.moving_block_bootstrap_ci(alphas_net, iters=2000, block=4)
    net_means = [float(x.mean()) * 100.0 for x in nets]
    cum_means = [float(np.prod(1.0 + x) - 1.0) * 100.0 for x in nets]

    ydf = pd.DataFrame(yearly_rows)
    by_year = {
        int(y): {
            "alpha_net_pct": round(float(grp["alpha_net"].mean()) * 100.0, 3),
            "net_pct": round(float(grp["net"].mean()) * 100.0, 3),
            "turnover_pct": round(float(grp["turn"].mean()) * 100.0, 1),
        }
        for y, grp in ydf.groupby("year")
    }

    return {
        "name": name,
        "n_phases": len(nets),
        "exposure_pct": round(float(np.mean(exposures)), 1),
        "turnover_pct": round(float(np.mean(turnovers)), 1),
        "net_mean_pct": round(float(np.mean(net_means)), 3),
        "cum_mean_pct": round(float(np.mean(cum_means)), 1),
        "mdd_mean_pct": round(float(np.mean(mdds)), 1),
        "sharpe_mean": round(float(np.mean(srs)), 3) if srs else None,
        "alpha_gross_pct": round(float(np.mean(alphas_gross)), 3),
        "alpha_net_pct": round(float(np.mean(alphas_net)), 3),
        "alpha_active_gross_pct": round(float(np.mean(active_alphas)) * 100.0, 3)
        if active_alphas else None,
        "ci95_alpha_net": [ci["lo"], ci["hi"]],
        "phases_net_positive": int(sum(1 for a in alphas_net if a > 0)),
        "alpha_vs_ihsg_pct": round(float(np.mean(vs_ihsg_means)), 3)
        if vs_ihsg_means else None,
        "dsr_20": dsr_single["dsr"],
        "best_sr": dsr_single["best_sr"],
        "sr0_20": dsr_single["sr0"],
        "by_year": by_year,
        "_nets": nets,
    }


def run_ablation(
    oos: pd.DataFrame,
    ihsg_df: pd.DataFrame | None = None,
    specs: tuple[dict, ...] = DEFAULT_SPECS,
    step: int = 20,
) -> dict:
    """Jalankan seluruh hipotesis ablasi dan hitung DSR jujur atas semua trial."""
    enriched = add_smoothed_scores(oos)
    cache_groups: dict[str, tuple[np.ndarray, dict[pd.Timestamp, pd.DataFrame]]] = {}
    results = []
    for sp in specs:
        sc = sp.get("score_col", "pred")
        if sc not in cache_groups:
            cache_groups[sc] = _prepare_groups(enriched, sc)
        kw = {k: v for k, v in sp.items() if k != "name"}
        results.append(
            simulate_variant(
                enriched, ihsg_df, name=sp["name"], step=step,
                _precomputed=cache_groups[sc], **kw
            )
        )

    total_trials = sum(r.get("n_phases", 0) for r in results)
    clean_variants = []
    for r in results:
        nets = r.pop("_nets", [])
        dsr_all = portfolio.deflated_sharpe(nets, n_trials=max(2, total_trials))
        r["n_trials_all"] = total_trials
        r["dsr_all"] = dsr_all["dsr"]
        r["sr0_all"] = dsr_all["sr0"]
        r["passes_dsr_gate"] = bool(
            dsr_all["dsr"] is not None and dsr_all["dsr"] >= DSR_GATE
        )
        clean_variants.append(r)

    slip_rows = []
    for bps in (0, 20, 40, 60):
        base = simulate_variant(
            enriched, ihsg_df, name="0_base_q20_h40", q=0.2, hold=0.4,
            step=step, extra_rt_bps=float(bps),
            _precomputed=cache_groups["pred"],
        )
        m2 = simulate_variant(
            enriched, ihsg_df, name="5_minhold2_h60", q=0.2, hold=0.6,
            min_hold=2, step=step, extra_rt_bps=float(bps),
            _precomputed=cache_groups["pred"],
        )
        base.pop("_nets", None)
        m2.pop("_nets", None)
        slip_rows.append({
            "extra_rt_bps": bps,
            "total_rt_bps": 40 + bps,
            "base_alpha_net_pct": base["alpha_net_pct"],
            "minhold2_h60_alpha_net_pct": m2["alpha_net_pct"],
        })

    return {
        "n_configs": len(clean_variants),
        "total_trials": total_trials,
        "dsr_gate": DSR_GATE,
        "variants": clean_variants,
        "slippage_sensitivity": slip_rows,
    }


HORIZON_SPECS: tuple[dict, ...] = (
    {"name": "q20_h40", "q": 0.2, "hold": 0.4},
    {"name": "q20_h60", "q": 0.2, "hold": 0.6},
    {"name": "m2_h60", "q": 0.2, "hold": 0.6, "min_hold": 2},
    {"name": "m2_h60_sma50_200_half", "q": 0.2, "hold": 0.6, "min_hold": 2,
     "regime": "sma50_200_half"},
)


def build_forward_dataset(ds: pd.DataFrame, horizon: int) -> tuple[pd.DataFrame, list[str]]:
    """Hitung target `fwd` kausal-bar `horizon` hari bursa ke depan per emiten."""
    if horizon < 1:
        raise ValueError("horizon harus >= 1")
    d = ds.sort_values(["ticker", "date"]).copy()
    d["fwd"] = d.groupby("ticker")["close"].transform(
        lambda s: s.shift(-horizon) / s - 1.0
    )
    d = d.dropna(subset=["fwd"]).copy()
    feats = [c for c in d.columns if c.startswith("f_")]
    return d, feats


def run_horizon_comparison(
    ds: pd.DataFrame,
    ihsg_raw: pd.DataFrame | None = None,
    horizons: tuple[int, ...] = (20, 40, 60),
    years: tuple[int, ...] = (2022, 2023, 2024, 2025, 2026),
) -> list[dict]:
    """Bandingkan walk-forward Ridge & portofolio antar-horizon (mis. 20/40/60 hari)."""
    rows: list[dict] = []
    for h in horizons:
        d, feats = build_forward_dataset(ds, h)
        if d.empty or not feats:
            rows.append({"horizon": int(h), "error": "dataset horizon kosong"})
            continue
        if "score" not in d.columns:
            d["score"] = 0.0
        wf, _, oos = evaluate.walk_forward(d, feats, h, list(years), 0)
        wf_sum = evaluate._wf_summary(wf)
        if oos is None or oos.empty:
            rows.append({
                "horizon": int(h),
                "walk_forward_summary": wf_sum,
                "error": "prediksi OOS kosong",
            })
            continue
        oos_vol = oos.merge(
            d[["date", "ticker", "f_volat"]].rename(columns={"f_volat": "volat20"}),
            on=["date", "ticker"], how="left",
        ) if "f_volat" in d.columns else oos
        ic_oos = evaluate.xs_ic(oos_vol, "pred")
        ic_sum = evaluate.ic_summary(ic_oos, nlags=h)
        ihsg_df = ihsg_regime_frame(ihsg_raw, step=h)
        ab = run_ablation(oos_vol, ihsg_df, specs=HORIZON_SPECS, step=h)
        rows.append({
            "horizon": int(h),
            "walk_forward_summary": wf_sum,
            "oos_ic": ic_sum,
            "variants": ab["variants"],
            "total_trials": ab["total_trials"],
        })
    return rows


def render_md(report: dict) -> str:
    """Format hasil ablasi sebagai laporan markdown yang jujur terhadap bukti."""
    L = [
        "# Laporan Ablasi Hipotesis Portofolio SahamBuddy\n",
        f"- Konfigurasi diuji: **{report['n_configs']}** · Total phase-trials: "
        f"**{report['total_trials']}** · Gate DSR jujur: **>= {report['dsr_gate']:.2f}**\n",
        "| Varian | Exp% | Turn% | Net% | Cum% | MDD% | SR | αNet% | CI95 | +Fase | DSR20 | DSRall | Gate |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for v in report["variants"]:
        if "error" in v:
            L.append(f"| `{v['name']}` | — | — | — | — | — | — | — | — | — | — | — | ERROR |")
            continue
        ci = v["ci95_alpha_net"]
        ci_s = f"[{ci[0]:+.2f}, {ci[1]:+.2f}]" if ci[0] is not None else "—"
        gate = "PASS" if v["passes_dsr_gate"] else "BELOW GATE"
        L.append(
            f"| `{v['name']}` | {v['exposure_pct']:.1f} | {v['turnover_pct']:.1f} | "
            f"{v['net_mean_pct']:+.3f} | {v['cum_mean_pct']:+.1f} | {v['mdd_mean_pct']:+.1f} | "
            f"{v['sharpe_mean']:.3f} | {v['alpha_net_pct']:+.3f} | {ci_s} | "
            f"{v['phases_net_positive']}/{v['n_phases']} | {v['dsr_20']:.3f} | "
            f"{v['dsr_all']:.3f} | {gate} |"
        )
    L.append("\n## Sensitivitas Slippage Eksekusi (Biaya Broker 40 bps + Slippage)\n")
    L.append("| Extra Slippage | Total Biaya RT | Baseline αNet% | MinHold2+H60 αNet% |")
    L.append("|---|---|---|---|")
    for s in report["slippage_sensitivity"]:
        L.append(
            f"| +{s['extra_rt_bps']} bps | {s['total_rt_bps']} bps | "
            f"{s['base_alpha_net_pct']:+.3f}% | {s['minhold2_h60_alpha_net_pct']:+.3f}% |"
        )
    hz = report.get("horizon_comparison") or []
    if hz:
        L.append("\n## Perbandingan Horizon Walk-Forward (OOS)\n")
        L.append(
            "| Horizon | WF IC Model | WF IC Eq | OOS IC (t_hac) | Varian | Turn% | MDD% | SR | αNet% | +Fase | DSR |"
        )
        L.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for row in hz:
            if "error" in row:
                L.append(f"| {row['horizon']}d | — | — | — | ERROR | — | — | — | — | — | — |")
                continue
            wfs = row.get("walk_forward_summary") or {}
            ic = row.get("oos_ic") or {}
            ic_m = wfs.get("ic_model_mean")
            ic_e = wfs.get("ic_eq_mean")
            ic_o = ic.get("mean_ic")
            t_h = ic.get("t_hac")
            for idx, v in enumerate(row.get("variants") or []):
                h_col = f"{row['horizon']}d" if idx == 0 else ""
                m_col = f"{ic_m:+.4f}" if (idx == 0 and ic_m is not None) else ""
                e_col = f"{ic_e:+.4f}" if (idx == 0 and ic_e is not None) else ""
                o_col = f"{ic_o:+.4f} ({t_h})" if (idx == 0 and ic_o is not None) else ""
                sr_s = f"{v['sharpe_mean']:.3f}" if v.get("sharpe_mean") is not None else "—"
                L.append(
                    f"| {h_col} | {m_col} | {e_col} | {o_col} | `{v['name']}` | "
                    f"{v['turnover_pct']:.1f} | {v['mdd_mean_pct']:+.1f} | {sr_s} | "
                    f"{v['alpha_net_pct']:+.3f} | {v['phases_net_positive']}/{v['n_phases']} | "
                    f"{v['dsr_all']:.3f} |"
                )
    return "\n".join(L) + "\n"


def _attach_volat(oos: pd.DataFrame, dataset_path: str) -> pd.DataFrame:
    if "volat20" in oos.columns or not os.path.exists(dataset_path):
        return oos
    ds = pd.read_pickle(dataset_path)
    if "f_volat" not in ds.columns:
        return oos
    sub = ds[["date", "ticker", "f_volat"]].rename(columns={"f_volat": "volat20"}).copy()
    sub["date"] = pd.to_datetime(sub["date"]).dt.normalize()
    out = oos.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    return out.merge(sub, on=["date", "ticker"], how="left")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default=os.path.join("temp", "research", "oos_predictions.pkl"))
    ap.add_argument("--dataset", default=os.path.join("temp", "research", "dataset_10y_45.pkl"))
    ap.add_argument("--out", default=os.path.join("temp", "research"))
    ap.add_argument("--step", type=int, default=20)
    ap.add_argument("--horizons", nargs="+", type=int, default=None,
                    help="bandingkan walk-forward multi-horizon pada dataset (mis. 20 40 60)")
    ap.add_argument("--offline", action="store_true",
                    help="baca ^JKSE dari cache lokal tanpa memanggil Yahoo")
    args = ap.parse_args(argv)

    if not os.path.exists(args.src):
        raise SystemExit(f"{args.src} belum ada — jalankan research/evaluate.py dulu")

    ihsg_raw = portfolio.load_ihsg_raw(offline=args.offline)
    oos = _attach_volat(pd.read_pickle(args.src), args.dataset)
    ihsg_df = ihsg_regime_frame(ihsg_raw, step=args.step)
    report = run_ablation(oos, ihsg_df, step=args.step)

    if args.horizons:
        if not os.path.exists(args.dataset):
            raise SystemExit(f"{args.dataset} belum ada untuk uji --horizons")
        ds = pd.read_pickle(args.dataset)
        hz_rows = run_horizon_comparison(
            ds, ihsg_raw, horizons=tuple(args.horizons)
        )
        report["horizon_comparison"] = hz_rows
        report["total_trials_with_horizons"] = (
            report["total_trials"] + sum(int(r.get("total_trials", 0)) for r in hz_rows)
        )

    os.makedirs(args.out, exist_ok=True)
    json_path = os.path.join(args.out, "experiments.json")
    md_path = os.path.join(args.out, "experiments.md")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    md = render_md(report)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    print(md)
    print(f"ditulis: {json_path} & {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
