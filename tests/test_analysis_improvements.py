"""Test TDD untuk perbaikan menyeluruh modul analisis, berita, fundamental,
backtest, paper trading, chart, dan perbandingan SahamBuddy (offline)."""
from __future__ import annotations

import asyncio
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import pytest

import backtest
import backtest_real
import bot
from core import (alerts, analysis, banding, chart, fundamental, learn, news,
                  news_ml, relatif, saran, store)
from tests.conftest import make_prices


# ---------------------------------------------------------------- analysis ---

def test_compute_indicators_mengisolasi_kegagalan_satu_indikator_pro(monkeypatch):
    import pandas_ta_classic as ta
    closes = 1000.0 + np.arange(120) * 2.0 + 30.0 * np.sin(np.arange(120) * 0.4)
    df = make_prices(closes)
    monkeypatch.setattr(ta.AnalysisIndicators, "bbands",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bbands rusak")),
                        raising=False)
    ind = analysis.compute_indicators(df)
    # OBV, StochRSI, dan ATR tetap terhitung meski BBands gagal
    assert "obv" in ind.columns and ind["obv"].notna().any()
    assert "stochrsi" in ind.columns and ind["stochrsi"].notna().any()
    assert "atr14" in ind.columns and ind["atr14"].notna().any()


def test_extra_confirmations_harga_datar_jujur_dan_bebas_typo():
    flat = pd.DataFrame(
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 0.0},
        index=pd.bdate_range("2026-01-01", periods=60),
    )
    ind = analysis.compute_indicators(flat)
    lines = analysis.extra_confirmations(ind)
    joined = "\n".join(lines).lower()
    assert "dikuti" not in joined
    # Volume/OBV datar (+0) tidak boleh dilabeli "distribusi"
    assert "distribusi" not in joined
    # Harga datar (tanpa gerak sama sekali) tidak boleh dipalsukan jadi "oversold pendek"
    # ataupun "Bollinger SQUEEZE ... pergerakan besar"
    assert "oversold" not in joined
    assert "pergerakan besar" not in joined
    # Semua baris pro masih cocok dengan catatan kamus learn
    for ln in lines:
        assert learn.pro_note(ln) is not None


def test_score_volume_nan_atau_nol_diperlakukan_netral():
    last = pd.Series({
        "close": 100.0, "rsi14": 60.0, "ret1d": 1.0,
        "sma20": 95.0, "sma50": 90.0, "sma200": 85.0,
        "macd": 1.0, "macd_signal": 0.5, "high20": 99.0,
        "volume": float("nan"), "vol_avg20": 1_000_000.0,
    })
    sc = analysis.score_from_rows(last, last)
    assert "Volume" not in " ".join(sc["bull"] + sc["bear"]).title()

    last_zero = last.copy()
    last_zero["volume"] = 0.0
    last_zero["vol_avg20"] = 0.0
    sc0 = analysis.score_from_rows(last_zero, last_zero)
    assert not any("volume" in s.lower() for s in sc0["bull"] + sc0["bear"])


# ------------------------------------------------------------ fundamental ----

def test_fundamental_bank_der_tidak_dihukum_penalti():
    bank = {
        "name": "Bank Central Asia",
        "sector": "Financial Services",
        "per": 18.0, "pbv": 3.5, "roe": 0.21, "div_yield": 0.03,
        "debt_eq": 550.0, "margin": 0.45,
    }
    sc = fundamental.score_fundamentals(bank)
    notes_lower = " | ".join(sc["notes"]).lower()
    assert "der 550%" in notes_lower
    assert "der 550% tinggi" not in notes_lower
    assert "bank" in notes_lower or "keuangan" in notes_lower

    # Non-bank dengan DER 550% tetap dihukum
    nonbank = {**bank, "sector": "Consumer Cyclical"}
    sc_nb = fundamental.score_fundamentals(nonbank)
    assert sc["pct"] > sc_nb["pct"]


def test_fundamental_pbv_negatif_tidak_dianggap_murah():
    sc = fundamental.score_fundamentals({"pbv": -1.5, "per": -5.0, "roe": -0.2})
    notes = " | ".join(sc["notes"]).lower()
    assert "di bawah nilai buku" not in notes
    assert "negatif" in notes
    assert sc["pct"] < 0


def test_fundamental_profit_margin_ditampilkan_dan_disambig_dividend_yield(monkeypatch):
    class _FakeTicker:
        info = {
            "longName": "PT Test Tbk",
            "sector": "Energy",
            "trailingPE": 8.5,
            "priceToBook": 1.2,
            "trailingEps": 200.0,
            "dividendYield": 0.85,                 # 0.85% sudah skala persen dari Yahoo baru
            "trailingAnnualDividendYield": 0.0085, # 0.85% versi fraksi
            "returnOnEquity": 0.16,
            "profitMargins": 0.22,
            "debtToEquity": 35.0,
        }
        quarterly_income_stmt = None

    monkeypatch.setattr(fundamental.yf, "Ticker", lambda _s: _FakeTicker())
    fundamental.clear_cache()
    f = fundamental.fetch_fundamentals("ADRO")
    assert f is not None
    dy = fundamental.normalize_div_yield(f["div_yield"])
    assert dy == pytest.approx(0.85, abs=0.05)  # BUKAN 85.0%!
    assert fundamental.normalize_margin(f["margin"]) == pytest.approx(22.0, abs=0.1)


