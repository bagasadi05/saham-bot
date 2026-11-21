"""Tes offline laporan kondisi pasar `/pasar` (`/market`), menu, dan job penutupan/pagi."""
from __future__ import annotations

import asyncio
import types
from datetime import datetime, timezone

import numpy as np
import pytest

import bot
from core import bandar, news_ml, pasar, store
from handlers import jobs as jobs_handler, menu as menu_handler
from tests.conftest import make_prices


@pytest.fixture(autouse=True)
def tanpa_ml_dan_cooldown(monkeypatch, tmp_store):
    monkeypatch.setattr(news_ml, "get_model", lambda: (None, None))
    bot._last_used.clear()
    bot.PENDING.clear()
    bot.ALLOWED_IDS.clear()


def _callbacks(markup) -> list[str]:
    if not markup:
        return []
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


class _FakeMsg:
    def __init__(self, text: str = ""):
        self.text = text
        self.replies: list[tuple[str, dict]] = []
        self.edits: list[tuple[str, dict]] = []

    async def reply_text(self, text, **kw):
        self.replies.append((text, kw))
        return self

    async def edit_text(self, text, **kw):
        self.edits.append((text, kw))
        return self


class _FakeChat:
    def __init__(self, cid: int = 100):
        self.id = cid
        self.actions: list = []

    async def send_action(self, action):
        self.actions.append(action)


class _FakeUpdate:
    def __init__(self, text: str = "/pasar", cid: int = 100, query=None):
        self.effective_chat = _FakeChat(cid)
        self.effective_user = types.SimpleNamespace(id=cid)
        self.effective_message = _FakeMsg(text)
        self.callback_query = query


class _FakeQuery:
    def __init__(self, data: str, uid: int = 100):
        self.data = data
        self.from_user = types.SimpleNamespace(id=uid)
        self.message = _FakeMsg("")
        self.answered = False

    async def answer(self, *_a, **_k):
        self.answered = True


class _FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str, dict]] = []

    async def send_message(self, chat_id: int, text: str, **kw):
        self.sent.append((chat_id, text, kw))


def _sample_scan_rows() -> list[dict]:
    return [
        {"ticker": "BBCA", "signal": "BELI", "ret1d": 1.8, "trade_date": "2026-11-20"},
        {"ticker": "BMRI", "signal": "BELI", "ret1d": 1.2, "trade_date": "2026-11-20"},
        {"ticker": "TLKM", "signal": "TAHAN", "ret1d": 0.0, "trade_date": "2026-11-20"},
        {"ticker": "BBRI", "signal": "BELI", "ret1d": 0.9, "trade_date": "2026-11-20"},
        {"ticker": "ASII", "signal": "JUAL", "ret1d": -1.5, "trade_date": "2026-11-20"},
    ]


def _sample_index_rows() -> list[dict]:
    return [
        {"IndexCode": "IDXFINANCE", "IndexPercent": 1.85, "Date": "2026-11-20"},
        {"IndexCode": "IDXENERGY", "IndexPercent": 1.20, "Date": "2026-11-20"},
        {"IndexCode": "IDXINFRA", "IndexPercent": 0.65, "Date": "2026-11-20"},
        {"IndexCode": "IDXHEALTH", "IndexPercent": 0.25, "Date": "2026-11-20"},
        {"IndexCode": "IDXTECHNO", "IndexPercent": -1.40, "Date": "2026-11-20"},
    ]


def _sample_news_items() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"title": "IHSG menguat 1,2 persen ditopang net buy asing", "link": "https://a", "date": now, "source": "Kontan"},
        {"title": "Saham perbankan besar melejit di sesi kedua", "link": "https://b", "date": now, "source": "Bisnis.com"},
    ]


# ----------------------------------------------------------- core/pasar.py

def test_core_pasar_laporan_lengkap_konstruktif(uptrend_df):
    stock_rows = [
        {"StockCode": "BBCA", "ForeignBuy": 100_000, "ForeignSell": 40_000, "ClosingPrice": 9500},
        {"StockCode": "BMRI", "ForeignBuy": 200_000, "ForeignSell": 100_000, "ClosingPrice": 6500},
    ]
    rep = pasar.build_market_report(
        ihsg_df=uptrend_df,
        scan_rows=_sample_scan_rows(),
        scan_meta={"trade_date": "2026-11-20"},
        index_rows=_sample_index_rows(),
        stock_rows=stock_rows,
        news_items=_sample_news_items(),
        as_of_label="20 Nov 2026 16:15 WIB",
    )
    assert rep["condition"] == pasar.CONDITION_CONSTRUCTIVE
    assert rep["missing"] == []
    assert rep["ihsg"]["ok"] is True
    assert rep["ihsg"]["regime"]["status"] == "BULL"
    assert rep["breadth"]["total"] == 5 and rep["breadth"]["beli"] == 3
    assert rep["sectors"]["ok"] is True and rep["sectors"]["n"] == 5
    assert rep["foreign"]["ok"] is True and rep["foreign"]["label"] == "NET BUY"
    assert rep["news"]["label"] == "POSITIF"

    txt = pasar.format_market_report(rep)
    assert "LAPORAN KONDISI PASAR BEI" in txt
    assert "KONSTRUKTIF" in txt
    assert "Keuangan" in txt and "Teknologi" in txt
    assert "NET BUY" in txt
    assert "POSITIF" in txt
    # Sektor ke-3 (Infrastruktur) tidak boleh muncul ganda di Terkuat dan Terlemah
    top_line = next(ln for ln in txt.splitlines() if "Terkuat:" in ln)
    bot_line = next(ln for ln in txt.splitlines() if "Terlemah:" in ln)
    assert "Infrastruktur" in top_line
    assert "Infrastruktur" not in bot_line

    brief = pasar.format_market_brief(rep)
    assert "Kondisi Pasar" in brief and "KONSTRUKTIF" in brief and "/pasar" in brief


