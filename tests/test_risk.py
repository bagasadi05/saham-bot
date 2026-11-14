"""Tes offline metrik risiko portofolio & batas rencana (P23/P24)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import risk


def _series(seed: int, n: int = 80, drift: float = 0.0004):
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.01, n)
    idx = pd.bdate_range("2026-07-01", periods=n)
    return pd.Series(rets, index=idx)


def _pos(t, lots=100, sector="Financials"):
    return {"ticker": t, "lots": lots, "avg_price": 1000.0, "sector": sector}


def test_portfolio_risk_menghitung_vol_drawdown_dan_var():
    quotes = {"AAAA": {"close": 1000.0, "rets": _series(1)},
              "BBBB": {"close": 2000.0, "rets": _series(2)}}
    out = risk.portfolio_risk([_pos("AAAA", 10), _pos("BBBB", 5)], quotes)
    assert out["ok"] is True
    assert out["value"] == 10 * 1000 * 100 + 5 * 2000 * 100
    assert out["vol_ann_pct"] > 0
    assert out["max_drawdown_pct"] <= 0
    assert out["var95_pct"] > 0 and out["var95_rp"] > 0
    assert out["corr_avg"] is not None and out["corr_max"] is not None
    assert set(out["weights"]) == {"AAAA", "BBBB"}


def test_beta_dihitung_terhadap_benchmark():
    rets = _series(3)
    quotes = {"AAAA": {"close": 1000.0, "rets": rets}}
    out = risk.portfolio_risk([_pos("AAAA")], quotes, bench_rets=rets * 1.0)
    # portofolio = aset tunggal, jadi beta terhadap dirinya sendiri = 1
    assert out["beta"] == 1.0


def test_tanpa_riwayat_tidak_mengarang_vol():
    quotes = {"AAAA": {"close": 1000.0}}   # tanpa rets
    out = risk.portfolio_risk([_pos("AAAA")], quotes)
    assert out["ok"] is True
    assert out["vol_ann_pct"] is None and out["var95_rp"] is None
    assert any("tanpa riwayat" in w for w in out["warnings"])


def test_posisi_tanpa_harga_dilewati():
    out = risk.portfolio_risk([_pos("AAAA"), _pos("BBBB")],
                              {"AAAA": {"close": 1000.0}})
    assert out["ok"] is True
    assert "BBBB" not in out["weights"]


def test_konsentrasi_dan_korelasi_tinggi_memberi_peringatan():
    same = _series(4)
    quotes = {"AAAA": {"close": 1000.0, "rets": same},
              "BBBB": {"close": 1000.0, "rets": same * 1.0}}
    out = risk.portfolio_risk([_pos("AAAA", 100), _pos("BBBB", 1)], quotes)
    assert any("di atas batas" in w for w in out["warnings"])
    assert any("korelasi tinggi" in w for w in out["warnings"])


# ------------------------------------------------------------------ batas
def _plan(ticker="AAAA", sector="Financials", lots=10, entry=1000.0):
    return {"ticker": ticker, "sector": sector, "entry": entry,
            "lots": lots, "value": lots * 100 * entry}


def test_batas_posisi_ditolak_dengan_lot_maks():
    plan = _plan(lots=30)          # 3 jt dari modal 10 jt = 30% > 25%
    out = risk.check_limits(plan, [], 10_000_000)
    assert out["ok"] is False
    assert any("melebihi batas 25%" in r for r in out["reasons"])
    assert out["max_lots"] == 25   # 2,5 jt / (1000 x 100)


def test_batas_sektor_dihitung_dari_posisi_lama():
    # posisi lama sektor sama: 3 jt; rencana 1,5 jt -> 45% > 40%
    out = risk.check_limits(_plan(lots=15), [_pos("BBBB", 30)], 10_000_000)
    assert out["ok"] is False
    assert any("sektor Financials" in r for r in out["reasons"])


def test_tanpa_modal_batas_dilewati():
    out = risk.check_limits(_plan(lots=30), [], 0)
    assert out["ok"] is True


def test_rencana_kecil_lolos():
    out = risk.check_limits(_plan(lots=10), [_pos("BBBB", 5)], 10_000_000)
    assert out["ok"] is True
    assert out["max_lots"] >= 10

def test_position_fit_mengingatkan_posisi_di_atas_batas():
    pos = [_pos("BBCA", lots=30, sector="Financials")]   # 3 jt / 10 jt = 30%
    out = risk.position_fit("BBCA", pos, 10_000_000)
    assert any("sudah 30% modal" in w for w in out)
    assert any("batas 25%" in w for w in out)


def test_position_fit_tidak_berisik_kalau_kecil():
    pos = [_pos("BBCA", lots=10)]
    assert risk.position_fit("BBCA", pos, 10_000_000) == []
