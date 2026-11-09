"""Test sizing posisi (risiko 1% modal) & pemilihan kandidat /saran."""
from __future__ import annotations

from core import saran


def test_position_size_risiko_satu_persen():
    r = saran.position_size(10_000_000, entry=1000, sl=900)
    assert r["risk"] == 100
    assert r["budget"] == 100_000
    assert r["lots"] == 10 and r["shares"] == 1000
    assert r["real_risk"] == 100_000  # 10 lot × 100 × risiko 100


def test_position_size_dibatasi_modal():
    # risiko/lembar sangat kecil -> tanpa cap bisa menyarankan beli > modal
    r = saran.position_size(1_000_000, entry=5000, sl=4999)
    assert r["value"] <= 1_000_000
    assert r["lots"] == 2 and r["shares"] == 200


def test_position_size_lot_nol_kalau_tak_cukup_modal():
    r = saran.position_size(100_000, entry=5000, sl=4990)
    assert r["lots"] == 0 and r["shares"] == 0
    assert r["value"] == 0 and r["real_risk"] == 0


def test_position_size_sl_di_atas_entry_tidak_minus():
    r = saran.position_size(1_000_000, entry=1000, sl=1100)
    assert r["risk"] == 0 and r["lots"] == 0


def _res(power_tech, ret20d, ind=None):
    return {
        "ticker": "TEST",
        "score": {"pct": power_tech},
        "snap": {"close": 1000, "ret20d": ret20d},
        "ind": ind if ind is not None else {"atr14": __import__("pandas").Series([30.0])},
    }


def _res_full(ticker, tech, snap_ret20d, **ind_kw):
    """Kandidat lengkap dengan DataFrame indikator (untuk mode relatif)."""
    import pandas as pd
    base = dict(close=1000.0, sma20=1000.0, sma50=1000.0, sma200=1000.0,
                high20=1000.0, volume=1_000_000.0, vol_avg20=1_000_000.0,
                macd_hist=0.0, rsi14=50.0, ret20d=0.0, ret1d=0.0,
                volat20=0.02, atr14=30.0)
    base.update(ind_kw)
    return {
        "ticker": ticker,
        "score": {"pct": tech},
        "snap": {"close": 1000, "ret20d": snap_ret20d},
        "ind": pd.DataFrame([base]),
    }


def test_pick_menyaring_kandidat_lemah_dan_jebakan_momentum():
    kuat = _res(80, 5)
    lemah = _res(-60, 2)
    trap = _res(90, 25)  # power tinggi tapi sudah naik 25% dalam 20 hari
    out = saran.pick([{"res": kuat}, {"res": lemah}, {"res": trap}])
    tickers = [c["ticker"] for c in out["picks"]]
    assert tickers == ["TEST"]  # hanya yang kuat & bukan trap
    assert out["avoid"]
    assert out["mode"] == "powerscore"  # 3 emiten < 8 → fallback


def test_build_candidate_memakai_atr_untuk_sl_tp():
    c = saran.build_candidate(_res(50, 1), None, None, modal=10_000_000)
    assert c["sl"] == 970 and c["tp"] == 1060
    assert 0 < c["lots"] * 100 * c["entry"] <= 10_000_000


def test_pick_mode_relatif_memakai_peringkat_antar_emiten():
    """≥8 kandidat → urutan dari skor relatif, bukan PowerScore."""
    good = dict(close=1000.0, sma50=1200.0, high20=1400.0, rsi14=70.0,
                ret20d=5.0, macd_hist=10.0, volat20=0.01, ret1d=-2.0,
                volume=2_000_000.0, sma20=1300.0, atr14=30.0)
    bad = dict(close=1000.0, sma50=800.0, high20=1000.0, rsi14=30.0,
               ret20d=-5.0, macd_hist=-10.0, volat20=0.05, ret1d=3.0,
               volume=500_000.0, sma20=750.0, atr14=5.0)
    analyzed = ([{"res": _res_full(f"G{i}", 0, 1.0, **good)} for i in range(5)]
                + [{"res": _res_full(f"B{i}", 90, 1.0, **bad)} for i in range(5)])
    out = saran.pick(analyzed, modal=10_000_000)

    assert out["mode"] == "relatif"
    # meski PowerScore B* jauh lebih tinggi, yang dipilih adalah G*
    assert all(c["ticker"].startswith("G") for c in out["picks"])
    assert all(c["rel_pct"] >= 60 for c in out["picks"])
    assert all(c["ticker"].startswith("B") for c in out["avoid"])
    # urut menurun menurut peringkat relatif
    pcts = [c["rel_pct"] for c in out["picks"]]
    assert pcts == sorted(pcts, reverse=True)


def test_pick_mode_relatif_menghormati_batas_portofolio():
    good = dict(close=1000.0, sma50=1200.0, high20=1400.0, rsi14=70.0,
                ret20d=5.0, macd_hist=10.0, volat20=0.01, ret1d=-2.0,
                volume=2_000_000.0, sma20=1300.0, atr14=30.0)
    bad = dict(close=1000.0, sma50=800.0, high20=1000.0, rsi14=30.0,
               ret20d=-5.0, macd_hist=-10.0, volat20=0.05, ret1d=3.0,
               volume=500_000.0, sma20=750.0, atr14=5.0)
    analyzed = (
        [{"res": _res_full(f"G{i}", 0, 1.0, **good), "fund_raw": {"sector": "Financials"}}
         for i in range(4)]
        + [{"res": _res_full("G4", 0, 1.0, **good), "fund_raw": {"sector": "Energy"}}]
        + [{"res": _res_full(f"B{i}", 90, 1.0, **bad)} for i in range(5)]
    )
    open_recs = {
        "X1": {"sector": "Financials"},
        "X2": {"sector": "Financials"},
        "G4": {"sector": "Energy"},
    }
    out = saran.pick(analyzed, modal=10_000_000, open_recs=open_recs)
    assert out["mode"] == "relatif"
    assert out["picks"] == []  # Financials penuh (2/2), G4 sudah terbuka
