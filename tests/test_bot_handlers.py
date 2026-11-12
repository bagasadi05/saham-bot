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


class _FakeJobQueue:
    def __init__(self):
        self.calls: list[tuple] = []

    def run_once(self, callback, when=None, data=None, name=None, **kwargs):
        self.calls.append((callback, when, data, name))


def _ctx(job_queue=None):
    return types.SimpleNamespace(bot=_FakeBot(), job_queue=job_queue)


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


def test_modal_tampil_ubah_dan_validasi():
    tampil = _FakeUpdate("/modal", cid=101)
    asyncio.run(bot.cmd_modal(tampil, _ctx()))
    assert "10.000.000" in tampil.effective_message.replies[-1][0]

    ok = _FakeUpdate("/modal 5.000.000", cid=101)
    asyncio.run(bot.cmd_modal(ok, _ctx()))
    assert store.get_modal(101) == 5_000_000
    assert "5.000.000" in ok.effective_message.replies[-1][0]

    salah = _FakeUpdate("/modal abc", cid=101)
    asyncio.run(bot.cmd_modal(salah, _ctx()))
    assert "tidak valid" in salah.effective_message.replies[-1][0]
    assert store.get_modal(101) == 5_000_000  # tidak berubah

    kecil = _FakeUpdate("/modal 1000", cid=101)
    asyncio.run(bot.cmd_modal(kecil, _ctx()))
    assert "100 ribu" in kecil.effective_message.replies[-1][0]
    assert store.get_modal(101) == 5_000_000


def test_auto_on_off_dan_status():
    on = _FakeUpdate("/auto on", cid=102)
    asyncio.run(bot.cmd_auto(on, _ctx()))
    assert store.get_auto(102) is True
    assert "AKTIF" in on.effective_message.replies[-1][0]

    off = _FakeUpdate("/auto off", cid=102)
    asyncio.run(bot.cmd_auto(off, _ctx()))
    assert store.get_auto(102) is False
    assert "mati" in off.effective_message.replies[-1][0].lower()

    status = _FakeUpdate("/auto", cid=102)
    asyncio.run(bot.cmd_auto(status, _ctx()))
    assert "mati (default)" in status.effective_message.replies[-1][0]


def test_banding_butuh_minimal_dua_emiten():
    upd = _FakeUpdate("/banding BBCA", cid=103)
    asyncio.run(bot.cmd_banding(upd, _ctx()))
    assert "2–4 emiten" in upd.effective_message.replies[-1][0]


def test_belajar_topik_dikenal_tidak_dikenal_dan_daftar():
    rsi = _FakeUpdate("/belajar RSI", cid=104)
    asyncio.run(bot.cmd_belajar(rsi, _ctx()))
    txt = rsi.effective_message.replies[-1][0]
    assert "RSI" in txt and len(txt) > 100

    asing = _FakeUpdate("/belajar istilah-ngawur", cid=104)
    asyncio.run(bot.cmd_belajar(asing, _ctx()))
    assert "Topik tidak dikenal" in asing.effective_message.replies[-1][0]

    daftar = _FakeUpdate("/belajar", cid=104)
    asyncio.run(bot.cmd_belajar(daftar, _ctx()))
    assert "Pilih topik" in daftar.effective_message.replies[-1][0]


def test_id_menampilkan_id_telegram():
    upd = _FakeUpdate("/id", uid=777, cid=777)
    asyncio.run(bot.cmd_id(upd, _ctx()))
    assert "<code>777</code>" in upd.effective_message.replies[-1][0]


def test_alert_add_valid_dan_jenis_tidak_dikenal():
    ok = _FakeUpdate("/alert add bbca.jk jump drop", cid=105)
    asyncio.run(bot.cmd_alert(ok, _ctx()))
    assert "Alert untuk" in ok.effective_message.replies[-1][0]
    assert store.get_alerts(105)["BBCA"] == ["drop", "jump"]

    asing = _FakeUpdate("/alert add BBCA jenis-ngawur", cid=105)
    asyncio.run(bot.cmd_alert(asing, _ctx()))
    assert "Jenis tidak dikenal" in asing.effective_message.replies[-1][0]
    assert "BBCA" in store.get_alerts(105)  # alert lama tidak rusak


