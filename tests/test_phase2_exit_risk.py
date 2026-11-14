"""Tes komprehensif Wave 2A: Sizing defensif, Panduan EXIT/JUAL, SL/Trailing/Ledger, MAE/MFE/R-multiple."""
from __future__ import annotations

import datetime as dt
import sqlite3

import numpy as np
import pandas as pd
import pytest

from core import jurnal, portofolio, pricing, rencana, risk, saran, store
from tests.conftest import make_prices


def _ohlcv_trend(start: str, closes: list[float], *, spread_pct: float = 0.015) -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=len(closes))
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "open": c * 0.998,
            "high": c * (1.0 + spread_pct),
            "low": c * (1.0 - spread_pct),
            "close": c,
            "volume": np.full(len(c), 5_000_000.0),
        },
        index=idx,
    )


# ------------------------------------------------------------ 1. core/saran.py
def test_fill_pending_menghormati_risk_scale_defensif(tmp_store):
    closes = [1000.0 + i * 2 for i in range(15)]
    df = _ohlcv_trend("2026-11-02", closes)
    # Catat dua posisi identik: satu normal, satu rezim defensif (0.5x)
    saran.queue_paper(
        1,
        [{"ticker": "NORM", "entry": 1000.0, "sl": 970.0, "tp": 1060.0, "atr": 30.0, "risk_scale": 1.0}],
        "2026-11-02",
    )
    saran.queue_paper(
        2,
        [{"ticker": "DEFN", "entry": 1000.0, "sl": 970.0, "tp": 1060.0, "atr": 30.0, "risk_scale": 0.5}],
        "2026-11-02",
    )
    msgs_norm = saran.fill_pending(1, {"NORM": df}, 10_000_000)
    msgs_defn = saran.fill_pending(2, {"DEFN": df}, 10_000_000)

    rec_norm = store.get_paper(1)["NORM"]
    rec_defn = store.get_paper(2)["DEFN"]
    assert len(msgs_norm) == 1 and len(msgs_defn) == 1
    assert rec_norm["lots"] == 33  # 1% x 10jt / 30 = 3333 lbr -> 33 lot
    assert rec_defn["lots"] == 16  # 0.5% x 10jt / 30 = 1666 lbr -> 16 lot
    assert rec_defn["risk_scale"] == pytest.approx(0.5)
    assert "defensif" in msgs_defn[0].lower()

    # Override market_regime langsung ke fill_pending juga memotong 0.5x
    saran.queue_paper(
        3,
        [{"ticker": "OVRD", "entry": 1000.0, "sl": 970.0, "tp": 1060.0, "atr": 30.0}],
        "2026-11-02",
    )
    saran.fill_pending(
        3, {"OVRD": df}, 10_000_000,
        market_regime={"status": "DEFENSIVE", "exposure_scale": 0.5},
    )
    assert store.get_paper(3)["OVRD"]["lots"] == 16


