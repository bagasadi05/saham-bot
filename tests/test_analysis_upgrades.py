"""Unit test untuk 5 peningkatan analisis: pivot S/R, CMF, RSI divergence, chart OHLC, valuasi sektor, dan RS vs IHSG."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import analysis, banding, chart, fundamental, learn


def _make_ohlcv(closes, volumes=None):
    close = np.asarray(closes, dtype=float)
    n = len(close)
    idx = pd.bdate_range("2025-01-01", periods=n)
    vol = np.full(n, 2_000_000.0) if volumes is None else np.asarray(volumes, dtype=float)
    return pd.DataFrame(
        {
            "open": close * 0.995,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "volume": vol,
        },
        index=idx,
    )


def test_support_resistance_swing_pivots_dan_jarak():
    wave = 1000 + 80 * np.sin(np.linspace(0, 6 * np.pi, 70))
    df = _make_ohlcv(wave)
    sr = analysis.support_resistance(df)
    assert sr["swing_support"] <= float(df["close"].iloc[-1])
    assert sr["swing_resistance"] >= float(df["close"].iloc[-1])
    assert sr["dist_support_pct"] is not None and sr["dist_support_pct"] >= 0
    assert sr["dist_resistance_pct"] is not None and sr["dist_resistance_pct"] >= 0


def test_cmf_dan_rsi_bullish_divergence():
    closes = np.linspace(1200, 1000, 40)
    df = _make_ohlcv(closes)
    ind = analysis.compute_indicators(df)
    assert "cmf20" in ind.columns
    assert -1.0 <= float(ind["cmf20"].iloc[-1]) <= 1.0

    ind["low"] = 1000.0
    ind["high"] = 1100.0
    ind["rsi14"] = 50.0
    ind.iloc[-20, ind.columns.get_loc("low")] = 950.0
    ind.iloc[-20, ind.columns.get_loc("rsi14")] = 25.0
    ind.iloc[-6, ind.columns.get_loc("low")] = 920.0
    ind.iloc[-6, ind.columns.get_loc("rsi14")] = 35.0
    div = analysis.detect_rsi_divergence(ind)
    assert div is not None
    assert div["kind"] == "bullish"
    assert div["price_curr"] < div["price_prev"]
    assert div["rsi_curr"] > div["rsi_prev"]
    assert learn.pro_note("RSI Bullish Divergence: ...") is not None
    assert learn.pro_note("CMF 20 hari: +0.20") is not None


def test_chart_candlestick_dan_levels(tmp_path):
    df = _make_ohlcv(np.linspace(1000, 1400, 90))
    ind = analysis.compute_indicators(df)
    out = tmp_path / "bbca_candle.png"
    path = chart.build_chart(
        "BBCA",
        ind,
        45.0,
        str(out),
        levels={"entry": 1380.0, "sl": 1310.0, "tp": 1520.0},
    )
    assert out.exists() and out.stat().st_size > 10_000
    assert path == str(out)


def test_fundamental_sector_relative_fcf_yield_ev_ebitda():
    f = {
        "sector": "Energy",
        "per": 7.2,
        "pbv": 1.1,
        "roe": 18.0,
        "roe_unit": "pct",
        "margin": 0.16,
        "div_yield": 0.06,
        "debt_eq": 35.0,
        "fcf": 1_500_000_000_000.0,
        "market_cap": 20_000_000_000_000.0,
        "ev_ebitda": 4.8,
    }
    res = fundamental.score_fundamentals(f)
    assert res["sector_rel"] is not None
    assert res["sector_rel"]["sector"] == "energy"
    assert res["sector_rel"]["per_diff_pct"] < 0
    assert res["fcf_yield"] == 7.5
    assert res["ev_ebitda"] == 4.8
    assert any("Relatif sektor" in n for n in res["notes"])
    assert any("FCF Yield" in n for n in res["notes"])
    assert any("EV/EBITDA" in n for n in res["notes"])


def test_relative_strength_vs_ihsg():
    df = _make_ohlcv(np.linspace(1000, 1250, 70))
    rs = banding.relative_strength(df, mkt={"ret20d": 1.0, "ret60d": 4.0})
    assert rs is not None
    assert rs["rs20"] > 0
    assert rs["rs60"] > 0
    assert "Outperform" in rs["label"]


def test_expected_move_range_dan_historical_tp_sl_odds():
    rng = np.random.default_rng(42)
    rets = rng.normal(0.001, 0.02, 160)
    closes = 3000.0 * np.exp(np.cumsum(rets))
    df = _make_ohlcv(closes)
    ind = analysis.compute_indicators(df)

    em = analysis.expected_move_range(ind)
    assert em is not None
    assert em["vol_daily_pct"] > 0
    assert em["bands"][5]["low"] < float(ind["close"].iloc[-1]) < em["bands"][5]["high"]
    assert em["bands"][20]["pct"] > em["bands"][5]["pct"]

    odds = analysis.historical_tp_sl_odds(ind)
    assert odds is not None
    assert odds["n"] >= 8
    assert abs((odds["tp_rate_pct"] + odds["sl_rate_pct"] + odds["timeout_rate_pct"]) - 100.0) < 0.5


def test_volume_profile_poc_risk_profile_dan_candle_pattern():
    rng = np.random.default_rng(7)
    closes = 3000.0 * np.exp(np.cumsum(rng.normal(0.0, 0.015, 120)))
    vols = np.full(120, 1_000_000.0)
    vols[50:60] = 20_000_000.0  # klaster volume besar
    df = _make_ohlcv(closes, volumes=vols)
    ind = analysis.compute_indicators(df)

    poc = analysis.volume_profile_poc(ind)
    assert poc is not None and poc["poc"] > 0
    assert poc["position"] in ("di atas POC", "di bawah POC")

    rp = analysis.risk_profile(ind)
    assert rp is not None
    assert rp["mdd_1y_pct"] <= 0
    assert rp["from_peak_pct"] <= 0
    assert rp["vol_annual_pct"] is not None and rp["vol_annual_pct"] > 0

    # Uji pola Bullish Engulfing sintetis pada 2 bar terakhir
    eng = df.tail(5).copy()
    eng.iloc[-2, eng.columns.get_loc("open")] = 3100.0
    eng.iloc[-2, eng.columns.get_loc("close")] = 3020.0
    eng.iloc[-2, eng.columns.get_loc("high")] = 3120.0
    eng.iloc[-2, eng.columns.get_loc("low")] = 3000.0
    eng.iloc[-1, eng.columns.get_loc("open")] = 3010.0
    eng.iloc[-1, eng.columns.get_loc("close")] = 3150.0
    eng.iloc[-1, eng.columns.get_loc("high")] = 3160.0
    eng.iloc[-1, eng.columns.get_loc("low")] = 3000.0
    pats = analysis.detect_candle_pattern(eng)
    assert any("Bullish Engulfing" in p for p in pats)
    assert learn.pro_note(pats[0]) is not None