def test_trend_line_turnaround_rugi_ke_untung_positif():
    t1 = bot._trend_line([("Q2 2026", -100e9), ("Q3 2026", 50e9)])
    assert t1 is not None
    assert "📈" in t1
    assert "-50.0%" not in t1
    assert "+150.0%" in t1

    t2 = bot._trend_line([("Q2 2026", -100e9), ("Q3 2026", -20e9)])
    assert t2 is not None
    assert "📈" in t2
    assert "+80.0%" in t2
    assert "-Rp100M" in t2


# ---------------------------------------------------------- news & news_ml ---

def test_news_fetch_menghormati_timezone_offset_dan_hapus_suffix_sumber(monkeypatch):
    rss = (
        b"<rss><channel>"
        b"<item><title>Saham BBRI belum menguat di tengah tekanan asing - Bisnis.com</title>"
        b"<link>https://a</link><pubDate>Fri, 06 Nov 2026 10:00:00 +0700</pubDate>"
        b"<source>Bisnis.com</source></item>"
        b"<item><title>Saham BBRI belum menguat di tengah tekanan asing - Bisnis.com</title>"
        b"<link>https://b</link><pubDate>Fri, 06 Nov 2026 09:00:00 +0700</pubDate>"
        b"<source>Bisnis.com</source></item>"
        b"</channel></rss>"
    )
    monkeypatch.setattr(news, "_http_get", lambda *_a, **_k: rss)
    monkeypatch.setattr(news.datamod, "fetch_quote_name", lambda _t: "Bank Rakyat")
    news.clear_cache()
    try:
        items = news.fetch_news("BBRI")
        assert len(items) == 1  # duplikat judul dibuang
        assert items[0]["title"] == "Saham BBRI belum menguat di tengah tekanan asing"
        # 10:00 +07:00 = 03:00 UTC (BUKAN 10:00 UTC)
        assert items[0]["date"].astimezone(dt.timezone.utc).hour == 3
    finally:
        news.clear_cache()


def test_news_lexicon_frasa_dan_negasi_konteks():
    assert news.score_title("Laba BBCA turun 20%")[0] < 0
    assert news.score_title("Saham TLKM terkoreksi tipis")[0] < 0
    assert news.score_title("Harga belum menguat hari ini")[0] < 0
    assert news.score_title("Harga tidak kunjung naik")[0] < 0
    assert news.score_title("Saham XYZ digembok BEI")[0] < 0


def test_news_ml_cache_thread_safe(monkeypatch):
    class _FakeLogits:
        def __init__(self, n):
            self.logits = __import__("torch").tensor([[3.0, 0.0, 0.0]] * n)

    class _FakeModel:
        config = type("C", (), {"id2label": {0: "Positive", 1: "Neutral", 2: "Negative"}})()

        def __call__(self, **kw):
            return _FakeLogits(len(kw["ids"]))

    def _fake_tok(batch, **_kw):
        return {"ids": batch}

    monkeypatch.setattr(news_ml, "get_model", lambda: (_FakeModel(), _fake_tok))
    news_ml._cache.clear()
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda i: news_ml.classify_batch([f"Judul {i % 3}", f"Laba {i}"]), range(30)))
    assert all(len(r) == 2 and r[0]["label"] == "positive" for r in results)


def test_do_berita_menampilkan_label_mesin_sebenarnya(monkeypatch):
    from tests.test_bot_handlers import _FakeUpdate
    items = [{"title": "Laba bank naik", "link": "https://a", "date": None, "source": "Kontan"}]
    monkeypatch.setattr(bot.news, "fetch_news", lambda _t: items)
    monkeypatch.setattr(news_ml, "get_model", lambda: (None, None))
    upd = _FakeUpdate("/berita BBCA", cid=101)
    asyncio.run(bot._do_berita(upd, "BBCA"))
    msg = upd.effective_message.replies[-1][0]
    assert "lexicon" in msg.lower()


# ----------------------------------------- banding, alerts, chart, relatif ---

def test_banding_ipo_pendek_tidak_dipalsukan_jadi_return_satu_tahun():
    short_df = make_prices(np.linspace(1000, 800, 45))
    res_short = analysis.analyze("NEWCO", short_df)
    row = banding.compare_row("NEWCO", res_short, None)
    assert row["ret1y"] is None

    # Banding table alignment check
    hdr = banding.format_table_header()
    row_txt = banding.format_table_row({
        "ticker": "BBCA", "per": 18.5, "pbv": 3.8, "roe": 21.5,
        "dy": 3.2, "ret1y": 12.4, "ret20d": 2.1, "power": 35,
    })
    assert len(hdr) == len(row_txt)


