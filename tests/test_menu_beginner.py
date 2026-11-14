"""Tes alur menu interaktif ramah pemula (progressive disclosure, belajar, modal)."""
from __future__ import annotations

import asyncio
import types

import numpy as np

import bot
from core import bandar, banding, data, fundamental, news, store
from tests.conftest import make_prices


class _User:
    def __init__(self, uid: int = 100):
        self.id = uid


class _Chat:
    def __init__(self, cid: int = 100):
        self.id = cid
        self.actions: list[str] = []

    async def send_action(self, action):
        self.actions.append(str(action))


class _Msg:
    def __init__(self, cid: int = 100, text: str = ""):
        self.chat_id = cid
        self.text = text
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
        self.answers: list[tuple[tuple, dict]] = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))


class _Update:
    def __init__(self, text: str = "", uid: int = 100, cid: int = 100, query=None):
        self.effective_user = _User(uid)
        self.effective_chat = _Chat(cid)
        self.callback_query = query
        self.effective_message = query.message if query else _Msg(cid, text)


def _ctx():
    return types.SimpleNamespace(bot=types.SimpleNamespace(), job_queue=None)


def _callbacks(markup) -> list[str]:
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def _setup(monkeypatch, tmp_store):
    bot.ALLOWED_IDS.clear()
    bot._last_used.clear()
    bot.PENDING.clear()
    monkeypatch.setattr(store, "get_watchlist", lambda cid: ["ANTM"])


def test_start_ramah_pemula_dan_tidak_membanjiri_perintah(monkeypatch, tmp_store):
    _setup(monkeypatch, tmp_store)
    upd = _Update("/start")
    asyncio.run(bot.cmd_start(upd, _ctx()))
    txt, kw = upd.effective_message.replies[-1]
    assert "SahamBuddy" in txt
    assert "Cek Layak Beli" in txt
    assert "tidak terhubung" in txt.lower()
    assert len(txt) < 900  # ringkas untuk layar ponsel pemula
    cbs = _callbacks(kw["reply_markup"])
    for expected in ("pick:cek", "pick:analisis", "sub:ide", "sub:rencana",
                     "sub:pantau", "run:portofolio", "run:belajar",
                     "run:help", "sub:pro"):
        assert expected in cbs


def test_help_menampilkan_panduan_3_langkah_dan_coba_bbca(monkeypatch, tmp_store):
    _setup(monkeypatch, tmp_store)
    upd = _Update("/help")
    asyncio.run(bot.cmd_help(upd, _ctx()))
    txt, kw = upd.effective_message.replies[-1]
    assert "3 Langkah Mulai" in txt
    assert "Coba Cek BBCA" not in txt  # ada di tombol, bukan teks panjang
    cbs = _callbacks(kw["reply_markup"])
    assert "tick:cek:BBCA" in cbs
    assert "sub:allcmd" in cbs

    q_all = _Query("sub:allcmd")
    asyncio.run(bot.menu_callback(_Update(query=q_all), _ctx()))
    all_txt, all_kw = q_all.message.edits[-1]
    assert "/cek BBCA" in all_txt and "/rencana" in all_txt
    assert "menu" in _callbacks(all_kw["reply_markup"])


def test_submenu_ide_rencana_pantau_dan_pro(monkeypatch, tmp_store):
    _setup(monkeypatch, tmp_store)
    store.set_modal(100, 15_000_000)

    for group, expected_cb in (
        ("ide", "run:saran"),
        ("rencana", "sub:modal"),
        ("pantau", "run:auto_on"),
        ("pro", "run:validasi"),
    ):
        q = _Query(f"sub:{group}")
        asyncio.run(bot.menu_callback(_Update(query=q), _ctx()))
        txt, kw = q.message.edits[-1]
        cbs = _callbacks(kw["reply_markup"])
        assert expected_cb in cbs
        assert "menu" in cbs
        if group == "rencana":
            assert "15.000.000" in txt


def test_belajar_interaktif_pilih_istilah_dan_kembali(monkeypatch, tmp_store):
    _setup(monkeypatch, tmp_store)
    q_list = _Query("run:belajar")
    asyncio.run(bot.menu_callback(_Update(query=q_list), _ctx()))
    _, kw_list = q_list.message.edits[-1]
    assert "belajar:per" in _callbacks(kw_list["reply_markup"])

    q_per = _Query("belajar:per")
    asyncio.run(bot.menu_callback(_Update(query=q_per), _ctx()))
    txt_per, kw_per = q_per.message.edits[-1]
    assert "Price-to-Earnings" in txt_per
    assert "Jebakan umum" in txt_per
    cbs_per = _callbacks(kw_per["reply_markup"])
    assert "run:belajar" in cbs_per and "menu" in cbs_per


