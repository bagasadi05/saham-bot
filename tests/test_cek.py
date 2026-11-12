"""Tes offline aturan /cek (core/cek.py) — tanpa jaringan."""
from __future__ import annotations

from core import cek


def _res(tech=40.0, weekly="naik", close=1000.0, pct_vs_ma=2.0):
    res = {"ticker": "BBCA",
           "snap": {"close": close},
           "score": {"pct": tech},
           "weekly": {"direction": weekly, "pct_vs_ma": pct_vs_ma}}
    if weekly is None:
        res["weekly"] = {}
    return res


def _fund(per=12.0, pbv=1.5, roe=18.0, sector="Financials"):
    return {"pct": 15.0}, {"per": per, "pbv": pbv, "roe": roe,
                           "sector": sector}


def _build(**over):
    fund, fund_raw = _fund()
    arg = {
        "res": _res(),
        "fund": fund,
        "fund_raw": fund_raw,
        "nsent": {"pct": 25.0, "n": 8, "top": "Laba naik"},
        "bandar_row": {"ForeignNet": 5_000_000_000},
        "agenda": None,
        "agenda_days": None,
        "peers": {"ret20d": 1.0, "sector": "Financials"},
        "value_avg20": 50_000_000_000.0,
        "board": "Utama",
    }
    arg.update(over)
    return cek.build(**arg)


# ------------------------------------------------------------------- helper
def test_miliar_format_indonesia():
    assert cek.miliar(5_000_000_000) == "Rp5,0 miliar"
    assert cek.miliar(750_000_000) == "Rp750 juta"
    assert cek.miliar(None) == "tidak tersedia"


def test_liquidity_tiga_tingkat():
    assert cek.liquidity(10_000_000_000)["level"] == "likuid"
    assert cek.liquidity(3_000_000_000)["level"] == "sedang"
    assert cek.liquidity(1_000_000_000)["level"] == "kurang"
    assert cek.liquidity(None)["level"] is None


def test_foreign_net_dari_field_langsung():
    fg = cek.foreign_net({"ForeignNet": "1.500.000.000"})
    assert fg["available"] is True
    assert fg["net"] == 1_500_000_000
    assert "beli bersih" in fg["text"]


def test_foreign_net_dihitung_dari_beli_dikurangi_jual():
    fg = cek.foreign_net({"ForeignBuy": 3e9, "ForeignSell": 1e9})
    assert fg["available"] is True and fg["net"] == 2_000_000_000


def test_foreign_net_tidak_menebak_kalau_field_tidak_dikenal():
    fg = cek.foreign_net({"ClosingPrice": 1000})
    assert fg["available"] is False and fg["net"] is None


def test_agenda_jauh_tidak_jadi_peringatan():
    nota = cek.agenda_note({"ex_date": "2026-12-01", "amount": 50}, 30)
    assert nota["near"] is False


def test_agenda_dekat_menyebut_dividen():
    nota = cek.agenda_note({"ex_date": "2026-11-20", "amount": 150}, 8)
    assert nota["near"] is True and "8 hari" in nota["text"]


# ------------------------------------------------------------------ verdict
def test_semua_baik_layak():
    out = _build()
    assert out["verdict"] == "LAYAK", out
    assert all(it["status"] != "na" for it in out["items"])
    assert len(out["reasons"]) == 2
    assert "Veto" not in out["reasons"][0]


def test_tren_turun_dan_valuasi_mahal_tidak_layak():
    fund, fund_raw = _fund(per=55.0, pbv=8.0, roe=3.0)
    out = _build(res=_res(tech=-40, weekly="turun"),
                 fund=fund, fund_raw=fund_raw)
    assert out["verdict"] in ("HINDARI", "TUNGGU")
    assert any(it["key"] == "tren" and it["status"] == "bad"
               for it in out["items"])


def test_likuiditas_kurang_langsung_hindari():
    out = _build(value_avg20=500_000_000)
    assert out["verdict"] == "HINDARI"
    assert any("likuiditas" in r for r in out["reasons"])


def test_laba_negatif_jadi_veto():
    fund, fund_raw = _fund(per=-8.0, pbv=1.0, roe=-5.0)
    out = _build(fund=fund, fund_raw=fund_raw)
    assert out["verdict"] == "HINDARI"
    assert "laba negatif" in out["vetoes"]


def test_data_kurang_tidak_disebut_hindari():
    """>= 3 bagian tanpa data -> TIDAK CUKUP DATA, bukan verdict karangan."""
    out = _build(res=None, fund=None, fund_raw=None, nsent=None,
                 bandar_row=None, value_avg20=None)
    assert out["verdict"] == "TIDAK CUKUP DATA"
    assert set(out["missing"]) >= {"tren", "valuasi", "berita", "likuiditas"}


def test_bagian_tanpa_data_ditulis_terbuka():
    out = _build(nsent=None, bandar_row=None)
    na = {it["key"] for it in out["items"] if it["status"] == "na"}
    assert na == {"berita", "asing"}
    assert "berita" in out["missing"] and "aliran asing" in out["missing"]


def test_agenda_dekat_masuk_alasan_negatif():
    out = _build(agenda={"ex_date": "2026-11-20", "amount": 100},
                 agenda_days=5)
    item = next(it for it in out["items"] if it["key"] == "agenda")
    assert item["status"] == "warn"
    assert "ex-dividen" in item["detail"]


def test_papan_pemantauan_jadi_veto():
    out = _build(board="Pemantauan Khusus")
    assert out["verdict"] == "HINDARI"
    assert any("Pemantauan" in r for r in out["reasons"])
