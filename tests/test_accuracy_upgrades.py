"""Tes offline untuk peningkatan akurasi analisis saham lintas-modul."""
from __future__ import annotations

import numpy as np

from core import (
    analysis,
    cek,
    data as datamod,
    desktop_monitor as dm,
    fundamental,
    kualitas,
    news as newsmod,
    pasar,
    regime,
    screener,
    store_scan,
    verdict,
)
from tests.conftest import make_prices


# ------------------------------------------------------------------ 1. Regime 3-tier
def test_regime_compute_breadth_ma50_and_3tier_scaling():
    assert regime.compute_breadth_ma50([]) is None
    assert regime.compute_breadth_ma50([{"price": 100, "sma50": 90}] * 10) is None

    # 25 emiten: hanya 5 di atas SMA50 -> breadth = 20% (<25% -> scale 0.25)
    rows_weak = [{"price": 110, "sma50": 100}] * 5 + [{"price": 90, "sma50": 100}] * 20
    b_weak = regime.compute_breadth_ma50(rows_weak)
    assert b_weak == 0.2

    ihsg_bull = make_prices(np.linspace(6500, 7500, 260))
    reg_normal = regime.evaluate_ihsg(ihsg_bull, breadth_ma50=0.65)
    assert reg_normal["status"] == "BULL"
    assert reg_normal["exposure_scale"] == 1.0
    assert reg_normal["breadth_ma50_pct"] == 65.0

    reg_mid = regime.evaluate_ihsg(ihsg_bull, breadth_ma50=0.35)
    assert reg_mid["status"] == "DEFENSIVE"
    assert reg_mid["exposure_scale"] == 0.5
    assert "breadth MA50 lemah" in reg_mid["label"]

    reg_deep = regime.evaluate_ihsg(ihsg_bull, breadth_ma50=b_weak)
    assert reg_deep["status"] == "DEFENSIVE"
    assert reg_deep["exposure_scale"] == 0.25
    assert "sangat lemah" in reg_deep["label"]

    # Integrasi otomatis di pasar.build_market_report
    rep = pasar.build_market_report(ihsg_df=ihsg_bull, scan_rows=rows_weak)
    assert rep["ihsg"]["regime"]["status"] == "DEFENSIVE"
    assert rep["ihsg"]["regime"]["exposure_scale"] == 0.25


# ------------------------------------------------------------------ 2. Verdict guardrails
def test_verdict_beli_guardrails_block_traps_and_preserve_compat():
    # Kompatibilitas mundur 1 argumen tetap sama
    judul_ok, _ = verdict._verdict_beli(60.0)
    assert "BELI" in judul_ok

    # Falling knife (close < SMA50 & SMA50 < SMA200) -> JANGAN BELI
    j_fk, a_fk = verdict._verdict_beli(60.0, close=900, sma50=1000, sma200=1150)
    assert "JANGAN BELI" in j_fk and "falling knife" in a_fk.lower()

    # Forensik kas berat >= 40 -> JANGAN BELI
    j_f40, _ = verdict._verdict_beli(55.0, forensic_penalty=45.0)
    assert "JANGAN BELI" in j_f40

    # Momentum chase trap (ret20d > 15% & ret1d > 0) -> TUNGGU
    j_chase, a_chase = verdict._verdict_beli(60.0, ret20d=22.0, ret1d=3.0)
    assert "TUNGGU" in j_chase and "jebakan momentum" in a_chase.lower()

    # Peringkat relatif lemah (< 30%) -> TUNGGU
    j_rel, _ = verdict._verdict_beli(55.0, rel_pct=20.0)
    assert "TUNGGU" in j_rel

    # Bearish RSI divergence + CMF distribusi -> TUNGGU
    j_div, _ = verdict._verdict_beli(
        55.0, cmf20=-0.22, rsi_div={"kind": "bearish"},
    )
    assert "TUNGGU" in j_div

    # Rezim IHSG defensif / tren mingguan turun -> downgrade ke BELI BERTAHAP
    j_def, a_def = verdict._verdict_beli(
        60.0, market_regime={"status": "DEFENSIVE"}, weekly={"direction": "turun"},
    )
    assert "BELI BERTAHAP" in j_def
    assert "defensif" in a_def.lower() or "mingguan" in a_def.lower()


