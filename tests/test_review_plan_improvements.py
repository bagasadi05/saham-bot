"""Tes offline untuk perbaikan R1-R3 dari PLAN-PERBAIKAN-REVIEW.md."""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import sqlite3
import types
import urllib.request

import pandas as pd
import pytest

import bot
import config
from core import bandar, data, fundamental, guards, news_ml, pricing, store
from handlers import analisis as analisis_h
from handlers import info as info_h
from handlers import jobs as jobs_h
from handlers import market as market_h
from handlers import menu as menu_h
from handlers import sinyal as sinyal_h
from handlers import trading as trading_h
from research import quality_combo
from tools import backup_db


class _SpySemaphore(asyncio.Semaphore):
    def __init__(self, value: int = 4):
        super().__init__(value)
        self.entries = 0

    async def __aenter__(self):
        self.entries += 1
        return await super().__aenter__()


class _FakeMsg:
    def __init__(self, text: str = "", cid: int = 100):
        self.text = text
        self.chat_id = cid
        self.replies: list[tuple[str, dict]] = []
        self.edits: list[tuple[str, dict]] = []
        self.photos: list[dict] = []

    async def reply_text(self, text: str, **kwargs):
        self.replies.append((text, kwargs))
        return self

    async def edit_text(self, text: str, **kwargs):
        self.edits.append((text, kwargs))
        return self

    async def reply_photo(self, **kwargs):
        self.photos.append(kwargs)
        return self


class _FakeQuery:
    def __init__(self, cb_data: str, uid: int = 100, cid: int = 100):
        self.data = cb_data
        self.from_user = types.SimpleNamespace(id=uid)
        self.message = _FakeMsg(cid=cid)
        self.answers: list[tuple[tuple, dict]] = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))


class _FakeChat:
    def __init__(self, cid: int = 100):
        self.id = cid

    async def send_action(self, *_a, **_kw):
        return None


def _upd(text: str = "", uid: int = 100, cid: int = 100, query=None):
    return types.SimpleNamespace(
        effective_user=types.SimpleNamespace(id=uid),
        effective_chat=_FakeChat(cid),
        callback_query=query,
        effective_message=query.message if query else _FakeMsg(text=text, cid=cid),
    )


