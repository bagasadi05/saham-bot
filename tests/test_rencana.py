"""Tes offline rencana beli siap Stockbit (filter, veto, sizing, teks)."""
from __future__ import annotations

import numpy as np

from core import rencana, store
from tests.conftest import make_prices


def _row(**over):
    row = {
        "ticker": "AAAA", "name": "PT Contoh", "board": "Utama",
        "signal": "BELI", "price": 1000.0, "rel_pct": 90.0,
        "ret20d": 2.0, "rsi": 60.0, "atr": 30.0, "tech_score": 50.0,
        "value_avg20": 5_000_000_000.0, "vol_avg20": 5_000_000.0,
        "reason": "Tren naik", "trade_date": "2026-11-11",
    }
    row.update(over)
    return row


def _analyzed(*, price=1000.0, atr=30.0, rsi=60.0, ret20=2.0,
              fund_pct=15.0, news_pct=10.0, sector="Financials",
              per=10.0, pbv=1.2, roe=15.0, der=50.0):
    ind = make_prices(np.linspace(price * 0.9, price, 80))
    ind["atr14"] = atr
    return {
        "res": {"ticker": "AAAA",
                "snap": {"close": price, "rsi": rsi, "ret20d": ret20},
                "ind": ind},
        "fund": {"pct": fund_pct, "label": "BAIK", "notes": ["ROE bagus"]},
        "fund_raw": {"sector": sector, "per": per, "pbv": pbv, "roe": roe,
                     "debt_eq": der, "div_yield": 2.0},
        "nsent": {"pct": news_pct, "label": "NETRAL", "n": 2,
                  "details": [{"title": "Berita uji", "sent": 0.1,
                               "source": "Contoh"}]},
    }


# -------------------------------------------------------------- prefilter
def test_prefilter_hanya_beli_dan_lolos_likuiditas():
    cands, rejected = rencana.prefilter([
        _row(ticker="LOLOS"),
        _row(ticker="JUAL", signal="JUAL"),
        _row(ticker="MURAH", price=40),
        _row(ticker="KHUSUS", board="Pemantauan Khusus"),
        _row(ticker="TRAP", ret20d=20),
        _row(ticker="LEMAH", rel_pct=50),
        _row(ticker="TIPIS", value_avg20=1_000_000_000),
        _row(ticker="LAMA", value_avg20=None),
    ])
    assert [c["ticker"] for c in cands] == ["LOLOS", "LAMA"]
    assert cands[1]["liquidity_unverified"] is True
    assert rejected["bukan sinyal BELI"] == 1
    assert rejected["likuiditas < Rp2 miliar/hari"] == 1
    assert rejected["papan Pemantauan Khusus"] == 1


def test_prefilter_urut_rel_dan_batasi_pool():
    rows = [_row(ticker=f"T{i:02d}", rel_pct=60 + i) for i in range(10)]
    cands, rejected = rencana.prefilter(rows, pool_limit=4)
    assert [c["ticker"] for c in cands] == ["T09", "T08", "T07", "T06"]
    assert sum(rejected.values()) == 6


# ----------------------------------------------------------------- enrich
def test_enrich_lengkap_tanpa_veto():
    c = _row()
    e = rencana.enrich(c, _analyzed())
    assert e is not None
    assert e["ticker"] == "AAAA"
    assert e["sector"] == "Financials"
    assert e["grade"] in ("A", "B", "C")
    assert not e["vetoes"]
    assert e["reason"] == "Tren naik"
    assert e["news_top"][0]["title"] == "Berita uji"
    assert e["value_avg20"] and e["atr"] == 30.0


def test_enrich_veto_rugi_ekuitas_utang_dan_berita():
    rugi = rencana.enrich(_row(), _analyzed(per=-5, roe=-10))
    assert any("merugi" in v for v in rugi["vetoes"])

    ekuitas = rencana.enrich(_row(), _analyzed(pbv=0))
    assert any("ekuitas" in v for v in ekuitas["vetoes"])

    utang = rencana.enrich(_row(), _analyzed(der=500, sector="Industrials"))
    assert any("DER" in v for v in utang["vetoes"])
    # bank: DER tinggi wajar, bukan veto
    bank = rencana.enrich(_row(), _analyzed(der=500, sector="Financials"))
    assert not any("DER" in v for v in bank["vetoes"])

    berita = rencana.enrich(_row(), _analyzed(news_pct=-40))
    assert any("berita" in v for v in berita["vetoes"])


def test_enrich_likuiditas_dihitung_dari_data_harga():
    c = _row(value_avg20=None, vol_avg20=None)
    e = rencana.enrich(c, _analyzed(price=1000, atr=30))
    # make_prices volume 1 juta × harga ±900-1000 → ±0,9-1,0 miliar/hari
    assert e["value_avg20"] is not None
    assert any("likuiditas" in v for v in e["vetoes"])


def test_enrich_atr_hilang_pakai_fallback_dan_peringatan():
    e = rencana.enrich(_row(atr=None), _analyzed(atr=0))
    assert e and e["atr"] > 0
    assert any("ATR" in w for w in e["warnings"])


def test_grade_bukan_probabilitas():
    base = {"vetoes": [], "rel_pct": 95, "fund_pct": 10, "news_pct": 10,
            "value_avg20": 6e9, "ret20d": 2}
    assert rencana.grade(base) == "A"
    assert rencana.grade({**base, "rel_pct": 70, "fund_pct": -10,
                          "news_pct": -5, "ret20d": 12}) == "C"
    assert rencana.grade({**base, "vetoes": ["x"]}) == "—"
    assert rencana.grade({**base, "warnings": ["ROE tipis (4,0%)"]}) == "B"


