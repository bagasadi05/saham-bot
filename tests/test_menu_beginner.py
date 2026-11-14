"""Tes alur menu interaktif ramah pemula (progressive disclosure, belajar, modal)."""
from __future__ import annotations

import asyncio
import types

import numpy as np

import bot
from core import alerts, bandar, banding, data, fundamental, kualitas, learn, marketcal, news, store, textutil
from handlers import menu as menu_mod, sinyal as sinyal_mod
from telegram.error import BadRequest
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
    assert "Saham Cocok Dibeli" in txt
    assert "Cek Nilai Saham" in txt
    assert "tidak terhubung" in txt.lower()
    assert len(txt) < 900  # ringkas untuk layar ponsel pemula
    cbs = _callbacks(kw["reply_markup"])
    assert len(cbs) <= 8  # layar utama disederhanakan untuk pemula
    for expected in ("guide:start", "guide:beli", "guide:jual", "guide:cek",
                     "run:portofolio", "run:belajar", "sub:more"):
        assert expected in cbs

    # Menu Lengkap (sub:more) tetap menyediakan semua alat analisis & submenu lama
    q_more = _Query("sub:more")
    asyncio.run(bot.menu_callback(_Update(query=q_more), _ctx()))
    more_cbs = _callbacks(q_more.message.edits[-1][1]["reply_markup"])
    for expected in ("pick:cek", "pick:fundamental", "pick:analisis",
                     "sub:rencana", "sub:ide", "sub:pantau",
                     "run:help", "sub:pro", "menu"):
        assert expected in more_cbs


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


class _FakeJobQueue:
    def __init__(self):
        self.jobs = []

    def run_once(self, fn, when=0, data=None, name=None):
        self.jobs.append((fn, when, data, name))
        return name


class _MediaMsg(_Msg):
    """Pesan foto/media Telegram: edit_text selalu gagal karena tidak punya text."""
    def __init__(self, cid: int = 100, *, photo=True, err_msg: str = "There is no text in the message to edit"):
        super().__init__(cid=cid, text="")
        self.photo = [object()] if photo else []
        self._err_msg = err_msg

    async def edit_text(self, text: str, **kwargs):
        raise BadRequest(self._err_msg)


def test_sg_scan_berfungsi_saat_kosong_dan_paksa_ulang_saat_sudah_ada(monkeypatch, tmp_store):
    _setup(monkeypatch, tmp_store)
    bot._screener_running = False
    jq = _FakeJobQueue()
    try:
        # 1) Belum ada hasil scan -> klik "🔄 Scan pasar sekarang" (sg:scan) wajib memicu scan!
        q_empty = _Query("sg:scan")
        asyncio.run(bot.menu_callback(_Update(query=q_empty), types.SimpleNamespace(bot=types.SimpleNamespace(), job_queue=jq)))
        assert len(jq.jobs) == 1
        assert bot._screener_running is True
        reply_txt, reply_kw = q_empty.message.replies[-1]
        assert "Memindai" in reply_txt
        assert "menu" in _callbacks(reply_kw["reply_markup"])

        # 2) Sudah ada snapshot hari ini -> klik "🔄 Scan ulang" (sg:scan) tetap scan ulang (paksa)
        bot._screener_running = False
        bot._last_used.clear()
        today = marketcal.now_wib().date().isoformat()
        store.save_market_scan(
            [{"ticker": "BBCA", "name": "PT BBCA", "board": "Utama", "sector": "Keuangan",
              "trade_date": today, "price": 9000.0, "rel_pct": 95.0, "rsi": 55.0,
              "ret20d": 3.0, "signal": "BELI", "reason": "", "entry": 9000.0,
              "sl": 8700.0, "tp": 9600.0, "ret1d": 0.5, "ret5d": 1.0,
              "sma20": 8900.0, "sma50": 8800.0, "sma200": 8500.0}],
            {"ok": 1, "trade_date": today, "scanned_at": f"{today}T08:00:00"},
        )
        jq2 = _FakeJobQueue()
        q_rescan = _Query("sg:scan")
        asyncio.run(bot.menu_callback(_Update(query=q_rescan), types.SimpleNamespace(bot=types.SimpleNamespace(), job_queue=jq2)))
        assert len(jq2.jobs) == 1
        assert "Memindai" in q_rescan.message.replies[-1][0]

        # 3) Tanpa job_queue aktif -> pesan ramah, flag tidak tersangkut
        bot._screener_running = False
        bot._last_used.clear()
        q_nojq = _Query("sg:scan")
        asyncio.run(bot.menu_callback(_Update(query=q_nojq), _ctx()))
        assert bot._screener_running is False
        assert "tidak aktif" in q_nojq.message.replies[-1][0].lower()
    finally:
        bot._screener_running = False