def test_core_pasar_defensif_dan_jujur_saat_data_sebagian_hilang(downtrend_df):
    rep = pasar.build_market_report(
        ihsg_df=downtrend_df,
        scan_rows=[],
        index_rows=None,
        index_error="data BEI (idx.co.id) sedang diblokir Cloudflare",
        news_items=[],
    )
    assert rep["condition"] == pasar.CONDITION_CAUTIOUS
    assert rep["ihsg"]["regime"]["status"] == "DEFENSIVE"
    assert "sektor BEI" in rep["missing"]
    assert "breadth (/scan)" in rep["missing"]
    assert "berita pasar" in rep["missing"]

    txt = pasar.format_market_report(rep)
    assert "WASPADA" in txt
    assert "Cloudflare" in txt
    assert "tidak tersedia" in txt.lower()


def test_core_pasar_kosong_total_jujur_tidak_mengarang():
    rep = pasar.build_market_report()
    assert rep["condition"] == pasar.CONDITION_NA
    assert len(rep["missing"]) == 4
    txt = pasar.format_market_report(rep)
    assert "TIDAK_CUKUP_DATA" in txt
    assert "tidak diisi nol" in txt.lower()
    assert pasar.format_market_brief(rep) == ""


def test_core_pasar_ihsg_pendek_dan_foreign_kosong():
    short_df = make_prices(np.linspace(7000, 7200, 30))
    snap = pasar.ihsg_snapshot(short_df)
    assert snap["ok"] is True
    assert snap["ret20d"] is not None
    assert snap["regime"]["status"] == "NA"
    assert pasar.foreign_snapshot([{"StockCode": "BBCA"}])["ok"] is False


# ----------------------------------------------------------- handlers/pasar.py + menu

def test_cmd_pasar_dan_degradasi_cloudflare(monkeypatch, uptrend_df):
    monkeypatch.setattr(bot.data, "fetch_history", lambda *a, **k: uptrend_df)
    monkeypatch.setattr(
        bot.bandar, "get_index_summary",
        lambda *a, **k: (_ for _ in ()).throw(bandar.IDXTidakTersedia("403")),
    )
    monkeypatch.setattr(bot.news, "fetch_market_news", lambda limit=10: _sample_news_items())
    store.save_market_scan(
        [
            {"ticker": "BBCA", "signal": "BELI", "ret1d": 1.5, "price": 9500, "trade_date": "2026-11-20"},
            {"ticker": "TLKM", "signal": "TAHAN", "ret1d": -0.2, "price": 3200, "trade_date": "2026-11-20"},
        ],
        {"trade_date": "2026-11-20"},
    )

    upd = _FakeUpdate("/pasar", cid=201)
    asyncio.run(bot.cmd_pasar(upd, types.SimpleNamespace()))
    assert upd.effective_message.replies
    txt, kw = upd.effective_message.replies[-1]
    assert "LAPORAN KONDISI PASAR BEI" in txt
    assert "Cloudflare" in txt
    assert "Emiten terpantau" in txt
    cbs = _callbacks(kw.get("reply_markup"))
    assert {"run:sektor", "run:sinyal", "run:pasar", "menu"} <= set(cbs)


def test_menu_callback_run_pasar_dan_tombol_terpasang(monkeypatch, uptrend_df):
    # Pemula menu tetap <= 8 tombol (aturan UX pemula)
    assert len(_callbacks(menu_handler.main_menu_markup("pemula"))) <= 8
    # Expert, sub:ide, sub:more memuat run:pasar; allcmd menyebut /pasar & /market
    assert "run:pasar" in _callbacks(menu_handler.main_menu_markup("expert"))
    assert "run:pasar" in _callbacks(menu_handler._submenu_content("ide", 10_000_000)[1])
    assert "run:pasar" in _callbacks(menu_handler._submenu_content("more", 10_000_000)[1])
    allcmd_txt = menu_handler._submenu_content("allcmd", 10_000_000)[0]
    assert "/pasar" in allcmd_txt and "/market" in allcmd_txt

    called: list[bool] = []

    async def fake_do_pasar(update, args=None, *, from_menu=False):
        called.append(from_menu)

    monkeypatch.setattr(bot, "_do_pasar", fake_do_pasar)
    q = _FakeQuery("run:pasar", uid=100)
    upd = _FakeUpdate(query=q, cid=100)
    ctx = types.SimpleNamespace(job_queue=None)
    asyncio.run(bot.menu_callback(upd, ctx))
    assert called == [True]
    assert q.message.edits