# ------------------------------------------------------------ plan sizing
def test_make_plan_hormati_risiko_dan_cap_posisi():
    e = rencana.enrich(_row(), _analyzed(price=1000, atr=30))
    p = rencana.make_plan(e, 10_000_000)
    assert p["entry"] == 1000 and p["sl"] == 970 and p["tp"] == 1060
    assert p["rr"] == 2.0
    # 20% modal = Rp2 juta → 20 lot (Rp2 juta), bukan 33 lot dari risiko 1%
    assert p["lots"] == 20
    assert p["value"] == 2_000_000
    assert p["real_risk"] == 60_000
    assert p["risk_pct"] == 0.6
    assert p["min_modal"] == 500_000
    assert p["per"] == 10.0 and p["roe"] == 15.0 and p["div_yield"] == 2.0
    assert p["reason"] == "Tren naik"


def test_make_plan_modal_terlalu_kecil_menghasilkan_lot_nol():
    e = rencana.enrich(_row(), _analyzed(price=1000, atr=30))
    p = rencana.make_plan(e, 100_000)
    assert p["lots"] == 0
    assert "minimum" in p["skip_reason"]


def test_finalize_batas_sektor_dan_modal():
    a = rencana.enrich(_row(ticker="AAAA", rel_pct=95),
                       _analyzed(price=1000, atr=30, sector="Financials"))
    b = rencana.enrich(_row(ticker="BBBB", rel_pct=90),
                       _analyzed(price=1000, atr=30, sector="Financials"))
    c = rencana.enrich(_row(ticker="CCCC", rel_pct=85),
                       _analyzed(price=1000, atr=30, sector="Financials"))
    d = rencana.enrich(_row(ticker="DDDD", rel_pct=80, value_avg20=5e9),
                       _analyzed(price=1000, atr=30, sector="Energy"))
    out = rencana.finalize([a, b, c, d], 10_000_000, max_plans=5)
    assert [p["ticker"] for p in out["picks"]] == ["AAAA", "BBBB", "DDDD"]
    assert out["rejected"]["konsentrasi sektor (>2)"] == 1

    poor = rencana.finalize([a], 100_000, max_plans=3)
    assert poor["picks"] == []
    assert poor["rejected"]["modal kurang untuk 1 lot"] == 1


def test_finalize_batas_total_alokasi():
    sectors = ["Financials", "Energy", "Industrials", "Technology", "Health"]
    cands = [rencana.enrich(_row(ticker=f"T{i}", rel_pct=95 - i),
                            _analyzed(price=1000, atr=30, sector=s))
             for i, s in enumerate(sectors)]
    out = rencana.finalize(cands, 10_000_000, max_plans=5)
    assert len(out["picks"]) == 4
    assert out["rejected"]["total alokasi >90% modal"] == 1


def test_finalize_membuang_kandidat_veto():
    ok = rencana.enrich(_row(ticker="OK"), _analyzed())
    bad = rencana.enrich(_row(ticker="BAD"), _analyzed(news_pct=-50))
    out = rencana.finalize([ok, bad], 10_000_000, max_plans=3)
    assert [p["ticker"] for p in out["picks"]] == ["OK"]
    assert sum(out["rejected"].values()) == 1


# ------------------------------------------------------------- plan cache
def test_cache_rencana_roundtrip(tmp_store):
    store.save_plan_cache("plan:uji", {"enriched": [1, 2]}, "2026-11-11")
    assert store.load_plan_cache("plan:uji")["enriched"] == [1, 2]
    assert store.load_plan_cache("plan:tidak-ada") is None


def test_enrich_menyaring_judul_halaman_otomatis():
    a = _analyzed()
    a["nsent"]["details"] = [
        {"title": "PT Contoh Tbk Estimates – IDX:AAAA", "sent": 0.2},
        {"title": "Berita sungguhan", "sent": 0.1},
    ]
    e = rencana.enrich(_row(), a)
    assert [n["title"] for n in e["news_top"]] == ["Berita sungguhan"]


def test_sl_tp_mengikuti_tick_idx():
    e = rencana.enrich(_row(), _analyzed(price=1080, atr=21.04))
    p = rencana.make_plan(e, 10_000_000)
    assert p["entry"] == 1080
    assert p["sl"] == 1060 and p["tp"] == 1120
    assert p["sl"] % 5 == 0 and p["tp"] % 5 == 0
    assert p["rr"] == 2.0


def test_finalize_defensif_pangkas_eksposur_dan_risiko():
    cands = [
        rencana.enrich(_row(ticker=f"T{i}", rel_pct=90 - i),
                       _analyzed(sector=f"S{i}", price=1000, atr=10))
        for i in range(4)
    ]
    def_reg = {"status": "DEFENSIVE", "exposure_scale": 0.5}
    out = rencana.finalize(cands, 10_000_000, max_plans=5, market_regime=def_reg)
    assert out["exposure_scale"] == 0.5
    assert out["risk_pct_limit"] == 0.5
    assert out["max_pos_pct"] == 10.0
    assert out["max_total_pct"] == 45.0
    assert all(p["lots"] == 10 for p in out["picks"])  # maks 10% x 10jt = 1jt = 10 lot
    assert sum(p["buy_cost"] for p in out["picks"]) <= 4_500_000

    # Status NA tetap memakai porsi normal 100% secara jujur (tidak memalsu jadi nol/defensif)
    na_out = rencana.finalize(
        cands, 10_000_000, max_plans=5, market_regime={"status": "NA", "exposure_scale": 1.0}
    )
    assert na_out["exposure_scale"] == 1.0
    assert na_out["max_total_pct"] == 90.0
    assert all(p["lots"] == 20 for p in na_out["picks"])
