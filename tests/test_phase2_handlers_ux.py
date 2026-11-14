"""Tes integrasi & UX Wave 2B: provenance, panduan beli/jual, portofolio, jurnal, job exit alert."""
from __future__ import annotations

import asyncio
import types

import numpy as np
import pytest

import bot
from core import analysis, marketcal, pricing, reports, store
from handlers import (
    cek as cek_handler,
    info as info_handler,
    jobs as jobs_handler,
    jurnal as jurnal_handler,
    market as market_handler,
    menu as menu_handler,
    portfolio as portfolio_handler,
    sinyal as sinyal_handler,
    trading as trading_handler,
    watchlist as watchlist_handler,
)
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

    async def reply_text(self, text: str, **kwargs):
        self.replies.append((text, kwargs))
        return self

    async def edit_text(self, text: str, **kwargs):
        self.edits.append((text, kwargs))
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
    return types.SimpleNamespace(bot=_FakeBot(), job_queue=None)


def _app():
    return types.SimpleNamespace(bot=_FakeBot())


def _all_callbacks(markup) -> list[str]:
    if markup is None:
        return []
    return [
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
        if btn.callback_data
    ]


@pytest.fixture(autouse=True)
def _clean_state(tmp_path, monkeypatch, tmp_store):
    monkeypatch.setattr(bot, "TMP_DIR", str(tmp_path / "tmp"))
    monkeypatch.setattr(bot, "ALLOWED_IDS", set())
    bot._last_used.clear()
    bot.PENDING.clear()
    yield
    bot._last_used.clear()
    bot.PENDING.clear()


# ---------------------------------------------------------------------------
# 1. Integrasi #25 Temuan 1: provenance_line tidak terblokir token singkat
# ---------------------------------------------------------------------------
def test_reports_provenance_line_renders_full_details_when_attr_is_live_token(uptrend_df):
    ind = analysis.compute_indicators(uptrend_df)
    ind.attrs["provenance"] = "live"
    ind.attrs["provider"] = "yahoo_v8_chart"
    ind.attrs["adjusted"] = True
    ind.attrs["last_bar_date"] = "2026-11-13"
    ind.attrs["repaired_count"] = 2
    res = {
        "ticker": "BBCA",
        "ind": ind,
        "as_of": "2026-11-13",
    }
    line = reports.provenance_line(res)
    assert "yahoo_v8_chart" in line
    assert "live · adjusted" in line
    assert "repaired 2 bar" in line
    assert "bar terakhir 2026-11-13 WIB" in line


# ---------------------------------------------------------------------------
# 2. Integrasi #25 Temuan 2: handlers/cek.py mengisi sl & prev_close,
#    merender warnings, dan menyembunyikan tombol beli saat HINDARI
# ---------------------------------------------------------------------------
def test_cek_populates_sl_prev_close_and_hides_buy_button_on_hindari(
    monkeypatch, downtrend_df
):
    df = downtrend_df.copy()
    df.attrs["as_of"] = "2026-11-13"
    df.attrs["provenance"] = "live"
    df.attrs["provider"] = "yahoo_v8_chart"
    monkeypatch.setattr(bot.data, "fetch_history", lambda *a, **k: df)
    monkeypatch.setattr(bot.data, "fetch_weekly", lambda *a, **k: None)
    monkeypatch.setattr(
        bot.fundamental,
        "fetch_fundamentals",
        lambda t: {
            "per": -5.0,
            "pbv": 6.0,
            "roe": -0.15,
            "debt_eq": 350.0,
            "profit_trend": [("Q1", -10e9), ("Q2", -20e9)],
        },
    )
    monkeypatch.setattr(bot.news, "fetch_news", lambda t: [])
    monkeypatch.setattr(bot.bandar, "get_stock_summary", lambda *a, **k: [])
    monkeypatch.setattr(bot.marketcal, "upcoming_dividend_exdate", lambda *a, **k: None)

    captured_res: dict = {}
    orig_build = cek_handler.cekmod.build

    def spy_build(**kwargs):
        captured_res.update(kwargs.get("res") or {})
        out = orig_build(**kwargs)
        out["verdict"] = "HINDARI"
        if not out.get("warnings"):
            out["warnings"] = ["Peringatan uji ARB / kualitas"]
        return out

    monkeypatch.setattr(cek_handler.cekmod, "build", spy_build)

    upd = _FakeUpdate("/cek BBCA", cid=100)
    asyncio.run(bot.cmd_cek(upd, _ctx()))
    assert captured_res.get("sl") is not None and captured_res["sl"] > 0
    assert captured_res.get("prev_close") is not None and captured_res["prev_close"] > 0

    text, kw = upd.effective_message.replies[-1]
    assert "HINDARI" in text
    assert "Peringatan uji ARB / kualitas" in text
    assert "tombol konfirmasi beli disembunyikan" in text
    cbs = _all_callbacks(kw.get("reply_markup"))
    assert "exec:buy:BBCA" not in cbs
    assert "tick:jual:BBCA" in cbs


