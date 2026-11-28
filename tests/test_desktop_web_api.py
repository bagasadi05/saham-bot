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
    # "LightweightCharts" bisa inline di index.html atau di /static/app.js (split)
    r_js = client.get("/static/app.js")
    combined = r_root.text + (r_js.text if r_js.status_code == 200 else "")
    assert "LightweightCharts" in combined

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


def test_web_pin_kdf_session_dan_lan_wajib_pin(tmp_store, tmp_path, monkeypatch):
    """PIN disimpan sebagai salt+PBKDF2, session token acak, dan LAN tanpa PIN ditolak."""
    monkeypatch.delenv("SAHAMBOT_WEB_PIN", raising=False)
    monkeypatch.setattr(web_api, "_PIN_FILE", tmp_path / "mobile_pin.json")
    monkeypatch.setattr(web_api, "_is_local_request", lambda req: True)  # mulai sebagai lokal
    client = TestClient(web_api.app)

    # set PIN dari lokal -> file berisi salt + pin_kdf (bukan hash polos / PIN mentah)
    r = client.post("/api/mobile-pin", json={"pin": "1234"})
    assert r.status_code == 200 and r.json()["pin_enabled"] is True
    rec = __import__("json").loads((tmp_path / "mobile_pin.json").read_text(encoding="utf-8"))
    assert "salt" in rec and "pin_kdf" in rec and "pin_hash" not in rec
    assert "1234" not in (tmp_path / "mobile_pin.json").read_text(encoding="utf-8")
    assert web_api._verify_pin("1234") and not web_api._verify_pin("9999")

    # simulasi request non-lokal
    monkeypatch.setattr(web_api, "_is_local_request", lambda req: False)
    # tanpa LAN pun, non-lokal tanpa session ditolak 401
    r = client.get("/api/profile?chat_id=x")
    assert r.status_code == 401
    # PIN salah ditolak
    r = client.post("/api/auth/unlock", json={"pin": "0000"})
    assert r.status_code == 401
    # PIN benar -> cookie session token (bukan hash PIN)
    r = client.post("/api/auth/unlock", json={"pin": "1234"})
    assert r.status_code == 200
    cookie = r.cookies.get(web_api._AUTH_COOKIE)
    assert cookie and cookie != rec["pin_kdf"]
    client.cookies.set(web_api._AUTH_COOKIE, cookie)
    r = client.get("/api/profile?chat_id=x")
    assert r.status_code == 200

    # LAN aktif TANPA PIN -> non-lokal 503 (API tidak dibuka ke jaringan)
    monkeypatch.setattr(web_api, "_PIN_FILE", tmp_path / "kosong.json")
    monkeypatch.setenv("SAHAMBOT_LAN", "1")
    client.cookies.clear()
    r = client.get("/api/profile?chat_id=x")
    assert r.status_code == 503
    monkeypatch.delenv("SAHAMBOT_LAN", raising=False)


def test_stockbit_bridge_whitelist_read_only(tmp_store):
    """Bridge Stockbit hanya mengizinkan tool read-only; nama lain ditolak."""
    from desktop_web import stockbit_bridge

    assert stockbit_bridge.ALLOWED_TOOLS == frozenset(
        {"quote", "orderbook", "bandar_detector", "keystats"}
    )
    # tool non-whitelist (mis. order) ditolak tanpa menjalankan proses
    assert stockbit_bridge._client.call_tool("place_order", {"side": "BUY"}) is None
    assert stockbit_bridge._client.call_tool("withdraw", {}) is None
    # bridge default nonaktif tanpa env opt-in
    import os

    os.environ.pop("SAHAMBOT_STOCKBIT_MCP", None)
    snap = stockbit_bridge.fetch_stockbit_snapshot("BBCA")
    assert snap["available"] is False


