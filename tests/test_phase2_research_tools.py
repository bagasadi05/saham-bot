"""Tes offline Wave 2A: sinkronisasi riset, PSR/IQR, scorecard, & tools."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

from core import marketcal, store
from research import evaluate, experiments, portfolio, publish
from tools import check_sync, record_fixtures


def _synthetic_oos(n_dates: int = 120, n_tickers: int = 10) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2023-01-02", periods=n_dates)
    rows = []
    tickers = [f"T{k:02d}" for k in range(n_tickers)]
    for d in dates:
        preds = np.linspace(-0.02, 0.02, n_tickers) + rng.normal(0.0, 0.002, n_tickers)
        fwds = 0.004 + 0.6 * preds + rng.normal(0.0, 0.015, n_tickers)
        ranks = pd.Series(preds).rank(method="first")
        deciles = pd.qcut(ranks, 5, labels=False)
        for idx, t in enumerate(tickers):
            rows.append({
                "date": d,
                "ticker": t,
                "score": 50.0 + idx * 3.0,
                "pred": float(preds[idx]),
                "fwd": float(fwds[idx]),
                "decile": int(deciles.iloc[idx]),
                "volat20": 0.02 + 0.002 * (idx % 3),
            })
    return pd.DataFrame(rows)


def test_probabilistic_sharpe_properties_and_deflated_sharpe():
    assert portfolio.probabilistic_sharpe([]) is None
    assert portfolio.probabilistic_sharpe([0.01, 0.02]) is None
    assert portfolio.probabilistic_sharpe([0.01] * 10) is None

    pos = np.array([0.02, 0.015, 0.025, 0.018, 0.03, 0.012, 0.022, 0.019])
    neg = -pos
    psr_pos = portfolio.probabilistic_sharpe(pos)
    psr_neg = portfolio.probabilistic_sharpe(neg)
    assert psr_pos is not None and psr_pos > 0.95
    assert psr_neg is not None and psr_neg < 0.05

    # Skewness negatif + ekor gemuk menurunkan keyakinan PSR pada SR > 0 yang sama
    rng = np.random.default_rng(11)
    sym = rng.normal(0.015, 0.02, size=80)
    skewed = np.concatenate([np.full(78, 0.019), np.array([-0.14, -0.14])])
    # Samakan mean/std kira-kira agar fokus uji penalti momen ke-3/4
    skewed_norm = (skewed - skewed.mean()) / skewed.std(ddof=1) * sym.std(ddof=1) + sym.mean()
    assert portfolio.probabilistic_sharpe(skewed_norm) < portfolio.probabilistic_sharpe(sym)

    dsr = portfolio.deflated_sharpe([pos, pos * 0.9, neg * -0.5])
    assert set(dsr) == {"n_trials", "best_sr", "sr0", "dsr"}
    psr_best = portfolio.best_phase_psr([pos, pos * 0.9, neg * -0.5])
    assert psr_best is not None and 0.0 <= psr_best <= 1.0


def test_portfolio_run_and_main_record_psr_and_cli_metadata(tmp_path):
    oos = _synthetic_oos(n_dates=140, n_tickers=10)
    res = portfolio.run(oos, q=0.2, step=10, hold=0.6, ihsg=pd.Series(dtype=float), min_hold=2)
    assert res["n_phases"] == 10
    assert all("psr" in p for p in res["phases"])
    assert res["across_phases"]["psr_mean"] is not None
    assert res["probabilistic_sharpe"]["psr"] is not None
    assert set(res["deflated_sharpe"]) == {"n_trials", "best_sr", "sr0", "dsr"}

    src_pkl = tmp_path / "oos_predictions.pkl"
    out_dir = tmp_path / "out"
    oos.to_pickle(src_pkl)
    portfolio.main([
        "--src", str(src_pkl),
        "--out", str(out_dir),
        "--step", "10",
        "--hold", "0.6",
        "--min-hold", "2",
        "--no-ihsg",
    ])
    written = json.loads((out_dir / "portfolio.json").read_text(encoding="utf-8"))
    assert "+07:00" in written["portfolio_generated"]
    assert written["cli_params"]["hold"] == 0.6
    assert written["cli_params"]["min_hold"] == 2
    assert written["cli_params"]["no_ihsg"] is True
    assert "--hold 0.6" in written["cli_command"]
    assert "--min-hold 2" in written["cli_command"]
    assert written["across_phases"]["psr_mean"] is not None


def test_experiments_ablation_and_main_record_psr_and_trials(tmp_path, monkeypatch):
    oos = _synthetic_oos(n_dates=100, n_tickers=10)
    monkeypatch.setattr(portfolio, "load_ihsg_raw", lambda offline=False: None)
    src_pkl = tmp_path / "oos.pkl"
    out_dir = tmp_path / "exp_out"
    oos.to_pickle(src_pkl)

    rc = experiments.main([
        "--src", str(src_pkl),
        "--out", str(out_dir),
        "--step", "10",
        "--offline",
    ])
    assert rc == 0
    rep = json.loads((out_dir / "experiments.json").read_text(encoding="utf-8"))
    md = (out_dir / "experiments.md").read_text(encoding="utf-8")
    assert "+07:00" in rep["generated"]
    assert rep["cli_params"] == {"step": 10, "horizons": None, "offline": True}
    assert rep["experiments_trials"]["n_configs"] == rep["n_configs"]
    assert rep["experiments_trials"]["total_phase_trials"] == rep["total_trials"]
    for v in rep["variants"]:
        if "error" not in v:
            assert "psr" in v and "psr_mean" in v and "psr_all" in v
    assert "| PSR |" in md


def test_evaluate_summarize_quintile_bands_and_publish_payload():
    oos = _synthetic_oos(n_dates=40, n_tickers=10)
    bands = evaluate.summarize_quintile_bands(oos)
    assert [b["quintile"] for b in bands] == [1, 2, 3, 4, 5]
    for b in bands:
        assert b["p25_pct"] <= b["median_pct"] <= b["p75_pct"]
        assert abs(b["iqr_pct"] - round(b["p75_pct"] - b["p25_pct"], 3)) <= 0.01

    ev = {
        "meta": {"n_tickers": 10, "n_rows": len(oos), "start": "2023-01-02",
                 "end": "2023-02-24", "horizon": 20, "period": "1y"},
        "ic_score": {"mean_ic": 0.01, "ir": 0.05, "t": 1.1, "t_hac": 0.7, "n_dates": 40},
        "ic_timeseries": {"n_tickers": 10, "mean_ic": -0.01, "t": -0.5},
        "years": [{"year": 2023, "n": len(oos), "mean_fwd_pct": 0.3, "win_rate": 50.0, "ic": 0.01}],
        "deciles": [{"decile": 1, "n": 40, "excess_ret_pct": -0.1, "win_rate": 45.0}],
        "ratings": [{"rating": "BUY", "n": 40, "mean_pct": 0.4, "excess_pct": 0.2,
                     "excess_win_rate": 48.0, "ci95_excess_daily_pct": [-0.1, 0.5]}],
        "features": [{"feature": "f_rsi", "mean_ic": 0.02, "ir": 0.1, "t": 1.5}],
        "walk_forward": [{"year": 2023, "n_train": 200, "n_test": 200,
                          "ic_score": 0.01, "ic_eq": 0.0, "ic_model": 0.03,
                          "lam_rel": 0.01, "ic_blend": 0.025,
                          "lam_cv": {"folds": 4, "n_candidates": 4}}],
        "walk_forward_summary": {
            "n_years": 1, "ic_model_mean": 0.03, "ic_eq_mean": 0.0,
            "ic_score_mean": 0.01, "years_model_beats_eq": 1,
            "lam_rel": [0.01], "cv_trials": 16,
        },
        "oos_quintiles": [
            {"quintile": q, "n": 80, "excess_pct": -0.2 + 0.15 * q, "win_rate": 45.0}
            for q in (1, 2, 3, 4, 5)
        ],
        "production_weights": {"f_rsi": 0.01},
    }
    md = evaluate.render_md(ev)
    assert "P25..P75 (IQR)" in md

    p = {
        "portfolio_generated": "2026-11-14T08:00:00+07:00",
        "cli_params": {"quintile": 0.2, "step": 20, "hold": 0.6, "min_hold": 2,
                       "regime": "sma50_200_half"},
        "cli_command": "python research/portfolio.py --hold 0.6 --min-hold 2 --regime sma50_200_half",
        "n_phases": 20, "holding_days": 20, "cost_per_round_trip_pct": 0.4,
        "avg_turnover_pct": 21.7, "avg_turnover_two_way_pct": 43.4,
        "quintile": 0.2, "hold_quintile": 0.6, "hysteresis": True,
        "min_hold": 2, "regime": "sma50_200_half",
        "deflated_sharpe": {"n_trials": 20, "best_sr": 0.279, "sr0": 0.142, "dsr": 0.86},
        "probabilistic_sharpe": {"sr_ref": 0.0, "psr": 0.91, "psr_mean": 0.82},
        "across_phases": {"avg_exposure_pct": 79.8, "max_drawdown_mean_pct": -20.0,
                          "psr_mean": 0.82, "alpha_gross_mean_pct": 0.416,
                          "alpha_net_mean_pct": 0.33, "alpha_net_median_pct": 0.385,
                          "phases_net_positive": 16},
        "ci95_alpha_net": {"lo": 0.243, "hi": 0.522},
    }
    exp = {"n_configs": 16, "total_trials": 320, "total_trials_with_horizons": 560, "dsr_gate": 0.95}
    payload = publish.build_validation_payload(ev, p=p, exp=exp, oos_df=oos)
    assert payload["meta"]["weights_synced_to"] == "core/relatif.py"
    assert "+07:00" in payload["meta"]["published_at"]
    assert all("iqr_pct" in q and "p25_pct" in q and "p75_pct" in q for q in payload["quintiles"])
    assert payload["portfolio"]["psr"] == 0.91
    assert payload["portfolio"]["psr_mean"] == 0.82
    assert payload["portfolio"]["experiments_trials"]["portfolio_phases"] == 20
    assert payload["portfolio"]["experiments_trials"]["cv_trials"] == 16
    assert payload["portfolio"]["experiments_trials"]["ablation_configs"] == 16
    assert payload["portfolio"]["experiments_trials"]["ablation_phase_trials"] == 320
    assert payload["portfolio"]["experiments_trials"]["total_trials_with_horizons"] == 560


def test_validation_json_empirical_metrics_preserved_and_readme_synced():
    root = Path(__file__).resolve().parents[1]
    val = json.loads((root / "data" / "validation.json").read_text(encoding="utf-8"))
    assert val["meta"]["n_tickers"] == 45
    assert val["meta"]["n_rows"] == 91395
    assert val["score"]["ic"]["t_hac"] == 0.85
    assert val["walk_forward_summary"]["ic_model_mean"] == 0.0304
    assert val["walk_forward_summary"]["ic_eq_mean"] == -0.0061
    assert val["portfolio"]["alpha_net_mean_pct"] == 0.33
    assert val["portfolio"]["cli_params"]["hold"] == 0.6
    assert val["portfolio"]["cli_params"]["min_hold"] == 2
    assert val["portfolio"]["cli_params"]["regime"] == "sma50_200_half"
    assert val["portfolio"]["experiments_trials"]["total_trials"] == 320

    readme = (root / "research" / "README.md").read_text(encoding="utf-8")
    for token in (
        "--hold 0.6 --min-hold 2 --regime sma50_200_half",
        "0,40%",
        "temp/research/evaluation.md",
        "tools/check_sync.py",
        "PSR",
        "+0,0304",
        "+0,330%",
    ):
        assert token in readme


def test_check_sync_passes_and_catches_drift_and_staleness(tmp_path, capsys):
    orig_db, orig_file, orig_ready = store.STORE_DB, store.STORE_FILE, store._ready_for
    assert check_sync.main(["--json"]) == 0
    captured = json.loads(capsys.readouterr().out)
    assert captured["ok"] is True
    assert {c["name"]: c["ok"] for c in captured["checks"]} == {
        "relatif_sync": True,
        "lq45_universe": True,
        "data_freshness": True,
        "backup_restore_roundtrip": True,
    }
    assert (store.STORE_DB, store.STORE_FILE, store._ready_for) == (orig_db, orig_file, orig_ready)

    # (a) Drift pada bobot -> wajib FAIL
    val = json.loads(check_sync.DEFAULT_VALIDATION.read_text(encoding="utf-8"))
    val["weights"]["f_rsi"] = 0.99999
    bad_val = tmp_path / "bad_val.json"
    bad_val.write_text(json.dumps(val), encoding="utf-8")
    res_drift = check_sync.check_weights_and_quintiles(bad_val)
    assert res_drift["ok"] is False
    assert any("rsi" in e for e in res_drift["errors"])

    # (b) LQ45 hilang dari universe -> wajib FAIL
    uni = json.loads(check_sync.DEFAULT_UNIVERSE.read_text(encoding="utf-8"))
    uni["stocks"] = [s for s in uni["stocks"] if s.get("code") != "BBCA"]
    bad_uni = tmp_path / "bad_uni.json"
    bad_uni.write_text(json.dumps(uni), encoding="utf-8")
    res_uni = check_sync.check_lq45_in_universe(bad_uni)
    assert res_uni["ok"] is False
    assert any("BBCA" in e for e in res_uni["errors"])

    # (c) Data stale -> warning default (ok=True), error saat strict_age=True
    future = dt.datetime(2027, 3, 1, 10, 0, tzinfo=marketcal.WIB)
    res_warn = check_sync.check_data_freshness(strict_age=False, now=future)
    assert res_warn["ok"] is True
    assert len(res_warn["warnings"]) == 2
    res_strict = check_sync.check_data_freshness(strict_age=True, now=future)
    assert res_strict["ok"] is False
    assert len(res_strict["errors"]) == 2


def test_record_fixtures_verify_and_research_workflow_contract(tmp_path, monkeypatch):
    ok, msgs = record_fixtures.verify_fixtures()
    assert ok is True
    assert len(msgs) >= 3
    assert record_fixtures.main(["--verify"]) == 0

    called = {}
    monkeypatch.setattr(record_fixtures, "FIX", tmp_path)
    monkeypatch.setattr(
        record_fixtures, "record_ohlcv",
        lambda t, p, r: called.update({"ohlcv": (t, p, r)}) or True,
    )
    monkeypatch.setattr(record_fixtures, "record_stock_summary", lambda r: True)
    monkeypatch.setattr(record_fixtures, "record_index_summary", lambda: True)
    assert record_fixtures.main(["--ticker", "BBCA", "--ohlcv-rows", "0"]) == 1
    assert called == {}
    assert record_fixtures.main(["--ticker", "BBCA", "--ohlcv-rows", "120"]) == 0
    assert called["ohlcv"] == ("BBCA", "1y", 120)

    fail_ok, fail_msgs = record_fixtures.verify_fixtures(tmp_path)
    assert fail_ok is False
    assert any("ohlcv" in m for m in fail_msgs)

    root = Path(__file__).resolve().parents[1]
    wf = (root / ".github" / "workflows" / "research.yml").read_text(encoding="utf-8")
    assert "python tools/check_sync.py" in wf
    assert "python tools/record_fixtures.py --verify" in wf
    assert "tests/test_phase2_research_tools.py" in wf
    assert "429" in wf and "Too Many Requests" in wf
    assert 'if [ "$rc" -eq 2 ]' in wf