# ---------------------------------------------------------------------------
# 3. parse_tanya & _TANYA_STOP kosakata transaksi umum
# ---------------------------------------------------------------------------
def test_parse_tanya_ignores_transaction_vocabulary():
    assert bot.parse_tanya("beli 5 lot BBCA?") == ("beli", "BBCA")
    assert bot.parse_tanya("sudah pegang 10 lot TLKM masih aman hold atau cut?") == (
        "jual",
        "TLKM",
    )
    assert bot.parse_tanya("cuan 2 juta di BMRI jual gak?") == ("jual", "BMRI")
    assert bot.parse_tanya("rugi 500 rb di ANTM cut loss?") == ("jual", "ANTM")


# ---------------------------------------------------------------------------
# 4. build_verdict fokus jual: portfolio-aware, tanpa "Kalau beli — SL",
#    tanpa STRONG BUY / STRONG SELL
# ---------------------------------------------------------------------------
def test_build_verdict_jual_portfolio_aware_and_no_buy_sl_line(uptrend_df):
    res = analysis.analyze("BBCA", uptrend_df)
    res["ticker"] = "BBCA"
    res["as_of"] = "2026-11-13"
    close_px = float(res["snap"]["close"])
    entry_px = pricing.round_tick(close_px * 0.90)
    sl_px = pricing.round_tick(entry_px * 0.95)
    tp_px = pricing.round_tick(close_px * 1.10)

    positions = [
        {
            "ticker": "BBCA",
            "lots": 10,
            "avg_price": entry_px,
            "sector": "Financials",
            "updated_at": "2026-10-01T10:00:00",
        }
    ]
    open_execs = {
        "BBCA": {
            "ticker": "BBCA",
            "lots": 10,
            "entry": entry_px,
            "sl": sl_px,
            "tp": tp_px,
            "trade_date": "2026-10-01",
        }
    }
    reg = {"status": "DEFENSIVE", "label": "defensif (SMA50 < SMA200)", "exposure_scale": 0.5}
    txt = bot.build_verdict(
        res,
        {"pct": 25.0, "base_pct": 25.0, "label": "SEHAT", "notes": ["ROE kuat"], "forensic_penalty": -10.0, "forensic_flags": ["OCF tipis"]},
        {"pct": 10.0, "n": 3},
        focus="jual",
        positions=positions,
        open_execs=open_execs,
        rel_pct=32.0,
        market_regime=reg,
    )
    assert "Kalau beli — SL:" not in txt
    assert "STRONG BUY" not in txt and "STRONG SELL" not in txt
    assert "Posisi tercatat: <b>10 lot</b>" in txt
    assert "SL eksekusi" in txt
    assert "Chandelier trailing SL" in txt
    assert "Scale-out TP1 (jual 50% lot)" in txt
    assert "BEP+0,40% fee BEI" in txt
    assert "Time-stop" in txt
    assert "keluar hold-band" in txt or "di bawah pita tahan" in txt
    assert "Forensik kas/dividen" in txt
    assert "defensif" in txt.lower()