def test_atur_modal_via_preset_dan_ketik_manual(monkeypatch, tmp_store):
    _setup(monkeypatch, tmp_store)
    q_preset = _Query("setmodal:5000000", uid=100, cid=100)
    asyncio.run(bot.menu_callback(_Update(query=q_preset), _ctx()))
    assert store.get_modal(100) == 5_000_000
    assert "5.000.000" in q_preset.message.edits[-1][0]

    q_manual = _Query("manual:modal", uid=100, cid=100)
    asyncio.run(bot.menu_callback(_Update(query=q_manual), _ctx()))
    assert bot._pending_get(100) == "modal"

    bad = _Update("ngawur", uid=100, cid=100)
    asyncio.run(bot.menu_text_input(bad, _ctx()))
    assert "tidak valid" in bad.effective_message.replies[-1][0].lower()
    assert bot._pending_get(100) == "modal"

    ok = _Update("12,5jt", uid=100, cid=100)
    asyncio.run(bot.menu_text_input(ok, _ctx()))
    assert store.get_modal(100) == 12_500_000
    assert bot._pending_get(100) is None
    assert "pick:rencana" in _callbacks(ok.effective_message.replies[-1][1]["reply_markup"])


def test_pick_cek_tampilkan_penjelasan_singkat_dan_hasil_punya_tombol_menu(monkeypatch, tmp_store):
    _setup(monkeypatch, tmp_store)
    q_pick = _Query("pick:cek")
    asyncio.run(bot.menu_callback(_Update(query=q_pick), _ctx()))
    pick_txt, pick_kw = q_pick.message.edits[-1]
    assert "6 poin keamanan" in pick_txt
    cbs = _callbacks(pick_kw["reply_markup"])
    assert "tick:cek:ANTM" in cbs and "tick:cek:BBCA" in cbs

    ind = make_prices(np.linspace(900, 1000, 80), volume=10_000_000)
    ind["atr14"] = 30.0
    async def fake_fetch(t):
        return {"ticker": t, "snap": {"close": 1000.0, "rsi": 55.0, "ret20d": 2.0},
                "score": {"pct": 35.0}, "ind": ind, "stale_note": ""}

    monkeypatch.setattr(bot, "fetch_and_analyze", fake_fetch)
    monkeypatch.setattr(fundamental, "fetch_fundamentals", lambda t: {"per": 12.0})
    monkeypatch.setattr(fundamental, "score_fundamentals", lambda raw: {"pct": 20.0})
    monkeypatch.setattr(news, "fetch_news", lambda t: [])
    monkeypatch.setattr(news, "news_sentiment", lambda items: None)
    monkeypatch.setattr(data, "fetch_weekly", lambda t: make_prices(np.linspace(900, 1000, 60)))
    monkeypatch.setattr(bot, "_cek_bandar_row", lambda t: None)
    monkeypatch.setattr(bot, "_cek_agenda", lambda t: (None, None))
    monkeypatch.setattr(banding, "sector_context", lambda *a, **k: None)

    q_run = _Query("tick:cek:BBCA")
    asyncio.run(bot.menu_callback(_Update(query=q_run), _ctx()))
    final_txt, final_kw = q_run.message.replies[-1]
    assert "CEK SEBELUM BELI — BBCA" in final_txt
    final_cbs = _callbacks(final_kw["reply_markup"])
    assert "refresh:cek:BBCA" in final_cbs and "menu" in final_cbs


def test_menu_portofolio_sektor_dan_reply_long_markup_hanya_di_akhir(monkeypatch, tmp_store):
    _setup(monkeypatch, tmp_store)
    monkeypatch.setattr(bandar, "get_index_summary", lambda: [])

    q_porto = _Query("run:portofolio")
    asyncio.run(bot.menu_callback(_Update(query=q_porto), _ctx()))
    assert "menu" in _callbacks(q_porto.message.replies[-1][1]["reply_markup"])

    q_sek = _Query("run:sektor")
    asyncio.run(bot.menu_callback(_Update(query=q_sek), _ctx()))
    assert "menu" in _callbacks(q_sek.message.replies[-1][1]["reply_markup"])

    msg = _Msg()
    long_text = ("baris uji panjang\n" * 350).strip()
    asyncio.run(bot.reply_long(msg, long_text, reply_markup=bot.home_markup()))
    assert len(msg.replies) >= 2
    assert "reply_markup" not in msg.replies[0][1]
    assert msg.replies[-1][1]["reply_markup"] is not None
