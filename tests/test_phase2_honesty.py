"""Tes regresi kejujuran Wave 2A: unit ROE, mata uang, forensik, label, dan /cek."""
from __future__ import annotations

import json

import pytest
import yfinance as yf

from core import alerts, analysis, cek, fundamental, kualitas, powerscore, pricing, reports
from tests.conftest import make_prices


def _fake_yf_info(info: dict):
    class _FakeTicker:
        quarterly_income_stmt = None

    _FakeTicker.info = info
    return lambda _sym: _FakeTicker()


# ------------------------------------------------------------ 1. P0 ROE unit
def test_fetch_live_roe_desimal_dikonversi_dan_bertahan_lintas_modul(monkeypatch):
    fundamental.clear_cache()
    monkeypatch.setattr(
        yf,
        "Ticker",
        _fake_yf_info(
            {
                "longName": "PT Bank Contoh Tbk",
                "sector": "Financial Services",
                "financialCurrency": "IDR",
                "trailingPE": 12.0,
                "priceToBook": 1.5,
                "returnOnEquity": 0.18,
                "dividendYield": 0.04,
            }
        ),
    )
    got = fundamental.fetch_fundamentals("BBCA")
    fundamental.clear_cache()
    assert got is not None
    assert got["_v"] == fundamental.CACHE_VERSION == 2
    assert got["roe"] == pytest.approx(18.0)
    assert got["roe_unit"] == "pct"

    kval = kualitas.judge(got)
    assert kval["ok"] is True and kval["label"] == "sehat"

    sc = fundamental.score_fundamentals(got)
    assert any("ROE 18.0%" in n for n in sc["notes"])

    out = cek.build(
        res={"ticker": "BBCA", "snap": {"close": 9000.0}, "score": {"pct": 40.0},
             "weekly": {"direction": "naik", "pct_vs_ma": 2.0}},
        fund=sc,
        fund_raw=got,
        nsent={"pct": 25.0, "n": 2},
        bandar_row={"ForeignNet": 5e9},
        agenda=None,
        agenda_days=None,
        peers={"ret20d": 1.0, "sector": "Financial Services"},
        value_avg20=50e9,
        board="Utama",
    )
    assert out["verdict"] == "LAYAK"
    assert "ROE < 2% (laba nyaris nol)" not in out["vetoes"]


def test_normalize_roe_melindungi_roe_mikro_berunit_pct():
    assert fundamental.normalize_roe(0.2, unit="pct") == pytest.approx(0.2)
    assert fundamental.normalize_roe(2.5, unit="pct") == pytest.approx(2.5)
    assert fundamental.normalize_roe(0.18, unit="fraction") == pytest.approx(18.0)
    # Dict legacy tanpa tag unit tetap menangani fraksi Yahoo 0.18 -> 18.0
    assert fundamental.normalize_roe(0.18) == pytest.approx(18.0)
    assert fundamental.normalize_roe(3.0) == pytest.approx(3.0)


def test_fetch_roe_mikro_0_002_tetap_terdeteksi_tipis(monkeypatch):
    fundamental.clear_cache()
    monkeypatch.setattr(
        yf,
        "Ticker",
        _fake_yf_info(
            {
                "longName": "PT Laba Tipis Tbk",
                "sector": "Industrials",
                "trailingPE": 11.0,
                "priceToBook": 1.2,
                "returnOnEquity": 0.002,  # 0.2%
            }
        ),
    )
    got = fundamental.fetch_fundamentals("TEST")
    fundamental.clear_cache()
    assert got is not None
    assert got["roe"] == pytest.approx(0.2)
    assert got["roe_unit"] == "pct"
    assert kualitas.judge(got)["label"] == "ROE tipis"


def test_disk_cache_versi_lama_dikedaluwarsakan_aman(tmp_path):
    old_file = tmp_path / "fund_BBCA_2026-11-14.json"
    old_file.write_text(json.dumps({"per": 12.0, "roe": 0.18}), encoding="utf-8")
    assert kualitas.load_cached(str(tmp_path), "BBCA", "2026-11-14") is None

    assert kualitas.save_cached(
        str(tmp_path), "BBCA", "2026-11-14", {"per": 12.0, "roe": 0.18}
    )
    loaded = kualitas.load_cached(str(tmp_path), "BBCA", "2026-11-14")
    assert loaded is not None
    assert loaded["_v"] == 2
    assert loaded["roe"] == pytest.approx(18.0)
    assert loaded["roe_unit"] == "pct"


# -------------------------------------------------------- 2. Mata uang non-IDR
def test_financial_currency_usd_dan_tidak_tersedia(monkeypatch):
    fundamental.clear_cache()
    monkeypatch.setattr(
        yf,
        "Ticker",
        _fake_yf_info(
            {
                "longName": "PT Batu Bara USD Tbk",
                "sector": "Energy",
                "financialCurrency": "usd",
                "currency": "IDR",
                "trailingPE": 6.0,
                "priceToBook": 1.1,
                "returnOnEquity": 0.25,
            }
        ),
    )
    usd_fund = fundamental.fetch_fundamentals("ADRO")
    assert usd_fund is not None and usd_fund["currency"] == "USD"

    fundamental.clear_cache()
    monkeypatch.setattr(
        yf,
        "Ticker",
        _fake_yf_info(
            {
                "longName": "PT Tanpa Kurs Tbk",
                "trailingPE": 10.0,
                "returnOnEquity": 0.15,
            }
        ),
    )
    na_fund = fundamental.fetch_fundamentals("XXXX")
    fundamental.clear_cache()
    assert na_fund is not None and na_fund["currency"] == "tidak tersedia"

    t_usd = reports._trend_line([("Q2 2026", 800e6), ("Q3 2026", 1.2e9)], currency="USD")
    assert t_usd is not None and "USD" in t_usd and "Rp" not in t_usd
    t_na = reports._trend_line([("Q2 2026", 800e6), ("Q3 2026", 1.2e9)], currency="tidak tersedia")
    assert t_na is not None and "Rp" not in t_na and "mata uang tidak tersedia" in t_na


