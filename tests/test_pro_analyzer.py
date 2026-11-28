"""Tes offline untuk core/pro_analyzer.py (Struktur Pasar, Konfluensi 6 Pilar, Skenario & Kill Criteria)."""
from __future__ import annotations

import numpy as np

from core import analysis, pro_analyzer
from tests.conftest import make_prices


def test_detect_market_structure_uptrend_squeeze_and_short_data():
    # Data terlalu pendek -> jujur available=False
    short_df = make_prices(np.linspace(1000, 1050, 20))
    st_short = pro_analyzer.detect_market_structure(analysis.compute_indicators(short_df))
    assert st_short["available"] is False
    assert st_short["phase"] == "TIDAK TERSEDIA"

    # Uptrend panjang dengan kontraksi volatilitas di akhir (VCP/Squeeze)
    base = np.linspace(1000, 1500, 100) + np.sin(np.linspace(0, 12, 100)) * 35.0
    tight = np.linspace(1500, 1515, 30)
    closes = np.concatenate([base, tight])
    df = make_prices(closes, volume=2_000_000)
    ind = analysis.compute_indicators(df)
    st = pro_analyzer.detect_market_structure(ind)
    assert st["available"] is True
    assert st["squeeze"] is True
    assert "VCP" in st["phase"] or "MARKUP" in st["phase"]


def test_confluence_matrix_aligned_vs_conflict_and_scenarios():
    closes = np.linspace(1000, 1400, 120)
    df = make_prices(closes, volume=5_000_000)
    ind = analysis.compute_indicators(df)
    detail_bull = {
        "res": {
            "ind": ind,
            "snap": analysis.last_snapshot(ind),
            "score": {"pct": 65.0},
            "weekly": {"direction": "naik"},
        },
        "fund": {"pct": 50.0, "forensic_flags": []},
        "news": {"pct": 40.0, "n": 4},
        "market_regime": {"regime": "BULL"},
        "cmf20": 0.18,
        "rsi_divergence": None,
        "cek": {"vetoes": []},
        "plan": {"entry": 1400.0, "sl": 1310.0, "tp": 1580.0, "atr": 25.0},
    }
    fv_bull = {"available": True, "fair_value": 1800.0, "mos_pct": 22.0}
    out = pro_analyzer.analyze_pro(detail_bull, fair_value=fv_bull)

    cf = out["confluence"]
    assert cf["counts"]["bullish"] >= 5
    assert cf["counts"]["bearish"] == 0
    assert cf["score"] is not None and cf["score"] >= 85.0
    assert "TINGGI" in cf["conviction"]
    assert cf["conflicts"] == []

    sc = out["scenarios"]
    assert sc["available"] is True
    assert len(sc["scenarios"]) == 3
    prices = [x["price"] for x in sc["scenarios"]]
    assert prices[0] < sc["current_price"] < prices[1] < prices[2]
    assert len(sc["kill_criteria"]) >= 2

    # Kasus konflik: teknikal naik tetapi CMF20 distribusi + fundamental bermasalah + weekly turun
    detail_conflict = {
        **detail_bull,
        "res": {**detail_bull["res"], "weekly": {"direction": "turun"}},
        "cmf20": -0.22,
        "fund": {"pct": -30.0, "forensic_flags": ["ocf_negatif", "laba_turun_beruntun"]},
    }
    out_c = pro_analyzer.analyze_pro(detail_conflict, fair_value={"available": False})
    assert len(out_c["confluence"]["conflicts"]) >= 3
    assert "KONFLIK" in out_c["confluence"]["conviction"] or "RISIKO" in out_c["confluence"]["conviction"]


def test_telegram_jurnal_hapus_dan_health_flags(tmp_store):
    """Perintah /jurnal hapus <id> sinkronkan portofolio & /api/health laporkan fitur aktif."""
    import asyncio
    from types import SimpleNamespace
    from fastapi.testclient import TestClient

    from core import jurnal as jurnalmod, store
    from desktop_web import api as web_api
    from handlers import jurnal as hj

    cid = 778899
    store.record_execution_buy(cid, "BBCA", 5, 9000.0, "2026-11-27")
    execs = store.real_executions(cid, limit=5)
    assert len(execs) == 1
    eid = execs[0]["id"]

    # format_journal_history menyertakan #ID
    hist = jurnalmod.format_journal_history(execs)
    assert f"#{eid}" in hist
    assert "/jurnal hapus" in hist

    replies: list[str] = []

    class _FakeMsg:
        async def reply_text(self, text, **kwargs):
            replies.append(text)

    upd = SimpleNamespace(effective_message=_FakeMsg(), effective_chat=SimpleNamespace(id=cid))
    deps = SimpleNamespace()
    asyncio.run(hj.do_jurnal(upd, ["hapus", str(eid)], deps))
    assert any("dihapus" in r and "BBCA" in r for r in replies)
    assert store.real_positions(cid) == []

    # /api/health melaporkan pro_analyzer & vendor_offline_ready
    client = TestClient(web_api.app)
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["pro_analyzer"] is True
    assert body["vendor_offline_ready"] is True