def test_build_verdict_surfaces_cmf_and_poc(uptrend_df):
    res = analysis.analyze("TLKM", uptrend_df)
    txt = verdict.build_verdict(
        res,
        fund={"pct": 30.0, "base_pct": 30.0, "label": "CUKUP", "notes": ["PER wajar"]},
        news_sent={"pct": 20.0, "n": 3},
        focus="beli",
        market_regime={"status": "DEFENSIVE", "label": "defensif"},
    )
    assert "Volume POC 60h" in txt
    assert "Rezim IHSG" in txt


# ------------------------------------------------------------------ 3. Cek forensic, ARB 7%, anti-chase
def test_cek_pbv_negatif_der_forensic_arb7_and_chase():
    base_res = {
        "ticker": "TEST",
        "snap": {"close": 2000.0, "ret1d": 0.5, "ret20d": 5.0},
        "score": {"pct": 45.0},
        "weekly": {"direction": "naik", "pct_vs_ma": 3.0},
    }
    base_fund = {"per": 10.0, "pbv": 1.2, "roe": 18.0, "roe_unit": "pct", "sector": "Industrials"}

    # PBV <= 0 -> veto HINDARI
    out_pbv = cek.build(
        res=base_res, fund={"pct": 20.0}, fund_raw={**base_fund, "pbv": -0.5},
        nsent={"pct": 20.0, "n": 3}, bandar_row={"ForeignNet": 2e9},
        agenda=None, agenda_days=None, peers={"ret20d": 1.0, "sector": "Industrials"},
        value_avg20=20e9, board="Utama",
    )
    assert out_pbv["verdict"] == "HINDARI"
    assert any("PBV" in v for v in out_pbv["vetoes"])

    # DER > 300% non-keuangan -> veto HINDARI; bank tidak diveto
    out_der = cek.build(
        res=base_res, fund={"pct": 20.0}, fund_raw={**base_fund, "der": 350.0},
        nsent={"pct": 20.0, "n": 3}, bandar_row={"ForeignNet": 2e9},
        agenda=None, agenda_days=None, peers={"ret20d": 1.0, "sector": "Industrials"},
        value_avg20=20e9, board="Utama",
    )
    assert out_der["verdict"] == "HINDARI"
    assert any("DER" in v for v in out_der["vetoes"])

    out_bank_der = cek.build(
        res=base_res, fund={"pct": 20.0},
        fund_raw={**base_fund, "sector": "Financial Services", "der": 450.0},
        nsent={"pct": 20.0, "n": 3}, bandar_row={"ForeignNet": 2e9},
        agenda=None, agenda_days=None, peers={"ret20d": 1.0, "sector": "Financial Services"},
        value_avg20=20e9, board="Utama",
    )
    assert out_bank_der["verdict"] == "LAYAK"

    # Forensik arus kas berat (>=40 poin) -> veto HINDARI
    out_cf = cek.build(
        res=base_res, fund={"pct": 10.0},
        fund_raw={
            **base_fund,
            "ocf": -200e9, "net_income": 500e9, "fcf": -400e9, "payout_ratio": 1.25,
        },
        nsent={"pct": 20.0, "n": 3}, bandar_row={"ForeignNet": 2e9},
        agenda=None, agenda_days=None, peers={"ret20d": 1.0, "sector": "Industrials"},
        value_avg20=20e9, board="Utama",
    )
    assert out_cf["verdict"] == "HINDARI"
    assert any("forensik" in v.lower() for v in out_cf["vetoes"])

    # BEI 2026 ARB -7% harus terdeteksi di warnings
    arb_res = {**base_res, "snap": {"close": 2000.0, "ret1d": -7.0, "ret20d": -5.0}}
    out_arb = cek.build(
        res=arb_res, fund={"pct": 20.0}, fund_raw=base_fund,
        nsent={"pct": 20.0, "n": 3}, bandar_row={"ForeignNet": 2e9},
        agenda=None, agenda_days=None, peers={"ret20d": 1.0, "sector": "Industrials"},
        value_avg20=20e9, board="Utama",
    )
    assert any("ARB" in w for w in out_arb["warnings"])

    # Momentum chase trap (ret20d > 15 & ret1d > 0) -> cap LAYAK ke TUNGGU
    chase_res = {**base_res, "snap": {"close": 2000.0, "ret1d": 3.5, "ret20d": 21.0}}
    out_chase = cek.build(
        res=chase_res, fund={"pct": 20.0}, fund_raw=base_fund,
        nsent={"pct": 20.0, "n": 3}, bandar_row={"ForeignNet": 2e9},
        agenda=None, agenda_days=None, peers={"ret20d": 1.0, "sector": "Industrials"},
        value_avg20=20e9, board="Utama",
    )
    assert out_chase["verdict"] == "TUNGGU"
    assert any("pullback" in w.lower() for w in out_chase["warnings"])


