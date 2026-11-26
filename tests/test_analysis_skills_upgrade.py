"""Tes offline peningkatan analisis saham (quarterly forensics, veto PowerScore, POC, RS vs IHSG)."""
from __future__ import annotations

import numpy as np

from core import analysis, cek, fundamental, powerscore
from tests.conftest import make_prices


def test_analyze_quarterly_trend_growth_decline_loss_and_missing():
    # Data kosong -> jujur available=False
    empty = fundamental.analyze_quarterly_trend({})
    assert empty["available"] is False
    assert empty["penalty"] == 0

    # Pertumbuhan pendapatan & laba konsisten 4 kuartal
    grow = fundamental.analyze_quarterly_trend({
        "rev_trend": [("Q1", 100), ("Q2", 110), ("Q3", 120), ("Q4", 135)],
        "profit_trend": [("Q1", 20), ("Q2", 22), ("Q3", 25), ("Q4", 30)],
    })
    assert grow["available"] is True
    assert grow["penalty"] == 0
    assert grow["flags"] == []
    assert len(grow["strengths"]) == 2
    assert grow["profit_growth_pct"] == 50.0

    # Kontraksi laba & pendapatan beruntun
    dec = fundamental.analyze_quarterly_trend({
        "rev_trend": [("Q1", 130), ("Q2", 120), ("Q3", 105)],
        "profit_trend": [("Q1", 30), ("Q2", 24), ("Q3", 15)],
    })
    assert dec["penalty"] == 15
    assert "laba_turun_beruntun" in dec["forensic_flags"]
    assert "pendapatan_menyusut" in dec["forensic_flags"]

    # Berbalik rugi di kuartal terakhir
    loss = fundamental.analyze_quarterly_trend({
        "profit_trend": [("Q1", 15), ("Q2", 8), ("Q3", -5)],
    })
    assert loss["penalty"] == 20
    assert "kuartal_terakhir_rugi" in loss["forensic_flags"]

    # Terintegrasi ke score_fundamentals
    sc_grow = fundamental.score_fundamentals({
        "per": 10.0, "pbv": 1.2, "roe": 18.0, "roe_unit": "pct",
        "rev_trend": [("Q1", 100), ("Q2", 115), ("Q3", 130)],
        "profit_trend": [("Q1", 20), ("Q2", 25), ("Q3", 32)],
    })
    sc_loss = fundamental.score_fundamentals({
        "per": 10.0, "pbv": 1.2, "roe": 18.0, "roe_unit": "pct",
        "profit_trend": [("Q1", 20), ("Q2", 5), ("Q3", -10)],
    })
    assert sc_loss["pct"] < sc_grow["pct"]
    assert "kuartal_terakhir_rugi" in sc_loss["forensic_flags"]


def test_powerscore_veto_alignment_prevents_contradiction():
    # Tanpa veto: STRONG BUY
    raw = powerscore.combine(70.0, 60.0, 50.0)
    assert raw["rating"] == "STRONG BUY"
    assert raw["veto_capped"] is False

    # Dengan veto (mis. likuiditas < Rp2M): rating dibatasi ke NETRAL (tidak STRONG BUY)
    capped = powerscore.combine(70.0, 60.0, 50.0, vetoes=["likuiditas < Rp2 miliar/hari"])
    assert capped["rating"] == "NETRAL"
    assert capped["veto_capped"] is True
    assert "Dibatasi veto" in capped["trend_label"]

    # Sinyal SELL/STRONG SELL tetap SELL meski ada veto
    sell = powerscore.combine(-65.0, -40.0, -30.0, vetoes=["likuiditas < Rp2 miliar/hari"])
    assert sell["rating"] == "STRONG SELL"
    assert sell["veto_capped"] is False


def test_cek_build_includes_poc_quarterly_and_rs_vs_ihsg():
    closes = np.concatenate([np.full(50, 1000.0), np.linspace(1000.0, 1250.0, 30)])
    df = make_prices(closes, volume=5_000_000)
    ind = analysis.compute_indicators(df)
    res = {
        "ticker": "TEST",
        "ind": ind,
        "score": analysis.score_stock(ind),
        "snap": analysis.last_snapshot(ind),
        "weekly": {"direction": "naik"},
    }
    fund_raw = {
        "per": 9.0, "pbv": 1.1, "roe": 18.0, "roe_unit": "pct",
        "rev_trend": [("Q1", 100), ("Q2", 112), ("Q3", 128)],
        "profit_trend": [("Q1", 15), ("Q2", 19), ("Q3", 25)],
    }
    out = cek.build(
        res=res,
        fund=fundamental.score_fundamentals(fund_raw),
        fund_raw=fund_raw,
        nsent={"pct": 40.0, "n": 4},
        bandar_row=None,
        agenda=None,
        agenda_days=None,
        peers={"sector": "Industrials", "ret20d": 2.0, "ihsg_ret20d": 1.0},
        value_avg20=10_000_000_000.0,
    )
    tren_item = next(it for it in out["items"] if it["key"] == "tren")
    val_item = next(it for it in out["items"] if it["key"] == "valuasi")
    assert "POC" in tren_item["detail"]
    assert "RS vs IHSG" in val_item["detail"]
    assert out["quarterly"]["available"] is True
    assert len(out["quarterly"]["strengths"]) == 2
    assert len(out["bull_points"]) >= 1