def test_safe_edit_fallback_ke_reply_pada_pesan_foto_dan_uneditable(monkeypatch, tmp_store):
    _setup(monkeypatch, tmp_store)
    q_photo = _Query("menu")
    q_photo.message = _MediaMsg(cid=100, photo=True)
    asyncio.run(bot.menu_callback(_Update(query=q_photo), _ctx()))
    assert q_photo.message.replies
    assert "SahamBuddy" in q_photo.message.replies[-1][0]
    assert "guide:start" in _callbacks(q_photo.message.replies[-1][1]["reply_markup"])

    # Pesan lama (>48 jam) -> BadRequest("Message can't be edited") jatuh aman ke reply_text
    q_old = _Query("menu")
    q_old.message = _MediaMsg(cid=100, photo=False, err_msg="Message can't be edited")
    asyncio.run(bot.menu_callback(_Update(query=q_old), _ctx()))
    assert q_old.message.replies
    assert "SahamBuddy" in q_old.message.replies[-1][0]

    # Double-tap ("Message is not modified") tetap diabaikan tanpa error atau reply ganda
    q_same = _Query("menu")
    q_same.message = _MediaMsg(cid=100, photo=False, err_msg="Message is not modified")
    asyncio.run(bot.menu_callback(_Update(query=q_same), _ctx()))
    assert not q_same.message.replies


def test_manual_alert_rencana_dan_clear_pending_saat_klik_run(monkeypatch, tmp_store):
    _setup(monkeypatch, tmp_store)

    # 1) Pasang alarm pakai target harga <9000 & 9500 via menu manual
    asyncio.run(bot.menu_callback(_Update(query=_Query("manual:alert_add")), _ctx()))
    assert bot._pending_get(100) == "alert_add"

    u_lt = _Update("BBCA <9000", uid=100, cid=100)
    asyncio.run(bot.menu_text_input(u_lt, _ctx()))
    assert bot._pending_get(100) is None
    assert set(store.get_alerts(100)["BBCA"]) == {"target<=9000"}

    asyncio.run(bot.menu_callback(_Update(query=_Query("manual:alert_add")), _ctx()))
    u_ge = _Update("BBRI 9500", uid=100, cid=100)
    asyncio.run(bot.menu_text_input(u_ge, _ctx()))
    assert set(store.get_alerts(100)["BBRI"]) == {"target>=9500"}

    asyncio.run(bot.menu_callback(_Update(query=_Query("manual:alert_add")), _ctx()))
    u_def = _Update("TLKM", uid=100, cid=100)
    asyncio.run(bot.menu_text_input(u_def, _ctx()))
    assert set(store.get_alerts(100)["TLKM"]) == set(alerts.ALL_KINDS)

    # 2) Input manual rencana mendukung kode + budget opsional
    seen = []

    async def fake_rencana(upd, args, *, from_menu=False):
        seen.append((list(args), from_menu))

    monkeypatch.setattr(bot, "_do_rencana", fake_rencana)
    asyncio.run(bot.menu_callback(_Update(query=_Query("manual:rencana")), _ctx()))
    asyncio.run(bot.menu_text_input(_Update("BBCA 5jt", uid=100, cid=100), _ctx()))
    assert seen == [(["BBCA", "5JT"], True)]

    # 3) Klik tombol run:* membatalkan PENDING input lama
    bot._pending_set(100, "cek")
    asyncio.run(bot.menu_callback(_Update(query=_Query("run:belajar")), _ctx()))
    assert bot._pending_get(100) is None


def test_semua_tombol_menu_submenu_dan_belajar_valid(monkeypatch, tmp_store):
    _setup(monkeypatch, tmp_store)
    monkeypatch.setattr(bandar, "get_index_summary", lambda: [])
    monkeypatch.setattr(bandar, "get_stock_summary", lambda *a, **k: [])

    async def fake_saran(upd, sub="", list_only=False, from_menu=False):
        await upd.effective_message.reply_text("ok saran", reply_markup=bot.home_markup())

    async def fake_scan(upd, args, jq, *, from_menu=False):
        await upd.effective_message.reply_text("ok scan", reply_markup=bot.home_markup())

    monkeypatch.setattr(bot, "_do_saran", fake_saran)
    monkeypatch.setattr(bot, "_do_scan", fake_scan)
    monkeypatch.setattr(bot, "load_validation", lambda: None)

    markups = [
        bot.main_menu_markup(),
        bot.main_menu_markup("expert"),
        bot.mode_menu_markup("pemula"),
        bot.mode_menu_markup("expert"),
        bot.help_markup(),
        bot.watch_menu_markup(),
        bot.alert_menu_markup(),
        bot.modal_menu_markup(),
        bot.belajar_markup(),
        bot.belajar_detail_markup(),
        bot.result_markup("cek", "BBCA"),
        bot.result_markup("cek", "BBCA", allow_buy=False),
        bot.result_markup("fundamental", "BBCA"),
        bot.result_markup("analisis", "BBCA"),
        bot.result_markup("rencana", "BBCA"),
        bot.result_markup("rencana", "BBCA", allow_buy=False),
        bot.result_markup("skor", "BBCA"),
        bot.result_markup("skor", "BBCA", allow_buy=False),
        bot.result_markup("berita", "BBCA"),
        bot.result_markup("backtest", "BBCA"),
        bot.result_markup("jual", "BBCA"),
        bot.result_markup("tanya_umum", "BBCA", allow_buy=False),
        menu_mod._onboard_s1_view({})[1],
        menu_mod._onboard_s2_view({"goal": "beli"}, 500_000, 0.25)[1],
        menu_mod._onboard_s3_view({
            "state": {"goal": "beli"}, "modal": 500_000, "max_pos": 0.25,
            "rows": [], "meta": {}, "positions": [], "wl": ["ANTM"],
        })[1],
        menu_mod._onboard_s4_view({"goal": "beli"}, 500_000, "BBCA")[1],
        menu_mod._onboard_s5_view({"goal": "beli"}, 500_000, "BBCA")[1],
        menu_mod._onboard_s5_view({"goal": "jual"}, 500_000, "BBCA")[1],
        menu_mod._onboard_done_view({"ticker": "BBCA"})[1],
        sinyal_mod.overview_markup(),
        sinyal_mod.section_markup("BELI"),
        sinyal_mod.section_markup("JUAL"),
        sinyal_mod.bagus_markup(),
        sinyal_mod.empty_markup(),
        sinyal_mod.detail_markup("BBCA", "BELI"),
        sinyal_mod.detail_markup("BBCA", "JUAL"),
    ]
    for g in ("ide", "rencana", "modal", "pantau", "pro", "allcmd", "more"):
        sub = menu_mod._submenu_content(g, 10_000_000)
        assert sub is not None
        markups.append(sub[1])
    for step in ("start", "beli", "jual", "cek", "pemula"):
        g_view = asyncio.run(menu_mod._guide_content(step, 100))
        assert g_view is not None
        markups.append(g_view[1])
    for act in menu_mod.NEED_TICKER:
        markups.append(bot.ticker_pick_markup(act, ["ANTM"]))

    all_cbs = {cb for kb in markups for cb in _callbacks(kb)}
    for cb in sorted(all_cbs):
        if cb.startswith(("tick:", "refresh:", "sg:", "exec:")):
            continue
        if cb.startswith("manual:"):
            act = cb.split(":", 1)[1]
            assert act in menu_mod._MANUAL_PROMPT
        if cb.startswith("belajar:"):
            topic = cb.split(":", 1)[1]
            assert learn.learn(topic) is not None
        bot._last_used.clear()
        q = _Query(cb, uid=100, cid=100)
        asyncio.run(bot.menu_callback(_Update(query=q), _ctx()))
        assert q.message.edits or q.message.replies, f"Tombol {cb} tidak merespon"
        if cb.startswith("manual:"):
            assert bot._pending_get(100) == cb.split(":", 1)[1]
        else:
            assert bot._pending_get(100) is None


