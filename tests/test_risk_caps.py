"""Tes offline batas sektor inv-vol, kontribusi risiko, dan VaR historis/ES.

Semua angka sintetis (seed tetap) — tidak memanggil jaringan. Konvensi tanda:
VaR/ES dilaporkan sebagai **besar kerugian** (positif), sehingga di sisi return
berlaku ES <= VaR, ekuivalen dengan ES >= VaR pada besar kerugian.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core import portofolio as portmod
from core import risk


def _rets(seed: int, sigma: float, n: int = 260) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2026-07-01", periods=n)
    return pd.Series(rng.normal(0.0004, sigma, n), index=idx)


def _quotes(vols: dict[str, float]) -> dict[str, dict]:
    return {t: {"close": 1000.0, "rets": _rets(i + 1, v)}
            for i, (t, v) in enumerate(vols.items())}


_SECTORS = {"B1": "Bank", "B2": "Bank", "B3": "Bank",
            "T1": "Teknologi", "T2": "Teknologi",
            "C1": "Konsumer", "C2": "Konsumer"}
_VOLS = {"B1": 0.01, "B2": 0.01, "B3": 0.01,
         "T1": 0.05, "T2": 0.05, "C1": 0.02, "C2": 0.02}


# ------------------------------------------------------- batas sektor inv-vol
def test_batas_sektor_ditegakkan_dan_total_bobot_100():
    quotes = _quotes(_VOLS)
    res = risk.inverse_vol_weights(quotes, list(_VOLS), sectors=_SECTORS)
    assert res["ok"] is True
    assert res["sector_capped"] is True
    assert "mengikat" in res["sector_note"]
    # tidak ada sektor yang melewati 40%
    for sektor, pct in res["sectors_pct"].items():
        assert pct <= risk.MAX_SECTOR_PCT * 100 + 0.1, sektor
    assert res["sectors_pct"]["Bank"] == pytest.approx(40.0, abs=0.2)
    assert sum(res["weights"].values()) == pytest.approx(100.0, abs=0.5)
    assert res["cash_pct"] == pytest.approx(0.0, abs=0.1)


def test_posisi_tanpa_sektor_masuk_tidak_diketahui_tanpa_dipaksa():
    vols = {"A1": 0.01, "A2": 0.01, "A3": 0.01, "A4": 0.01, "A5": 0.01}
    res = risk.inverse_vol_weights(_quotes(vols), list(vols))
    assert res["ok"] is True
    assert res["sector_capped"] is False
    assert "tidak diterapkan" in res["sector_note"]
    assert set(res["sectors_pct"]) == {risk.UNKNOWN_SECTOR}
    assert res["sectors_pct"][risk.UNKNOWN_SECTOR] == pytest.approx(100.0, abs=0.4)
    assert sum(res["weights"].values()) == pytest.approx(100.0, abs=0.5)


def test_sektor_tak_dikenal_tidak_ikut_dibatasi_40_persen():
    # Tiga emiten tanpa sektor mendominasi; satu emiten ber-sektor "Bank".
    vols = {"A1": 0.01, "A2": 0.01, "A3": 0.01, "B1": 0.05}
    res = risk.inverse_vol_weights(
        _quotes(vols), list(vols), sectors={"B1": "Bank"})
    assert res["ok"] is True
    assert res["sector_capped"] is False
    assert res["sectors_pct"][risk.UNKNOWN_SECTOR] > risk.MAX_SECTOR_PCT * 100
    assert "tidak dipaksa" in res["sector_note"]
    # emiten tanpa sektor tetap patuh batas per posisi
    for t in ("A1", "A2", "A3"):
        assert res["weights"][t] <= risk.MAX_POS_PCT * 100 + 0.1


def test_inv_vol_dalam_portfolio_risk_memakai_sektor_posisi():
    positions = [{"ticker": t, "lots": 10, "avg_price": 1000.0,
                  "sector": _SECTORS[t]} for t in _VOLS]
    out = risk.portfolio_risk(positions, _quotes(_VOLS))
    inv = out["inv_vol"]
    assert inv["ok"] is True and inv["sector_capped"] is True
    assert inv["sectors_pct"]["Bank"] <= risk.MAX_SECTOR_PCT * 100 + 0.1


# ------------------------------------------------------- kontribusi risiko
def test_kontribusi_risiko_menjumlah_seratus():
    vols = {"LOW": 0.01, "MID": 0.02, "HIGH": 0.05}
    positions = [{"ticker": t, "lots": 10, "avg_price": 1000.0,
                  "sector": "X"} for t in vols]
    out = risk.portfolio_risk(positions, _quotes(vols))
    rc = out["risk_contrib_pct"]
    assert rc is not None and set(rc) == set(vols)
    assert sum(rc.values()) == pytest.approx(100.0, abs=0.4)
    assert rc["HIGH"] > rc["LOW"]      # volatil tinggi, bobot sama


def test_kontribusi_risiko_none_bila_riwayat_kurang():
    quotes = {"AAAA": {"close": 1000.0, "rets": _rets(1, 0.01, n=10)},
              "BBBB": {"close": 1000.0, "rets": _rets(2, 0.02, n=10)}}
    positions = [{"ticker": t, "lots": 10, "avg_price": 1000.0}
                 for t in quotes]
    out = risk.portfolio_risk(positions, quotes)
    assert out["risk_contrib_pct"] is None
    assert out["risk_contrib_note"] and "< 60" in out["risk_contrib_note"]


# ------------------------------------------------------- VaR historis & ES
def _pos_7() -> list[dict]:
    return [{"ticker": t, "lots": 10, "avg_price": 1000.0,
             "sector": _SECTORS[t]} for t in _VOLS]


def test_var_historis_dan_es_berurutan():
    out = risk.portfolio_risk(_pos_7(), _quotes(_VOLS))
    assert out["hist_var95_pct"] > 0
    assert out["hist_es95_pct"] >= out["hist_var95_pct"]   # ES lebih buruk
    assert out["hist_es95_rp"] >= out["hist_var95_rp"] >= 0
    assert out["hist_var95_rp"] == round(
        out["value"] * out["hist_var95_pct"] / 100)
    # field lama tetap ada & konsisten
    assert out["var95_pct"] > 0 and out["var95_rp"] > 0


def test_var_breaches_dihitung_dari_persentil_5():
    quotes = _quotes(_VOLS)
    positions = _pos_7()
    out = risk.portfolio_risk(positions, quotes)

    frame = pd.concat({t: quotes[t]["rets"] for t in _VOLS}, axis=1,
                      join="inner").dropna()
    nilai = {t: 1000.0 * 10 * 100 for t in _VOLS}
    total = sum(nilai.values())
    w = pd.Series({t: nilai[t] / total for t in _VOLS})
    port = (frame * w).sum(axis=1)
    assert out["var_n_days"] == len(port) == 260
    assert out["var_breaches"] is not None
    assert out["var_kupiec_p"] is not None
    assert out["var_expected_breaches"] == pytest.approx((260 - risk.MIN_DAYS_VAR) * 0.05)
    assert out["var_note"] is None


def test_var_dan_es_none_tanpa_return_dengan_alasan_jujur():
    out = risk.portfolio_risk([{"ticker": "AAAA", "lots": 10,
                                "avg_price": 1000.0}],
                              {"AAAA": {"close": 1000.0}})
    for key in ("hist_var95_pct", "hist_var95_rp",
                "hist_es95_pct", "hist_es95_rp",
                "var_n_days", "var_breaches", "var_expected_breaches"):
        assert out[key] is None, key
    assert out["var_note"] == "tidak ada posisi dengan riwayat return harian"
    assert out["var95_pct"] is None


# ------------------------------------------------------- teks handler
class _Deps:
    _SINYAL_DISCLAIMER = "bukan ajakan membeli"

    @staticmethod
    def _money(v):
        return f"Rp{v:,.0f}".replace(",", ".")

    @staticmethod
    def _money_delta(v):
        return f"{v:+,.0f}".replace(",", ".")

    @staticmethod
    def _plan_pct(v, sign=True):
        return f"{v:.1f}%".replace(".", ",")


def test_handler_menampilkan_es_pelanggaran_dan_kontribusi():
    from handlers.portfolio import _porto_text

    quotes = _quotes(_VOLS)
    positions = _pos_7()
    summary = portmod.summarize(positions, quotes)
    out = risk.portfolio_risk(positions, quotes)
    teks = _porto_text(summary, 0, _Deps, out)
    assert "Risiko portofolio" in teks
    assert "ES 95%" in teks
    assert "Pelanggaran VaR:" in teks and "dari 260 hari" in teks
    assert "Kontribusi risiko:" in teks
    assert "batas sektor 40% mengikat" in teks
    assert "Bobot seimbang (inv-vol)" in teks
