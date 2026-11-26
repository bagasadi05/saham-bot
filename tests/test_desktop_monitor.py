"""Tes offline untuk mesin pemantau desktop (`core/desktop_monitor.py`) & `desktop.py`."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from core import bandar, desktop_helpers as dh, desktop_monitor as dm, store_portfolio
from tests.conftest import make_prices


def _make_ohlcv(n: int, start_price: float, drift: float, seed: int = 1):
    rng = np.random.default_rng(seed)
    rets = 1.0 + drift + rng.normal(0.0, 0.008, size=n)
    closes = start_price * np.cumprod(rets)
    return make_prices(closes)


def test_fetch_market_pulse_success_and_degradation():
    ihsg_df = _make_ohlcv(260, start_price=7200.0, drift=0.0005, seed=11)

    class FakeData:
        @staticmethod
        def fetch_history(ticker, period="2y", raw_symbol=False):
            assert ticker == "^JKSE"
            return ihsg_df

    class FakeScan:
        @staticmethod
        def load_market_scan():
            return [
                {
                    "ticker": "BBCA",
                    "price": 9000.0,
                    "ret1d": 1.8,
                    "sma50": 8800.0,
                    "sma200": 8500.0,
                    "value_avg20": 50_000_000_000,
                }
            ]

        @staticmethod
        def market_scan_meta():
            return {"trade_date": "2026-11-24"}

    class BlockedBandar:
        IDXTidakTersedia = bandar.IDXTidakTersedia

        @staticmethod
        def get_index_summary():
            raise bandar.IDXTidakTersedia("blocked")

    class FakeNews:
        @staticmethod
        def fetch_market_news(limit=10):
            return [{"title": "IHSG menguat ditopang saham bank", "link": "", "date": None, "source": "Test"}]

        @staticmethod
        def news_sentiment(items):
            return {"label": "POSITIF", "pct": 50.0, "emoji": "🟢"}

    rep = dm.fetch_market_pulse(
        data_mod=FakeData,
        store_scan_mod=FakeScan,
        bandar_mod=BlockedBandar,
        news_mod=FakeNews,
    )
    assert rep["ihsg"]["ok"] is True
    assert rep["breadth"]["ok"] is True
    assert rep["sectors"]["ok"] is False
    assert "Cloudflare" in (rep["sectors"]["reason"] or "")
    assert rep["news"]["label"] == "POSITIF"
    assert "delay" in rep["delay_note"].lower()


def test_evaluate_watchlist_rows_actions_and_missing():
    bull_df = _make_ohlcv(120, start_price=1000.0, drift=0.004, seed=7)
    bear_df = _make_ohlcv(120, start_price=2000.0, drift=-0.005, seed=9)

    class FakeData:
        @staticmethod
        def fetch_history(sym, period="6mo"):
            if sym == "BULL":
                return bull_df
            if sym == "BEAR":
                return bear_df
            return None

    class EmptyScan:
        @staticmethod
        def load_market_scan():
            return []

    rows = dm.evaluate_watchlist_rows(
        ["BULL", "BEAR", "MISS"],
        modal=20_000_000,
        data_mod=FakeData,
        store_scan_mod=EmptyScan,
    )
    by_t = {r["ticker"]: r for r in rows}
    assert by_t["BULL"]["action"] in ("BELI", "SIAP BELI", "TAHAN")
    assert by_t["BULL"]["lots"] >= 0
    assert by_t["BEAR"]["action"] == "JUAL"
    assert by_t["MISS"]["action"] == "TIDAK TERSEDIA"
    assert by_t["MISS"]["price"] is None


def test_evaluate_portfolio_rows_and_alert_dedup(tmp_store):
    cid = "desktop-test"
    store_portfolio.record_execution_buy(
        cid, "BBCA", 5, 9000.0, trade_date="2026-11-24", sl=8700.0, tp=9600.0
    )
    # Buat harga drop tajam di bawah SL -> tesis GUGUR
    drop_df = _make_ohlcv(80, start_price=9500.0, drift=-0.003, seed=3)
    drop_df.iloc[-1, drop_df.columns.get_loc("close")] = 8400.0
    drop_df.iloc[-1, drop_df.columns.get_loc("low")] = 8350.0

    class FakeData:
        @staticmethod
        def fetch_history(sym, period="6mo"):
            return drop_df

    class EmptyScan:
        @staticmethod
        def load_market_scan():
            return []

    port_rows = dm.evaluate_portfolio_rows(
        cid, data_mod=FakeData, store_scan_mod=EmptyScan
    )
    assert len(port_rows) == 1
    assert port_rows[0]["ticker"] == "BBCA"
    assert port_rows[0]["thesis"] == "GUGUR"

    watch_rows = [
        {"ticker": "BBRI", "action": "BELI", "price": 4500.0, "entry": 4480.0, "sl": 4350.0, "reason": "tren kuat"},
        {"ticker": "MISS", "action": "TIDAK TERSEDIA", "price": None},
    ]
    state: dict[str, str] = {}
    alerts1 = dm.detect_action_alerts(state, watch_rows, port_rows)
    assert len(alerts1) == 2
    assert {a["ticker"] for a in alerts1} == {"BBRI", "BBCA"}

    # Status sama pada putaran berikutnya -> tidak spam alert ulang
    alerts2 = dm.detect_action_alerts(state, watch_rows, port_rows)
    assert alerts2 == []


def test_inspect_ticker_detail_and_desktop_helpers(tmp_store):
    df = _make_ohlcv(260, start_price=8000.0, drift=0.001, seed=5)

    class FakeData:
        @staticmethod
        def fetch_history(sym, period="1y"):
            return df

    class FakeFund:
        @staticmethod
        def fetch_fundamental(sym):
            return {"per": 12.0, "pbv": 1.8, "roe": 18.0, "roe_unit": "pct", "sector": "Financial Services"}

        @staticmethod
        def score_fundamental(raw):
            return {"pct": 45.0, "rating": "BAIK", "forensic_penalty": 0.0, "forensic_flags": []}

    class FakeNews:
        @staticmethod
        def fetch_news(sym, limit=8):
            return [{"title": f"{sym} catat pertumbuhan laba bersih", "link": "", "date": None, "source": "Test"}]

        @staticmethod
        def news_sentiment(items):
            return {"label": "POSITIF", "pct": 40.0, "emoji": "🟢"}

    detail = dm.inspect_ticker_detail(
        "BBCA",
        10_000_000,
        chat_id="desktop-test",
        data_mod=FakeData,
        fundamental_mod=FakeFund,
        news_mod=FakeNews,
    )
    assert detail["ticker"] == "BBCA"
    assert detail["cek"]["verdict"] in ("LAYAK", "TUNGGU", "HINDARI", "TIDAK CUKUP DATA")
    assert detail["plan"] is not None
    assert isinstance(detail["plan"]["lots"], int)

    with pytest.raises(ValueError):
        dm.inspect_ticker_detail("", 10_000_000)

    assert dh._fmt_rp(1500000) == "Rp1.500.000"
    assert dh._fmt_rp(None) == "-"
    assert dh._fmt_pct(1.234) == "+1.23%"
    assert dh._fmt_pct(None) == "-"
    assert dh._strip_html("<b>Halo</b> &amp; <i>dunia</i>") == "Halo & dunia"
    assert dh.create_tray_icon_image(32).size == (32, 32)


def test_desktop_gui_class_available_when_tk_installed():
    pytest.importorskip("tkinter")
    import desktop

    assert isinstance(SimpleNamespace(app=desktop.SahamBotDesktopApp), object)
    assert desktop._fmt_rp is dh._fmt_rp


def test_load_sinyal_paket_jurnal_and_scan(tmp_store, tmp_path):
    class FakeScanStore:
        @staticmethod
        def load_market_scan(sig=None):
            rows = [
                {
                    "ticker": "BBCA",
                    "name": "Bank Central Asia",
                    "price": 9000.0,
                    "ret1d": 1.2,
                    "signal": "BELI",
                    "rel_pct": 88.0,
                    "entry": 8950.0,
                    "sl": 8700.0,
                    "tp": 9500.0,
                    "atr": 150.0,
                    "value_avg20": 80_000_000_000.0,
                    "board": "Utama",
                    "reason": "tren naik",
                },
                {
                    "ticker": "BBRI",
                    "name": "Bank Rakyat Indonesia",
                    "price": 4500.0,
                    "ret1d": -1.5,
                    "signal": "JUAL",
                    "rel_pct": 18.0,
                    "entry": 4500.0,
                    "sl": 4650.0,
                    "tp": 4200.0,
                    "atr": 90.0,
                    "value_avg20": 70_000_000_000.0,
                    "board": "Utama",
                    "reason": "lemah",
                },
            ]
            if sig:
                return [r for r in rows if r["signal"] == sig]
            return rows

        @staticmethod
        def market_scan_meta():
            return {"trade_date": "2026-11-24"}

        @staticmethod
        def market_scan_counts():
            return {"BELI": 1, "JUAL": 1, "TAHAN": 0}

    class FakeFund:
        CACHE_DIR = str(tmp_path / "fund_cache")

        @staticmethod
        def fetch_fundamental(sym):
            return {"per": 14.0, "pbv": 2.0, "roe": 16.0, "roe_unit": "pct"}

        @staticmethod
        def score_fundamental(raw):
            return {"pct": 50.0, "rating": "BAIK", "forensic_penalty": 0.0, "forensic_flags": []}

    class FakeNews:
        @staticmethod
        def fetch_news(sym, limit=5):
            return []

    s_all = dm.load_sinyal_rows("ALL", store_scan_mod=FakeScanStore, fundamental_mod=FakeFund)
    assert len(s_all["rows"]) == 2
    s_beli_q = dm.load_sinyal_rows(
        "BELI", quality_only=True, store_scan_mod=FakeScanStore, fundamental_mod=FakeFund
    )
    assert len(s_beli_q["rows"]) == 1
    assert s_beli_q["rows"][0]["ticker"] == "BBCA"

    paket = dm.build_paket_picks(
        50_000_000,
        chat_id="desktop-test",
        store_scan_mod=FakeScanStore,
        fundamental_mod=FakeFund,
        news_mod=FakeNews,
    )
    assert paket["ok"] is True
    assert paket["plans"][0]["ticker"] == "BBCA"

    j_empty = dm.build_journal_summary("desktop-test")
    assert j_empty["ok"] is False

    store_portfolio.record_execution_buy("desktop-test", "BBCA", 2, 9000.0, trade_date="2026-11-20")
    store_portfolio.record_execution_sell("desktop-test", "BBCA", 2, 9400.0, trade_date="2026-11-24")
    j_ok = dm.build_journal_summary("desktop-test")
    assert j_ok["ok"] is True
    assert "BBCA" in j_ok["history_text"]

    class FakeScreener:
        @staticmethod
        def scan(force=True):
            return {"ok": True, "scanned": 944, "force": force}

    assert dm.run_full_market_scan(force=True, screener_mod=FakeScreener)["scanned"] == 944