# ------------------------------------------------ screener pasar (/scan, /sinyal)
def _seed_scan():
    """Snapshot sintetis: satu BELI, satu JUAL — tanpa jaringan."""
    from core import marketcal
    rows = [
        {"ticker": "BBCA", "name": "Bank Central Asia Tbk.", "board": "Utama",
         "trade_date": "2026-11-11", "price": 8750, "ret1d": 0.5,
         "ret5d": 1.2, "ret20d": 3.2, "rsi": 58.0, "macd_hist": 12.0,
         "sma20": 8600, "sma50": 8400, "sma200": 7900, "atr": 150,
         "tech_score": 55.0, "rel_pct": 92.0, "signal": "BELI",
         "reason": "Tren naik (MA20 > MA50, MACD+), RSI 58",
         "entry": 8750, "sl": 8600, "tp": 9050},
        {"ticker": "ANTM", "name": "Aneka Tambang Tbk.", "board": "Utama",
         "trade_date": "2026-11-11", "price": 1500, "ret1d": -1.0,
         "ret5d": -3.0, "ret20d": -8.0, "rsi": 35.0, "macd_hist": -9.0,
         "sma20": 1560, "sma50": 1620, "sma200": 1700, "atr": 40,
         "tech_score": -50.0, "rel_pct": 12.0, "signal": "JUAL",
         "reason": "Tren turun (MA20 < MA50, MACD−), RSI 35",
         "entry": None, "sl": None, "tp": None},
    ]
    meta = {
        "scanned_at": marketcal.now_wib().strftime("%Y-%m-%dT%H:%M:%S"),
        "trade_date": "2026-11-11", "universe": 2, "ok": 2,
        "skipped": 0, "failed": 0, "elapsed_sec": 1.0,
        "universe_source": "uji", "universe_snapshot": "2026-11-01",
        "buy": 1, "sell": 1, "hold": 0,
    }
    store.save_market_scan(rows, meta)
    return rows, meta


def test_sinyal_tanpa_snapshot_jujur_arahkan_scan():
    upd = _FakeUpdate("/sinyal")
    asyncio.run(bot.cmd_sinyal(upd, _ctx()))
    text = upd.effective_message.replies[-1][0]
    assert "Belum ada" in text and "/scan" in text


def test_sinyal_ringkasan_memuat_beli_jual_dan_disclaimer():
    _seed_scan()
    upd = _FakeUpdate("/sinyal")
    asyncio.run(bot.cmd_sinyal(upd, _ctx()))
    text = upd.effective_message.replies[-1][0]
    assert "SINYAL PASAR IDX" in text
    assert "BBCA" in text and "ANTM" in text
    assert "BELI" in text and "JUAL" in text
    assert "bukan jaminan" in text


def test_sinyal_beli_hanya_kandidat_beli():
    _seed_scan()
    upd = _FakeUpdate("/sinyal beli")
    asyncio.run(bot.cmd_sinyal(upd, _ctx()))
    text = upd.effective_message.replies[-1][0]
    assert "PERINGKAT ATAS" in text and "BBCA" in text
    assert "ANTM" not in text


def test_sinyal_jual_dan_detail_kode():
    _seed_scan()
    jual = _FakeUpdate("/sinyal jual")
    asyncio.run(bot.cmd_sinyal(jual, _ctx()))
    text = jual.effective_message.replies[-1][0]
    assert "PERINGKAT BAWAH" in text and "ANTM" in text and "BBCA" not in text

    detail = _FakeUpdate("/sinyal bbca")
    bot._last_used.clear()
    asyncio.run(bot.cmd_sinyal(detail, _ctx()))
    text = detail.effective_message.replies[-1][0]
    assert "Bank Central Asia" in text
    assert "Entry 8,750" in text and "SL 8,600" in text
    assert "/analisis BBCA" in text


def test_sinyal_kode_tidak_dikenal_tidak_mengarang():
    _seed_scan()
    upd = _FakeUpdate("/sinyal ZZZZ")
    asyncio.run(bot.cmd_sinyal(upd, _ctx()))
    text = upd.effective_message.replies[-1][0]
    assert "tidak ada di snapshot" in text
    assert "/analisis ZZZZ" in text


def test_sinyal_csv_berisi_header_dan_semua_emiten():
    _seed_scan()
    data = bot._sinyal_csv(store.load_market_scan()).decode("utf-8")
    assert data.splitlines()[0].startswith("ticker,")
    assert "BBCA" in data and "ANTM" in data


def test_scan_saat_snapshot_hari_ini_sudah_ada_tidak_scan_ulang():
    _seed_scan()
    jq = _FakeJobQueue()
    upd = _FakeUpdate("/scan")
    asyncio.run(bot.cmd_scan(upd, _ctx(jq)))
    text = upd.effective_message.replies[-1][0]
    assert "sudah ada" in text.lower()
    assert jq.calls == []


def test_scan_memicu_job_latar_belakang():
    jq = _FakeJobQueue()
    upd = _FakeUpdate("/scan")
    try:
        asyncio.run(bot.cmd_scan(upd, _ctx(jq)))
    finally:
        bot._screener_running = False
    assert "Memindai" in upd.effective_message.replies[0][0]
    assert jq.calls and jq.calls[0][2]["chat_id"] == 100


def test_run_scan_job_mengirim_ringkasan_dan_reset_flag(monkeypatch):
    _seed_scan()
    res = {"cached": False, "counts": store.market_scan_counts(),
           "meta": store.market_scan_meta()}
    monkeypatch.setattr(bot.screener, "scan", lambda force: res)
    ctx = types.SimpleNamespace(
        bot=_FakeBot(), job=types.SimpleNamespace(data={"chat_id": 100}))
    bot._screener_running = True
    asyncio.run(bot._run_scan_job(ctx))
    assert bot._screener_running is False
    assert ctx.bot.sent and "Scan pasar selesai" in ctx.bot.sent[0][1]


