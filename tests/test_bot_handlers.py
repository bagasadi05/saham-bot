"""Test offline handler bot.py (menu callback, watchlist, alert, P8-P11)."""
from __future__ import annotations

import asyncio
import datetime as dt
import types

import numpy as np
import pandas as pd
import pytest

import backtest as btmod
import bot
from core import btcache, data, store
from tests.conftest import make_prices


class _FakeUser:
    def __init__(self, uid: int = 100):
        self.id = uid


class _FakeChat:
    def __init__(self, cid: int = 100):
        self.id = cid
        self.actions: list[str] = []

    async def send_action(self, action):
        self.actions.append(str(action))


class _FakeMessage:
    def __init__(self, chat_id: int = 100, text: str = ""):
        self.chat_id = chat_id
        self.text = text
        self.replies: list[tuple[str, dict]] = []
        self.edits: list[tuple[str, dict]] = []
        self.photos: list[tuple[str, dict]] = []

    async def reply_text(self, text: str, **kwargs):
        self.replies.append((text, kwargs))
        return self

    async def edit_text(self, text: str, **kwargs):
        self.edits.append((text, kwargs))
        return self

    async def reply_photo(self, **kwargs):
        self.photos.append((kwargs.get("caption", ""), kwargs))
        return self


class _FakeQuery:
    def __init__(self, cb_data: str, uid: int = 100, cid: int = 100):
        self.data = cb_data
        self.from_user = _FakeUser(uid)
        self.message = _FakeMessage(chat_id=cid)
        self.answers: list[tuple[tuple, dict]] = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))


class _FakeUpdate:
    def __init__(self, text: str = "", uid: int = 100, cid: int = 100, query=None):
        self.effective_user = _FakeUser(uid)
        self.effective_chat = _FakeChat(cid)
        self.callback_query = query
        self.effective_message = query.message if query else _FakeMessage(cid, text)


class _FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str, dict]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs):
        self.sent.append((chat_id, text, kwargs))


def _ctx():
    return types.SimpleNamespace(bot=_FakeBot())


def _app():
    return types.SimpleNamespace(bot=_FakeBot())


@pytest.fixture(autouse=True)
def _clean_bot_state(tmp_path, monkeypatch, tmp_store):
    monkeypatch.setattr(bot, "TMP_DIR", str(tmp_path / "tmp"))
    monkeypatch.setattr(bot, "ALLOWED_IDS", set())
    bot._last_used.clear()
    bot.PENDING.clear()
    yield
    bot._last_used.clear()
    bot.PENDING.clear()


