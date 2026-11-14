"""Tes offline batas risiko kustom (P23 lanjutan) & pembobotan inverse-vol."""
from __future__ import annotations

import asyncio
import sqlite3
import types

import numpy as np
import pandas as pd
import pytest

import bot
from core import banding, data, fundamental, news, risk, store
from tests.conftest import make_prices


def _rets(seed: int, sigma: float, n: int = 60) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2026-07-01", periods=n)
    return pd.Series(rng.normal(0.0003, sigma, n), index=idx)


def test_inverse_vol_weights_lebih_berat_ke_vol_rendah_dan_capped():
    quotes = {
        "LOWV": {"close": 5000.0, "rets": _rets(1, 0.005)},
        "MIDV": {"close": 2000.0, "rets": _rets(2, 0.020)},
        "HIGV": {"close": 1000.0, "rets": _rets(3, 0.040)},
    }
    res = risk.inverse_vol_weights(quotes, ["LOWV", "MIDV", "HIGV"], max_weight=0.40)
    assert res["ok"] is True
    assert res["capped"] is True
    w = res["weights"]
    assert w["LOWV"] == pytest.approx(40.0, abs=0.2)
    assert w["MIDV"] > w["HIGV"]
    assert sum(w.values()) == pytest.approx(100.0, abs=0.4)


def test_inverse_vol_jujur_saat_riwayat_kurang():
    res = risk.inverse_vol_weights(
        {"AAAA": {"close": 1000.0, "rets": _rets(4, 0.01)}, "BBBB": {"close": 1000.0}},
        ["AAAA", "BBBB"],
    )
    assert res["ok"] is False
    assert res["weights"] == {}