# ------------------------------------------------- 3. Forensik ke skor & PS
def test_forensik_arus_kas_dan_dividend_trap_menurunkan_skor_dan_powerscore():
    clean = {
        "sector": "Energy",
        "per": 8.0,
        "pbv": 1.1,
        "roe": 20.0,
        "roe_unit": "pct",
        "div_yield": 0.08,
        "ocf": 3_000e9,
        "net_income": 2_000e9,
        "fcf": 1_800e9,
        "total_cash": 4_000e9,
        "total_debt": 1_000e9,
        "current_ratio": 2.0,
        "payout_ratio": 0.45,
    }
    trap = {
        **clean,
        "ocf": -400e9,
        "fcf": -700e9,
        "total_cash": 300e9,
        "total_debt": 1_500e9,
        "current_ratio": 0.8,
        "payout_ratio": 1.35,
    }
    sc_clean = fundamental.score_fundamentals(clean)
    sc_trap = fundamental.score_fundamentals(trap)
    assert sc_clean["forensic_penalty"] == 0
    assert sc_trap["forensic_penalty"] >= 60
    assert sc_trap["pct"] < sc_clean["pct"]
    assert sc_trap["label"] != "FUNDAMENTAL KUAT"
    assert "POJK 14" in sc_trap["filing_note"]
    assert any("⚠️" in n for n in sc_trap["notes"])

    pw_clean = powerscore.combine(
        50, sc_clean["base_pct"], 20, forensic_penalty=sc_clean["forensic_penalty"]
    )
    pw_trap = powerscore.combine(
        50, sc_trap["base_pct"], 20, forensic_penalty=sc_trap["forensic_penalty"]
    )
    assert pw_trap["pct"] < pw_clean["pct"]
    assert pw_trap["components"]["forensic_impact"] < 0
    judge_txt = powerscore.judge(50, sc_trap, {"pct": 20}, {"ret20d": 2, "ret1d": 0.5})
    assert "Penalti forensik fundamental" in judge_txt


# ----------------------------------------- 4. Label jujur vs rating internal
def test_label_jujur_tanpa_merusak_kontrak_rating_internal():
    df = make_prices([1000.0 + i * 5.0 for i in range(250)], volume=2_000_000)
    df.attrs["provider"] = "yfinance"
    df.attrs["repaired_count"] = 1
    df.attrs["last_bar_date"] = "2026-11-14"
    res = analysis.analyze("BBCA", df)
    sc = res["score"]
    assert sc["rating"] in {"STRONG BUY", "BUY", "NETRAL", "SELL", "STRONG SELL"}
    assert sc["trend_label"] == analysis.TREND_LABELS[sc["rating"]]
    assert sc["disclaimer"] == "Indikator tren historis, bukan prediksi pasti — IC harian ±0,01"

    res["stale_note"] = ""
    res["quality_note"] = "⚠️ <b>Kualitas data:</b> 1 lompatan harga\n"
    report = reports.build_report(res, None, None)
    assert "STRONG BUY" not in report and "STRONG SELL" not in report
    assert sc["trend_label"] in report
    assert "Kualitas data" in report
    assert "Provenance data" in report and "1 bar diperbaiki" in report

    alert_txt = alerts.build_alert_message(
        "BBCA", ["breakout"], {"close": 5000, "ret1d": 2.0}, sc
    )
    assert "STRONG BUY" not in alert_txt and "STRONG SELL" not in alert_txt
    assert sc["trend_label"] in alert_txt
    assert "IC harian" in alert_txt


# ----------------------------------------------- 5. Kejujuran /cek & ARB risk
def test_cek_jujur_valuasi_peers_dan_arb_callback(monkeypatch):
    monkeypatch.setattr(
        pricing,
        "check_arb_gap_risk",
        lambda **_kw: {"risk": True, "warning": "Gap ARB melewati SL"},
        raising=False,
    )
    res = {
        "ticker": "TEST",
        "snap": {"close": 1000.0, "volume": 0, "sl": 920.0},
        "score": {"pct": 30.0},
        "weekly": {"direction": "naik", "pct_vs_ma": 1.0},
        "quality_note": "⚠️ <b>Kualitas data:</b> 2 bar volume 0\n",
    }
    out = cek.build(
        res=res,
        fund={"pct": 10.0},
        fund_raw={"per": 38.0, "pbv": 1.9, "roe": 16.0, "roe_unit": "pct", "sector": "Energy"},
        nsent={"pct": 25.0, "n": 2},
        bandar_row={"ForeignNet": 3e9},
        agenda=None,
        agenda_days=None,
        peers={"ret20d": 1.5, "sector": "Energy"},
        value_avg20=10e9,
        board="Utama",
    )
    val = next(it for it in out["items"] if it["key"] == "valuasi")
    assert val["label"] == "Kinerja harga 20h vs peers"
    assert val["headline"] == "Campuran (salah satu murah, satunya tinggi)"
    assert "murah menurut ambang" not in val["headline"].lower()
    assert "median PER/PBV sektor tidak tersedia" in val["detail"]
    joined_warns = " | ".join(out["warnings"])
    assert "Gap ARB melewati SL" in joined_warns
    assert "Volume hari terakhir 0" in joined_warns
    assert "2 bar volume 0" in joined_warns