def test_build_verdict_menampilkan_peringatan_divergensi_mingguan(uptrend_df):
    res = analysis.analyze("BBCA", uptrend_df)
    res["stale_note"] = ""
    weekly_warn = {
        "direction": "turun", "close": 5000.0, "ma20": 5500.0, "pct_vs_ma": -9.1,
        "warning": "daily membaik tapi weekly masih downtrend — pantulan, bukan pembalikan",
    }
    txt = bot.build_verdict(res, None, None, "beli", None, weekly=weekly_warn)
    assert "pantulan" in txt.lower()


def test_relatif_tie_dan_ddof1_adil():
    base = make_prices(np.linspace(1000, 1100, 60))
    ind = analysis.compute_indicators(base)
    items = [{"ticker": f"T{i}", "ind": ind} for i in range(10)]
    out = relatif.score_universe(items)
    # Semua identik → semua dapat persentil yang sama (50.0), bukan 5..95 acak
    assert len({it["rel_pct"] for it in out}) == 1
    assert out[0]["rel_pct"] == pytest.approx(50.0)


def test_alerts_aman_saat_nan_pada_kolom_level_atau_volume(uptrend_df):
    ind = analysis.compute_indicators(uptrend_df)
    for col in ("volume", "vol_avg20", "rsi14", "ret1d", "close"):
        broken = ind.copy()
        broken.iloc[-1, broken.columns.get_loc(col)] = float("nan")
        res = alerts.check_triggers(broken)
        assert isinstance(res, list)


def test_chart_render_harga_datar_tanpa_gap_tetap_valid(tmp_path):
    flat = pd.DataFrame(
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 0.0},
        index=pd.bdate_range("2026-01-01", periods=60),
    )
    ind = analysis.compute_indicators(flat)
    out = tmp_path / "flat.png"
    path = chart.build_chart("FLAT", ind, 0.0, str(out))
    assert out.exists() and out.stat().st_size > 10_000 and path == str(out)


# ---------------------------------------------------- paper trading & atr ----

def test_saran_paper_trading_menolak_bar_lama_dan_hitung_hari_bursa(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE_DB", str(tmp_path / "store.db"))
    monkeypatch.setattr(store, "STORE_FILE", str(tmp_path / "none.json"))
    store.set_paper_record(1, "BBCA", {
        "status": "open", "entry": 9000.0, "sl": 8900.0, "tp": 9200.0,
        "filled_at": "2026-11-09", "date": "09 Nov", "hit": "(belum)",
    })
    stale = pd.DataFrame(
        {"open": [8400.0], "high": [8500.0], "low": [8300.0], "close": [8400.0], "volume": [1e6]},
        index=pd.to_datetime(["2026-11-06"]),
    )
    assert saran.update_paper(1, {"BBCA": stale}, today=dt.date(2026, 11, 9)) == []
    assert store.get_paper(1)["BBCA"]["status"] == "open"

    # Uji libur bursa: 28 hari kalender yang memuat 10 hari libur hanya ~12 hari bursa
    holidays = ",".join(f"2026-11-{d:02d}" for d in (10, 11, 12, 13, 16, 17, 18, 19, 20, 23))
    monkeypatch.setenv("MARKET_HOLIDAYS", holidays)
    from core import marketcal
    marketcal.extra_holidays.cache_clear()
    try:
        assert saran._expired({"filled_at": "2026-11-09"}, dt.date(2026, 12, 7)) is False
    finally:
        marketcal.extra_holidays.cache_clear()


def test_saran_build_candidate_fallback_saat_atr_nol():
    res = {
        "ticker": "FLAT",
        "score": {"pct": 30},
        "snap": {"close": 1000.0, "ret20d": 0.0, "ret1d": 0.0},
        "ind": pd.DataFrame({"atr14": [0.0]}),
    }
    c = saran.build_candidate(res, None, None, modal=10_000_000)
    assert c["sl"] < c["entry"] < c["tp"]
    assert c["lots"] > 0


# --------------------------------------------------------------- backtest ----

def test_backtest_real_menunggu_warmup_ma200_dan_ringkas_aman(monkeypatch):
    n = 400
    df = make_prices(np.linspace(1000, 1500, n))
    monkeypatch.setattr(backtest_real.data, "fetch_history", lambda *_a, **_k: df)
    sig_df = backtest_real.build_statistics("BBCA")
    # Sebelum WARMUP (bar < 210), sinyal BUY tidak boleh menyala
    assert not sig_df["signal"].iloc[:backtest_real.WARMUP].any()
    stats = backtest_real.run_backtest("BBCA")
    s = backtest_real.summarize_stats("BBCA", stats)
    assert "expectancy" in s
    assert "Expectancy" in backtest_real.fmt_report("BBCA", stats) or "expectancy" in s


def test_backtest_summarize_kosong_tidak_error():
    empty = pd.DataFrame(columns=["date", "rating", "pct", "fwd_ret"])
    assert "tidak ada" in backtest.summarize(empty).lower()