def test_desktop_web_static_import_benchmark_and_alert_scanner(tmp_store, monkeypatch):
    client = TestClient(web_api.app)

    # Modular static assets (/static/styles.css & /static/app.js)
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200

    # Bulk CSV/Paste execution import
    r_imp = client.post(
        "/api/execution/import",
        json={
            "chat_id": "modern-import-test",
            "raw_text": "ticker,side,price,lots\nBBCA, BELI, 5900, 1, uji impor\nBADLINE",
        },
    )
    assert r_imp.status_code == 200
    assert r_imp.json()["imported"] == 1
    assert len(r_imp.json()["errors"]) == 1

    # Portfolio vs IHSG benchmark (offline mocked)
    df_ihsg = make_prices([5000.0 + i * 5 for i in range(40)], volume=10_000_000)
    df_bbca = make_prices([5800.0 + i * 10 for i in range(40)], volume=2_000_000)
    bm = web_api.analysis_enhancer.compute_portfolio_vs_ihsg(
        [{"ticker": "BBCA", "lots": 2}],
        days=30,
        fetch_fn=lambda sym, raw=False: df_ihsg if raw else df_bbca,
    )
    assert bm["ok"] is True
    assert len(bm["series"]) >= 20

    # Live alerts + portfolio SL/TP scanner (offline mocked)
    monkeypatch.setattr(
        web_api.dm,
        "evaluate_portfolio_rows",
        lambda cid, **kw: [
            {
                "ticker": "BRIS",
                "lots": 1,
                "price": 1090.0,
                "effective_sl": 1100.0,
                "scale_out_tp1": 1245.0,
                "tp": 1275.0,
                "thesis": "MELEMAH",
                "reasons": [],
            },
        ],
    )
    r_chk = client.post("/api/alerts/check?chat_id=modern-import-test")
    assert r_chk.status_code == 200
    assert any(h["ticker"] == "BRIS" and h["severity"] == "danger" for h in r_chk.json()["triggered"])


def test_unlock_rate_limit_dan_lockout(tmp_store, tmp_path, monkeypatch):
    """Brute-force /api/auth/unlock dibatasi 5 gagal lalu lockout HTTP 429 + Retry-After."""
    monkeypatch.delenv("SAHAMBOT_WEB_PIN", raising=False)
    monkeypatch.setattr(web_api, "_PIN_FILE", tmp_path / "mobile_pin.json")
    monkeypatch.setattr(web_api, "_is_local_request", lambda req: True)
    web_api._reset_unlock_rate_limit()
    client = TestClient(web_api.app)

    assert client.post("/api/mobile-pin", json={"pin": "4321"}).status_code == 200
    monkeypatch.setattr(web_api, "_is_local_request", lambda req: False)

    # 4 percobaan salah pertama -> 401 dengan sisa percobaan
    for idx in range(1, web_api._UNLOCK_MAX_ATTEMPTS):
        r = client.post("/api/auth/unlock", json={"pin": "0000"})
        assert r.status_code == 401
        assert f"Sisa percobaan: {web_api._UNLOCK_MAX_ATTEMPTS - idx}" in r.json()["detail"]

    # Percobaan salah ke-5 -> 429 + Retry-After
    r_lock = client.post("/api/auth/unlock", json={"pin": "0000"})
    assert r_lock.status_code == 429
    assert int(r_lock.headers.get("Retry-After", "0")) > 0
    assert r_lock.json()["retry_after"] > 0

    # Selama lockout, PIN yang benar sekalipun tetap ditolak 429
    r_blocked = client.post("/api/auth/unlock", json={"pin": "4321"})
    assert r_blocked.status_code == 429

    # Reset lockout (mis. waktu lockout habis atau PIN diperbarui dari PC) -> sukses 200
    web_api._reset_unlock_rate_limit()
    r_ok = client.post("/api/auth/unlock", json={"pin": "4321"})
    assert r_ok.status_code == 200
    assert r_ok.cookies.get(web_api._AUTH_COOKIE)


def test_index_esc_helper_dan_sinkronisasi_profil_default(tmp_store):
    """index.html menyediakan helper esc() dan /api/profile sinkron dengan opsi default core."""
    from core import desktop_helpers as dh
    from core import desktop_monitor as dm
    from core import store_profile

    client = TestClient(web_api.app)
    r_root = client.get("/")
    assert r_root.status_code == 200
    assert "window.esc" in r_root.text
    assert "function esc(" in r_root.text

    r_prof = client.get("/api/profile")
    assert r_prof.status_code == 200
    body = r_prof.json()
    assert body["chat_id"] == dm.DEFAULT_PROFILE_ID
    assert body["default_profile_id"] == dm.DEFAULT_PROFILE_ID
    assert body["modal"] == dh.DEFAULT_MODAL_RP
    assert body["default_modal"] == dh.DEFAULT_MODAL_RP
    assert body["refresh"] == store_profile.DEFAULT_DESKTOP_UI_STATE["refresh"]
    assert body["refresh_options"] == dict(dh.VALID_REFRESH_MIN)

    # Persistensi refresh valid + sanitasi nilai invalid
    r_save = client.post(
        "/api/profile",
        json={"chat_id": "sync-test", "modal": 15_000_000, "syariah": True, "refresh": "30m"},
    )
    assert r_save.status_code == 200
    assert r_save.json()["refresh"] == "30m"
    assert client.get("/api/profile?chat_id=sync-test").json()["refresh"] == "30m"

    r_bad = client.post(
        "/api/profile",
        json={"chat_id": "sync-test", "modal": 15_000_000, "syariah": True, "refresh": "999m"},
    )
    assert r_bad.status_code == 200
    assert r_bad.json()["refresh"] == store_profile.DEFAULT_DESKTOP_UI_STATE["refresh"]


