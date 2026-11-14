"""Tes offline P28: jurnal eksekusi manual Stockbit, slippage nyata, dan realisasi."""
from __future__ import annotations

import asyncio
import datetime as dt
import sqlite3
import types

import numpy as np
import pytest

import bot
from core import bandar, banding, data, fundamental, jurnal, marketcal, news, store
from tests.conftest import make_prices


# ------------------------------------------------------------ fake Telegram UI
class _User:
    def __init__(self, uid: int = 100):
        self.id = uid


class _Chat:
    def __init__(self, cid: int = 100):
        self.id = cid

    async def send_action(self, *a, **k):
        return None


class _Msg:
    def __init__(self, text: str = "", cid: int = 100):
        self.text = text
        self.chat_id = cid
        self.replies: list[tuple[str, dict]] = []
        self.edits: list[tuple[str, dict]] = []

    async def reply_text(self, text: str, **kwargs):
        self.replies.append((text, kwargs))
        return self

    async def edit_text(self, text: str, **kwargs):
        self.edits.append((text, kwargs))
        return self


class _Query:
    def __init__(self, cb_data: str, uid: int = 100, cid: int = 100):
        self.data = cb_data
        self.from_user = _User(uid)
        self.message = _Msg(cid=cid)
        self.answers: list[tuple] = []

    async def answer(self, *a, **k):
        self.answers.append((a, k))


class _Update:
    def __init__(self, text: str = "", uid: int = 100, cid: int = 100, query=None):
        self.effective_user = _User(uid)
        self.effective_chat = _Chat(cid)
        self.callback_query = query
        self.effective_message = query.message if query else _Msg(text, cid=cid)


class _Bot:
    def __init__(self):
        self.sent: list[tuple[int, str, dict]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs):
        self.sent.append((chat_id, text, kwargs))


def _ctx():
    return types.SimpleNamespace(bot=_Bot(), job_queue=None)