def test_do_tanya_jual_does_not_overwrite_buy_signal_context(monkeypatch, uptrend_df):
    monkeypatch.setattr(bot.data, "fetch_history", lambda *a, **k: uptrend_df)
    monkeypatch.setattr(bot.fundamental, "fetch_fundamentals", lambda t: None)
    monkeypatch.setattr(bot.news, "fetch_news", lambda t: [])

    store.save_signal_context(
        100, "BBCA", "rencana", 8800.0, 8600.0, 9200.0, 5, "2026-11-13", intent="beli"
    )
    upd = _FakeUpdate("/jual BBCA", cid=100)
    asyncio.run(bot.cmd_jual(upd, _ctx()))

    buy_ctx = store.get_signal_context(100, "BBCA", intent="beli")
    sell_ctx = store.get_signal_context(100, "BBCA", intent="jual")
    assert buy_ctx is not None and buy_ctx["source"] == "rencana"
    assert buy_ctx["signal_price"] == 8800.0 and buy_ctx["suggested_lots"] == 5
    assert sell_ctx is not None and sell_ctx["source"] == "tanya_jual"
    assert sell_ctx["intent"] == "jual"


# ---------------------------------------------------------------------------
# 5. handlers/info.py: mata uang USD di /fundamental & metadata scan di /status
# ---------------------------------------------------------------------------
def test_info_fundamental_respects_non_idr_currency(monkeypatch):
    monkeypatch.setattr(
        bot.fundamental,
        "fetch_fundamentals",
        lambda t: {
            "name": "Vale Indonesia Tbk",
            "sector": "Basic Materials",
            "currency": "USD",
            "per": 11.5,
            "pbv": 1.2,
            "roe": 0.14,
            "div_yield": 0.03,
            "debt_eq": 12.0,
            "eps": 0.08,
            "market_cap": 2_500_000_000.0,
            "ocf": 90_000_000.0,
            "fcf": 55_000_000.0,
            "net_income": 45_000_000.0,
            "total_cash": 120_000_000.0,
            "total_debt": 30_000_000.0,
            "current_ratio": 2.4,
            "payout_ratio": 0.30,
            "as_of": "Q3 2026",
            "filing_note": "Laporan Q3 2026 (jeda 35 hari)",
            "rev_trend": [("Q2 2026", 250_000_000.0), ("Q3 2026", 280_000_000.0)],
            "profit_trend": [("Q2 2026", 40_000_000.0), ("Q3 2026", 45_000_000.0)],
        },
    )
    upd = _FakeUpdate("/fundamental INCO", cid=100)
    asyncio.run(info_handler.do_fundamental(upd, "INCO", bot))
    txt = upd.effective_message.replies[-1][0]
    assert "Mata uang laporan: <b>USD</b>" in txt
    assert "Rp" not in txt
    assert "USD 0.08" in txt
    assert "Periode Q3 2026" in txt and "POJK 14" in txt


