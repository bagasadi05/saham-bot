"""Laporan /analisis menampilkan catatan kualitas data bila ada."""
from __future__ import annotations

import bot
from core import analysis, data_quality


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
