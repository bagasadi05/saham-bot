"""Test pembentuk pesan Telegram (tanpa memanggil API Telegram)."""
from __future__ import annotations

import bot
from core import analysis


def _res(df):
    res = analysis.analyze("BBCA", df)
    res["stale_note"] = ""
    return res


def test_build_report_menampilkan_konteks_ihsg(uptrend_df):
    # Regresi: dulu `mkt` tidak diteruskan ke build_report sehingga baris
    # "Konteks pasar" selalu berbunyi "data pembanding tidak tersedia".
    txt = bot.build_report(_res(uptrend_df), None, None,
                           {"ret20d": 1.5, "ret1y": -4.0})
    assert "IHSG" in txt
    assert "data pembanding tidak tersedia" not in txt


def test_build_report_jujur_saat_ihsg_tidak_ada(uptrend_df):
    txt = bot.build_report(_res(uptrend_df), None, None, None)
    assert "data pembanding tidak tersedia" in txt


def test_build_report_mengescape_judul_berita(uptrend_df):
    news_sent = {
        "emoji": "⚪", "pct": 0.0,
        "details": [{"title": "<b>BBCA</b> & co", "link": "https://x", "sent": 2}],
    }
    txt = bot.build_report(_res(uptrend_df), None, news_sent)
    assert "&lt;b&gt;BBCA&lt;/b&gt; &amp; co" in txt
    assert "<b>BBCA</b> & co" not in txt


def test_build_quick_menandai_data_cache(uptrend_df):
    res = _res(uptrend_df)
    res["stale_note"] = "⚠️ cache"
    assert "(cache)" in bot.build_quick(res)


def test_format_rupiah():
    assert bot.fmt_rp(5_200_000) == "Rp5.200.000"


def test_escape_html():
    assert bot.esc("MA20 < MA50 & co") == "MA20 &lt; MA50 &amp; co"


def test_load_validation_file_tidak_ada():
    assert bot.load_validation("file-yang-tidak-ada.json") is None


def test_format_validasi_memuat_angka_uji():
    v = {
        "meta": {"start": "2017-09-07", "end": "2026-10-09",
                 "n_tickers": 44, "n_rows": 100_000, "horizon": 20},
        "score": {"ic": {"mean_ic": 0.0115, "t": 2.68},
                  "ic_timeseries": {"mean_ic": -0.0207, "t": -2.33}},
        "ratings": [{"rating": "STRONG BUY", "n": 12755, "excess_pct": 0.461,
                     "excess_win_rate": 46.2}],
        "walk_forward": [{"year": 2022, "ic_model": 0.047,
                          "ic_score": 0.0251, "ic_blend": 0.0563}],
        "quintiles": [{"quintile": 1, "excess_pct": -0.242},
                      {"quintile": 5, "excess_pct": 0.424}],
        "portfolio": {
            "n_phases": 20, "holding_days": 20,
            "cost_per_round_trip_pct": 0.5, "avg_turnover_pct": 73.6,
            "alpha_gross_mean_pct": 0.443, "alpha_net_mean_pct": 0.076,
            "alpha_net_median_pct": -0.05, "phases_net_positive": 9,
        },
    }
    txt = bot.format_validasi(v)
    assert "0.0115" in txt and "0.047" in txt
    assert "STRONG BUY" in txt
    assert "Q5 +0.42%" in txt
    assert "praktis nol" in txt          # IC 0,0115 → jujur disebut nol
    assert "bacaan tren" in txt
    assert "neto +0.08%" in txt          # biaya transaksi ditampilkan
    assert "9/20" in txt