# ----------------------------------------------------------- jobs: closing 16:15 & morning brief

def test_closing_market_report_job_kirim_ke_auto_on_dan_guard(monkeypatch, uptrend_df):
    assert hasattr(bot.closing_market_report_job, "__wrapped__")

    monkeypatch.setattr(bot.marketcal, "is_trading_day", lambda _d: True)
    monkeypatch.setattr(bot.data, "fetch_history", lambda *a, **k: uptrend_df)
    monkeypatch.setattr(bot.bandar, "get_index_summary", lambda *a, **k: _sample_index_rows())
    monkeypatch.setattr(bot.news, "fetch_market_news", lambda limit=10: _sample_news_items())

    store.set_auto(101, True)
    store.set_auto(102, False)
    app = types.SimpleNamespace(bot=_FakeBot())

    sent = asyncio.run(jobs_handler.closing_market_report(app, bot))
    assert sent == 1
    assert len(app.bot.sent) == 1
    cid, txt, kw = app.bot.sent[0]
    assert cid == 101
    assert "LAPORAN PENUTUPAN PASAR" in txt
    assert "run:pasar" in _callbacks(kw.get("reply_markup"))

    # Hari libur bursa -> tidak mengirim apa-apa
    monkeypatch.setattr(bot.marketcal, "is_trading_day", lambda _d: False)
    app2 = types.SimpleNamespace(bot=_FakeBot())
    assert asyncio.run(jobs_handler.closing_market_report(app2, bot)) == 0
    assert app2.bot.sent == []


def test_morning_scan_menyisipkan_ringkasan_kondisi_pasar(monkeypatch, uptrend_df):
    monkeypatch.setattr(bot.marketcal, "is_trading_day", lambda _d: True)
    monkeypatch.setattr(bot.data, "fetch_history", lambda *a, **k: uptrend_df)
    monkeypatch.setattr(bot.news, "fetch_market_news", lambda limit=10: _sample_news_items())

    async def fake_saran_text(_cid):
        return "💡 Kandidat harian: BBCA"

    async def fake_div(_app, _d):
        return None

    async def fake_sleep(_sec):
        return None

    monkeypatch.setattr(bot, "saran_scan_text", fake_saran_text)
    monkeypatch.setattr(bot, "check_dividend_reminders", fake_div)
    monkeypatch.setattr(jobs_handler.asyncio, "sleep", fake_sleep)

    store.set_auto(301, True)
    app = types.SimpleNamespace(bot=_FakeBot())
    asyncio.run(jobs_handler.morning_scan(app, bot))

    assert len(app.bot.sent) == 1
    cid, txt, _ = app.bot.sent[0]
    assert cid == 301
    assert "AUTOSCAN" in txt
    assert "Kondisi Pasar" in txt
    assert "Kandidat harian: BBCA" in txt


def test_gather_market_data_semua_sumber_gagal_dan_morning_brief_fail_soft(monkeypatch):
    from handlers import pasar as pasar_handler

    def _boom(*_a, **_k):
        raise RuntimeError("jaringan putus")

    monkeypatch.setattr(bot.data, "fetch_history", _boom)
    monkeypatch.setattr(bot.store, "load_market_scan", _boom)
    monkeypatch.setattr(bot.bandar, "get_index_summary", _boom)
    monkeypatch.setattr(bot.news, "fetch_market_news", _boom)

    rep = asyncio.run(pasar_handler.gather_market_data(bot, include_idx=True, include_news=True))
    assert rep["condition"] == pasar.CONDITION_NA
    assert len(rep["missing"]) == 4

    # Jika gather_market_brief gagal tak terduga, morning_scan tetap mengirim /saran
    monkeypatch.setattr(bot.marketcal, "is_trading_day", lambda _d: True)
    monkeypatch.setattr(
        bot.pasar_handler, "gather_market_brief",
        lambda _deps: (_ for _ in ()).throw(RuntimeError("brief error")),
    )

    async def fake_saran_text(_cid):
        return "💡 Kandidat harian: BMRI"

    async def fake_div(_app, _d):
        return None

    async def fake_sleep(_sec):
        return None

    monkeypatch.setattr(bot, "saran_scan_text", fake_saran_text)
    monkeypatch.setattr(bot, "check_dividend_reminders", fake_div)
    monkeypatch.setattr(jobs_handler.asyncio, "sleep", fake_sleep)

    store.set_auto(302, True)
    app = types.SimpleNamespace(bot=_FakeBot())
    asyncio.run(jobs_handler.morning_scan(app, bot))
    assert len(app.bot.sent) == 1
    assert "Kandidat harian: BMRI" in app.bot.sent[0][1]