def test_semua_tick_refresh_dan_unknown_callback_valid(monkeypatch, tmp_store):
    _setup(monkeypatch, tmp_store)
    dispatched = []

    async def fake_analisis(upd, tks):
        dispatched.append(("analisis", tks))

    async def fake_cek(upd, args, *, from_menu=False):
        dispatched.append(("cek", args))

    async def fake_rencana(upd, args, *, from_menu=False):
        dispatched.append(("rencana", args))

    async def fake_skor(upd, tks, from_menu=False):
        dispatched.append(("skor", tks))

    async def fake_berita(upd, t):
        dispatched.append(("berita", [t]))

    async def fake_fund(upd, t):
        dispatched.append(("fundamental", [t]))

    async def fake_bt(upd, t, from_menu=False):
        dispatched.append(("backtest", [t]))

    async def fake_tanya(upd, t, focus):
        dispatched.append((f"tanya_{focus}", [t]))

    async def fake_sinyal(upd, args, *, from_menu=False):
        dispatched.append(("sinyal_detail", args))

    monkeypatch.setattr(bot, "_do_analisis", fake_analisis)
    monkeypatch.setattr(bot, "_do_cek", fake_cek)
    monkeypatch.setattr(bot, "_do_rencana", fake_rencana)
    monkeypatch.setattr(bot, "_do_skor", fake_skor)
    monkeypatch.setattr(bot, "_do_berita", fake_berita)
    monkeypatch.setattr(bot, "_do_fundamental", fake_fund)
    monkeypatch.setattr(bot, "_do_backtest", fake_bt)
    monkeypatch.setattr(bot, "_do_tanya", fake_tanya)
    monkeypatch.setattr(bot, "_do_sinyal", fake_sinyal)

    expected = {
        "cek": ("cek", ["BBCA"]),
        "rencana": ("rencana", ["BBCA"]),
        "analisis": ("analisis", ["BBCA"]),
        "skor": ("skor", ["BBCA"]),
        "berita": ("berita", ["BBCA"]),
        "fundamental": ("fundamental", ["BBCA"]),
        "backtest": ("backtest", ["BBCA"]),
        "beli": ("tanya_beli", ["BBCA"]),
        "tanya_beli": ("tanya_beli", ["BBCA"]),
        "jual": ("tanya_jual", ["BBCA"]),
        "tanya_jual": ("tanya_jual", ["BBCA"]),
        "tanya_umum": ("tanya_umum", ["BBCA"]),
        "sinyal_detail": ("sinyal_detail", ["BBCA"]),
    }
    for action, want in expected.items():
        dispatched.clear()
        q_tick = _Query(f"tick:{action}:BBCA")
        asyncio.run(bot.menu_callback(_Update(query=q_tick), _ctx()))
        assert dispatched == [want], f"tick:{action} salah arah"

        dispatched.clear()
        q_ref = _Query(f"refresh:{action}:BBCA")
        asyncio.run(bot.menu_callback(_Update(query=q_ref), _ctx()))
        assert dispatched == [want], f"refresh:{action} salah arah"

    q_unk = _Query("tidak_ada:tombol")
    asyncio.run(bot.menu_callback(_Update(query=q_unk), _ctx()))
    assert "tidak dikenal" in q_unk.message.edits[-1][0].lower()
    assert "menu" in _callbacks(q_unk.message.edits[-1][1]["reply_markup"])


