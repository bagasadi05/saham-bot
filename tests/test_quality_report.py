"""Laporan /analisis menampilkan catatan kualitas data bila ada."""
from __future__ import annotations

import bot
from core import analysis, data_quality, reports


def _res(df):
    res = analysis.analyze("BBCA", df)
    res["stale_note"] = ""
    res["quality_note"] = data_quality.summary_line(
        {"ok": False, "flags": {"volume_nol": 3}})
    return res


def test_build_report_menampilkan_kualitas_data(uptrend_df):
    txt = bot.build_report(_res(uptrend_df), None, None)
    assert "Kualitas data" in txt
    assert "3 bar volume 0" in txt


def test_build_report_tanpa_catatan_tidak_menambah_baris(uptrend_df):
    res = _res(uptrend_df)
    res["quality_note"] = ""
    txt = bot.build_report(res, None, None)
    assert "Kualitas data" not in txt


def test_build_report_menampilkan_provenance_dan_label_tren_jujur(uptrend_df):
    res = _res(uptrend_df)
    res["provenance"] = {
        "provider": "yfinance",
        "last_bar_date": "2026-11-14",
        "repaired_count": 2,
    }
    txt = bot.build_report(res, None, None)
    assert "Provenance data" in txt
    assert "yfinance" in txt and "2 bar diperbaiki" in txt
    assert "STRONG BUY" not in txt and "STRONG SELL" not in txt


def test_reports_mata_uang_non_idr_tanpa_mengarang_kurs():
    line_usd = reports._trend_line(
        [("Q2 2026", 1.2e9), ("Q3 2026", 1.5e9)], currency="USD"
    )
    assert line_usd is not None
    assert "USD" in line_usd and "Rp" not in line_usd
    assert "USD" in reports.fmt_amount(1250.0, currency="USD")
    assert "Rp" not in reports.fmt_amount(1250.0, currency=None)