# ------------------------------------------------------------------ 4. Kualitas forensic & DER
def test_kualitas_rejects_high_der_and_accrual_trap_but_allows_banks():
    k_der = kualitas.judge({
        "per": 10.0, "pbv": 1.2, "roe": 16.0, "sector": "Industrials", "der": 340.0,
    })
    assert k_der["ok"] is False and k_der["label"] == "utang tinggi"

    k_bank = kualitas.judge({
        "per": 12.0, "pbv": 1.8, "roe": 18.0, "sector": "Financial Services", "der": 450.0,
    })
    assert k_bank["ok"] is True

    k_ocf = kualitas.judge({
        "per": 10.0, "pbv": 1.2, "roe": 16.0, "sector": "Energy",
        "ocf": -100e9, "net_income": 500e9,
    })
    assert k_ocf["ok"] is False and k_ocf["label"] == "arus kas lemah"


# ------------------------------------------------------------------ 5. Screener precision
def test_screener_holds_bearish_div_dist_and_micro_price(monkeypatch):
    sig, reason = screener.classify(
        120, 115, 110, 60, 0.5, 5, 85, 5, True, 50,
        cmf20=-0.20, rsi_div={"kind": "bearish"},
    )
    assert sig == "TAHAN"
    assert "divergensi" in reason.lower()

    sig_ok, reason_ok = screener.classify(
        120, 115, 110, 60, 0.5, 5, 85, 5, True, 50, cmf20=0.20,
    )
    assert sig_ok == "BELI"
    assert "akumulasi CMF" in reason_ok

    # Saham harga < Rp50 ditahan dari BELI saat evaluate
    monkeypatch.setattr(screener, "classify", lambda *a, **k: ("BELI", "uji"))
    penny_df = make_prices(np.linspace(30, 40, 80), start="2026-06-01", volume=50_000_000)
    rows, _ = screener.evaluate({"GOCAP": penny_df})
    assert rows[0]["signal"] == "TAHAN"
    assert "Rp50" in rows[0]["reason"]


# ------------------------------------------------------------------ 6. Desktop advanced & watchlist/port sync
def test_desktop_advanced_signals_and_watchlist_guardrail(monkeypatch, tmp_store, uptrend_df):
    store_scan.save_market_scan(
        [{
            "ticker": "TLKM", "name": "Telkom", "board": "Utama",
            "trade_date": "2026-11-25", "price": 3000, "ret1d": 1.0, "ret5d": 2.0,
            "ret20d": 4.0, "rsi": 55.0, "macd_hist": 10.0, "sma20": 2950,
            "sma50": 2900, "sma200": 2800, "atr": 60.0, "vol_avg20": 5e7,
            "value_avg20": 150e9, "tech_score": 70.0, "rel_pct": 88.0,
            "signal": "BELI", "reason": "kuat", "entry": 3000, "sl": 2940, "tp": 3120,
        }],
        {"trade_date": "2026-11-25"},
    )
    monkeypatch.setattr(
        datamod, "fetch_history",
        lambda sym, period="1y", raw_symbol=False: uptrend_df.copy(),
    )
    monkeypatch.setattr(fundamental, "_fetch_uncached", lambda sym: {
        "symbol": sym, "per": 12.0, "pbv": 1.5, "roe": 18.0, "roe_unit": "pct",
        "sector": "Communication Services", "currency": "IDR",
    })
    monkeypatch.setattr(newsmod, "fetch_news", lambda sym, limit=8: [])

    det = dm.inspect_ticker_detail("TLKM", 20_000_000, chat_id="desktop")
    adv = det["advanced"]
    assert adv["rel_pct"] == 88.0
    assert adv["poc"] is not None
    assert adv["expected_move"] is not None
    assert adv["tp_sl_odds"] is not None

    # Watchlist guardrail: jika rel_pct dari scan rendah (<40%), tidak boleh langsung BELI
    ind = analysis.compute_indicators(uptrend_df)
    act, rsn = dm._classify_watch_action(3000.0, 60.0, ind, 3000.0, 2900.0, rel_pct=25.0)
    assert act == "TAHAN"
    assert "relatif lemah" in rsn.lower()