def test_paket_regime_fallback_dan_exec_sell_clear_pending(monkeypatch, tmp_store):
    _setup(monkeypatch, tmp_store)
    today = marketcal.now_wib().date().isoformat()
    store.save_market_scan(
        [{"ticker": "BBCA", "name": "PT BBCA", "board": "Utama", "sector": "Keuangan",
          "trade_date": today, "price": 9000.0, "rel_pct": 95.0, "rsi": 55.0,
          "ret20d": 3.0, "signal": "BELI", "reason": "", "entry": 9000.0,
          "sl": 8700.0, "tp": 9600.0, "ret1d": 0.5, "ret5d": 1.0,
          "sma20": 8900.0, "sma50": 8800.0, "sma200": 8500.0}],
        {"ok": 1, "trade_date": today, "scanned_at": f"{today}T08:00:00"},
    )
    key = f"plan:v1:{today}T08:00:00"
    store.save_plan_cache(key, {"enriched": [], "rejected": {}}, today)

    def boom():
        raise RuntimeError("IHSG down")

    monkeypatch.setattr(bot.regime, "fetch_ihsg_regime", boom)
    q_pkt = _Query("run:paket")
    asyncio.run(bot.menu_callback(_Update(query=q_pkt), _ctx()))
    assert "menu" in _callbacks(q_pkt.message.replies[-1][1]["reply_markup"])

    # exec:sell utk saham yg belum dimiliki tetap membersihkan PENDING lama
    bot._pending_set(100, "cek")
    q_sell = _Query("exec:sell:BBCA")
    asyncio.run(bot.menu_callback(_Update(query=q_sell), _ctx()))
    assert bot._pending_get(100) is None
    assert "belum punya posisi" in q_sell.message.replies[-1][0].lower()


def test_guide_interaktif_pemula_kosong_dan_terisi(monkeypatch, tmp_store):
    _setup(monkeypatch, tmp_store)
    bot._pending_set(100, "cek")

    q_start = _Query("guide:start")
    asyncio.run(bot.menu_callback(_Update(query=q_start), _ctx()))
    assert bot._pending_get(100) is None
    cbs_start = _callbacks(q_start.message.edits[-1][1]["reply_markup"])
    assert {"guide:beli", "guide:cek", "guide:jual", "guide:pemula", "menu"} <= set(cbs_start)

    # guide:beli tanpa snapshot menawarkan opsi cepat tanpa harus tunggu /scan
    q_beli0 = _Query("guide:beli")
    asyncio.run(bot.menu_callback(_Update(query=q_beli0), _ctx()))
    txt_b0, kw_b0 = q_beli0.message.edits[-1]
    assert "belum ada hasil scan" in txt_b0.lower()
    cbs_b0 = _callbacks(kw_b0["reply_markup"])
    assert {"run:saran", "sg:scan", "guide:cek", "guide:start"} <= set(cbs_b0)

    # guide:cek dan guide:pemula
    q_cek = _Query("guide:cek")
    asyncio.run(bot.menu_callback(_Update(query=q_cek), _ctx()))
    cbs_cek = _callbacks(q_cek.message.edits[-1][1]["reply_markup"])
    assert {"tick:cek:BBCA", "guide:start", "menu"} <= set(cbs_cek)

    q_pemula = _Query("guide:pemula")
    asyncio.run(bot.menu_callback(_Update(query=q_pemula), _ctx()))
    cbs_pemula = _callbacks(q_pemula.message.edits[-1][1]["reply_markup"])
    assert {"guide:beli", "tick:cek:BBCA", "run:belajar", "guide:start"} <= set(cbs_pemula)

    # guide:jual saat portofolio kosong & tanpa sinyal JUAL tidak menampilkan sg:sell mati
    q_jual0 = _Query("guide:jual")
    asyncio.run(bot.menu_callback(_Update(query=q_jual0), _ctx()))
    txt_j0, kw_j0 = q_jual0.message.edits[-1]
    cbs_j0 = _callbacks(kw_j0["reply_markup"])
    assert "belum ada posisi" in txt_j0.lower()
    assert "sg:sell" not in cbs_j0
    assert {"manual:jual", "run:portofolio", "guide:start"} <= set(cbs_j0)

    # Seed snapshot pasar + posisi terbuka nyata
    today = marketcal.now_wib().date().isoformat()
    base = {"board": "Utama", "sector": "Keuangan", "trade_date": today,
            "price": 5000.0, "rsi": 55.0, "ret20d": 3.0, "reason": "",
            "entry": 5000.0, "sl": 4800.0, "tp": 5400.0, "ret1d": 0.5,
            "ret5d": 1.0, "sma20": 4950.0, "sma50": 4900.0, "sma200": 4700.0}
    store.save_market_scan(
        [
            {**base, "ticker": "BBRI", "name": "BBRI", "rel_pct": 70.0, "signal": "BELI"},
            {**base, "ticker": "BBCA", "name": "BBCA", "rel_pct": 95.0, "signal": "BELI"},
            {**base, "ticker": "BMRI", "name": "BMRI", "rel_pct": 91.0, "signal": "BELI"},
            {**base, "ticker": "TLKM", "name": "TLKM", "rel_pct": 88.0, "signal": "BELI"},
            {**base, "ticker": "ASII", "name": "ASII", "rel_pct": 25.0, "signal": "JUAL"},
        ],
        {"ok": 5, "trade_date": today, "scanned_at": f"{today}T09:00:00"},
    )
    store.add_real_position(100, "ANTM", 10, 1500.0, today)

    bot._last_used.clear()
    q_beli1 = _Query("guide:beli")
    asyncio.run(bot.menu_callback(_Update(query=q_beli1), _ctx()))
    txt_b1, kw_b1 = q_beli1.message.edits[-1]
    cbs_b1 = _callbacks(kw_b1["reply_markup"])
    assert "bukan jaminan untung" in txt_b1.lower()
    assert {"tick:cek:BBCA", "tick:cek:BMRI", "tick:cek:TLKM"} <= set(cbs_b1)
    assert "tick:cek:BBRI" not in cbs_b1
    assert {"run:sinyal_bagus", "run:saran", "run:paket"} <= set(cbs_b1)

    q_jual1 = _Query("guide:jual")
    asyncio.run(bot.menu_callback(_Update(query=q_jual1), _ctx()))
    txt_j1, kw_j1 = q_jual1.message.edits[-1]
    cbs_j1 = _callbacks(kw_j1["reply_markup"])
    assert "ANTM" in txt_j1
    assert "bukan instruksi short" in txt_j1.lower()
    assert "tick:jual:ANTM" in cbs_j1
    assert "sg:sell" in cbs_j1

    # Step tidak dikenal jatuh ke menu utama dengan rapi
    q_bad = _Query("guide:tidak_ada")
    asyncio.run(bot.menu_callback(_Update(query=q_bad), _ctx()))
    assert "guide:start" in _callbacks(q_bad.message.edits[-1][1]["reply_markup"])