def test_yahoo_429_circuit_breaker_short_circuits_and_resets(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(data, "_YF_COOLDOWN_SEC", 120.0)
    data.clear_cache()

    calls = {"yf": 0}

    def boom_download(*_a, **_kw):
        calls["yf"] += 1
        raise RuntimeError("429 Too Many Requests: Rate limited")

    monkeypatch.setattr(data.yf, "download", boom_download)
    with pytest.raises(ValueError, match="429"):
        data.fetch_history("BBCA", period="6mo")
    assert calls["yf"] == 1
    assert data.is_yahoo_cooled_down() is True
    assert data.cache_stats()["yahoo_cooled_down"] is True

    # Selama breaker aktif, yfinance tidak boleh dipanggil lagi sama sekali.
    res2 = data.fetch_history("BBRI", period="6mo")
    assert res2 is None
    assert calls["yf"] == 1

    # Jika ada cache disk saat breaker aktif, fallback disk tetap melayani.
    dates = pd.bdate_range("2025-10-01", periods=10)
    cached_df = pd.DataFrame(
        {
            "open": [4000.0] * 10,
            "high": [4050.0] * 10,
            "low": [3950.0] * 10,
            "close": [4000.0] * 10,
            "volume": [1_000_000.0] * 10,
        },
        index=dates,
    )
    data._save_cache("TLKM.JK", cached_df, interval="1d", period="6mo")
    hit = data.fetch_history("TLKM", period="6mo")
    assert hit is not None and len(hit) == 10
    assert calls["yf"] == 1

    data.clear_cache()
    assert data.is_yahoo_cooled_down() is False


def test_fundamental_disk_cache_hit_and_stale_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(fundamental, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(fundamental, "_FUND_TTL", 3600.0)
    monkeypatch.setattr(fundamental, "_STALE_MAX_SEC", 86400.0)
    fundamental.clear_cache()

    payload = {
        "_v": fundamental.CACHE_VERSION,
        "name": "Bank Central Asia",
        "sector": "Financial Services",
        "currency": "IDR",
        "per": 20.0,
        "pbv": 4.2,
        "div_yield": 0.025,
        "roe": 21.0,
        "roe_unit": "pct",
    }
    fetch_count = {"n": 0}

    def fake_uncached(_symbol: str):
        fetch_count["n"] += 1
        return dict(payload)

    monkeypatch.setattr(fundamental, "_fetch_uncached", fake_uncached)
    first = fundamental.fetch_fundamentals("BBCA")
    assert first is not None and fetch_count["n"] == 1

    # Kosongkan RAM cache: disk cache masih segar -> tidak memanggil Yahoo.
    fundamental.clear_cache()
    second = fundamental.fetch_fundamentals("BBCA")
    assert second is not None and second["name"] == "Bank Central Asia"
    assert fetch_count["n"] == 1

    # Offline reader tidak pernah menyentuh jaringan.
    assert fundamental.fetch_cached_fundamentals("BBCA")["roe"] == 21.0

    # Simulasikan disk cache kedaluwarsa TTL + Yahoo rate-limit -> stale fallback.
    disk_file = next(tmp_path.glob("BBCA.JK*.json"))
    raw = json.loads(disk_file.read_text(encoding="utf-8"))
    raw["ts"] -= 7200.0
    disk_file.write_text(json.dumps(raw), encoding="utf-8")
    fundamental.clear_cache()

    def rl_fail(_symbol: str):
        raise RuntimeError("429 Too Many Requests")

    monkeypatch.setattr(fundamental, "_fetch_uncached", rl_fail)
    stale = fundamental.fetch_fundamentals("BBCA")
    assert stale is not None
    assert stale.get("fund_stale") is True
    assert "Cache lokal fundamental" in (stale.get("filing_note") or "")


def test_bandar_idx_direct_fetch_opt_out_blocks_scraper(tmp_path, monkeypatch):
    monkeypatch.setattr(bandar, "CACHE_DIR", str(tmp_path))
    bandar.clear_cache()
    monkeypatch.setenv("IDX_DIRECT_FETCH", "0")
    monkeypatch.setattr(
        bandar,
        "_scraper",
        lambda: pytest.fail("cloudscraper tidak boleh dipanggil saat IDX_DIRECT_FETCH=0"),
    )
    with pytest.raises(bandar.IDXTidakTersedia, match="IDX_DIRECT_FETCH=0"):
        bandar.get_stock_summary(ttl=0)


def test_pricing_window_expiry_warning():
    assert pricing.window_expiry_warning(days_warn=21, day=dt.date(2026, 10, 1)) is None
    near = pricing.window_expiry_warning(days_warn=21, day=dt.date(2026, 12, 20))
    assert near is not None and "2026-12-31" in near and "11 hari lagi" in near
    expired = pricing.window_expiry_warning(days_warn=21, day=dt.date(2027, 1, 5))
    assert expired is not None and "sudah lewat" in expired


def test_status_public_lock_and_telemetry(tmp_store, monkeypatch):
    ctx = types.SimpleNamespace(bot=types.SimpleNamespace(), job_queue=None)

    # ALLOW_PUBLIC=1 tanpa ADMIN_IDS -> /status wajib ditolak.
    monkeypatch.setenv("ALLOW_PUBLIC", "1")
    monkeypatch.setattr(bot, "ADMIN_IDS", set())
    upd_pub = _upd("/status", uid=999, cid=999)
    asyncio.run(info_h.do_status(upd_pub, ctx, bot))
    assert "mode publik" in upd_pub.effective_message.replies[-1][0]

    # Mode admin aktif -> tampilkan breaker Yahoo, telemetry sentimen & peringatan BEI.
    monkeypatch.setenv("ALLOW_PUBLIC", "0")
    monkeypatch.setattr(bot, "ADMIN_IDS", {100})
    data.clear_cache()
    data.mark_yahoo_rate_limited(RuntimeError("429 Too Many Requests"))
    news_ml.clear_usage()
    news_ml.record_usage("ml", 2)
    news_ml.record_usage("lexicon", 3)
    monkeypatch.setattr(
        pricing,
        "window_expiry_warning",
        lambda days_warn=21, day=None: "Jendela BEI berakhir 2026-12-31 (5 hari lagi)",
    )

    upd_ok = _upd("/status", uid=100, cid=100)
    asyncio.run(info_h.do_status(upd_ok, ctx, bot))
    msg_txt = upd_ok.effective_message.replies[-1][0]
    assert "Yahoo circuit-breaker" in msg_txt
    assert "pakai ML 2, lexicon 3" in msg_txt
    assert "2026-12-31" in msg_txt
    data.clear_cache()


def test_burst_ok_menu_heavy_and_text_tanya_throttling(tmp_store, monkeypatch):
    guards.clear_state()
    q_upds = [_upd(query=_FakeQuery("run:saran", uid=55, cid=55)) for _ in range(4)]

    async def _run_burst():
        return [
            await guards.burst_ok(u, "unit_bucket", limit=3, window=10.0)
            for u in q_upds
        ]

    res = asyncio.run(_run_burst())
    assert res == [True, True, True, False]
    assert "Terlalu cepat" in q_upds[-1].callback_query.answers[-1][0][0]

    # Menu callback: 30 heavy callbacks lolos, ke-31 kena throttle; navigasi menu ringan tetap jalan.
    guards.clear_state()
    bot.ALLOWED_IDS.clear()
    ctx = types.SimpleNamespace(bot=types.SimpleNamespace(), job_queue=None)

    async def _run_menu_burst():
        monkeypatch.setattr(menu_h, "_handle_run", lambda *_a, **_kw: asyncio.sleep(0))
        outcomes = []
        for _ in range(31):
            q = _FakeQuery("run:sinyal", uid=77, cid=77)
            await menu_h.menu_callback(_upd(query=q), ctx, bot)
            outcomes.append(q.answers[-1][0][0] if q.answers and q.answers[-1][0] else "ok")
        q_nav = _FakeQuery("menu", uid=77, cid=77)
        await menu_h.menu_callback(_upd(query=q_nav), ctx, bot)
        return outcomes, q_nav

    outcomes, q_nav = asyncio.run(_run_menu_burst())
    assert outcomes[:30] == ["ok"] * 30
    assert "Terlalu cepat" in outcomes[30]
    assert q_nav.message.edits

    # Free-text parse_tanya burst guard: 8 lolos, ke-9 diblokir.
    guards.clear_state()
    monkeypatch.setattr(bot, "parse_tanya", lambda _txt: ("beli", "BBCA"))
    called = {"tanya": 0}

    async def _fake_tanya(*_a, **_kw):
        called["tanya"] += 1

    monkeypatch.setattr(bot, "_do_tanya", _fake_tanya)

    async def _run_tanya_burst():
        last_upd = None
        for _ in range(9):
            last_upd = _upd("BBCA layak beli?", uid=88, cid=88)
            await menu_h.menu_text_input(last_upd, ctx, bot)
        return last_upd

    last = asyncio.run(_run_tanya_burst())
    assert called["tanya"] == 8
    assert "Terlalu cepat" in last.effective_message.replies[-1][0]


def test_global_heavy_semaphore_wired_to_heavy_handlers(tmp_store, monkeypatch):
    bot.ALLOWED_IDS.clear()
    bot._last_used.clear()
    spy = _SpySemaphore(2)
    monkeypatch.setattr(bot, "global_heavy_semaphore", lambda: spy)

    async def _fake_saran_scan(_cid, _univ, _prog=None):
        return "Hasil saran dummy"

    monkeypatch.setattr(bot, "saran_scan_text", _fake_saran_scan)
    upd_saran = _upd("/saran", uid=101, cid=101)
    asyncio.run(trading_h.do_saran(upd_saran, bot, sub="", from_menu=True))
    assert spy.entries == 1

    monkeypatch.setattr(
        store,
        "load_market_scan",
        lambda: [{"ticker": "BBCA", "signal": "BELI", "price": 9000, "atr": 150, "rel_pct": 95}],
    )
    monkeypatch.setattr(store, "market_scan_meta", lambda: {"scanned_at": "2026-11-26T16:15:00", "trade_date": "2026-11-26"})
    monkeypatch.setattr(store, "load_plan_cache", lambda _k: None)
    monkeypatch.setattr(store, "save_plan_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(bot, "regime", types.SimpleNamespace(fetch_ihsg_regime=lambda: {"regime": "BULL"}))

    async def _fake_build_plan(_rows, _meta):
        return {"enriched": [{"ticker": "BBCA", "price": 9000, "entry": 9000, "sl": 8850, "tp": 9300}], "rejected": {}}

    monkeypatch.setattr(bot, "_build_plan_payload", _fake_build_plan)
    upd_paket = _upd("/paket", uid=101, cid=101)
    asyncio.run(market_h.do_paket(upd_paket, [], bot))
    assert spy.entries == 2

    async def _boom_fetch(_t):
        raise ValueError("Ticker tidak ditemukan")

    monkeypatch.setattr(bot, "fetch_and_analyze", _boom_fetch)
    monkeypatch.setattr(bot, "banding", types.SimpleNamespace(market_context=lambda: None))
    upd_an = _upd("/analisis ZZZZ", uid=101, cid=101)
    asyncio.run(analisis_h.do_analisis(upd_an, ["ZZZZ"], bot))
    assert spy.entries == 3

    monkeypatch.setattr(bot, "tokens", lambda _u: ["BBCA", "BBRI"])
    upd_cmp = _upd("/banding BBCA BBRI", uid=101, cid=101)
    ctx = types.SimpleNamespace(bot=types.SimpleNamespace())
    asyncio.run(analisis_h.cmd_banding(upd_cmp, ctx, bot))
    assert spy.entries == 4


def test_heartbeat_ping_fail_soft_and_backup_verify(tmp_path, tmp_store, monkeypatch):
    hb = tmp_path / "hb.txt"
    pinged: list[str] = []

    class _OkResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    def fake_urlopen(req, timeout=4):
        pinged.append(req.full_url)
        return _OkResp(b"ok")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(config, "HEARTBEAT_FILE", str(hb))
    monkeypatch.setattr(config, "HEALTHCHECK_PING_URL", "https://hc-ping.invalid/abc")
    app = types.SimpleNamespace()
    asyncio.run(jobs_h.heartbeat(app, bot))
    assert hb.exists()
    assert pinged == ["https://hc-ping.invalid/abc"]

    # Gagal ping tidak boleh melempar exception.
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("offline")))
    asyncio.run(jobs_h.heartbeat(app, bot))

    # Backup CLI --verify lulus pada DB sehat, dan gagal pada file korup.
    dest = tmp_path / "baks"
    assert backup_db.main([str(dest), "--keep", "2", "--verify"]) == 0
    bak_file = next(dest.glob("store-*.db"))
    assert backup_db.verify_backup(str(bak_file)).lower() == "ok"

    bad_db = tmp_path / "bad.db"
    bad_db.write_bytes(b"not a sqlite file")
    with pytest.raises((sqlite3.DatabaseError, RuntimeError)):
        backup_db.verify_backup(str(bad_db))


def test_quality_combo_offline_and_regime_context(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "_CACHE_DIR", str(tmp_path / "ohlcv"))
    monkeypatch.setattr(fundamental, "CACHE_DIR", str(tmp_path / "fund"))
    data.clear_cache()
    fundamental.clear_cache()

    dates = pd.bdate_range("2025-10-01", periods=100)
    base = pd.DataFrame(
        {
            "open": [9000.0 + i * 5 for i in range(100)],
            "high": [9050.0 + i * 5 for i in range(100)],
            "low": [8950.0 + i * 5 for i in range(100)],
            "close": [9000.0 + i * 5 for i in range(100)],
            "volume": [2_000_000.0] * 100,
        },
        index=dates,
    )
    data._save_cache("BBCA.JK", base, interval="1d", period="1y")
    fundamental._disk_save(
        "BBCA.JK",
        {
            "_v": fundamental.CACHE_VERSION,
            "per": 15.0,
            "pbv": 2.5,
            "roe": 18.0,
            "roe_unit": "pct",
            "eps": 400.0,
        },
    )

    monkeypatch.setattr(
        quality_combo.train_lgbm,
        "select_universe",
        lambda _u, max_tickers=60: ["BBCA", "TLKM"],
    )
    reg_file = tmp_path / "regime_eval.json"
    reg_file.write_text(json.dumps({"recommended": "sma50_200_half", "modes": {"half": {"alpha": 0.33}}}), encoding="utf-8")
    out_file = tmp_path / "out.json"
    monkeypatch.setattr(quality_combo, "ROOT", tmp_path)

    rc = quality_combo.main(
        ["--universe", "lq45", "--offline", "--regime-file", str(reg_file.name), "--out", str(out_file.name)]
    )
    assert rc == 0
    report = json.loads(out_file.read_text(encoding="utf-8"))
    assert report["offline"] is True
    assert report["n_evaluated"] == 1
    assert report["regime_context"]["best_variant"] == "sma50_200_half"


def test_validasi_embargo_note_sinyal_liquidity_and_menu_framing():
    val_txt = bot.format_validasi(
        {
            "meta": {
                "start": "2021-01-01",
                "end": "2026-01-01",
                "n_tickers": 45,
                "n_rows": 12000,
                "horizon": 20,
                "embargo_days": 0,
            },
            "score": {"ic": {"mean_ic": 0.01, "t": 1.2}, "ic_timeseries": {"mean_ic": -0.01}},
            "signals": {},
            "portfolio": {},
            "relative": {},
        }
    )
    assert "embargo=0 hari" in val_txt
    assert "--embargo" in val_txt

    thin_row = {
        "ticker": "MINI",
        "price": 120,
        "rel_pct": 91.0,
        "rsi": 55.0,
        "ret20d": 4.0,
        "value_avg20": 500_000_000,
    }
    row_txt = sinyal_h._sinyal_row(1, thin_row)
    detail_txt = sinyal_h._sinyal_detail(thin_row, {"ok": 100}, bot)
    assert "likuiditas tipis" in row_txt and "luar sampel LQ45" in row_txt
    assert "Likuiditas tipis" in detail_txt and "LQ45" in detail_txt

    liquid_row = {**thin_row, "ticker": "BBCA", "value_avg20": 50_000_000_000}
    assert "likuiditas tipis" not in sinyal_h._sinyal_row(1, liquid_row)

    exp_txt = menu_h.menu_text("expert")
    ide_txt, _ = menu_h._submenu_content("ide", 5_000_000)
    assert "Alat Analisis" in exp_txt and "Statistik Tipis" in exp_txt
    assert "Edge statistik sinyal tipis" in ide_txt
