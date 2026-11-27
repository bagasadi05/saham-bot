"""Tes offline untuk FastAPI backend SahamBot Modern Desktop (`desktop_web/api.py`)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from core import analysis
from desktop_web import api as web_api
from tests.conftest import make_prices


def test_desktop_web_root_and_profile_endpoints(tmp_store):
    client = TestClient(web_api.app)

    r_root = client.get("/")
    assert r_root.status_code == 200
    assert "SAHAMBOT" in r_root.text
    assert "LightweightCharts" in r_root.text

    r_prof = client.get("/api/profile?chat_id=modern-test")
    assert r_prof.status_code == 200
    assert r_prof.json()["chat_id"] == "modern-test"

    r_save = client.post(
        "/api/profile",
        json={"chat_id": "modern-test", "modal": 25000000, "syariah": True},
    )
    assert r_save.status_code == 200
    assert r_save.json()["modal"] == 25000000
    assert r_save.json()["syariah"] is True


def test_desktop_web_market_watchlist_and_cek_offline(tmp_store, monkeypatch):
    client = TestClient(web_api.app)

    monkeypatch.setattr(
        web_api.dm,
        "fetch_market_pulse",
        lambda **kw: {
            "condition": "WASPADA",
            "as_of_label": "26 Nov 2026 19:00 WIB",
            "index_stale_age": 900.0,
            "ihsg": {"ok": True, "close": 5231.0, "ret1d": 0.64, "ret20d": -2.5, "regime": {"status": "DEFENSIVE", "exposure_scale": 0.2}},
            "breadth": {"ok": True, "advancing": 277, "declining": 337, "total": 619, "beli": 5},
            "sectors": {"ok": False},
            "sectors_fallback": {"ok": True, "source": "snapshot /scan", "sectors": [{"name": "Keuangan", "pct": 1.1, "n": 20}]},
            "news": {"n": 1, "label": "POSITIF", "pct": 40.0, "details": [{"title": "IHSG menguat", "sent": 0.6}]},
        },
    )
    r_mkt = client.get("/api/market")
    assert r_mkt.status_code == 200
    body = r_mkt.json()
    assert body["stale_age_text"] == "15 mnt lalu"
    assert body["sectors_fallback"]["ok"] is True

    # Add & remove watchlist
    assert client.post("/api/watchlist", json={"chat_id": "modern-test", "ticker": "BBCA"}).status_code == 200
    assert client.delete("/api/watchlist/BBCA?chat_id=modern-test").status_code == 200

    # Cek endpoint with candles serialization
    df = make_prices([9000.0 + i * 10 for i in range(60)], volume=2_000_000)
    ind = analysis.compute_indicators(df)
    monkeypatch.setattr(
        web_api.dm,
        "inspect_ticker_detail",
        lambda sym, modal, **kw: {
            "ok": True,
            "ticker": sym,
            "name": "Bank Central Asia",
            "sector": "Keuangan",
            "board": "Utama",
            "syariah": False,
            "res": {"ind": ind, "snap": analysis.last_snapshot(ind)},
            "cek": {"verdict": "LAYAK", "points": 6, "bull_points": ["Tren naik"], "bear_points": [], "items": []},
            "powerscore": {"rating": "BUY", "pct": 45},
            "plan": {"ok": True, "ticker": sym, "entry": 9500, "sl": 9200, "tp": 10100, "lots": 2, "buy_cost": 1900000, "max_loss": 60000, "max_loss_pct": 3.1, "rr": 2.0},
        },
    )
    r_cek = client.get("/api/cek/BBCA?chat_id=modern-test&modal=10000000")
    assert r_cek.status_code == 200
    cek_json = r_cek.json()
    assert cek_json["ticker"] == "BBCA"
    assert len(cek_json["candles"]) == 60
    assert cek_json["plan_levels"]["entry"] == "9.500"
    assert cek_json["plan_display"]["entry"] == "Rp9.500"
    assert cek_json["poc_60d"] is not None
    assert "BBRI" in cek_json["peers"]


def test_desktop_web_alerts_health_and_portfolio_offline(tmp_store, monkeypatch):
    client = TestClient(web_api.app)

    r_hp = client.get("/api/health")
    assert r_hp.status_code == 200
    assert r_hp.json()["app"] == "sahambot-desktop"

    r_uni = client.get("/api/universe?q=BBCA")
    assert r_uni.status_code == 200
    assert any(it["code"] == "BBCA" for it in r_uni.json()["items"])

    # PWA manifest, icon & mobile access info
    r_mf = client.get("/manifest.json")
    assert r_mf.status_code == 200
    assert r_mf.json()["short_name"] == "SahamBot"
    r_ic = client.get("/icon.png")
    assert r_ic.status_code == 200
    assert r_ic.headers["content-type"] == "image/png"
    r_mi = client.get("/api/mobile-info")
    assert r_mi.status_code == 200
    assert r_mi.json()["is_local"] is True

    # Alerts CRUD
    r_add = client.post(
        "/api/alerts",
        json={"chat_id": "modern-test", "ticker": "BBCA", "kinds": ["breakout", "vol_spike"]},
    )
    assert r_add.status_code == 200
    assert sorted(r_add.json()["kinds"]) == ["breakout", "vol_spike"]

    r_list = client.get("/api/alerts?chat_id=modern-test")
    assert r_list.status_code == 200
    assert r_list.json()["total"] == 1

    r_del = client.delete("/api/alerts/BBCA?chat_id=modern-test")
    assert r_del.status_code == 200
    assert client.get("/api/alerts?chat_id=modern-test").json()["total"] == 0

    # Portfolio summary aggregation
    monkeypatch.setattr(
        web_api.dm,
        "evaluate_portfolio_rows",
        lambda cid, **kw: [
            {
                "ticker": "PBID",
                "lots": 3,
                "avg_price": 620.0,
                "price": 625.0,
                "cost_rp": 186000,
                "value_rp": 187500,
                "pnl_rp": 1500,
                "pnl_net_pct": 0.4,
                "effective_sl": 595.0,
                "thesis": "MENGUAT",
                "reasons": ["tren kuat"],
            }
        ],
    )
    r_port = client.get("/api/portfolio?chat_id=modern-test")
    assert r_port.status_code == 200
    s = r_port.json()["summary"]
    assert s["count"] == 1
    assert s["total_value"] == 187500
    assert s["total_pnl_rp"] == 1500
    assert s["total_risk_sl"] == 9000

    # Stockbit bridge must default OFF in offline CI
    monkeypatch.delenv("SAHAMBOT_STOCKBIT_MCP", raising=False)
    r_sb_off = client.get("/api/stockbit/BRIS")
    assert r_sb_off.status_code == 200
    assert r_sb_off.json()["available"] is False

    # Mocked Stockbit snapshot
    monkeypatch.setattr(
        web_api.stockbit_bridge,
        "fetch_stockbit_snapshot",
        lambda sym: {
            "available": True,
            "symbol": sym,
            "orderbook": {"bids": [{"price": 1165, "lots": 1568, "que": 10}], "offers": [], "bid_pct": 35.1, "fnet": 1181169000},
            "bandar_1d": {"status": "Big Dist", "top_buyers": [], "top_sellers": []},
            "bandar_7d": {"status": "Small Dist", "top_buyers": [], "top_sellers": []},
        },
    )
    r_sb_on = client.get("/api/stockbit/BRIS")
    assert r_sb_on.status_code == 200
    assert r_sb_on.json()["available"] is True
    assert r_sb_on.json()["bandar_1d"]["status"] == "Big Dist"


def test_desktop_web_compare_and_accuracy_offline(tmp_store, tmp_path, monkeypatch):
    client = TestClient(web_api.app)

    # Isolate accuracy tracker to tmp_path
    tracker_file = tmp_path / "rec_tracker.json"
    monkeypatch.setattr(web_api.accuracy_tracker, "DEFAULT_TRACKER_PATH", tracker_file)
    orig_load = web_api.accuracy_tracker._load_records
    orig_rec = web_api.accuracy_tracker.record_recommendation
    monkeypatch.setattr(web_api.accuracy_tracker, "_load_records", lambda path=tracker_file: orig_load(tracker_file))
    monkeypatch.setattr(
        web_api.accuracy_tracker,
        "record_recommendation",
        lambda *a, **kw: orig_rec(*a, **{**kw, "path": tracker_file}),
    )

    r_rec = client.post(
        "/api/accuracy/record",
        json={
            "ticker": "BRIS",
            "verdict": "TUNGGU",
            "entry_low": 1150,
            "entry_high": 1165,
            "sl": 1100,
            "tp1": 1240,
            "regime": "DEFENSIVE",
            "power_score": 40,
        },
    )
    assert r_rec.status_code == 200
    assert r_rec.json()["record"]["ticker"] == "BRIS"

    r_acc = client.get("/api/accuracy")
    assert r_acc.status_code == 200
    assert r_acc.json()["summary"]["total_records"] == 1

    # Compare endpoint (offline mocked inspect_ticker_detail)
    monkeypatch.delenv("SAHAMBOT_STOCKBIT_MCP", raising=False)
    monkeypatch.setattr(
        web_api.dm,
        "inspect_ticker_detail",
        lambda sym, modal, **kw: {
            "ticker": sym,
            "name": f"PT {sym} Tbk",
            "sector": "Financial Services",
            "syariah": sym == "BRIS",
            "res": {"snap": {"close": 1165.0 if sym == "BRIS" else 5900.0, "ret1d": 0.5, "ret20d": 4.2, "rsi": 52.0}},
            "cek": {"verdict": "LAYAK", "points": 7},
            "powerscore": {"pct": 68, "rating": "BUY"},
            "fund": {"pct": 80, "fcf_yield": 5.5, "ev_ebitda": 8.0},
            "plan": {"entry": 1160, "sl": 1100, "tp": 1280, "rr": 2.0},
        },
    )
    r_cmp = client.get("/api/compare?tickers=BBCA,BRIS")
    assert r_cmp.status_code == 200
    items = r_cmp.json()["items"]
    assert len(items) == 2
    assert {it["ticker"] for it in items} == {"BBCA", "BRIS"}

    # Fair value + simple summary + backtest endpoint (offline)
    from desktop_web import analysis_enhancer as ae

    fv_bank = ae.compute_fair_value(
        1165.0,
        {"pbv": 1.1, "roe": 18.0, "roe_unit": "pct", "sector": "Financial Services"},
        {"forensic_penalty": 0},
        "Financial Services",
    )
    assert fv_bank["available"] is True
    assert fv_bank["fair_value"] > 1165
    assert fv_bank["mos_pct"] > 0

    fv_missing = ae.compute_fair_value(1000.0, {}, {}, "Energy")
    assert fv_missing["available"] is False

    monkeypatch.setattr(
        web_api.analysis_enhancer,
        "run_backtest_summary",
        lambda sym: {
            "ok": True,
            "ticker": sym,
            "horizon_days": 20,
            "total_obs": 800,
            "baseline_avg_pct": 0.4,
            "buy_trades": 90,
            "buy_avg_pct": 2.1,
            "buy_win_rate_pct": 58.0,
            "plain_note": "Test OK",
        },
    )
    r_bt = client.get("/api/backtest/BRIS")
    assert r_bt.status_code == 200
    assert r_bt.json()["buy_win_rate_pct"] == 58.0

    # Auto corp-action news detector & pre-buy checklist
    alerts = ae.detect_corp_action_news({
        "details": [
            {"title": "BRIS siapkan Rights Issue 6,8 miliar saham tanpa pembeli siaga"},
            {"title": "Saham ini resmi keluar dari indeks FTSE Russell Desember"},
            {"title": "Emiten umumkan cum date dividen tunai pekan depan"},
        ]
    })
    cats = {a["category"] for a in alerts}
    assert "Rights Issue / HMETD" in cats
    assert "Rebalancing Indeks" in cats
    assert "Dividen / Cum-Ex Date" in cats

    chk = ae.build_prebuy_checklist(
        {
            "fund": {"forensic_penalty": 0},
            "plan": {"ok": True, "lots": 2, "pct_modal": 10.0, "sl_dist_pct": 4.0, "arb_gap_risk": False},
        },
        alerts,
    )
    assert len(chk) == 4
    assert chk[0]["status"] == "bad"  # danger corp action detected
    assert chk[1]["status"] == "ok"   # SL <= 7%
    assert chk[2]["status"] == "ok"   # sizing OK

    # Keystats parser from Stockbit MCP payload
    ks_rows = web_api.stockbit_bridge._simplify_keystats({
        "closure_fin_items_results": [
            {
                "keystats_name": "Current Valuation",
                "fin_name_results": [
                    {"fitem_name": "Current PE Ratio (TTM)", "fitem_value": "6.64"},
                    {"fitem_name": "Forward PE Ratio", "fitem_value": "5.70"},
                ],
            },
            {
                "keystats_name": "Profitability",
                "fin_name_results": [
                    {"fitem_name": "Net Interest Margin (NIM)", "fitem_value": "5.42%"},
                    {"fitem_name": "CASA Ratio", "fitem_value": "65.68%"},
                ],
            },
        ]
    })
    ks_map = {r["key"]: r["value"] for r in ks_rows}
    assert ks_map["pe_ttm"] == "6.64"
    assert ks_map["nim"] == "5.42%"
    assert ks_map["casa"] == "65.68%"

    # Full equity-curve causal backtest simulator (offline synthetic OHLCV)
    prices = [1000.0 + (i * 2.5) + (25.0 if (i % 14) < 7 else -15.0) for i in range(180)]
    df_bt = make_prices(prices, volume=3_000_000)
    sim = ae.simulate_equity_backtest(
        "BBCA",
        period="3y",
        modal=10_000_000,
        sl_pct=5.0,
        tp_pct=10.0,
        max_hold=15,
        strategy="ma_trend",
        df_override=df_bt,
    )
    assert sim["ok"] is True
    assert len(sim["equity_curve"]) > 50
    assert sim["metrics"]["trades_count"] >= 1
    assert sim["metrics"]["initial_capital"] == 10_000_000

    monkeypatch.setattr(web_api.analysis_enhancer, "simulate_equity_backtest", lambda *a, **kw: sim)
    r_sim = client.get("/api/backtest/BBCA/simulate?period=3y&modal=10000000&strategy=ma_trend")
    assert r_sim.status_code == 200
    assert r_sim.json()["metrics"]["trades_count"] >= 1