def test_scan_lock_tersinkronisasi_antara_api_dan_core(tmp_store, monkeypatch):
    """Lock /api/scan berbagi _SCAN_LOCK/_SCAN_STATE yang sama dengan core.desktop_monitor."""
    import time
    from core import desktop_monitor as dm

    assert web_api._scan_lock is dm._SCAN_LOCK
    assert web_api._scan_state is dm._SCAN_STATE
    dm.finish_market_scan()

    client = TestClient(web_api.app)

    # 1. Jika core sudah mengklaim lock scan, POST /api/scan tidak memulai scan ganda
    acquired, st = dm.try_begin_market_scan(started_at=12345.0)
    assert acquired is True
    try:
        r_dup = client.post("/api/scan")
        assert r_dup.status_code == 200
        assert r_dup.json()["running"] is True
        assert r_dup.json()["started_at"] == 12345.0

        r_core_dup = dm.run_full_market_scan(force=True)
        assert r_core_dup["ok"] is False and r_core_dup["running"] is True

        r_st = client.get("/api/scan/status")
        assert r_st.status_code == 200 and r_st.json()["running"] is True
    finally:
        dm.finish_market_scan(result={"scanned": 944})

    # 2. Setelah dilepas, POST /api/scan menjalankan scan background dan merilis lock
    called: list[bool] = []
    monkeypatch.setattr(dm.screener, "scan", lambda force=True: (called.append(force), {"scanned": 12, "beli": 2})[1])
    r_go = client.post("/api/scan")
    assert r_go.status_code == 200
    assert r_go.json()["running"] is True

    for _ in range(50):
        st_now = client.get("/api/scan/status").json()
        if not st_now["running"]:
            break
        time.sleep(0.02)
    assert called == [True]
    st_final = client.get("/api/scan/status").json()
    assert st_final["running"] is False
    assert st_final["last_result"]["scanned"] == 12


def test_scan_stream_sse_dan_aset_vendor_lokal(tmp_store):
    """SSE /api/scan/stream mengirim event status dan aset vendor statis tersedia offline."""
    from core import desktop_monitor as dm

    dm.finish_market_scan(result={"scanned": 42})
    client = TestClient(web_api.app)

    with client.stream("GET", "/api/scan/stream") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        body = "".join(resp.iter_text())
    assert "event: status" in body
    assert '"running": false' in body
    assert '"scanned": 42' in body

    for fname in (
        "tailwindcss.js",
        "lucide.min.js",
        "lightweight-charts.standalone.production.js",
        "qrcode.min.js",
    ):
        r = client.get(f"/static/vendor/{fname}")
        assert r.status_code == 200, f"Vendor {fname} gagal disajikan"
        assert len(r.content) > 1000


