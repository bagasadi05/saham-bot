"""Tes alur menu interaktif ramah pemula (progressive disclosure, belajar, modal)."""
from __future__ import annotations

import asyncio
import types

import numpy as np

import bot
from core import alerts, bandar, banding, data, fundamental, learn, marketcal, news, store
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
        bot.result_markup("jual", "BBCA"),
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