def test_info_status_shows_scan_staleness_and_quality_meta():
    store.save_market_scan(
        [
            {
                "ticker": "BBCA",
                "name": "BCA",
                "board": "Utama",
                "trade_date": "2026-11-13",
                "price": 9000,
                "ret1d": 0.5,
                "ret5d": 1.0,
                "ret20d": 2.0,
                "rsi": 55.0,
                "macd_hist": 5.0,
                "sma20": 8900,
                "sma50": 8800,
                "sma200": 8500,
                "atr": 150,
                "tech_score": 40.0,
                "rel_pct": 85.0,
                "signal": "BELI",
                "reason": "ok",
                "entry": 9000,
                "sl": 8850,
                "tp": 9300,
            }
        ],
        {
            "scanned_at": "2026-11-13T16:20:00",
            "trade_date": "2026-11-13",
            "trade_date_min": "2026-11-12",
            "universe": 944,
            "ok": 900,
            "skipped": 30,
            "skipped_stale": 12,
            "skipped_quality": 4,
            "quality_flagged": 7,
            "failed": 14,
            "elapsed_sec": 120.0,
            "buy": 1,
            "sell": 0,
            "hold": 0,
        },
    )
    upd = _FakeUpdate("/status", cid=100)
    asyncio.run(info_handler.do_status(upd, _ctx(), bot))
    txt = upd.effective_message.replies[-1][0]
    assert "2026-11-13 (min 2026-11-12)" in txt
    assert "dilewati basi 12" in txt
    assert "kualitas 4" in txt
    assert "perbaikan bar 7" in txt


# ---------------------------------------------------------------------------
# 6. handlers/trading.py: exposure_scale 50% (bukan 0.5%) & tinjauan posisi asli
# ---------------------------------------------------------------------------
def test_saran_scan_text_defensive_percentage_and_real_position_review(
    monkeypatch, uptrend_df
):
    store.record_execution_buy(
        100,
        "BBCA",
        6,
        8500.0,
        "2026-10-10",
        signal_price=8500.0,
        sl=8300.0,
        tp=8900.0,
        source="rencana",
        sector="Financials",
    )
    ind = analysis.compute_indicators(uptrend_df)
    ind.attrs["as_of"] = "2026-11-13"
    res = {
        "ticker": "BBCA",
        "ind": ind,
        "snap": analysis.last_snapshot(ind),
        "score": analysis.score_stock(ind),
        "sr": analysis.support_resistance(ind),
        "as_of": "2026-11-13",
    }

    async def fake_gather(t):
        return {
            "res": {**res, "ticker": t},
            "fund": {"pct": 20.0, "sector": "Financials"},
            "news": {"pct": 10.0},
        }

    monkeypatch.setattr(bot, "gather_candidate", fake_gather)
    monkeypatch.setattr(
        bot.regime,
        "fetch_ihsg_regime",
        lambda: {
            "status": "DEFENSIVE",
            "label": "defensif (SMA50 < SMA200)",
            "exposure_scale": 0.5,
            "sma50": 6800.0,
            "sma200": 7100.0,
            "gap_pct": -4.2,
        },
    )
    txt = asyncio.run(trading_handler.saran_scan_text(100, bot, ["BBCA", "BBRI"]))
    assert txt is not None
    assert "eksposur 50%" in txt
    assert "risiko 0.5%/posisi" in txt
    assert "Data bar terakhir: 2026-11-13 WIB" in txt
    assert "[Portofolio 6 lot @" in txt


# ---------------------------------------------------------------------------
# 7. handlers/portfolio.py & handlers/jurnal.py: SL eksekusi, diagnostik,
#    stale context warning, tombol cepat
# ---------------------------------------------------------------------------
def test_portfolio_and_jurnal_display_sl_source_diagnostics_and_stale_warning(
    monkeypatch, uptrend_df
):
    store.record_execution_buy(
        100,
        "BBCA",
        4,
        9000.0,
        "2026-10-05",
        signal_price=8975.0,
        sl=8750.0,
        tp=9500.0,
        source="rencana",
        sector="Financials",
    )
    monkeypatch.setattr(bot.data, "fetch_history", lambda *a, **k: uptrend_df)

    upd_p = _FakeUpdate("/portofolio", cid=100)
    asyncio.run(portfolio_handler.do(upd_p, [], bot))
    txt_p, kw_p = upd_p.effective_message.replies[-1]
    assert "(eksekusi)" in txt_p
    assert "Chandelier" in txt_p
    cbs_p = _all_callbacks(kw_p.get("reply_markup"))
    assert "tick:jual:BBCA" in cbs_p
    assert "exec:sell:BBCA" in cbs_p

    # Catat jual untuk cek diagnostik jurnal (R-multiple, capture, fee BEI)
    store.record_execution_sell(
        100, "BBCA", 2, 9300.0, "2026-11-13", signal_price=9300.0, source="portofolio"
    )
    upd_j = _FakeUpdate("/jurnal", cid=100)
    asyncio.run(bot.cmd_jurnal(upd_j, _ctx()))
    txt_j = upd_j.effective_message.replies[-1][0]
    assert "Diagnostik" in txt_j or "R-multiple" in txt_j or "capture" in txt_j.lower()

    # Stale signal context warning saat tombol konfirmasi ditekan berhari-hari kemudian
    store.save_signal_context(
        100, "BMRI", "rencana", 5000.0, 4800.0, 5400.0, 3, "2026-10-01", intent="beli"
    )
    q = _FakeQuery("exec:buy:BMRI", uid=100, cid=100)
    asyncio.run(jurnal_handler.handle_exec_callback(_FakeUpdate(query=q, cid=100), "exec:buy:BMRI", bot))
    reply_txt = q.message.replies[-1][0]
    assert "Konteks sinyal berasal dari" in reply_txt and "kedaluwarsa" in reply_txt