def _cbs(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


# ------------------------------------------------------------ unit: jurnal math
def test_calc_slippage_konvensi_beli_jual_dan_kosong():
    # Beli lebih mahal dari sinyal -> positif (biaya)
    rp, pct, cost = jurnal.calc_slippage("BELI", 9050, 9000, 5)
    assert rp == pytest.approx(50.0)
    assert pct == pytest.approx(0.5556, abs=1e-3)
    assert cost == 25_000

    # Beli lebih murah -> negatif (lebih hemat)
    rp2, pct2, cost2 = jurnal.calc_slippage("BELI", 8950, 9000, 2)
    assert rp2 == pytest.approx(-50.0)
    assert pct2 < 0 and cost2 == -10_000

    # Jual di bawah harga acuan -> positif (adverse slippage)
    rp3, pct3, cost3 = jurnal.calc_slippage("JUAL", 9400, 9500, 3)
    assert rp3 == pytest.approx(100.0)
    assert pct3 > 0 and cost3 == 30_000

    # Tanpa sinyal -> tidak mengarang nol
    assert jurnal.calc_slippage("BELI", 9000, None, 5) == (None, None, None)


def test_calc_realized_pnl_memperhitungkan_fee_bei():
    gross, net_rp, net_pct = jurnal.calc_realized_pnl(10, 9500, 9000)
    assert gross == 500_000
    # Beli = 9jt * 1,0015 = 9.013.500; Jual = 9,5jt * 0,9975 = 9.476.250
    assert net_rp == round(9_476_250 - 9_013_500)
    assert net_pct == pytest.approx((9_476_250 / 9_013_500 - 1) * 100, abs=0.01)


def test_parse_exec_input_berbagai_format():
    assert jurnal.parse_exec_input("5 9050") == (5, 9050.0)
    assert jurnal.parse_exec_input("5 lot @ 9.050") == (5, 9050.0)
    assert jurnal.parse_exec_input("9050", default_lots=4) == (4, 9050.0)
    assert jurnal.parse_exec_input("9050", default_lots=None) is None
    assert jurnal.parse_exec_input("0 9050") is None
    assert jurnal.parse_exec_input("abc xyz") is None


def test_summarize_asli_dan_paper_terpisah(tmp_store):
    store.record_execution_buy(100, "BBCA", 5, 9050, "2026-11-10",
                               signal_price=9000, source="rencana")
    store.record_execution_sell(100, "BBCA", 5, 9500, "2026-11-12")
    store.record_execution_buy(100, "TLKM", 10, 3000, "2026-11-11",
                               signal_price=3000, source="cek")
    store.record_execution_sell(100, "TLKM", 10, 2900, "2026-11-13")

    # Paper trading terpisah: 1 menang (+10%), 2 kalah (-4%, -4%)
    store.set_paper_record(100, "ASII", {"status": "closed", "entry": 5000, "exit_price": 5500})
    store.set_paper_record(100, "BBRI", {"status": "closed", "entry": 4000, "exit_price": 3840})
    store.set_paper_record(100, "BMRI", {"status": "closed", "entry": 6000, "exit_price": 5760})

    real_sum = jurnal.summarize_executions(store.real_executions(100))
    paper_sum = jurnal.summarize_paper_realized(store.get_paper(100))

    assert real_sum["n_exec"] == 4
    assert real_sum["n_with_signal"] == 2
    assert real_sum["total_slippage_rp"] == 25_000
    assert real_sum["n_closed"] == 2
    assert real_sum["wins"] == 1 and real_sum["losses"] == 1
    assert real_sum["win_rate_pct"] == 50.0
    assert real_sum["profit_factor"] is not None and real_sum["profit_factor"] > 1.0

    assert paper_sum["n_closed"] == 3
    assert paper_sum["wins"] == 1 and paper_sum["losses"] == 2
    assert paper_sum["win_rate_pct"] == pytest.approx(33.3, abs=0.1)

    report = jurnal.format_journal_report(real_sum, paper_sum, open_real_count=0)
    assert "ASLI (uang sungguhan)" in report
    assert "PAPER TRADING" in report
    assert "50,0%" in report and "33,3%" in report
    assert "tanpa login stockbit" in report.lower()


# ------------------------------------------------------------ store migration v5
def test_store_migrasi_v4_ke_v5_aman(tmp_store):
    store.add_real_position(100, "BBCA", 2, 9000, "2026-11-12", "Financials")
    conn = sqlite3.connect(store.STORE_DB)
    try:
        conn.execute("PRAGMA user_version = 4")
        conn.execute("DROP TABLE IF EXISTS real_executions")
        conn.execute("DROP TABLE IF EXISTS signal_context")
        conn.commit()
    finally:
        conn.close()

    # Reset gate so _ensure_ready runs again cleanly on existing DB file
    import core.store as storemod
    storemod._ready_for = None

    store.save_signal_context(100, "ANTM", "rencana", 1500.0, sl=1400.0, tp=1700.0,
                              suggested_lots=4, trade_date="2026-11-14")
    res = store.record_execution_buy(100, "ANTM", 4, 1520.0, "2026-11-14",
                                     signal_price=1500.0, source="rencana")
    assert res["slippage_rp"] == pytest.approx(20.0)
    assert store.get_signal_context(100, "ANTM") is None  # dibersihkan setelah tercatat
    assert store.open_execution_lots(100, "ANTM") == 4
    assert len(store.real_positions(100)) == 2  # BBCA lama tetap utuh

    conn2 = sqlite3.connect(store.STORE_DB)
    try:
        assert conn2.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
    finally:
        conn2.close()


# ------------------------------------------------------------ handler & E2E flow
def test_alur_konfirmasi_stockbit_dari_rencana_sampai_jurnal(monkeypatch, tmp_store):
    bot.ALLOWED_IDS.clear()
    bot._last_used.clear()
    bot.PENDING.clear()
    store.set_modal(100, 25_000_000)

    ind = make_prices(np.linspace(8500, 9000, 80), volume=10_000_000)
    ind["atr14"] = 200.0

    async def fake_gather(t):
        return {
            "res": {"ticker": t, "snap": {"close": 9000.0, "rsi": 58.0, "ret20d": 3.0}, "ind": ind},
            "fund": {"pct": 20.0, "label": "BAIK", "notes": []},
            "fund_raw": {"sector": "Financials", "per": 12.0, "pbv": 1.5, "roe": 18.0},
            "nsent": {"pct": 10.0, "label": "NETRAL", "n": 1, "details": []},
        }

    monkeypatch.setattr(bot, "gather_candidate", fake_gather)
    monkeypatch.setattr(fundamental, "fetch_fundamentals", lambda t: {"sector": "Financials"})

    # 1. User jalankan /rencana BBCA -> tombol "Sudah beli di Stockbit" muncul & context tersimpan
    upd = _Update("/rencana BBCA 4600000")
    asyncio.run(bot.cmd_rencana(upd, _ctx()))
    text, kw = upd.effective_message.replies[-1]
    assert "RENCANA BELI — BBCA" in text
    assert "exec:buy:BBCA" in _cbs(kw["reply_markup"])
    ctx_saved = store.get_signal_context(100, "BBCA")
    assert ctx_saved and ctx_saved["signal_price"] == 9000.0 and ctx_saved["suggested_lots"] == 5

    # 2. User klik tombol "Sudah beli di Stockbit"
    q_buy = _Query("exec:buy:BBCA")
    asyncio.run(bot.menu_callback(_Update(query=q_buy), _ctx()))
    assert bot._pending_get(100) == "exec_buy:BBCA"
    assert "Rp9.000" in q_buy.message.replies[-1][0]

    # 3. User ketik harga eksekusi nyata saja ("9050" -> otomatis 5 lot)
    upd_fill = _Update("9050")
    asyncio.run(bot.menu_text_input(upd_fill, _ctx()))
    assert bot._pending_get(100) is None
    reply_buy = upd_fill.effective_message.replies[-1][0]
    assert "Eksekusi BELI BBCA tercatat" in reply_buy
    assert "+0,56%" in reply_buy and "+Rp25.000" in reply_buy
    assert store.real_positions(100)[0]["lots"] == 5

    # 4. User menjual lewat /portofolio jual -> otomatis tutup realisasi di jurnal
    upd_sell = _Update("/portofolio jual BBCA 5 9500")
    asyncio.run(bot.cmd_portofolio(upd_sell, _ctx()))
    assert "tercatat di /jurnal" in upd_sell.effective_message.replies[-1][0]

    # 5. User buka /jurnal & /jurnal riwayat
    upd_j = _Update("/jurnal")
    asyncio.run(bot.cmd_jurnal(upd_j, _ctx()))
    j_txt, j_kw = upd_j.effective_message.replies[-1]
    assert "Win Rate <b>100,0%</b>" in j_txt
    assert "belum ada rugi" in j_txt
    assert "run:jurnal_history" in _cbs(j_kw["reply_markup"])

    upd_h = _Update("/jurnal riwayat")
    asyncio.run(bot.cmd_jurnal(upd_h, _ctx()))
    h_txt = upd_h.effective_message.replies[-1][0]
    assert "BELI 5 lot" in h_txt and "JUAL 5 lot" in h_txt and "+0,56%" in h_txt


def test_cek_dan_tanya_dan_laporan_berkala_jurnal(monkeypatch, tmp_store):
    bot.ALLOWED_IDS.clear()
    bot._last_used.clear()
    bot.PENDING.clear()

    ind = make_prices(np.linspace(950, 1000, 80), volume=10_000_000)
    ind["atr14"] = 30.0

    async def fake_fetch(t):
        return {"ticker": t, "snap": {"close": 1000.0, "rsi": 55.0, "ret20d": 2.0},
                "score": {"pct": 35.0}, "ind": ind, "stale_note": ""}

    monkeypatch.setattr(bot, "fetch_and_analyze", fake_fetch)
    monkeypatch.setattr(fundamental, "fetch_fundamentals",
                        lambda t: {"per": 12.0, "pbv": 1.4, "roe": 18.0, "sector": "Fin"})
    monkeypatch.setattr(fundamental, "score_fundamentals", lambda r: {"pct": 20.0, "label": "BAIK"})
    monkeypatch.setattr(news, "fetch_news", lambda t: [])
    monkeypatch.setattr(news, "news_sentiment", lambda items: None)
    monkeypatch.setattr(data, "fetch_weekly", lambda t: make_prices(np.linspace(900, 1000, 60)))
    monkeypatch.setattr(bot, "_cek_bandar_row", lambda t: None)
    monkeypatch.setattr(bot, "_cek_agenda", lambda t: (None, None))
    monkeypatch.setattr(banding, "sector_context", lambda *a, **k: None)
    monkeypatch.setattr(bandar, "get_index_summary", lambda: [])

    upd_cek = _Update("/cek BBCA")
    asyncio.run(bot.cmd_cek(upd_cek, _ctx()))
    assert "exec:buy:BBCA" in _cbs(upd_cek.effective_message.replies[-1][1]["reply_markup"])
    assert store.get_signal_context(100, "BBCA")["source"] == "cek"

    # Weekly job: hanya chat /auto on yang punya real_executions
    store.set_auto(100, True)
    store.set_auto(200, False)
    store.set_auto(300, True)  # tidak punya eksekusi -> tidak dikirimi spam
    store.record_execution_buy(100, "BBCA", 2, 1020.0, "2026-11-14", signal_price=1000.0)
    store.record_execution_buy(200, "TLKM", 2, 3000.0, "2026-11-14", signal_price=3000.0)

    # Mock Jumat hari bursa
    friday = dt.datetime(2026, 11, 13, 16, 45, tzinfo=bot.WIB)
    monkeypatch.setattr(marketcal, "now_wib", lambda: friday)
    monkeypatch.setattr(marketcal, "is_trading_day", lambda d: True)

    app = types.SimpleNamespace(bot=_Bot())
    asyncio.run(bot.weekly_journal_report(app))
    assert len(app.bot.sent) == 1
    assert app.bot.sent[0][0] == 100
    assert "LAPORAN BERKALA JURNAL" in app.bot.sent[0][1]
    assert "EKSEKUSI NYATA — ASLI" in app.bot.sent[0][1]


def test_jurnal_perintah_langsung_dan_exec_sell_cancel(monkeypatch, tmp_store):
    bot.ALLOWED_IDS.clear()
    bot._last_used.clear()
    bot.PENDING.clear()
    monkeypatch.setattr(fundamental, "fetch_fundamentals", lambda t: {"sector": "Basic"})

    # Catat beli & jual lewat perintah langsung
    u_b = _Update("/jurnal beli ANTM 3 1520 1500")
    asyncio.run(bot.cmd_jurnal(u_b, _ctx()))
    assert "Eksekusi BELI ANTM tercatat" in u_b.effective_message.replies[-1][0]
    assert "+1,33%" in u_b.effective_message.replies[-1][0]

    # Uji tombol exec:sell dan exec:cancel
    q_sell = _Query("exec:sell:ANTM")
    asyncio.run(bot.menu_callback(_Update(query=q_sell), _ctx()))
    assert bot._pending_get(100) == "exec_sell:ANTM"
    assert "3 lot" in q_sell.message.replies[-1][0]

    q_cancel = _Query("exec:cancel")
    asyncio.run(bot.menu_callback(_Update(query=q_cancel), _ctx()))
    assert bot._pending_get(100) is None
    assert "dibatalkan" in q_cancel.message.edits[-1][0].lower()

    # Jual interaktif lewat tombol exec:sell:ANTM lagi -> ketik harga saja (otomatis 3 lot)
    q_sell2 = _Query("exec:sell:ANTM")
    asyncio.run(bot.menu_callback(_Update(query=q_sell2), _ctx()))
    u_fill = _Update("1600")
    asyncio.run(bot.menu_text_input(u_fill, _ctx()))
    reply = u_fill.effective_message.replies[-1][0]
    assert "Eksekusi JUAL ANTM tercatat" in reply
    assert "Posisi tertutup penuh" in reply
    assert store.real_positions(100) == []