def test_store_migrasi_v5_ke_v6_dan_batas_kustom(tmp_path, monkeypatch):
    db = tmp_path / "legacy_v5.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE chats (chat_id TEXT PRIMARY KEY, modal INTEGER NOT NULL DEFAULT 10000000, auto INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE watchlist (chat_id TEXT NOT NULL, symbol TEXT NOT NULL, PRIMARY KEY (chat_id, symbol));
        CREATE TABLE alerts (chat_id TEXT NOT NULL, symbol TEXT NOT NULL, kinds TEXT NOT NULL DEFAULT '[]', active TEXT NOT NULL DEFAULT '[]', PRIMARY KEY (chat_id, symbol));
        CREATE TABLE paper (chat_id TEXT NOT NULL, symbol TEXT NOT NULL, rec TEXT NOT NULL, PRIMARY KEY (chat_id, symbol));
        CREATE TABLE score_bands (chat_id TEXT NOT NULL, symbol TEXT NOT NULL, band TEXT NOT NULL, PRIMARY KEY (chat_id, symbol));
        CREATE TABLE div_reminders (chat_id TEXT NOT NULL, symbol TEXT NOT NULL, ex_date TEXT NOT NULL, days INTEGER NOT NULL, PRIMARY KEY (chat_id, symbol, ex_date, days));
        CREATE TABLE market_scan (ticker TEXT PRIMARY KEY, name TEXT, board TEXT, trade_date TEXT, price REAL, ret1d REAL, ret5d REAL, ret20d REAL, rsi REAL, macd_hist REAL, sma20 REAL, sma50 REAL, sma200 REAL, atr REAL, vol_avg20 REAL, value_avg20 REAL, tech_score REAL, rel_pct REAL, signal TEXT NOT NULL, reason TEXT, entry REAL, sl REAL, tp REAL);
        CREATE TABLE scan_history (trade_date TEXT NOT NULL, ticker TEXT NOT NULL, signal TEXT NOT NULL, price REAL, entry REAL, sl REAL, tp REAL, atr REAL, rel_pct REAL, reason TEXT, recorded_at TEXT NOT NULL, closed_at TEXT, bars INTEGER, fwd_ret REAL, r_multiple REAL, outcome TEXT, PRIMARY KEY (trade_date, ticker));
        CREATE TABLE scan_meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE plan_cache (cache_key TEXT PRIMARY KEY, trade_date TEXT, created_at TEXT, payload TEXT NOT NULL);
        CREATE TABLE real_positions (chat_id TEXT NOT NULL, ticker TEXT NOT NULL, lots INTEGER NOT NULL, avg_price REAL NOT NULL, sector TEXT, updated_at TEXT NOT NULL, PRIMARY KEY (chat_id, ticker));
        CREATE TABLE real_trades (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL, ticker TEXT NOT NULL, side TEXT NOT NULL, lots INTEGER NOT NULL, price REAL NOT NULL, trade_date TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE zone_state (chat_id TEXT NOT NULL, ticker TEXT NOT NULL, zone TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (chat_id, ticker));
        CREATE TABLE signal_context (chat_id TEXT NOT NULL, ticker TEXT NOT NULL, source TEXT NOT NULL, signal_price REAL NOT NULL, sl REAL, tp REAL, suggested_lots INTEGER, trade_date TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (chat_id, ticker));
        CREATE TABLE real_executions (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL, ticker TEXT NOT NULL, side TEXT NOT NULL, lots INTEGER NOT NULL, exec_price REAL NOT NULL, signal_price REAL, sl REAL, tp REAL, source TEXT NOT NULL DEFAULT 'manual', slippage_rp REAL, slippage_pct REAL, slippage_cost REAL, avg_entry REAL, gross_pnl REAL, net_pnl REAL, net_pnl_pct REAL, trade_date TEXT NOT NULL, created_at TEXT NOT NULL);
        PRAGMA user_version = 5;
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(store, "STORE_DB", str(db))
    store._ready_for = None

    assert store.get_risk_limits(100) == (0.25, 0.40)
    pos, sec = store.set_risk_limits(100, 0.15, 0.30)
    assert (pos, sec) == (0.15, 0.30)
    assert store.get_risk_limits(100) == (0.15, 0.30)

    with pytest.raises(ValueError):
        store.set_risk_limits(100, 0.35, 0.25)

    assert store.set_risk_limits(100, None, None) == (0.25, 0.40)
    assert store.get_risk_limits(100) == (0.25, 0.40)

    conn2 = sqlite3.connect(store.STORE_DB)
    try:
        assert conn2.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
    finally:
        conn2.close()


def test_handler_portofolio_batas_dan_inv_vol(monkeypatch, tmp_store):
    class _Msg:
        def __init__(self, text=""):
            self.text = text
            self.replies = []

        async def reply_text(self, text, **kw):
            self.replies.append((text, kw))
            return self

    class _Chat:
        id = 888

        async def send_action(self, *a, **kw):
            return None

    class _User:
        id = 888

    class _Upd:
        def __init__(self, text):
            self.effective_message = _Msg(text)
            self.effective_chat = _Chat()
            self.effective_user = _User()

    bot.ALLOWED_IDS.clear()
    bot._last_used.clear()
    store.set_modal(888, 10_000_000)

    upd1 = _Upd("/portofolio batas 15 30")
    asyncio.run(bot.cmd_portofolio(upd1, types.SimpleNamespace()))
    assert "15%" in upd1.effective_message.replies[-1][0]
    assert "30%" in upd1.effective_message.replies[-1][0]

    # Cek /rencana menolak bila melanggar batas kustom 15%
    ind = make_prices(np.linspace(950, 1000, 80), volume=10_000_000)
    ind["atr14"] = 25.0

    async def fake_gather(t):
        return {
            "res": {
                "ticker": t,
                "snap": {"close": 1000.0, "rsi": 55.0, "ret20d": 3.0},
                "ind": ind,
            },
            "fund": {"pct": 20.0, "label": "CUKUP"},
            "fund_raw": {"per": 12.0, "pbv": 1.5, "roe": 0.16, "sector": "Financials"},
            "nsent": {"pct": 10.0, "n": 1, "label": "NETRAL"},
        }

    monkeypatch.setattr(bot, "gather_candidate", fake_gather)
    monkeypatch.setattr(store, "load_market_scan", lambda: [])
    upd_ren = _Upd("/rencana BBCA 2000000")
    asyncio.run(bot.cmd_rencana(upd_ren, types.SimpleNamespace()))
    ren_txt = "\n".join(t for t, _ in upd_ren.effective_message.replies)
    assert "ditolak" in ren_txt and "15%" in ren_txt

    # Cek /cek memberi peringatan memakai batas kustom 15%
    store.add_real_position(888, "BBCA", 20, 1000.0, "2026-11-14", "Financials")
    store.add_real_position(888, "TLKM", 10, 1000.0, "2026-11-14", "Infrastructure")

    async def fake_fetch(t):
        return {
            "ticker": t,
            "snap": {"close": 1000.0, "rsi": 55.0, "ret20d": 2.0},
            "score": {"pct": 35.0},
            "ind": ind,
            "stale_note": "",
        }

    monkeypatch.setattr(bot, "fetch_and_analyze", fake_fetch)
    monkeypatch.setattr(fundamental, "fetch_fundamentals",
                        lambda t: {"per": 12.0, "pbv": 1.5, "roe": 18.0, "sector": "Financials"})
    monkeypatch.setattr(news, "fetch_news", lambda t: [])
    monkeypatch.setattr(news, "news_sentiment", lambda items: {"pct": 0.0, "n": 0})
    monkeypatch.setattr(data, "fetch_weekly", lambda t: make_prices(np.linspace(900, 1000, 60)))
    monkeypatch.setattr(bot, "_cek_bandar_row", lambda t: None)
    monkeypatch.setattr(bot, "_cek_agenda", lambda t: (None, None))
    monkeypatch.setattr(banding, "sector_context", lambda *a, **k: {"ret20d": 1.0, "sector": "Financials"})
    bot._last_used.clear()

    upd_cek = _Upd("/cek BBCA")
    asyncio.run(bot.cmd_cek(upd_cek, types.SimpleNamespace()))
    cek_txt = "\n".join(t for t, _ in upd_cek.effective_message.replies)
    assert "batas 15%" in cek_txt

    # Cek /portofolio menampilkan inv-vol target
    monkeypatch.setattr(bot, "_quote_posisi", lambda t: {
        "close": 1000.0,
        "atr": 25.0,
        "stale": False,
        "rets": _rets(10 if t == "BBCA" else 20, 0.01 if t == "BBCA" else 0.03),
    })
    monkeypatch.setattr(data, "fetch_history", lambda *a, **k: None)
    bot._last_used.clear()
    upd_p = _Upd("/portofolio")
    asyncio.run(bot.cmd_portofolio(upd_p, types.SimpleNamespace()))
    p_txt = "\n".join(t for t, _ in upd_p.effective_message.replies)
    assert "Bobot seimbang (inv-vol)" in p_txt

    upd_reset = _Upd("/portofolio batas reset")
    asyncio.run(bot.cmd_portofolio(upd_reset, types.SimpleNamespace()))
    assert store.get_risk_limits(888) == (0.25, 0.40)