def test_min_hold_cycles_menahan_timeout_dan_catat_sl_awal(tmp_store):
    df = _ohlcv_trend("2026-10-01", [1000.0 + i for i in range(10)])
    saran.queue_paper(
        10,
        [{"ticker": "HOLD", "entry": 1000.0, "sl": 950.0, "tp": 1200.0, "atr": 50.0}],
        "2026-10-01",
    )
    saran.fill_pending(10, {"HOLD": df}, 10_000_000)  # fill 2026-10-02

    # 25 hari bursa (>= MAX_HOLD_DAYS=20, tapi < MIN_HOLD_CYCLES * MAX_HOLD_DAYS = 40): tahan!
    msgs_mid = saran.update_paper(10, {"HOLD": 1010.0}, today=dt.date(2026, 11, 10))
    assert msgs_mid == []
    assert store.get_paper(10)["HOLD"]["status"] == "open"

    # >= 40 hari bursa: baru ditutup karena batas waktu MIN_HOLD_CYCLES
    msgs_exp = saran.update_paper(10, {"HOLD": 1010.0}, today=dt.date(2026, 12, 5))
    assert len(msgs_exp) == 1
    rec = store.get_paper(10)["HOLD"]
    assert rec["hit"] == "timeout"
    assert "siklus" in msgs_exp[0].lower()

    # SL tetap dieksekusi sebelum MIN_HOLD_CYCLES, dengan alasan jelas
    saran.queue_paper(
        11,
        [{"ticker": "CUT1", "entry": 1000.0, "sl": 950.0, "tp": 1200.0, "atr": 50.0}],
        "2026-10-01",
    )
    saran.fill_pending(11, {"CUT1": df}, 10_000_000)
    msgs_sl = saran.update_paper(11, {"CUT1": 940.0}, today=dt.date(2026, 10, 6))
    assert len(msgs_sl) == 1
    assert "sebelum masa tahan" in msgs_sl[0].lower()
    assert "stop_loss_sebelum_min_hold" in store.get_paper(11)["CUT1"]["exit_reason"]

    # hold_review menghormati MIN_HOLD_CYCLES bila filled_at tercatat
    open_recs = {
        "CUT2": {"status": "open", "filled_at": "2026-10-02", "sector": "Energy"},
    }
    notes = saran.hold_review(
        open_recs,
        {"CUT2": {"rel_ok": True, "rel_pct": 15.0, "rel_quintil": "Q1"}},
        today=dt.date(2026, 10, 20),
    )
    assert notes[0]["action"] == "TAHAN"
    assert "masa tahan minimum" in notes[0]["reason"].lower()


@pytest.mark.parametrize("entry", [150.0, 400.0, 1000.0, 3500.0, 9000.0])
def test_breakeven_stop_menutup_biaya_bei_dan_sah_fraksi_tick(entry):
    bep = saran.breakeven_stop(entry)
    assert bep > entry
    assert bep % pricing.tick_size(bep) == 0
    assert saran.pnl_pct(entry, bep) >= 0.0


def test_chandelier_trailing_ratcheting_dan_fallback(monkeypatch):
    # Reli kuat lalu pullback ringan: trailing stop ratcheting naik dan tidak boleh turun
    closes = [1000.0 + i * 25 for i in range(28)] + [1640.0, 1620.0, 1610.0, 1600.0]
    df = _ohlcv_trend("2026-09-01", closes, spread_pct=0.01)

    peak_slice = df.iloc[:28]
    r_peak = saran.chandelier_trailing_stop(
        peak_slice, since_date="2026-09-01", length=22, mult=3.0,
    )
    r_pull = saran.chandelier_trailing_stop(
        df, prev_stop=r_peak["stop"], since_date="2026-09-01", length=22, mult=3.0,
    )
    assert r_peak["stop"] is not None
    assert r_peak["source"] == "pandas_ta_ce"
    assert r_pull["stop"] >= r_peak["stop"]
    assert r_pull["stop"] % pricing.tick_size(r_pull["stop"]) == 0

    # Fallback pure-Python ketika histori < 22 bar atau pandas_ta gagal
    short_df = df.iloc[-8:]
    r_short = saran.chandelier_trailing_stop(short_df, length=22, mult=3.0)
    assert r_short["stop"] is not None
    assert r_short["source"] == "chandelier_fallback"

    monkeypatch.setattr(saran, "_chandelier_pandas_ta", lambda *a, **k: None)
    r_fb = saran.chandelier_trailing_stop(df, length=22, mult=3.0, since_date="2026-09-01")
    assert r_fb["stop"] is not None and r_fb["source"] == "chandelier_fallback"

    # Data hilang -> jujur tidak tersedia
    r_none = saran.chandelier_trailing_stop(None)
    assert r_none["stop"] is None and r_none["source"] == "tidak_tersedia"


def test_scale_out_plan_dan_single_lot():
    multi = saran.scale_out_plan(1000.0, 40.0, lots=6, close=1065.0)
    assert multi["can_scale_out"] is True
    assert multi["lots_tp1"] == 3 and multi["lots_runner"] == 3
    assert multi["tp1"] == 1060.0  # 1000 + 1.5*40
    assert multi["tp_full"] == 1080.0
    assert multi["bep_sl"] == saran.breakeven_stop(1000.0)
    assert multi["tp1_reached"] is True

    single = saran.scale_out_plan(1000.0, 40.0, lots=1, close=1010.0)
    assert single["can_scale_out"] is False
    assert single["lots_tp1"] == 0 and single["lots_runner"] == 1
    assert "minimal 2 lot" in single["note"].lower()