def test_result_markup_langkah_lanjutan_jujur():
    ok_cek = _callbacks(bot.result_markup("cek", "BBCA", allow_buy=True))
    assert {"tick:fundamental:BBCA", "tick:rencana:BBCA",
            "exec:buy:BBCA", "tick:jual:BBCA"} <= set(ok_cek)

    hindari_cek = _callbacks(bot.result_markup("cek", "BBCA", allow_buy=False))
    assert "exec:buy:BBCA" not in hindari_cek
    assert "tick:rencana:BBCA" not in hindari_cek
    assert {"tick:fundamental:BBCA", "tick:analisis:BBCA", "tick:jual:BBCA"} <= set(hindari_cek)

    hindari_fund = _callbacks(bot.result_markup("fundamental", "BBCA", allow_buy=False))
    assert "exec:buy:BBCA" not in hindari_fund
    assert "tick:rencana:BBCA" not in hindari_fund
    assert {"tick:cek:BBCA", "tick:analisis:BBCA"} <= set(hindari_fund)

    for act in ("jual", "tanya_jual"):
        cbs = _callbacks(bot.result_markup(act, "BBCA"))
        assert not any(c.startswith("exec:buy:") for c in cbs)
        assert not any(c.startswith("tick:rencana:") for c in cbs)
        assert {"exec:sell:BBCA", "run:portofolio",
                "tick:analisis:BBCA", "tick:fundamental:BBCA"} <= set(cbs)

    # Cabang skor, berita, backtest, rencana & larangan exec:buy/tick:rencana saat allow_buy=False
    for act in ("skor", "berita", "backtest", "rencana"):
        cbs_ok = _callbacks(bot.result_markup(act, "BBCA", allow_buy=True))
        assert "exec:buy:BBCA" in cbs_ok
        if act != "rencana":
            assert "tick:rencana:BBCA" in cbs_ok
        else:
            assert {"sub:modal", "manual:rencana", "tick:cek:BBCA"} <= set(cbs_ok)

        cbs_no = _callbacks(bot.result_markup(act, "BBCA", allow_buy=False))
        assert not any(c.startswith("exec:buy:") for c in cbs_no)
        assert not any(c.startswith("tick:rencana:") for c in cbs_no)