# ---------------------------------------------------------------------------
# 8. handlers/jobs.py: exit alert portofolio asli + dedup harian & target harga
# ---------------------------------------------------------------------------
def test_scan_alerts_real_position_exit_and_daily_dedup(monkeypatch):
    # Buat data di mana harga terakhir turun menembus SL 8800
    closes = np.concatenate([np.linspace(9000, 9200, 40), np.array([8900.0, 8700.0])])
    df = make_prices(closes, start="2026-09-01")
    monkeypatch.setattr(bot.data, "fetch_history", lambda *a, **k: df)
    monkeypatch.setattr(bot.marketcal, "market_open", lambda: True)

    store.record_execution_buy(
        100,
        "BBCA",
        4,
        9000.0,
        "2026-09-15",
        signal_price=9000.0,
        sl=8800.0,
        tp=9500.0,
        source="rencana",
        sector="Financials",
    )
    app = _app()
    asyncio.run(jobs_handler.scan_alerts(app, bot))
    assert len(app.bot.sent) == 1
    cid, msg_txt, kw = app.bot.sent[0]
    assert cid == 100
    assert "ALERT POSISI PORTOFOLIO — BBCA" in msg_txt
    assert "SL Tersentuh" in msg_txt
    cbs = _all_callbacks(kw.get("reply_markup"))
    assert "tick:jual:BBCA" in cbs and "exec:sell:BBCA" in cbs

    # Putaran kedua di hari yang sama -> dedup (0 pesan tambahan)
    asyncio.run(jobs_handler.scan_alerts(app, bot))
    assert len(app.bot.sent) == 1


def test_watchlist_price_target_alert_and_scan_trigger(monkeypatch, uptrend_df):
    upd1 = _FakeUpdate("/alert BBCA 2500", cid=100)
    asyncio.run(bot.cmd_alert(upd1, _ctx()))
    alerts_saved = store.get_alerts(100)
    assert "target>=2500" in alerts_saved["BBCA"]

    upd2 = _FakeUpdate("/alert BBRI <1500", cid=100)
    asyncio.run(bot.cmd_alert(upd2, _ctx()))
    assert "target<=1500" in store.get_alerts(100)["BBRI"]

    monkeypatch.setattr(bot.data, "fetch_history", lambda *a, **k: uptrend_df)
    monkeypatch.setattr(bot.marketcal, "market_open", lambda: True)
    app = _app()
    asyncio.run(jobs_handler.scan_alerts(app, bot))
    bbca_msgs = [m for c, m, _ in app.bot.sent if c == 100 and "BBCA" in m]
    assert len(bbca_msgs) == 1
    assert "Target harga &gt;= Rp2.500 tercapai" in bbca_msgs[0]