def test_time_stop_status_dan_arb_gap_risk_dan_guidance():
    assert saran.time_stop_status(None)["action"] == "TIDAK_TERSEDIA"
    t_hold = saran.time_stop_status("2026-11-02", today=dt.date(2026, 11, 16))
    assert t_hold["triggered"] is False and t_hold["action"] == "TAHAN"

    t_rot = saran.time_stop_status("2026-10-01", today=dt.date(2026, 11, 12), pnl_net_pct=0.5)
    assert t_rot["triggered"] is True
    assert t_rot["days_held"] >= saran.MAX_HOLD_DAYS
    assert t_rot["action"] == "PERTIMBANGKAN ROTASI/KELUAR"

    gap = saran.arb_gap_risk(1000.0, 900.0, price=1000.0)
    assert gap["sl_exceeds_arb"] is True
    assert gap["arb_floor"] == 930.0
    assert any("7%" in w for w in gap["warnings"])

    pem = saran.arb_gap_risk(50.0, 47.0, price=48.0)
    assert pem["near_pemantauan"] is True and pem["below_rp50"] is True
    assert any("rp50" in w.lower() and "rp1" in w.lower() for w in pem["warnings"])

    df = _ohlcv_trend("2026-09-01", [1000.0 + i * 10 for i in range(35)], spread_pct=0.01)
    guide = saran.build_exit_guidance(
        "BBCA", 1000.0, lots=4, atr=40.0, close=1200.0,
        df=df, entry_date="2026-09-01", today=dt.date(2026, 10, 20),
    )
    assert guide["bep_sl"] >= 1005.0
    assert guide["trailing_sl"] is not None
    assert guide["effective_sl"] >= guide["bep_sl"]


# ------------------------------------------------------------ 2. core/rencana.py
def _cand(ticker="BBCA", sector="Financials", price=1000.0, atr=30.0, rel_pct=90.0):
    return {
        "ticker": ticker, "name": f"PT {ticker}", "board": "Utama",
        "sector": sector, "price": price, "atr": atr, "rel_pct": rel_pct,
        "warnings": [], "vetoes": [],
    }


def test_budget_plan_defensif_dan_custom_limits_dan_real_positions(tmp_store):
    store.set_risk_limits(100, 0.20, 0.35)  # 20% posisi, 35% sektor
    store.add_real_position(100, "BBCA", 15, 1000.0, "2026-11-01", "Financials")  # Rp1,5jt (15%)

    # Rezim defensif 0.5x menurunkan anggaran efektif 5jt -> 2.5jt dan risiko 1% -> 0.5%
    plan_def = rencana.budget_plan(
        _cand("TLKM", "Infra"), 5_000_000, 10_000_000,
        market_regime={"status": "DEFENSIVE", "exposure_scale": 0.5},
    )
    assert plan_def["ok"] is True
    assert plan_def["budget"] == 2_500_000
    assert plan_def["lots"] == 24
    assert plan_def["lots_risk"] == 16  # 0.5% x 10jt / 30 = 166 lbr -> 16 lot

    # Sisa batas BBCA = (20% x 10jt) - 1.5jt = 500rb -> maksimal hanya 5 lot meski budget 3jt
    plan_cap = rencana.budget_plan(
        _cand("BBCA", "Financials"), 3_000_000, 10_000_000, chat_id=100,
    )
    assert plan_cap["ok"] is True
    assert plan_cap["lots"] == 5

    # Tambah 5 lot lagi -> sudah tepat 20%, tidak boleh tambah lagi (max_lots=0)
    store.add_real_position(100, "BBCA", 5, 1000.0, "2026-11-02", "Financials")
    plan_full = rencana.budget_plan(
        _cand("BBCA", "Financials"), 1_000_000, 10_000_000, chat_id=100,
    )
    assert plan_full["ok"] is False and plan_full["lots"] == 0