def test_mode_pemula_vs_expert_dan_perintah_mode_start(monkeypatch, tmp_store):
    _setup(monkeypatch, tmp_store)

    # Default mode: pemula (<= 8 tombol, slot #8 mode:menu, teks < 900)
    pemula_kb = bot.main_menu_markup("pemula")
    pemula_cbs = _callbacks(pemula_kb)
    assert len(pemula_cbs) <= 8
    assert pemula_cbs[-1] == "mode:menu"
    assert len(bot._menu_text("pemula")) < 900

    # Expert mode: grid akses cepat alat pro + mode:menu
    expert_kb = bot.main_menu_markup("expert")
    expert_cbs = _callbacks(expert_kb)
    assert {
        "pick:cek", "pick:analisis", "sub:rencana", "run:paket",
        "run:sinyal", "run:sinyal_bagus", "run:saran", "run:sektor",
        "run:portofolio", "run:jurnal", "sub:pantau", "sub:pro",
        "sub:allcmd", "mode:menu",
    } <= set(expert_cbs)
    assert "Mode Expert" in bot._menu_text("expert")

    # Callback mode:menu -> mode:set:expert -> menu mempertahankan mode expert
    q_mode = _Query("mode:menu")
    asyncio.run(bot.menu_callback(_Update(query=q_mode), _ctx()))
    assert {"mode:set:pemula", "mode:set:expert", "onboard:s1"} <= set(
        _callbacks(q_mode.message.edits[-1][1]["reply_markup"])
    )

    q_exp = _Query("mode:set:expert")
    asyncio.run(bot.menu_callback(_Update(query=q_exp), _ctx()))
    assert store.get_user_mode(100) == "expert"
    assert "run:jurnal" in _callbacks(q_exp.message.edits[-1][1]["reply_markup"])

    q_menu = _Query("menu")
    asyncio.run(bot.menu_callback(_Update(query=q_menu), _ctx()))
    assert "run:jurnal" in _callbacks(q_menu.message.edits[-1][1]["reply_markup"])

    # Switch kembali ke pemula -> tautkan onboard:s1 secara menonjol
    q_pem = _Query("mode:set:pemula")
    asyncio.run(bot.menu_callback(_Update(query=q_pem), _ctx()))
    assert store.get_user_mode(100) == "pemula"
    assert "onboard:s1" in _callbacks(q_pem.message.edits[-1][1]["reply_markup"])

    # /mode & /start menerima argumen pemula/expert + /mode tanpa argumen
    ctx_exp = types.SimpleNamespace(bot=types.SimpleNamespace(), job_queue=None, args=["expert"])
    upd_m = _Update("/mode expert")
    asyncio.run(bot.cmd_mode(upd_m, ctx_exp))
    assert store.get_user_mode(100) == "expert"
    assert "run:jurnal" in _callbacks(upd_m.effective_message.replies[-1][1]["reply_markup"])

    upd_s0 = _Update("/start")
    asyncio.run(bot.cmd_start(upd_s0, _ctx()))
    assert "Mode Expert" in upd_s0.effective_message.replies[-1][0]

    ctx_pem = types.SimpleNamespace(bot=types.SimpleNamespace(), job_queue=None, args=["pemula"])
    upd_sp = _Update("/start pemula")
    asyncio.run(bot.cmd_start(upd_sp, ctx_pem))
    assert store.get_user_mode(100) == "pemula"
    assert len(_callbacks(upd_sp.effective_message.replies[-1][1]["reply_markup"])) <= 8

    # /mode ngawur -> pesan error jujur
    ctx_bad = types.SimpleNamespace(bot=types.SimpleNamespace(), job_queue=None, args=["ngawur"])
    upd_mb = _Update("/mode ngawur")
    asyncio.run(bot.cmd_mode(upd_mb, ctx_bad))
    assert "tidak dikenal" in upd_mb.effective_message.replies[-1][0].lower()

    # Monkeypatch 0-arg fallback pada _menu_text & main_menu_markup tetap aman
    monkeypatch.setattr(bot, "_menu_text", lambda: "Teks Custom 0-arg")
    monkeypatch.setattr(bot, "main_menu_markup", lambda: bot.home_markup())
    upd_mp = _Update("/start")
    asyncio.run(bot.cmd_start(upd_mp, _ctx()))
    assert upd_mp.effective_message.replies[-1][0] == "Teks Custom 0-arg"
    q_mp = _Query("menu")
    asyncio.run(bot.menu_callback(_Update(query=q_mp), _ctx()))
    assert q_mp.message.edits[-1][0] == "Teks Custom 0-arg"

    # sub:allcmd & _post_init mendaftarkan /mode
    allcmd = menu_mod._submenu_content("allcmd", 5_000_000)
    assert allcmd is not None and "/mode" in allcmd[0]
    registered = []

    class _BotRec:
        async def set_my_commands(self, cmds):
            registered.extend(c.command for c in cmds)

    asyncio.run(bot._post_init(types.SimpleNamespace(bot=_BotRec())))
    assert "mode" in registered