def test_score_candidate_precision_grades_and_penalties():
    # Kandidat premium A+ (relatif tinggi, uptrend sehat, RSI ideal, likuid, fundamental kuat)
    top = pro_analyzer.score_candidate_precision(
        {
            "rel_pct": 88.0, "price": 1500.0, "sma50": 1400.0, "sma200": 1300.0,
            "rsi": 56.0, "macd_hist": 5.0, "ret20d": 6.5,
            "value_avg20": 25_000_000_000, "board": "Utama", "quality_ok": True,
        },
        fund={"pct": 45.0, "forensic_flags": []},
        nsent={"pct": 35.0, "n": 3},
    )
    assert top["grade"] == "A+"
    assert top["score"] >= 82.0

    # Kandidat jebakan (death cross + chase trap + FCA + forensik) -> HINDARI
    trap = pro_analyzer.score_candidate_precision(
        {
            "rel_pct": 25.0, "price": 100.0, "sma50": 120.0, "sma200": 150.0,
            "rsi": 78.0, "ret20d": 22.0, "value_avg20": 300_000_000,
            "board": "Pemantauan Khusus", "quality_ok": False,
        },
        fund={"pct": -30.0, "forensic_flags": ["ocf_negatif", "laba_turun_beruntun"]},
        nsent={"pct": -40.0, "n": 4},
    )
    assert trap["grade"] == "HINDARI"
    assert trap["score"] < 40.0

    html_out = pro_analyzer.format_pro_summary_html({
        "structure": {"available": True, "phase": "KONSOLIDASI VCP", "structure": "HH + HL", "squeeze": True},
        "confluence": {"score": 88.0, "conviction": "KEYAKINAN TINGGI", "counts": {"bullish": 5, "neutral": 1, "bearish": 0}, "conflicts": []},
        "scenarios": {"available": True, "scenarios": [
            {"case": "Bear Case", "price": 1300, "pct": -7.1},
            {"case": "Base Case", "price": 1550, "pct": 10.7},
            {"case": "Bull Case", "price": 1750, "pct": 25.0},
        ]},
    })
    assert "ANALISATOR PRO" in html_out
    assert "VCP/SQUEEZE" in html_out
    assert "88.0/100" in html_out


def test_quality_gate_bank_piotroski_and_bei_red_flag_veto():
    from core import cek, fundamental

    # 1. Jalur Bank 6 poin (BRIS via override)
    ov_bris = fundamental.load_corp_action_override("BRIS")
    assert ov_bris is not None
    qg_bank = fundamental.compute_quality_gate({"sector": "Financial Services", "symbol": "BRIS.JK"}, ov=ov_bris)
    assert qg_bank["mode"] == "bank"
    assert qg_bank["passed"] >= 4
    assert qg_bank["label"] == "KUAT"

    # 2. Jalur Non-Keuangan Piotroski F-Score
    qg_nonfin = fundamental.compute_quality_gate({
        "sector": "Consumer Defensive",
        "net_income": 500e9,
        "operating_cashflow": 650e9,
        "free_cashflow": 450e9,
        "der": 40.0,
        "current_ratio": 2.1,
        "margin": 14.0,
        "roe": 18.0,
        "roe_unit": "pct",
        "rev_trend": [("Q1", 100), ("Q2", 110), ("Q3", 125)],
        "profit_trend": [("Q1", 15), ("Q2", 18), ("Q3", 22)],
    })
    assert qg_nonfin["mode"] == "piotroski"
    assert qg_nonfin["passed"] >= 7
    assert qg_nonfin["label"] == "KUAT"

    # 3. Radar Red Flag BEI 2026: suspensi free float Peng-S-00039 -> veto HINDARI di /cek
    df = make_prices(np.linspace(1000, 1200, 60), volume=10_000_000)
    ind = analysis.compute_indicators(df)
    res_akku = {
        "ticker": "AKKU",
        "ind": ind,
        "score": analysis.score_stock(ind),
        "snap": analysis.last_snapshot(ind),
        "weekly": {"direction": "naik"},
    }
    chk = cek.build(
        res=res_akku, fund=None, fund_raw=None, nsent=None,
        bandar_row=None, agenda=None, agenda_days=None,
        peers=None, value_avg20=10_000_000_000.0,
    )
    assert chk["verdict"] == "HINDARI"
    assert any("free float" in v.lower() for v in chk["vetoes"])


def test_bei_calculator_endpoints_and_portfolio_risk_audit(tmp_store):
    from fastapi.testclient import TestClient
    from desktop_web import api as web_api

    client = TestClient(web_api.app)

    # TERP Right Issue
    r_terp = client.post("/api/calc/bei", json={
        "mode": "terp", "price": 1500, "exercise_price": 1000, "old_ratio": 5, "new_ratio": 2,
    })
    assert r_terp.status_code == 200
    assert r_terp.json()["terp_tick"] == 1355.0 or 1350.0 <= r_terp.json()["terp_tick"] <= 1365.0
    assert r_terp.json()["dilution_pct"] == 28.57

    # Reverse DCF
    r_dcf = client.post("/api/calc/bei", json={
        "mode": "dcf", "price": 2480, "fcf_per_share": 250, "growth_pct": 8, "hurdle_pct": 12, "tg_pct": 4.5,
    })
    assert r_dcf.status_code == 200
    b_dcf = r_dcf.json()
    assert b_dcf["fair_bear"] < b_dcf["fair_base"] < b_dcf["fair_bull"]
    assert "implied_growth_pct" in b_dcf

    # Dividen PMK 18/2021 (0% pajak + deteksi yield >= 7% dividend trap)
    r_div = client.post("/api/calc/bei", json={
        "mode": "div", "price": 3000, "dps": 300, "lots": 2, "reinvest_id": True,
    })
    assert r_div.status_code == 200
    b_div = r_div.json()
    assert b_div["tax_rp"] == 0
    assert b_div["net_rp"] == 60000
    assert b_div["dividend_trap_risk"] is True

    # Audit risiko portofolio tersedia di GET /api/portfolio
    r_port = client.get("/api/portfolio?chat_id=risk-audit-test")
    assert r_port.status_code == 200
    assert "risk_audit" in r_port.json()
    assert r_port.json()["risk_audit"]["max_pos_pct"] > 0