def test_run_scan_job_gagal_tidak_bocorkan_traceback(monkeypatch):
    def boom(force):
        raise RuntimeError("Yahoo down")

    monkeypatch.setattr(bot.screener, "scan", boom)
    ctx = types.SimpleNamespace(
        bot=_FakeBot(), job=types.SimpleNamespace(data={"chat_id": 100}))
    bot._screener_running = True
    asyncio.run(bot._run_scan_job(ctx))
    text = ctx.bot.sent[-1][1]
    assert "Scan gagal" in text and "Yahoo down" in text
    assert bot._screener_running is False


def test_full_market_scan_job_patuhi_auto_off(monkeypatch):
    calls = []
    monkeypatch.setattr(bot.config, "FULL_SCAN_AUTO", False)
    monkeypatch.setattr(bot.screener, "scan", lambda force: calls.append(force))
    asyncio.run(bot.full_market_scan_job(_app()))
    assert calls == []


def test_full_market_scan_job_kirim_ke_chat_auto(monkeypatch):
    _seed_scan()
    store.set_auto(200, True)
    res = {"cached": False, "counts": store.market_scan_counts(),
           "meta": store.market_scan_meta()}
    monkeypatch.setattr(bot.config, "FULL_SCAN_AUTO", True)
    monkeypatch.setattr(bot.marketcal, "is_trading_day", lambda d: True)
    monkeypatch.setattr(bot.screener, "scan", lambda force: res)
    app = _app()
    asyncio.run(bot.full_market_scan_job(app))
    assert app.bot.sent and app.bot.sent[0][0] == 200
    assert "SCREENER HARIAN" in app.bot.sent[0][1]


# ------------------------------------------- rencana beli siap Stockbit
def _plan_enriched():
    return {
        "ticker": "BBCA", "name": "Bank Central Asia Tbk.", "board": "Utama",
        "sector": "Financials", "price": 8750.0, "atr": 150.0,
        "rel_pct": 92.0, "rsi": 58.0, "ret20d": 3.2,
        "value_avg20": 7_500_000_000.0, "vol_avg20": 900_000.0,
        "per": 18.0, "pbv": 4.1, "roe": 21.0, "der": 20.0,
        "div_yield": 2.5, "fund_pct": 20.0, "fund_label": "BAIK",
        "fund_notes": ["ROE bagus"], "news_pct": 5.0,
        "news_label": "NETRAL", "news_n": 2,
        "news_top": [{"title": "Berita uji", "sent": 0.2,
                      "source": "Contoh"}],
        "reason": "Tren naik (MA20 > MA50, MACD+)",
        "vetoes": [], "warnings": [], "grade": "A",
    }


def test_rencana_tanpa_snapshot_mengarahkan_scan():
    # perilaku paket pindah ke /paket; /rencana kini satu emiten
    upd = _FakeUpdate("/paket")
    asyncio.run(bot.cmd_paket(upd, _ctx()))
    text = upd.effective_message.replies[-1][0]
    assert "Belum ada snapshot" in text and "/scan" in text
    assert "/rencana BBCA" in text


def test_rencana_menyusun_lalu_memakai_cache(monkeypatch):
    _seed_scan()

    async def fake_build(rows, meta):
        return {"enriched": [_plan_enriched()], "rejected": {},
                "meta": meta, "pool": 1}

    monkeypatch.setattr(bot, "_build_plan_payload", fake_build)
    upd = _FakeUpdate("/paket")
    asyncio.run(bot.cmd_paket(upd, _ctx()))
    replies = [t for t, _ in upd.effective_message.replies]
    assert any("Menyusun rencana" in t for t in replies)
    final = replies[-1]
    assert "RENCANA BELI" in final and "BBCA" in final
    assert "LIMIT" in final and "lot" in final
    assert "Stockbit" in final and "SL" in final and "TP" in final

    key = "plan:v1:" + str(store.market_scan_meta()["scanned_at"])
    assert store.load_plan_cache(key)

    bot._last_used.clear()
    upd2 = _FakeUpdate("/paket")
    asyncio.run(bot.cmd_paket(upd2, _ctx()))
    assert not any("Menyusun rencana" in t for t, _ in upd2
                   .effective_message.replies)


def test_rencana_teks_tanpa_kandidat_jujur():
    _seed_scan()
    text = bot._rencana_text(
        {"picks": [], "rejected": {"likuiditas < Rp2 miliar/hari": 3},
         "modal": 10_000_000},
        {"rejected": {"bukan sinyal BELI": 5}}, store.market_scan_meta())
    assert "Tidak ada kandidat" in text
    assert "likuiditas" in text.lower()
    assert "/sinyal beli" in text


def test_format_rupiah_rencana_gaya_indonesia():
    assert bot._money(1080) == "Rp1.080"
    assert bot._money(1_944_000) == "Rp1.944.000"
    assert bot._money(246.5) == "Rp246,50"