def test_panduan_bertahap_linear_stage_1_sampai_5(monkeypatch, tmp_store, tmp_path):
    _setup(monkeypatch, tmp_store)
    monkeypatch.setattr(bot, "TMP_DIR", str(tmp_path))
    today = marketcal.now_wib().date().isoformat()
    base = {
        "board": "Utama", "sector": "Keuangan", "trade_date": today,
        "rsi": 55.0, "ret20d": 3.0, "reason": "", "entry": 4000.0,
        "sl": 3800.0, "tp": 4400.0, "ret1d": 0.5, "ret5d": 1.0,
        "sma20": 3950.0, "sma50": 3900.0, "sma200": 3700.0,
    }
    store.save_market_scan(
        [
            {**base, "ticker": "BBRI", "name": "BBRI", "price": 4000.0, "rel_pct": 72.0, "signal": "BELI"},
            {**base, "ticker": "BBCA", "name": "BBCA", "price": 9000.0, "rel_pct": 95.0, "signal": "BELI"},
            {**base, "ticker": "ASII", "name": "ASII", "price": 5000.0, "rel_pct": 99.0, "signal": "BELI"},
            {**base, "ticker": "TLKM", "name": "TLKM", "price": 3200.0, "rel_pct": 85.0, "signal": "BELI"},
        ],
        {"ok": 4, "trade_date": today, "scanned_at": f"{today}T09:00:00"},
    )
    sehat = {"per": 12.0, "pbv": 1.8, "roe": 18.0, "roe_unit": "pct"}
    gagal = {"per": 40.0, "pbv": 5.0, "roe": 4.0, "roe_unit": "pct"}
    assert kualitas.save_cached(str(tmp_path), "BBRI", today, sehat)
    assert kualitas.save_cached(str(tmp_path), "BBCA", today, sehat)
    assert kualitas.save_cached(str(tmp_path), "ASII", today, gagal)

    # guide:start menautkan onboard:s1 di baris pertama
    q_gs = _Query("guide:start")
    asyncio.run(bot.menu_callback(_Update(query=q_gs), _ctx()))
    assert _callbacks(q_gs.message.edits[-1][1]["reply_markup"])[0] == "onboard:s1"

    # Stage 1 -> Goal Beli (Stage 2)
    q_s1 = _Query("onboard:s1")
    asyncio.run(bot.menu_callback(_Update(query=q_s1), _ctx()))
    assert {"onboard:goal:beli", "onboard:goal:cek", "onboard:goal:jual"} <= set(
        _callbacks(q_s1.message.edits[-1][1]["reply_markup"])
    )

    q_gb = _Query("onboard:goal:beli")
    asyncio.run(bot.menu_callback(_Update(query=q_gb), _ctx()))
    s2_txt, s2_kw = q_gb.message.edits[-1]
    s2_cbs = _callbacks(s2_kw["reply_markup"])
    assert store.get_onboard_state(100)["goal"] == "beli"
    assert store.get_onboard_state(100)["stage"] == "s2"
    assert "1 lot = 100 lembar" in s2_txt
    assert {f"onboard:modal:{v}" for v in (500_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000, 25_000_000)} <= set(s2_cbs)
    assert "manual:onboard_modal" in s2_cbs

    # Stage 2 -> Preset Rp 500rb -> Stage 3 adaptif (BBRI sehat & muat Rp500rb > BBCA > modal, ASII gagal disaring)
    q_m500 = _Query("onboard:modal:500000")
    asyncio.run(bot.menu_callback(_Update(query=q_m500), _ctx()))
    assert store.get_modal(100) == 500_000
    s3_txt, s3_kw = q_m500.message.edits[-1]
    s3_cbs = _callbacks(s3_kw["reply_markup"])
    assert "onboard:s4:BBRI" in s3_cbs
    assert "onboard:s4:ASII" not in s3_cbs
    assert s3_txt.index("BBRI") < s3_txt.index("BBCA")
    assert "&gt; modal" in s3_txt or "> modal" in s3_txt

    # Stage 2 manual modal via pending input
    q_mm = _Query("manual:onboard_modal")
    asyncio.run(bot.menu_callback(_Update(query=q_mm), _ctx()))
    assert bot._pending_get(100) == "onboard_modal"
    u_ok_m = _Update("2jt", uid=100, cid=100)
    asyncio.run(bot.menu_text_input(u_ok_m, _ctx()))
    assert store.get_modal(100) == 2_000_000
    assert bot._pending_get(100) is None
    assert "onboard:s4:BBCA" in _callbacks(u_ok_m.effective_message.replies[-1][1]["reply_markup"])

    # Stage 3 manual ticker -> langsung Stage 4
    q_mt = _Query("manual:onboard_ticker")
    asyncio.run(bot.menu_callback(_Update(query=q_mt), _ctx()))
    assert bot._pending_get(100) == "onboard_ticker"
    u_bad_t = _Update("BBCA TLKM", uid=100, cid=100)
    asyncio.run(bot.menu_text_input(u_bad_t, _ctx()))
    assert bot._pending_get(100) == "onboard_ticker"
    u_ok_t = _Update("BBRI", uid=100, cid=100)
    asyncio.run(bot.menu_text_input(u_ok_t, _ctx()))
    assert bot._pending_get(100) is None
    assert store.get_onboard_state(100)["stage"] == "s4"
    assert store.get_onboard_state(100)["ticker"] == "BBRI"
    s4_cbs = _callbacks(u_ok_t.effective_message.replies[-1][1]["reply_markup"])
    assert {"tick:cek:BBRI", "tick:fundamental:BBRI", "tick:analisis:BBRI", "onboard:s5:BBRI"} <= set(s4_cbs)

    # Stage 5 -> Done clears state
    q_s5 = _Query("onboard:s5:BBRI")
    asyncio.run(bot.menu_callback(_Update(query=q_s5), _ctx()))
    s5_cbs = _callbacks(q_s5.message.edits[-1][1]["reply_markup"])
    assert {"tick:rencana:BBRI", "exec:buy:BBRI", "sub:pantau", "onboard:done"} <= set(s5_cbs)

    q_done = _Query("onboard:done")
    asyncio.run(bot.menu_callback(_Update(query=q_done), _ctx()))
    assert store.get_onboard_state(100) == {}

    # Adaptive S3 untuk goal='jual': hanya lots > 0 yg ditampilkan
    store.add_real_position(100, "ANTM", 5, 1500.0, today)
    store.reduce_real_position(100, "ANTM", 5, 1600.0, today)  # ditutup (lots == 0)
    store.add_real_position(100, "TLKM", 4, 3000.0, today)     # lots == 4
    asyncio.run(bot.menu_callback(_Update(query=_Query("onboard:goal:jual")), _ctx()))
    q_s3j = _Query("onboard:s3")
    asyncio.run(bot.menu_callback(_Update(query=q_s3j), _ctx()))
    s3j_txt, s3j_kw = q_s3j.message.edits[-1]
    s3j_cbs = _callbacks(s3j_kw["reply_markup"])
    assert "onboard:s4:TLKM" in s3j_cbs
    assert "onboard:s4:ANTM" not in s3j_cbs
    assert "4 lot" in s3j_txt

    # Cek filter lots > 0 langsung meski ada entri posisi lots=0
    s3j_direct_txt, s3j_direct_kb = menu_mod._onboard_s3_view({
        "state": {"goal": "jual"}, "modal": 2_000_000, "max_pos": 0.25,
        "rows": [], "meta": {}, "positions": [
            {"ticker": "INDF", "lots": 0, "avg_price": 6000.0},
            {"ticker": "TLKM", "lots": 4, "avg_price": 3000.0},
        ], "wl": [],
    })
    assert "INDF" not in s3j_direct_txt
    assert "onboard:s4:INDF" not in _callbacks(s3j_direct_kb)
    assert "onboard:s4:TLKM" in _callbacks(s3j_direct_kb)

    q_s5j = _Query("onboard:s5:TLKM")
    asyncio.run(bot.menu_callback(_Update(query=q_s5j), _ctx()))
    s5j_cbs = _callbacks(q_s5j.message.edits[-1][1]["reply_markup"])
    assert {"tick:jual:TLKM", "exec:sell:TLKM", "sub:pantau", "onboard:done"} <= set(s5j_cbs)
    assert not any(c.startswith("exec:buy:") for c in s5j_cbs)


