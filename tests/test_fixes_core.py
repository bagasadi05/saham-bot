"""Test TDD untuk perbaikan bug inti, kelengkapan kamus, dan laporan backtest."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import backtest_real
from core import alerts, analysis, btcache, fundamental, learn, news, saran, store, textutil
from tests.conftest import make_prices


def test_rsi_datar_tidak_menghasilkan_nan():
    # Regresi: harga datar (saham tidur / ARB Rp50 beruntun) dulu 0/0 -> NaN
    df = make_prices([1000.0] * 60)
    ind = analysis.compute_indicators(df)
    rsi_valid = ind["rsi14"].dropna()
    assert len(rsi_valid) > 0
    assert rsi_valid.iloc[-1] == pytest.approx(50.0)
    snap = analysis.last_snapshot(ind)
    assert snap["rsi"] == pytest.approx(50.0)


def test_last_snapshot_aman_saat_vol_avg_nol_atau_nan():
    df = make_prices(np.linspace(1000, 1200, 40), volume=0)
    ind = analysis.compute_indicators(df)
    snap = analysis.last_snapshot(ind)
    assert snap["vol_avg20"] >= 1.0
    assert not pd.isna(snap["vol_avg20"])


def test_weekly_trend_konfirmasi_dan_divergensi():
    # Uptrend mingguan vs daily bearish -> warning koreksi
    up_w = make_prices(np.linspace(1000, 2000, 30))
    wt = analysis.weekly_trend(up_w, daily_score_pct=-30)
    assert wt is not None and wt["direction"] == "naik"
    assert wt["warning"]

    # Downtrend mingguan vs daily bullish -> warning pantulan
    dn_w = make_prices(np.linspace(2000, 1000, 30))
    wt2 = analysis.weekly_trend(dn_w, daily_score_pct=40)
    assert wt2 is not None and wt2["direction"] == "turun"
    assert "pantulan" in wt2["warning"].lower()

    # Data mingguan tidak cukup -> None (jujur, tidak dikarang)
    assert analysis.weekly_trend(make_prices(np.linspace(1000, 1100, 10)), 40) is None


@pytest.mark.parametrize(
    "signal",
    [
        "RSI overbought (75) - jenuh beli",
        "RSI oversold (25) - jenuh jual",
        "RSI lemah (42) - momentum di bawah 50",
        "Volume di atas rata-rata, tapi harga turun",
        "Volume di atas rata-rata dengan harga naik",
        "Harga naik, tapi volume di bawah rata-rata",
        "Volume di bawah rata-rata, harga turun",
        "Volume di bawah rata-rata, harga turun (pasar masih buka — volume hari ini belum lengkap)",
    ],
)
def test_learn_signal_note_cocok_semua_label_analysis(signal):
    note = learn.signal_note(signal)
    assert note is not None and note.strip()
    if "tapi harga turun" in signal:
        assert "kenaikan" not in note.lower()


def test_fundamental_roe_negatif_dan_der_konsisten():
    # Regresi: roe=-5.0 dulu dianggap fraksi (<3) -> -500.0%;
    # debt_eq=3.2 dulu dikali 100 -> DER 320% tinggi meski sebenarnya 3.2%!
    sc = fundamental.score_fundamentals({
        "per": 12.0,
        "pbv": 1.5,
        "roe": -5.0,
        "div_yield": 0.04,
        "debt_eq": 3.2,
    })
    notes = " | ".join(sc["notes"])
    assert "-5.0%" in notes
    assert "-500" not in notes
    assert "DER 3%" in notes and "sehat" in notes
    assert "320%" not in notes


def test_fundamental_der_negatif_ditandai_masalah():
    sc = fundamental.score_fundamentals({"debt_eq": -12.0})
    assert any("negatif" in n.lower() for n in sc["notes"])


def test_alert_message_escape_html_dan_format_rupiah():
    txt = alerts.build_alert_message(
        "A&B<X>",
        ["oversold"],
        {"close": 5200, "ret1d": -3.1},
        {"pct": -40, "emoji": "📉", "rating": "SELL"},
    )
    assert "Rp5.200" in txt
    assert "A&amp;B&lt;X&gt;" in txt
    assert "<X>" not in txt


def test_split_message_menutup_dan_membuka_pre_lintas_potongan():
    rows = "\n".join(f"EMTN{i:02d}  12.3  4.5  +10%" for i in range(40))
    html_msg = f"Header\n<pre>{rows}</pre>\nFooter"
    parts = textutil.split_message(html_msg, limit=250)
    assert len(parts) > 1
    for part in parts:
        assert len(part) <= 250
        assert part.count("<pre>") == part.count("</pre>")


def test_fill_pending_skip_open_tidak_valid(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE_DB", str(tmp_path / "store.db"))
    monkeypatch.setattr(store, "STORE_FILE", str(tmp_path / "none.json"))
    saran.queue_paper(1, [{
        "ticker": "BBCA", "price": 1000.0, "tech": 50, "fund": None, "news": None,
        "power": 50, "rating": "BUY", "emoji": "📈", "ret20d": 1.0,
        "sector": "Financials", "atr": 20.0, "entry": 1000.0, "sl": 980.0, "tp": 1040.0,
    }], "2026-11-06")
    bad_bars = pd.DataFrame(
        {"open": [0.0], "high": [1010.0], "low": [990.0], "close": [1000.0], "volume": [1e6]},
        index=pd.to_datetime(["2026-11-09"]),
    )
    msgs = saran.fill_pending(1, {"BBCA": bad_bars}, 10_000_000)
    assert msgs == []
    assert store.get_paper(1)["BBCA"]["status"] == "pending"


def test_saran_exposes_paper_summary_and_alias():
    assert saran.paper_summary is saran.quotes_forgotten


def test_backtest_real_summarize_annualizes_bh_and_handles_nan():
    # Simulasi 4 tahun: Buy & Hold total +100% -> annualized ≈ +18.9%/thn, bukan +100%/thn!
    fake_stats = pd.Series({
        "Start": pd.Timestamp("2022-01-01"),
        "End": pd.Timestamp("2026-01-01"),
        "Duration": pd.Timedelta(days=1461),
        "Return (Ann.) [%]": 2.5,
        "Buy & Hold Return [%]": 100.0,
        "# Trades": 0,
        "Win Rate [%]": float("nan"),
        "Profit Factor": float("nan"),
        "Max. Drawdown [%]": 0.0,
        "Exposure Time [%]": 0.0,
    })
    s = backtest_real.summarize_stats("BBCA", fake_stats)
    assert s["bh_ann"] == pytest.approx(18.92, abs=0.2)
    assert s["profit_factor"] is None
    assert s["win_rate"] is None

    rep = backtest_real.fmt_report("BBCA", fake_stats)
    assert "nan" not in rep.lower()
    assert "18.9% per tahun" in rep
    assert "0.15%" in rep and "0.25%" in rep


def test_btcache_save_load_dan_kedaluwarsa(tmp_path):
    btcache.save_summary(str(tmp_path), {
        "ticker": "BBCA", "as_of": "2026-11-06", "trades": 12,
        "win_rate": 41.7, "profit_factor": 1.12, "ret_ann": 5.4, "bh_ann": 7.1,
    })
    loaded = btcache.load_summary(str(tmp_path), "bbca")
    assert loaded is not None and loaded["trades"] == 12
    assert btcache.load_summary(str(tmp_path), "bbca", max_age=0) is None


def test_news_score_title_tanpa_dead_var():
    s, hits = news.score_title("Laba tumbuh dan harga breakout")
    assert s > 0 and "laba" in hits



def test_marketcal_upcoming_dividend_exdate_parsing(monkeypatch):
    import datetime as dt
    import yfinance as yf
    from core import marketcal

    today = dt.date(2026, 11, 6)

    class _FakeTicker:
        def __init__(self, cal):
            self.calendar = cal

    # Ex-date 3 hari ke depan (Timestamp) + dividend rate
    monkeypatch.setattr(
        yf, "Ticker",
        lambda sym: _FakeTicker({
            "Ex-Dividend Date": pd.Timestamp("2026-11-09"),
            "Dividend Rate": 210.0,
        }),
    )
    info = marketcal.upcoming_dividend_exdate("BBCA", today=today)
    assert info == {"ex_date": dt.date(2026, 11, 9), "amount": 210.0}

    # Ex-date lampau -> None (jangan memicu pengingat)
    monkeypatch.setattr(
        yf, "Ticker",
        lambda sym: _FakeTicker({"Ex-Dividend Date": dt.date(2026, 10, 1)}),
    )
    assert marketcal.upcoming_dividend_exdate("BBCA", today=today) is None


def test_store_band_and_div_reminder_idempotence(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE_DB", str(tmp_path / "store.db"))
    monkeypatch.setattr(store, "STORE_FILE", str(tmp_path / "none.json"))
    assert store.update_score_band(1, "BBCA", "NETRAL") is None
    assert store.update_score_band(1, "BBCA", "NETRAL") is None
    assert store.update_score_band(1, "BBCA", "BUY") == "NETRAL"
    assert store.mark_div_reminder(1, "BBCA", "2026-11-09", 3) is True
    assert store.mark_div_reminder(1, "BBCA", "2026-11-09", 3) is False


def test_data_fetch_weekly_uses_1wk_interval(tmp_path, monkeypatch):
    from core import data

    calls = []

    def fake_dl(sym, period=None, interval=None, **_k):
        calls.append((sym, period, interval))
        return make_prices(np.linspace(1000, 1500, 30))

    monkeypatch.setattr(data.yf, "download", fake_dl)
    monkeypatch.setattr(data, "_CACHE_DIR", str(tmp_path))
    data.clear_cache()
    df = data.fetch_weekly("BBCA")
    assert df is not None and len(df) == 30
    assert calls == [("BBCA.JK", "2y", "1wk")]