def test_finalize_menghormati_custom_limits_dan_posisi_eksisting(tmp_store):
    store.set_risk_limits(101, 0.15, 0.30)  # 15% posisi, 30% sektor
    store.add_real_position(101, "BBRI", 15, 1000.0, "2026-11-01", "Financials")  # 1.5jt Financials
    assert risk.get_risk_limits(101) == (0.15, 0.30)

    cands = [
        _cand("BMRI", "Financials", price=1000.0, atr=10.0, rel_pct=95.0),
        _cand("BBNI", "Financials", price=1000.0, atr=10.0, rel_pct=92.0),
        _cand("ADRO", "Energy", price=1000.0, atr=10.0, rel_pct=90.0),
    ]
    out = rencana.finalize(cands, 10_000_000, max_plans=5, chat_id=101)
    tickers = [p["ticker"] for p in out["picks"]]
    assert tickers == ["BMRI", "ADRO"]  # BBNI ditolak karena Financials sudah 15% (BBRI) + 15% (BMRI) = 30%
    assert all(p["lots"] == 15 for p in out["picks"])
    assert out["rejected"]["batas posisi/sektor portofolio penuh"] == 1


def test_rencana_teks_rp50_dan_arb_gap_warning():
    _, rej = rencana.prefilter([{"ticker": "TIDU", "signal": "BELI", "price": 45}])
    assert any("Rp1" in k and "Rp50" in k for k in rej)

    lv = rencana.price_levels(_cand("WIDE", price=1000.0, atr=100.0))  # SL -10% > 7% ARB
    assert lv["arb_gap_risk"] is True
    plan = rencana.make_plan(_cand("WIDE", price=1000.0, atr=100.0), 10_000_000)
    assert any("7%" in w for w in plan["warnings"])


# ------------------------------------------------------------ 3. store & portofolio
def test_store_ledger_terpadu_dan_open_executions_by_ticker(tmp_store):
    store.add_real_position(
        200, "BBCA", 5, 9000.0, "2026-10-01", "Financials",
        sl=8800.0, tp=9600.0,
    )
    assert store.open_execution_lots(200, "BBCA") == 5
    oe = store.open_executions_by_ticker(200)
    assert oe["BBCA"]["sl"] == 8800.0
    assert oe["BBCA"]["tp"] == 9600.0
    assert oe["BBCA"]["trade_date"] == "2026-10-01"

    sell_res = store.reduce_real_position(200, "BBCA", 5, 9500.0, "2026-10-10")
    assert sell_res["realized"] == 250_000
    assert sell_res["fee_rp"] > 0
    assert sell_res["net_realized"] == sell_res["realized"] - sell_res["fee_rp"]
    assert store.open_executions_by_ticker(200) == {}

    # Siklus kedua terbuka menggantikan catatan SL/tanggal siklus lama yang sudah tutup
    store.record_execution_buy(
        200, "BBCA", 3, 9200.0, "2026-11-02", sl=9000.0, tp=9700.0, source="rencana",
    )
    oe2 = store.open_executions_by_ticker(200)
    assert oe2["BBCA"]["lots"] == 3
    assert oe2["BBCA"]["sl"] == 9000.0
    assert oe2["BBCA"]["trade_date"] == "2026-11-02"


def test_signal_context_isolasi_intent_beli_vs_jual_dan_migrasi(tmp_path, monkeypatch):
    # Simulasikan DB v6 lama sebelum kolom intent & sell_payload ditambahkan
    db = tmp_path / "old_v6.db"
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
        CREATE TABLE chat_settings (chat_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (chat_id, key));
        PRAGMA user_version = 6;
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(store, "STORE_DB", str(db))
    store._ready_for = None

    store.save_signal_context(
        10, "BBCA", "rencana", 9000.0, sl=8800.0, tp=9600.0,
        suggested_lots=4, trade_date="2026-11-10",
    )
    # Tanya jual tidak boleh menimpa rencana beli
    store.save_signal_context(
        10, "BBCA", "tanya_jual", 9300.0, sl=9100.0, tp=9700.0,
        suggested_lots=None, trade_date="2026-11-12",
    )

    beli_ctx = store.get_signal_context(10, "BBCA", intent="beli")
    default_ctx = store.get_signal_context(10, "BBCA")
    jual_ctx = store.get_signal_context(10, "BBCA", intent="jual")

    assert beli_ctx["source"] == "rencana" and beli_ctx["sl"] == 8800.0
    assert default_ctx["source"] == "rencana" and default_ctx["suggested_lots"] == 4
    assert jual_ctx["source"] == "tanya_jual" and jual_ctx["signal_price"] == 9300.0