# ---------------------------------------------------------------------------
# 9. handlers/menu.py & handlers/watchlist.py: tombol Cek Jual, allcmd, /modal
# ---------------------------------------------------------------------------
def test_menu_has_cek_jual_and_complete_allcmd_and_modal_copy():
    cbs = _all_callbacks(menu_handler.main_menu_markup())
    assert "pick:jual" in cbs

    sub = menu_handler._submenu_content("allcmd", 10_000_000)
    assert sub is not None
    allcmd_txt, _ = sub
    for expected in (
        "/portofolio batas",
        "/saran lq45",
        "/saran list",
        "/auto off",
        "/jurnal beli",
        "/jurnal jual",
        "/jurnal riwayat",
    ):
        assert expected in allcmd_txt

    upd_m = _FakeUpdate("/modal", cid=100)
    asyncio.run(watchlist_handler.cmd_modal(upd_m, _ctx(), bot))
    modal_reply = upd_m.effective_message.replies[-1][0]
    assert "sizing posisi asli" in modal_reply
    assert "simulasi paper trading" in modal_reply


# ---------------------------------------------------------------------------
# 10. handlers/market.py & handlers/sinyal.py: batas risiko kustom & sinyal sort
# ---------------------------------------------------------------------------
def test_market_rencana_single_text_shows_custom_risk_limits_and_sinyal_sort():
    plan = {
        "ok": True,
        "ticker": "BBCA",
        "name": "BCA",
        "grade": "A",
        "sector": "Financials",
        "board": "Utama",
        "price": 9000.0,
        "entry": 9000.0,
        "chase_above": 9050.0,
        "sl": 8800.0,
        "tp": 9400.0,
        "net_tp_pct": 4.2,
        "rr": 2.0,
        "lots": 2,
        "shares": 200,
        "value": 1_800_000.0,
        "buy_cost": 1_802_700.0,
        "cash_left": 197_300.0,
        "budget": 2_000_000.0,
        "modal": 10_000_000,
        "pct_modal": 18.0,
        "max_pos_pct": 0.20,
        "max_sector_pct": 0.35,
        "max_loss": 45_000.0,
        "max_loss_pct": 0.45,
        "profit_tp": 72_000.0,
        "lots_risk": 2,
        "risk_budget": 100_000.0,
        "effective_risk_pct": 0.01,
        "as_of": "2026-11-13",
        "stale": True,
        "warnings": ["Peringatan uji ARB"],
    }
    c = {
        "name": "BCA",
        "sector": "Financials",
        "board": "Utama",
        "as_of": "2026-11-13",
        "stale": True,
        "quality_note": "1 bar diperbaiki",
    }
    txt = market_handler._rencana_single_text(
        plan, c, budget_from_modal=True, deps=bot
    )
    assert "20%/posisi" in txt
    assert "Data per 2026-11-13 WIB" in txt
    assert "⚠️ cache lama" in txt
    assert "Peringatan uji ARB" in txt

    # Sinyal overview mengurutkan kandidat BELI dari rel_pct tertinggi
    rows = [
        {"ticker": "BBRI", "signal": "BELI", "price": 4500, "rel_pct": 75.0, "ret20d": 2.0},
        {"ticker": "BBCA", "signal": "BELI", "price": 9000, "rel_pct": 95.0, "ret20d": 4.0},
    ]
    meta = {"scanned_at": marketcal.now_wib().strftime("%Y-%m-%dT%H:%M:%S"), "trade_date": "2026-11-13", "ok": 2}
    ov = sinyal_handler._sinyal_overview(rows, meta, bot)
    assert ov.index("BBCA") < ov.index("BBRI")

    kb_jual = sinyal_handler.detail_markup("ANTM", sig="JUAL")
    cbs_jual = _all_callbacks(kb_jual)
    assert "exec:buy:ANTM" not in cbs_jual
    assert "exec:sell:ANTM" in cbs_jual
    assert "tick:jual:ANTM" in cbs_jual