def test_scan_mode_cepat_dan_progres_paralel(tmp_store, monkeypatch):
    """POST /api/scan?mode=fast memakai Papan Utama, melacak progres live, & fetch_frames paralel."""
    import time
    import numpy as np
    from core import desktop_monitor as dm, screener, universe
    from tests.conftest import make_prices

    dm.finish_market_scan()
    main_codes = universe.main_board_codes()
    assert len(main_codes) > 100
    assert len(main_codes) < len(universe.codes())

    captured: dict = {}

    def _fake_scan(force=True, symbols=None, progress=None):
        captured["symbols"] = list(symbols or [])
        if progress:
            progress(len(captured["symbols"]) // 2, len(captured["symbols"]), 10)
            progress(len(captured["symbols"]), len(captured["symbols"]), 20)
        return {"scanned": len(captured["symbols"]), "beli": 3}

    monkeypatch.setattr(dm.screener, "scan", _fake_scan)
    client = TestClient(web_api.app)

    r = client.post("/api/scan?mode=fast")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "fast"
    assert body["total"] == len(main_codes)

    for _ in range(50):
        st = client.get("/api/scan/status").json()
        if not st["running"]:
            break
        time.sleep(0.02)

    assert captured["symbols"] == main_codes
    st_end = client.get("/api/scan/status").json()
    assert st_end["running"] is False
    assert st_end["phase"] == "done"
    assert st_end["pct"] == 100.0
    assert st_end["ok"] == 20

    # Uji fetch_frames paralel (workers=2) secara offline
    df_syn = make_prices(np.linspace(1000, 1200, 40))

    def _fake_dl(batch, period):
        import pandas as pd
        return pd.concat({f"{s}.JK": df_syn for s in batch}, axis=1)

    monkeypatch.setattr(screener, "_download_chunk", _fake_dl)
    ticks: list[tuple[int, int, int]] = []
    out = screener.fetch_frames(
        ["BBCA", "BBRI", "BMRI", "TLKM"], chunk=2, pause=0, workers=2,
        progress=lambda d, t, o: ticks.append((d, t, o)),
    )
    assert set(out.keys()) == {"BBCA", "BBRI", "BMRI", "TLKM"}
    assert ticks[-1] == (4, 4, 4)


def test_hapus_inputan_beli_jual_dan_posisi_sinkron(tmp_store):
    """DELETE /api/execution/{id} & DELETE /api/portfolio/{ticker} menjaga integritas ledger."""
    from core import store_portfolio

    client = TestClient(web_api.app)
    cid = "hapus-test"

    # 1. Catat BELI 10 lot @ 1000, lalu BELI lagi 10 lot @ 1200 (avg=1100, total=20 lot)
    r1 = client.post("/api/execution", json={"chat_id": cid, "ticker": "BBCA", "side": "BELI", "lots": 10, "price": 1000})
    r2 = client.post("/api/execution", json={"chat_id": cid, "ticker": "BBCA", "side": "BELI", "lots": 10, "price": 1200})
    assert r1.status_code == 200 and r2.status_code == 200

    # Catat JUAL 15 lot @ 1300 (sisa 5 lot @ 1100)
    r3 = client.post("/api/execution", json={"chat_id": cid, "ticker": "BBCA", "side": "JUAL", "lots": 15, "price": 1300})
    assert r3.status_code == 200

    execs = store_portfolio.real_executions(cid, limit=10)
    assert len(execs) == 3
    sell_id = next(e["id"] for e in execs if e["side"] == "JUAL")
    buy2_id = next(e["id"] for e in execs if e["side"] == "BELI" and e["exec_price"] == 1200)

    # 2. Hapus BELI kedua saat masih ada JUAL 15 lot -> harus ditolak (400) karena 15 > 10 lot tersisa
    r_bad = client.delete(f"/api/execution/{buy2_id}?chat_id={cid}")
    assert r_bad.status_code == 400
    assert len(store_portfolio.real_executions(cid, limit=10)) == 3

    # 3. Hapus JUAL salah input -> posisi kembali pulih ke 20 lot @ avg 1100
    r_del_sell = client.delete(f"/api/execution/{sell_id}?chat_id={cid}")
    assert r_del_sell.status_code == 200
    body_sell = r_del_sell.json()
    assert body_sell["result"]["lots_left"] == 20
    assert body_sell["result"]["avg_price"] == 1100.0

    # 4. Sekarang hapus BELI kedua (10 lot @ 1200) -> sisa 10 lot @ avg 1000
    r_del_buy = client.delete(f"/api/execution/{buy2_id}?chat_id={cid}")
    assert r_del_buy.status_code == 200
    assert r_del_buy.json()["result"]["lots_left"] == 10
    assert r_del_buy.json()["result"]["avg_price"] == 1000.0

    # 5. Hapus seluruh posisi & riwayat BBCA via DELETE /api/portfolio/BBCA
    r_del_pos = client.delete(f"/api/portfolio/BBCA?chat_id={cid}")
    assert r_del_pos.status_code == 200
    assert store_portfolio.real_positions(cid) == []
    assert store_portfolio.real_executions(cid, limit=10) == []

    # 6. Hapus lagi -> 404
    assert client.delete(f"/api/portfolio/BBCA?chat_id={cid}").status_code == 404
    assert client.delete(f"/api/execution/999999?chat_id={cid}").status_code == 404