def test_ux_modal_presets_beli_cache_jual_lots_pick_dan_parse_tokens(monkeypatch, tmp_store, tmp_path):
    _setup(monkeypatch, tmp_store)
    monkeypatch.setattr(bot, "TMP_DIR", str(tmp_path))

    # 1) Modal presets mencakup 500rb & 1jt + tombol lanjutan pasca setmodal & manual
    assert menu_mod._MODAL_PRESETS[:2] == (500_000, 1_000_000)
    modal_cbs = _callbacks(bot.modal_menu_markup())
    assert {"setmodal:500000", "setmodal:1000000"} <= set(modal_cbs)

    q_sm = _Query("setmodal:500000")
    asyncio.run(bot.menu_callback(_Update(query=q_sm), _ctx()))
    sm_cbs = _callbacks(q_sm.message.edits[-1][1]["reply_markup"])
    assert {"pick:rencana", "guide:beli", "run:sinyal_bagus", "sub:rencana", "menu"} <= set(sm_cbs)

    # 2) _guide_beli_view: prioritas cache sehat > uncached > skip gagal, fallback rel_pct bila cache kosong
    today = marketcal.now_wib().date().isoformat()
    base = {"price": 5000.0, "signal": "BELI"}
    rows = [
        {**base, "ticker": "BBCA", "rel_pct": 95.0},
        {**base, "ticker": "BMRI", "rel_pct": 91.0},
        {**base, "ticker": "BBRI", "rel_pct": 70.0},
        {**base, "ticker": "ASII", "rel_pct": 99.0},
    ]
    txt_no_cache, kb_no_cache = menu_mod._guide_beli_view(rows, {}, 5_000_000, cache_dir=str(tmp_path))
    assert "belum ada" in txt_no_cache.lower()
    assert [c for c in _callbacks(kb_no_cache) if c.startswith("tick:cek:")] == [
        "tick:cek:ASII", "tick:cek:BBCA", "tick:cek:BMRI"
    ]

    kualitas.save_cached(str(tmp_path), "BBRI", today, {"per": 10.0, "pbv": 1.5, "roe": 19.0, "roe_unit": "pct"})
    kualitas.save_cached(str(tmp_path), "ASII", today, {"per": 45.0, "pbv": 6.0, "roe": 3.0, "roe_unit": "pct"})
    txt_cached, kb_cached = menu_mod._guide_beli_view(rows, {}, 5_000_000, cache_dir=str(tmp_path))
    cek_btns = [c for c in _callbacks(kb_cached) if c.startswith("tick:cek:")]
    assert cek_btns[0] == "tick:cek:BBRI"
    assert "tick:cek:ASII" not in cek_btns
    assert "1 lot" in txt_cached

    # 3) _guide_jual_view filter lots > 0
    pos = [{"ticker": "ANTM", "lots": 0}, {"ticker": "BMRI", "lots": 3}]
    txt_j, kb_j = menu_mod._guide_jual_view(pos, ["TLKM"], rows)
    assert "ANTM" not in txt_j
    assert "BMRI" in txt_j and "tick:jual:BMRI" in _callbacks(kb_j)

    # 4) _parse_menu_tokens terima 'BBCA Rp 500.000'
    toks_rp = menu_mod._parse_menu_tokens("rencana", "BBCA Rp 500.000")
    assert len(toks_rp) == 2 and toks_rp[0] == "BBCA"
    assert textutil.parse_rupiah(toks_rp[1]) == 500_000.0
    assert menu_mod._parse_menu_tokens("rencana", "BBCA 5jt 10jt") == []

    # 5) ticker_pick_markup(extra_tickers=...) & pick:sinyal_detail top scan
    pk_cbs = _callbacks(bot.ticker_pick_markup("sinyal_detail", ["ANTM"], extra_tickers=["BMRI", "ANTM", "TLKM"]))
    assert {"tick:sinyal_detail:BMRI", "tick:sinyal_detail:ANTM", "tick:sinyal_detail:TLKM"} <= set(pk_cbs)
    store.save_market_scan(rows, {"ok": 4, "trade_date": today})
    q_sd = _Query("pick:sinyal_detail")
    asyncio.run(bot.menu_callback(_Update(query=q_sd), _ctx()))
    sd_cbs = _callbacks(q_sd.message.edits[-1][1]["reply_markup"])
    assert {"tick:sinyal_detail:ASII", "tick:sinyal_detail:BBCA", "tick:sinyal_detail:ANTM"} <= set(sd_cbs)