def test_portofolio_summarize_sl_eksekusi_trailing_dan_timestop(tmp_store):
    store.record_execution_buy(
        300, "BBCA", 4, 9000.0, "2026-09-01",
        sl=8800.0, tp=9800.0, source="rencana", sector="Financials",
    )
    df = make_prices([9000.0 + i * 20 for i in range(40)], start="2026-09-01")
    summary = portofolio.summarize(
        store.real_positions(300),
        {"BBCA": {"close": 9500.0, "atr": 200.0, "df": df}},
        open_execs=store.open_executions_by_ticker(300),
        today=dt.date(2026, 10, 20),
    )
    row = summary["rows"][0]
    assert row["sl"] == 8800.0
    assert row["sl_source"] == "eksekusi"
    assert row["tp"] == 9800.0
    assert row["trailing_sl"] is not None
    assert row["time_stop_flag"] is True
    assert row["time_stop_action"] == "PERTIMBANGKAN ROTASI/KELUAR"
    assert row["days_held"] >= 20


# ------------------------------------------------------------ 4. core/jurnal.py
def test_jurnal_mae_mfe_r_multiple_dan_fee_hurdle(tmp_store):
    dates = pd.bdate_range("2026-11-02", periods=6)
    df = pd.DataFrame(
        {
            "open": [1000, 990, 1020, 1080, 1100, 1070],
            "high": [1010, 1020, 1060, 1120, 1110, 1080],
            "low": [985, 970, 1005, 1040, 1060, 1045],
            "close": [1005, 1015, 1055, 1105, 1075, 1060],
        },
        index=dates,
    )
    exc = jurnal.compute_trade_excursions(
        df, entry_price=1000.0, exit_price=1060.0, sl=950.0,
        entry_date="2026-11-02", exit_date="2026-11-09", lots=5,
    )
    assert exc["ok"] is True
    assert exc["mae_pct"] == pytest.approx(-3.0, abs=0.01)
    assert exc["mfe_pct"] == pytest.approx(12.0, abs=0.01)
    assert exc["mae_r"] == pytest.approx(-0.6, abs=0.01)
    assert exc["mfe_r"] == pytest.approx(2.4, abs=0.01)
    assert exc["r_multiple_gross"] == pytest.approx(1.2, abs=0.01)
    assert exc["r_multiple"] < exc["r_multiple_gross"]  # dipotong biaya BEI 0.40%
    assert exc["beat_bei_fee"] is True
    assert exc["capture_ratio"] == pytest.approx(0.5, abs=0.01)

    # Tanpa OHLCV / tanpa SL awal: tidak boleh mengarang angka
    honest = jurnal.compute_trade_excursions(None, entry_price=1000.0, exit_price=1060.0, sl=None)
    assert honest["ok"] is False
    assert honest["mae_pct"] is None and honest["mfe_pct"] is None and honest["r_multiple"] is None

    # Trade untung tipis bruto +0.20% kalah oleh biaya BEI 0.40% -> rugi neto
    store.record_execution_buy(400, "BBCA", 5, 1000.0, "2026-11-02", sl=950.0)
    store.record_execution_sell(400, "BBCA", 5, 1002.0, "2026-11-06")
    diag = jurnal.jurnal_diagnostics(store.real_executions(400), {"BBCA": df})
    assert diag["n_trades"] == 1
    assert diag["eaten_by_fee_count"] == 1
    assert diag["beat_fee_count"] == 0
    assert diag["avg_r_multiple"] < 0
    assert any("0,40%" in n or "0.40%" in n for n in diag["notes"])
    txt = jurnal.format_jurnal_diagnostics(diag)
    assert "R-multiple" in txt and "MAE" in txt
