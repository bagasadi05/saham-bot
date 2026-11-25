"""Tes regresi offline untuk perbaikan audit lanjutan (Gelombang Audit 2026-11)."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import sqlite3
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import bot
from core import (
    analysis,
    bandar,
    banding,
    data,
    data_quality,
    fundamental,
    news,
    news_ml,
    pasar as pasarmod,
    pricing,
    relatif,
    rencana,
    risk,
    saran,
    screener,
    store,
    store_portfolio,
    verdict,
)
from handlers import (
    analisis as h_analisis,
    cek as h_cek,
    info as h_info,
    jobs as h_jobs,
    jurnal as h_jurnal,
    market as h_market,
    pasar as h_pasar,
    sektor as h_sektor,
    sinyal as h_sinyal,
    trading as h_trading,
    watchlist as h_watchlist,
)
from tests.conftest import make_prices


class _FakeMsg:
    def __init__(self, text: str = ""):
        self.text = text
        self.replies: list[tuple[str, dict]] = []

    async def reply_text(self, text: str, **kw):
        self.replies.append((text, kw))
        return self


class _FakeChat:
    def __init__(self, cid: int = 100):
        self.id = cid
        self.actions: list[str] = []

    async def send_action(self, action: str):
        self.actions.append(action)


class _FakeUpdate:
    def __init__(self, text: str = "", cid: int = 100, uid: int = 100):
        self.effective_chat = _FakeChat(cid)
        self.effective_user = SimpleNamespace(id=uid)
        self.effective_message = _FakeMsg(text)
        self.message = self.effective_message


async def _async_val(v):
    return v


def _is_legal_bei_tick(price: float) -> bool:
    tick = pricing.tick_size(price)
    return round(price / tick) * tick == pytest.approx(price, abs=1e-6)


# ------------------------------------------------------------------ ROE & cash=0
def test_roe_ratio_unit_not_double_scaled_in_rencana_and_banding(monkeypatch):
    ind = make_prices(np.linspace(8000, 9000, 60))
    ind["atr14"] = 150.0
    ind["sma20"] = 8800.0
    ind["sma50"] = 8600.0
    ind["sma200"] = 8200.0
    fund_raw = {
        "per": 12.0,
        "pbv": 1.8,
        "roe": 0.18,
        "roe_unit": "ratio",
        "debt_eq": 0.5,
        "div_yield": 0.04,
        "sector": "Financials",
    }
    res_real = analysis.analyze("BBCA", ind)
    analyzed = {
        "res": res_real,
        "fund": {"pct": 20.0},
        "nsent": {"score": 0, "n": 0},
        "fund_raw": fund_raw,
    }
    enriched = rencana.enrich(
        {"ticker": "BBCA", "rel_pct": 75.0, "sector": "Financials"},
        analyzed,
        require_rel=False,
    )
    assert enriched is not None
    assert enriched["roe"] == pytest.approx(18.0, abs=0.1)

    row = banding.compare_row("BBCA", res_real, fund_raw)
    assert row["roe"] == pytest.approx(18.0, abs=0.1)


def test_fundamental_zero_cash_with_debt_gets_forensic_penalty():
    out = fundamental.analyze_cashflow_quality({
        "sector": "Energy",
        "total_cash": 0.0,
        "total_debt": 500_000_000_000.0,
    })
    assert out["available"] is True
    assert "utang_gt_2x_kas" in out["forensic_flags"]
    assert out["forensic_penalty"] >= 15


# ------------------------------------------------------------------ Tick & ARB
@pytest.mark.parametrize(
    ("price", "atr", "sma20"),
    [
        (202.0, 12.0, 195.0),
        (498.0, 10.0, 480.0),
        (2010.0, 40.0, 1950.0),
        (5025.0, 100.0, 4900.0),
    ],
)
def test_rencana_and_scale_out_respect_cross_band_bei_ticks(price, atr, sma20):
    lv = rencana.price_levels({"price": price, "atr": atr, "sma20": sma20})
    for key in ("entry", "sl", "tp", "chase_above"):
        assert _is_legal_bei_tick(lv[key]), f"{key}={lv[key]} invalid for entry={price}"
    if lv["entry_pullback"] is not None:
        assert _is_legal_bei_tick(lv["entry_pullback"])
    so = saran.scale_out_plan(price, atr, 10, tp_full=lv["tp"])
    assert _is_legal_bei_tick(so["tp1"])


def test_arb_gap_risk_rp12_no_false_arb_locked():
    # Pada rezim Kep-00158 (7%), Rp12 -> floor Rp11 (bukan ceil_tick(11.16)=12)
    res = saran.arb_gap_risk(12.0, 11.0, prev_close=12.0)
    assert res["arb_floor"] == 11.0
    assert res["arb_locked"] is False


# ------------------------------------------------------------------ Quality note HTML
def test_quality_note_not_double_escaped_in_outputs():
    q_html = data_quality.summary_line({"ok": False, "flags": {"volume_nol": 2}})
    assert "<i>" in q_html
    assert data_quality.plain_note(q_html) == "Kualitas data: 2 bar volume 0."

    res = analysis.analyze("BBCA", make_prices(np.linspace(8000, 9000, 60)))
    res["quality_note"] = q_html
    v_txt = verdict.build_verdict(res, {"pct": 20.0, "label": "Bagus"}, {"pct": 10.0, "n": 1}, "beli")
    assert "&lt;i&gt;" not in v_txt
    assert "<i>Kualitas data: 2 bar volume 0.</i>" in v_txt

    s_row = h_sinyal._sinyal_row(1, {"ticker": "BBCA", "price": 9000.0, "quality_note": q_html})
    assert "&lt;i&gt;" not in s_row

    m_card = h_market._rencana_single_text(
        {
            "ticker": "BBCA", "entry": 9000, "sl": 8800, "tp": 9400,
            "chase_above": 9100, "lots": 5, "cost": 4500000, "pct_modal": 45.0,
            "ok": False, "budget": 500_000, "lot_value": 900_000, "min_budget": 902_000,
            "risk_rp": 100000, "risk_pct": 1.0, "rr": 2.0,
            "grade": "A", "reasons": [], "warnings": [], "vetoes": [],
        },
        {"quality_note": q_html},
        budget_from_modal=True,
        deps=bot,
    )
    assert "&lt;i&gt;" not in m_card

    qn = data_quality.plain_note(q_html)
    assert "&lt;i&gt;" not in f"⚠️ <i>{h_cek.esc(qn)}</i>"


# ------------------------------------------------------------------ Job guard & alerts
def test_backup_and_scan_jobs_propagate_errors_to_job_guard(tmp_store, monkeypatch):
    bot.JOB_STATUS.clear()
    monkeypatch.setattr(bot.config, "ADMIN_CHAT_ID", "555")
    monkeypatch.setattr(bot.store, "backup_to", lambda *_a: (_ for _ in ()).throw(OSError("disk full")))
    sent: list[str] = []

    class _App:
        class bot:
            @staticmethod
            async def send_message(_cid, text, **_kw):
                sent.append(text)

    asyncio.run(bot.backup_job(_App()))
    assert bot.JOB_STATUS["backup-store"]["last_ok"] == ""
    assert "disk full" in bot.JOB_STATUS["backup-store"]["last_error"]
    assert any("backup-store" in s for s in sent)

    monkeypatch.setattr(bot.config, "FULL_SCAN_AUTO", True)
    monkeypatch.setattr(bot.marketcal, "is_trading_day", lambda _d: True)
    monkeypatch.setattr(bot, "_scan_with_guard", lambda _f: (_ for _ in ()).throw(RuntimeError("timeout")))
    asyncio.run(bot.full_market_scan_job(_App()))
    assert bot.JOB_STATUS["full-market-scan"]["last_ok"] == ""
    assert "timeout" in bot.JOB_STATUS["full-market-scan"]["last_error"]
    assert any("Screener harian gagal" in s for s in sent)


# ------------------------------------------------------------------ Security, PENDING & Watchlist
def test_menu_text_input_handles_none_user_and_preserves_pending(monkeypatch):
    bot.PENDING.clear()
    monkeypatch.setattr(bot, "ALLOWED_IDS", {999})
    bot._pending_set(100, "analisis")

    upd_none = SimpleNamespace(
        effective_chat=_FakeChat(100), effective_user=None, effective_message=_FakeMsg("BBCA"),
    )
    asyncio.run(bot.menu_text_input(upd_none, None))
    assert bot._pending_get(100) == "analisis"

    upd_unauth = _FakeUpdate("BBCA", cid=100, uid=111)
    asyncio.run(bot.menu_text_input(upd_unauth, None))
    assert bot._pending_get(100) == "analisis"


def test_watchlist_alert_cap_and_long_reply(tmp_store, monkeypatch):
    monkeypatch.setattr(bot, "MAX_WATCH", 2)
    asyncio.run(h_watchlist.do_alert_add(_FakeUpdate(), bot, "BBCA", {"breakout"}, from_menu=False))
    asyncio.run(h_watchlist.do_alert_add(_FakeUpdate(), bot, "BBRI", {"breakout"}, from_menu=False))
    upd_full = _FakeUpdate()
    asyncio.run(h_watchlist.do_alert_add(upd_full, bot, "BMRI", {"breakout"}, from_menu=False))
    assert "Alert penuh" in upd_full.effective_message.replies[-1][0]
    assert "BMRI" not in store.get_alerts(100)

    for i in range(80):
        store.set_alert(200, f"X{i:03d}", {f"target>={1000 + j}" for j in range(15)})
    upd_long = _FakeUpdate(cid=200, uid=200)
    asyncio.run(h_watchlist.do_alert_list(upd_long, bot, from_menu=False))
    assert len(upd_long.effective_message.replies) >= 2


# ------------------------------------------------------------------ Jurnal MAE/MFE & Cooldown
def test_jurnal_cached_and_weekly_report_include_mae_mfe(tmp_store, monkeypatch):
    bot._last_used.clear()
    df = make_prices(np.linspace(8800, 9600, 30), start="2026-01-02")
    store.record_execution_buy(100, "BBCA", 5, 9000.0, "2026-01-05")
    store.record_execution_sell(100, "BBCA", 5, 9500.0, "2026-01-20")

    monkeypatch.setattr(h_jurnal.data, "fetch_cached_history", lambda t, *_a, **_kw: df)
    monkeypatch.setattr(h_jurnal.data, "fetch_history", lambda t, *_a, **_kw: df)
    upd = _FakeUpdate("/jurnal", cid=100, uid=100)
    asyncio.run(h_jurnal.do_jurnal(upd, [], bot))
    txt = upd.effective_message.replies[-1][0]
    assert "MAE" in txt and "MFE" in txt

    store.set_auto(601, True)
    store.record_execution_buy(601, "BBCA", 5, 9000.0, "2026-01-05")
    store.record_execution_sell(601, "BBCA", 5, 9500.0, "2026-01-20")
    sent: list[str] = []

    class _App:
        class bot:
            @staticmethod
            async def send_message(_cid, text, **_kw):
                sent.append(text)

    asyncio.run(h_jobs.weekly_journal_report(_App(), bot, force=True))
    assert sent and "MAE" in sent[0] and "MFE" in sent[0]


def test_info_and_watchlist_cooldowns(tmp_store, monkeypatch):
    bot._last_used.clear()
    monkeypatch.setattr(h_info.news, "fetch_news", lambda _t: [])
    u1 = _FakeUpdate("/berita BBCA", cid=501, uid=501)
    asyncio.run(h_info.do_berita(u1, "BBCA", bot))
    u2 = _FakeUpdate("/berita BBCA", cid=501, uid=501)
    asyncio.run(h_info.do_berita(u2, "BBCA", bot))
    assert "Tenang sebentar" in u2.effective_message.replies[-1][0]

    monkeypatch.setattr(h_info.fundamental, "fetch_fundamentals", lambda _t: {
        "per": 12.0, "pbv": 1.8, "roe": 0.16, "roe_unit": "ratio", "sector": "Finance",
    })
    uf1 = _FakeUpdate("/fundamental BBCA", cid=502, uid=502)
    asyncio.run(h_info.do_fundamental(uf1, "BBCA", bot))
    assert "16.0%" in uf1.effective_message.replies[-1][0]
    uf2 = _FakeUpdate("/fundamental BBCA", cid=502, uid=502)
    asyncio.run(h_info.do_fundamental(uf2, "BBCA", bot))
    assert "Tenang sebentar" in uf2.effective_message.replies[-1][0]

    store.watchlist_add(503, "BBCA")
    w_df = make_prices(np.linspace(8000, 9000, 60))
    monkeypatch.setattr(bot, "fetch_and_analyze", lambda t: _async_val(analysis.analyze(t, w_df)))
    uw1 = _FakeUpdate(cid=503, uid=503)
    asyncio.run(h_watchlist.do_watch_list(uw1, bot))
    uw2 = _FakeUpdate(cid=503, uid=503)
    asyncio.run(h_watchlist.do_watch_list(uw2, bot))
    assert "Tenang sebentar" in uw2.effective_message.replies[-1][0]

    us1 = _FakeUpdate(cid=504, uid=504)
    asyncio.run(h_analisis.do_skor(us1, ["BBCA"], bot))
    us2 = _FakeUpdate(cid=504, uid=504)
    asyncio.run(h_analisis.do_skor(us2, ["BBCA"], bot))
    assert "Tenang sebentar" in us2.effective_message.replies[-1][0]


def test_tanya_passes_market_regime_and_cooldowns_block_spam(tmp_store, monkeypatch):
    bot._last_used.clear()
    captured = {}

    def _spy_verdict(*_a, **kw):
        captured["regime"] = kw.get("market_regime")
        return "⏳ <b>TUNGGU</b>"

    df = make_prices(np.linspace(8000, 9000, 260))
    monkeypatch.setattr(bot, "fetch_and_analyze", lambda t: _async_val({
        "ticker": t.upper(),
        "snap": {"close": 9000.0, "atr": 100.0, "sl": 8900.0, "tp": 9200.0},
        "score": {"pct": 10.0},
        "ind": df,
    }))
    monkeypatch.setattr(bot.regime, "fetch_ihsg_regime", lambda: {"status": "DEFENSIVE", "label": "Defensif"})
    monkeypatch.setattr(bot, "build_verdict", _spy_verdict)
    monkeypatch.setattr(bot.fundamental, "fetch_fundamentals", lambda _t: None)
    monkeypatch.setattr(bot.news, "fetch_news", lambda _t: [])

    upd1 = _FakeUpdate(cid=301, uid=301)
    asyncio.run(h_trading.do_tanya(upd1, bot, "BBCA", "beli"))
    assert captured["regime"]["status"] == "DEFENSIVE"

    upd2 = _FakeUpdate(cid=301, uid=301)
    asyncio.run(h_trading.do_tanya(upd2, bot, "BBCA", "beli"))
    assert "Tenang sebentar" in upd2.effective_message.replies[-1][0]


# ------------------------------------------------------------------ Screener, Bandar, News, Relatif, Risk, Store
def test_screener_rescan_after_16_wib_when_morning_cached(tmp_store, monkeypatch):
    store.save_market_scan(
        [{"ticker": "BBCA", "signal": "BELI", "price": 9000, "rel_pct": 80}],
        {"scanned_at": "2026-11-25T08:30:00", "trade_date": "2026-11-24"},
    )
    monkeypatch.setattr(
        screener.marketcal,
        "now_wib",
        lambda: dt.datetime(2026, 11, 25, 17, 0, tzinfo=screener.marketcal.WIB),
    )
    called = []
    with pytest.raises(RuntimeError):
        screener.scan(
            force=False,
            downloader=lambda syms: called.append(syms) or {},
            symbols=["BBCA"],
        )
    assert called == [["BBCA"]]


def test_bandar_call_with_stale_propagates_to_sektor_and_pasar(tmp_path, monkeypatch):
    monkeypatch.setattr(bandar, "CACHE_DIR", str(tmp_path))
    bandar.clear_cache()
    sample = [{"IndexCode": "IDXFINANCE", "IndexName": "Financials", "IndexPercent": 1.2}]
    monkeypatch.setattr(
        bandar,
        "get_index_summary",
        lambda: (setattr(bandar._stale_local, "age", 7200.0) or sample),
    )
    rows, age = asyncio.run(asyncio.to_thread(bandar.call_with_stale, bandar.get_index_summary))
    assert rows == sample and age == 7200.0

    bot._last_used.clear()
    upd = _FakeUpdate("/sektor", cid=401, uid=401)
    asyncio.run(h_sektor.do(upd, [], bot))
    assert "cache terakhir" in upd.effective_message.replies[-1][0]

    monkeypatch.setattr(h_pasar.data, "fetch_history", lambda *_a, **_kw: None)
    monkeypatch.setattr(h_pasar.news, "fetch_market_news", lambda *_a, **_kw: [])
    rep = asyncio.run(h_pasar.gather_market_data(bot, include_news=False))
    txt = pasarmod.format_market_report(rep)
    assert "memakai cache terakhir" in txt


def test_news_ara_case_sensitivity_and_onnx_id2label(tmp_path, monkeypatch):
    s_person, _ = news.score_title("Menteri Ara tinjau proyek perumahan")
    assert s_person == 0.0
    s_ara, hits_ara = news.score_title("Saham XYZ langsung ARA")
    assert s_ara == pytest.approx(2.0)
    assert "ARA" in hits_ara

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"id2label": {"0": "negative", "1": "neutral", "2": "positive"}}), encoding="utf-8")
    monkeypatch.setattr(news_ml, "LOCAL_MODEL_DIR", str(tmp_path))
    assert news_ml._id_to_label(object()) == {0: "negative", 1: "neutral", 2: "positive"}


def test_relatif_empty_ind_risk_nan_corr_and_atomic_reduce():
    empty_df = pd.DataFrame()
    good_df = analysis.analyze("BBCA", make_prices(np.linspace(8000, 9000, 260)))["ind"]
    items = [{"ticker": f"T{i}", "ind": good_df.copy()} for i in range(8)]
    items.append({"ticker": "EMPTY", "ind": empty_df})
    out = relatif.score_universe(items)
    assert out[-1]["rel_ok"] is False

    r1 = pd.Series(np.random.default_rng(1).normal(0, 0.01, 70), index=pd.bdate_range("2025-01-01", periods=70))
    r2 = pd.Series(np.random.default_rng(2).normal(0, 0.01, 70), index=pd.bdate_range("2026-01-01", periods=70))
    pos = [
        {"ticker": "AAA", "lots": 10, "sector": "Energy"},
        {"ticker": "BBB", "lots": 10, "sector": "Finance"},
    ]
    quotes = {
        "AAA": {"close": 5000.0, "rets": r1},
        "BBB": {"close": 5000.0, "rets": r2},
    }
    summ = risk.portfolio_risk(pos, quotes)
    assert summ["corr_avg"] is None

    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE real_positions (chat_id TEXT, ticker TEXT, lots INTEGER, avg_price REAL, sector TEXT, updated_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE real_trades (chat_id TEXT, ticker TEXT, side TEXT, lots INTEGER, price REAL, trade_date TEXT, created_at TEXT)"
        )
        conn.execute("INSERT INTO real_positions VALUES ('1', 'BBCA', 5, 9000, 'Fin', '2026-01-01')")
        res = store_portfolio._reduce_real_position_conn(conn, 1, "BBCA", 5, 9500.0, "2026-01-02", "2026-01-02")
        assert res["lots_left"] == 0
        # Panggilan kedua gagal karena baris sudah terhapus (simulasi race)
        with pytest.raises(ValueError):
            store_portfolio._reduce_real_position_conn(conn, 1, "BBCA", 5, 9500.0, "2026-01-02", "2026-01-02")


def test_data_fetch_cached_history_reads_mem_without_network(monkeypatch):
    data.clear_cache()
    df = make_prices(np.linspace(8000, 9000, 40))
    data._mem_put(("BBCA.JK", "1d", "1y"), df)
    got = data.fetch_cached_history("BBCA", "1y")
    assert got is not None and len(got) == 40


def test_wave4_reports_atr_store_migration_news_ekuitas_and_pending_isolation(tmp_path, monkeypatch):
    from core import guards, portofolio, reports

    # 1) fmt_compact_amount tidak pernah mencetak Rp0M / -Rp0M
    assert reports.fmt_compact_amount(250_000_000) == "Rp250jt"
    assert reports.fmt_compact_amount(-250_000_000) == "-Rp250jt"
    assert reports.fmt_compact_amount(500_000) == "Rp500.000"

    # 2) portofolio.atr14 memakai Wilder RMA selaras analysis.py
    df_atr = make_prices(np.linspace(1000, 1400, 30))
    p_atr = portofolio.atr14(df_atr)
    a_atr = float(analysis.analyze("BBCA", df_atr)["ind"]["atr14"].iloc[-1])
    assert p_atr == pytest.approx(a_atr, rel=1e-6)

    # 3) _migrate_json hanya me-rename store.json setelah commit sukses
    legacy_json = tmp_path / "store.json"
    legacy_json.write_text(json.dumps({"101": {"modal": 5_000_000, "auto": True}}), encoding="utf-8")
    db_file = tmp_path / "store.db"
    monkeypatch.setattr(store, "STORE_DB", str(db_file))
    monkeypatch.setattr(store, "STORE_FILE", str(legacy_json))
    store._ready_for = None
    assert store.get_modal(101) == 5_000_000
    assert not legacy_json.exists()
    assert (tmp_path / "store.json.migrated").exists()

    # 4) news.score_title: "Rasio utang terhadap ekuitas turun" -> positif
    sc_eq, _ = news.score_title("Rasio utang terhadap ekuitas turun signifikan")
    assert sc_eq > 0

    # 5) PENDING_OWNERS mencegah user lain di grup mengambil alih PENDING, dan /command membersihkan PENDING
    bot.PENDING.clear()
    bot.ALLOWED_IDS.clear()
    guards._pending_set(700, "analisis", user_id=10)
    upd_other = _FakeUpdate("BBCA", cid=700, uid=20)
    asyncio.run(bot.menu_text_input(upd_other, None))
    assert bot._pending_get(700) == "analisis"

    called_cmd = []

    @bot.timed
    async def _fake_cmd(upd, *_a):
        called_cmd.append(upd.effective_chat.id)

    asyncio.run(_fake_cmd(_FakeUpdate("/cek BBCA", cid=700, uid=10)))
    assert called_cmd == [700]
    assert bot._pending_get(700) is None


def test_jobs_auto_chats_filters_by_allowed_ids(tmp_store, monkeypatch):
    store.set_auto(101, True)
    store.set_auto(202, True)
    monkeypatch.setattr(bot, "ALLOWED_IDS", {101})
    chats = asyncio.run(h_jobs._auto_chats(bot))
    assert [int(c) for c in chats] == [101]
