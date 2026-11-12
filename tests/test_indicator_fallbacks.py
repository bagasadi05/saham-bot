"""Fallback indikator harus terlihat di laporan, bukan ditelan diam-diam."""
from __future__ import annotations

import sys

import bot
from core import analysis


def test_missing_pandas_ta_is_reported(monkeypatch, uptrend_df):
    monkeypatch.setitem(sys.modules, "pandas_ta_classic", None)

    res = analysis.analyze("BBCA", uptrend_df)
    joined = " ".join(res["fallbacks"])

    assert "pandas-ta-classic" in joined
    assert "ATR" in joined


def test_clean_run_has_no_fallback(uptrend_df):
    res = analysis.analyze("BBCA", uptrend_df)
    assert res["fallbacks"] == []


def test_report_renders_fallback_note(uptrend_df):
    res = analysis.analyze("BBCA", uptrend_df)
    res["stale_note"] = ""
    res["fallback_note"] = "🧩 <i>Indikator cadangan: ATR fallback.</i>\n"

    txt = bot.build_report(res, None, None)

    assert "Indikator cadangan" in txt