def test_menu_callback_checks_allowlist_before_answer(monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_IDS", {999})
    q = _FakeQuery("menu", uid=111, cid=111)
    upd = _FakeUpdate(uid=111, cid=111, query=q)
    asyncio.run(bot.menu_callback(upd, _ctx()))
    assert len(q.answers) == 1
    assert q.answers[0][1].get("show_alert") is True
    assert "privat" in q.answers[0][0][0].lower()


def test_menu_callback_malformed_tick_and_escapes_html(monkeypatch):
    q_bad = _FakeQuery("tick:analisis")
    asyncio.run(bot.menu_callback(_FakeUpdate(query=q_bad), _ctx()))
    assert q_bad.answers and q_bad.message.edits

    called = []

    async def fake_run(upd, action, tickers):
        called.append((action, tickers))

    monkeypatch.setattr(bot, "run_menu_action", fake_run)
    q_xss = _FakeQuery("tick:analisis:A&<B>")
    asyncio.run(bot.menu_callback(_FakeUpdate(query=q_xss), _ctx()))
    assert "A&amp;&lt;B&gt;" in q_xss.message.edits[-1][0]
    assert called == [("analisis", ["A&<B>"])]


def test_watch_list_and_remove_with_jk_suffix(monkeypatch):
    store.watchlist_add(100, "BBCA")
    df = make_prices(np.linspace(1000, 1200, 60))
    monkeypatch.setattr(bot.data, "fetch_history", lambda *a, **k: df)

    upd = _FakeUpdate(cid=100)
    asyncio.run(bot._do_watch_list(upd))
    texts = [t for t, _ in upd.effective_message.replies]
    assert any("BBCA" in t for t in texts)

    # remove pakai akhiran .JK harus tetap menghapus "BBCA"
    upd_del = _FakeUpdate("/watch remove BBCA.JK", cid=100)
    asyncio.run(bot.cmd_watch(upd_del, _ctx()))
    assert store.get_watchlist(100) == []


def test_watch_add_normalizes_and_enforces_max_watch(monkeypatch):
    monkeypatch.setattr(bot, "MAX_WATCH", 2)
    store.watchlist_add(100, "BBCA")
    store.watchlist_add(100, "BBRI")
    asyncio.run(bot.run_menu_action(_FakeUpdate(cid=100), "watch_add", ["TLKM.JK"]))
    assert store.get_watchlist(100) == ["BBCA", "BBRI"]


def test_alert_remove_normalizes_jk_suffix():
    store.set_alert(100, "BBCA", {"jump"})
    upd = _FakeUpdate("/alert remove BBCA.JK", cid=100)
    asyncio.run(bot.cmd_alert(upd, _ctx()))
    assert store.get_alerts(100) == {}


def test_berita_handles_none_date_and_escapes_html(monkeypatch):
    items = [
        {"title": "Laba <b>Naik</b> & Untung", "link": "https://x?a=1&b=2",
         "date": None, "source": "CNBC & Co"},
    ]
    monkeypatch.setattr(bot.news, "fetch_news", lambda t: items)
    monkeypatch.setattr(
        bot.news, "news_sentiment",
        lambda its: {"pct": 50.0, "emoji": "🟢", "n": 1,
                     "details": [{**its[0], "sent": 1.0, "age_days": 0.0}]},
    )
    upd = _FakeUpdate(cid=100)
    asyncio.run(bot.run_menu_action(upd, "berita", ["BBCA"]))
    msg = upd.effective_message.replies[-1][0]
    assert "&lt;b&gt;Naik&lt;/b&gt; &amp; Untung" in msg
    assert "CNBC &amp; Co" in msg


def test_menu_fundamental_includes_quarterly_trends(monkeypatch):
    monkeypatch.setattr(
        bot.fundamental, "fetch_fundamentals",
        lambda t: {
            "name": "Bank A & B", "sector": "Fin", "per": 12.0, "pbv": 2.0,
            "roe": 0.18, "div_yield": 0.04, "debt_eq": 5.0, "market_cap": 100e12,
            "as_of": "Q3 2026",
            "rev_trend": [("Q2 2026", 10e12), ("Q3 2026", 11e12)],
            "profit_trend": [("Q2 2026", 2e12), ("Q3 2026", 2.4e12)],
        },
    )
    upd = _FakeUpdate(cid=100)
    asyncio.run(bot.run_menu_action(upd, "fundamental", ["BBCA"]))
    msg = upd.effective_message.replies[-1][0]
    assert "Pendapatan 4K" in msg and "Laba bersih 4K" in msg
    assert "Bank A &amp; B" in msg


def test_backtest_saves_p8_cache_and_uses_annualized_bh(monkeypatch, tmp_path):
    bt_df = pd.DataFrame({
        "date": pd.bdate_range("2025-01-01", periods=10),
        "rating": ["BUY"] * 10,
        "pct": [30.0] * 10,
        "fwd_ret": [1.5] * 10,
    })
    monkeypatch.setattr(btmod, "backtest", lambda t: bt_df)
    import backtest_real
    fake_stats = pd.Series({
        "Start": pd.Timestamp("2022-01-01"),
        "End": pd.Timestamp("2026-01-01"),
        "Duration": pd.Timedelta(days=1461),
        "Return (Ann.) [%]": 3.0,
        "Buy & Hold Return [%]": 100.0,
        "# Trades": 10,
        "Win Rate [%]": 40.0,
        "Profit Factor": 1.25,
        "Max. Drawdown [%]": -8.5,
        "Exposure Time [%]": 25.0,
    })
    monkeypatch.setattr(backtest_real, "run_backtest", lambda t: fake_stats)

    upd = _FakeUpdate("/backtest BBCA", cid=100)
    asyncio.run(bot.cmd_backtest(upd, _ctx()))
    reply = upd.effective_message.replies[-1][0]
    assert "Buy &amp; hold/thn" in reply or "Buy & hold/thn" in reply
    assert "+18.9%" in reply
    assert "0.15%" in reply and "0.25%" in reply

    cached = btcache.load_summary(bot.TMP_DIR, "BBCA")
    assert cached is not None and cached["trades"] == 10 and cached["win_rate"] == 40.0


def test_p8_verdict_beli_uses_cache_and_fast_fallback(monkeypatch, uptrend_df):
    monkeypatch.setattr(bot.data, "fetch_history", lambda *a, **k: uptrend_df)
    monkeypatch.setattr(bot.fundamental, "fetch_fundamentals", lambda t: None)
    monkeypatch.setattr(bot.news, "fetch_news", lambda t: [])

    # Tanpa cache: cepat, tampilkan catatan /backtest
    upd1 = _FakeUpdate("/beli BBCA", cid=100)
    asyncio.run(bot.cmd_beli(upd1, _ctx()))
    msg1 = upd1.effective_message.replies[-1][0]
    assert "/backtest BBCA" in msg1

    # Dengan cache: tampilkan rekam jejak
    btcache.save_summary(bot.TMP_DIR, {
        "ticker": "BBCA", "as_of": "2026-11-06", "trades": 14,
        "win_rate": 42.9, "profit_factor": 1.15, "ret_ann": 4.2, "bh_ann": 7.5,
    })
    upd2 = _FakeUpdate("/beli BBCA", cid=100)
    asyncio.run(bot.cmd_beli(upd2, _ctx()))
    msg2 = upd2.effective_message.replies[-1][0]
    assert "Backtest 5 thn" in msg2
    assert "42.9%" in msg2 and "1.15" in msg2 and "14 transaksi" in msg2


def test_p9_analisis_weekly_confirmation_divergence_and_fallback(
    monkeypatch, uptrend_df, downtrend_df,
):
    monkeypatch.setattr(bot.data, "fetch_history", lambda *a, **k: uptrend_df)
    monkeypatch.setattr(bot.banding, "market_context", lambda: {"ret20d": 1.0, "ret1y": 2.0})
    monkeypatch.setattr(bot.fundamental, "fetch_fundamentals", lambda t: None)
    monkeypatch.setattr(bot.news, "fetch_news", lambda t: [])
    monkeypatch.setattr(bot, "build_chart", lambda *a, **k: None)

    # Weekly downtrend saat daily uptrend -> peringatan pantulan
    weekly_down = make_prices(np.linspace(3000, 1000, 40))
    monkeypatch.setattr(data, "fetch_weekly", lambda *a, **k: weekly_down)
    upd = _FakeUpdate("/analisis BBCA", cid=100)
    asyncio.run(bot.cmd_analisis(upd, _ctx()))
    msg = upd.effective_message.replies[-1][0]
    assert "Tren mingguan" in msg
    assert "pantulan" in msg.lower()

    # Weekly None -> fallback jujur
    bot._last_used.clear()
    monkeypatch.setattr(data, "fetch_weekly", lambda *a, **k: None)
    upd2 = _FakeUpdate("/analisis BBCA", cid=100)
    asyncio.run(bot.cmd_analisis(upd2, _ctx()))
    msg2 = upd2.effective_message.replies[-1][0]
    assert "data mingguan tidak tersedia" in msg2.lower()


def test_p10_band_change_alert_triggers_once_for_auto_chats(
    monkeypatch, uptrend_df, downtrend_df,
):
    store.set_auto(10, True)
    store.watchlist_add(10, "BBCA")
    store.set_auto(20, False)
    store.watchlist_add(20, "BBCA")

    flat_df = make_prices([1000.0] * 260)  # NETRAL (0%)
    cur = {"df": flat_df}
    monkeypatch.setattr(bot.data, "fetch_history", lambda *a, **k: cur["df"])
    monkeypatch.setattr(bot.fundamental, "fetch_fundamentals", lambda t: None)
    monkeypatch.setattr(
        bot.marketcal, "market_open", lambda: True,
    )

    app = _app()
    # Putaran 1: baseline NETRAL disimpan diam-diam -> 0 pesan
    asyncio.run(bot.scan_alerts(app))
    assert app.bot.sent == []

    # Putaran 2: berubah ke STRONG BUY -> tepat 1 pesan ke chat 10 (chat 20 auto=off)
    cur["df"] = uptrend_df
    asyncio.run(bot.scan_alerts(app))
    assert len(app.bot.sent) == 1
    assert app.bot.sent[0][0] == 10
    assert "BBCA" in app.bot.sent[0][1] and "BUY" in app.bot.sent[0][1]

    # Putaran 3: tetap BUY -> 0 pesan tambahan
    asyncio.run(bot.scan_alerts(app))
    assert len(app.bot.sent) == 1


def test_p10_dividend_exdate_reminder_deduped(monkeypatch):
    today = dt.date(2026, 11, 6)
    store.set_auto(10, True)
    store.watchlist_add(10, "BBCA")
    store.watchlist_add(10, "TLKM")
    store.set_auto(20, False)
    store.watchlist_add(20, "BBCA")

    ex_in_3d = today + dt.timedelta(days=3)
    monkeypatch.setattr(
        bot.marketcal, "upcoming_dividend_exdate",
        lambda t, today=None: {"ex_date": ex_in_3d, "amount": 150.0} if t == "BBCA" else None,
    )
    app = _app()
    sent1 = asyncio.run(bot.check_dividend_reminders(app, today=today))
    assert sent1 == 1
    assert len(app.bot.sent) == 1
    assert app.bot.sent[0][0] == 10
    assert "BBCA" in app.bot.sent[0][1] and "Rp150" in app.bot.sent[0][1]

    # Putaran ulang di hari yang sama -> 0 (tidak spam)
    sent2 = asyncio.run(bot.check_dividend_reminders(app, today=today))
    assert sent2 == 0 and len(app.bot.sent) == 1


def test_p11_cmd_id_returns_numeric_user_id():
    upd = _FakeUpdate("/id", uid=12345678, cid=12345678)
    asyncio.run(bot.cmd_id(upd, _ctx()))
    assert "12345678" in upd.effective_message.replies[-1][0]



def test_menu_text_input_jk_suffix_dan_natural_tanya(monkeypatch, uptrend_df):
    called = []

    async def fake_run(upd, action, tickers):
        called.append((action, tickers))

    monkeypatch.setattr(bot, "run_menu_action", fake_run)
    bot.PENDING[100] = "analisis"
    asyncio.run(bot.menu_text_input(_FakeUpdate("bad!ticker", cid=100), _ctx()))
    assert bot.PENDING.get(100) == "analisis"  # tetap menunggu input valid

    asyncio.run(bot.menu_text_input(_FakeUpdate("BBCA.JK", cid=100), _ctx()))
    assert called == [("analisis", ["BBCA"])]
    assert 100 not in bot.PENDING

    # Tanpa PENDING: pertanyaan natural memicu verdict
    monkeypatch.setattr(bot.data, "fetch_history", lambda *a, **k: uptrend_df)
    monkeypatch.setattr(bot.fundamental, "fetch_fundamentals", lambda t: None)
    monkeypatch.setattr(bot.news, "fetch_news", lambda t: [])
    upd_nat = _FakeUpdate("BBCA beli gak?", cid=100)
    asyncio.run(bot.menu_text_input(upd_nat, _ctx()))
    assert "Beli BBCA?" in upd_nat.effective_message.replies[-1][0]


def test_p9_analisis_merges_sector_context_without_duplicating_ihsg(monkeypatch, uptrend_df):
    monkeypatch.setattr(bot.data, "fetch_history", lambda *a, **k: uptrend_df)
    monkeypatch.setattr(bot.banding, "market_context", lambda: {"ret20d": 1.0, "ret1y": 2.0})
    monkeypatch.setattr(bot.fundamental, "fetch_fundamentals", lambda t: None)
    monkeypatch.setattr(bot.news, "fetch_news", lambda t: [])
    monkeypatch.setattr(bot, "build_chart", lambda *a, **k: None)
    monkeypatch.setattr(data, "fetch_weekly", lambda *a, **k: make_prices(np.linspace(1000, 2000, 30)))

    upd = _FakeUpdate("/analisis BBCA", cid=100)
    asyncio.run(bot.cmd_analisis(upd, _ctx()))
    msg = upd.effective_message.replies[-1][0]
    assert msg.count("Konteks pasar (IHSG") == 1
    assert "Se-sektor (BBRI, BMRI)" in msg


def test_watch_no_args_and_bandar_error_handling(monkeypatch):
    store.watchlist_add(100, "BBCA")
    upd_w = _FakeUpdate("/watch", cid=100)
    asyncio.run(bot.cmd_watch(upd_w, _ctx()))
    assert "BBCA" in upd_w.effective_message.replies[-1][0]

    monkeypatch.setattr(bot.bandar, "get_index_summary", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    upd_b = _FakeUpdate("/bandar", cid=100)
    asyncio.run(bot.cmd_bandar(upd_b, _ctx()))
    assert "tidak tersedia" in upd_b.effective_message.replies[-1][0].lower()
