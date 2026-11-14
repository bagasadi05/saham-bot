"""Tes Wave 2A data & infrastruktur: fallback P25, cache period, WIB provenance,
BEI 2026 ARB, filter kualitas screener, query berita ketat, dan Docker/CI."""
from __future__ import annotations

import datetime as dt
import email.message
import inspect
import urllib.error
from pathlib import Path

import numpy as np
import pandas as pd

from core import data, data_quality, news, pricing, screener, store
from tests.conftest import make_prices

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------ 1. Cache period & fallback ---

def test_p25_fallback_nonaktif_bila_env_kosong(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(data, "_MAX_RETRIES", 1)
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    monkeypatch.setattr(data.yf, "download", lambda *_a, **_k: pd.DataFrame())

    called = {"n": 0}
    monkeypatch.setattr(
        data, "_http_get_json",
        lambda *_a, **_k: called.__setitem__("n", called["n"] + 1),
    )

    assert data.fetch_history("BBCA") is None
    assert called["n"] == 0


def test_p25_fallback_eodhd_opt_in_dan_simpan_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(data, "_MAX_RETRIES", 1)
    monkeypatch.setenv("EODHD_API_KEY", "demo-eodhd-key")
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    monkeypatch.setattr(data.yf, "download", lambda *_a, **_k: pd.DataFrame())

    today = dt.date.today()
    rows = [
        {
            "date": (today - dt.timedelta(days=5 - i)).isoformat(),
            "open": 10000.0,
            "high": 10200.0,
            "low": 9900.0,
            "close": 10000.0,
            "adjusted_close": 5000.0,
            "volume": 1_000_000,
        }
        for i in range(4)
    ]
    urls: list[str] = []

    def fake_get_json(url, timeout=15):
        urls.append(url)
        return rows

    monkeypatch.setattr(data, "_http_get_json", fake_get_json)

    df = data.fetch_history("BBCA", "1mo")
    assert df is not None
    assert len(urls) == 1 and "eodhd.com/api/eod/BBCA.JK" in urls[0]
    assert df.attrs["provider"] == "eodhd"
    assert df.attrs["provenance"] == "fallback"
    assert df.attrs["adjusted"] is True
    assert df.attrs["repaired_count"] == 0
    # Proporsional disesuaikan dengan adjusted_close / close (0.5x)
    assert float(df["close"].iloc[-1]) == 5000.0
    assert float(df["high"].iloc[-1]) == 5100.0
    assert "eodhd" in data_quality.format_provenance(df)


def test_p25_fallback_alphavantage_saat_eodhd_gagal(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(data, "_MAX_RETRIES", 1)
    monkeypatch.setenv("EODHD_API_KEY", "bad-key")
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "demo-av-key")
    monkeypatch.setattr(data.yf, "download", lambda *_a, **_k: pd.DataFrame())

    today = dt.date.today()
    d1 = (today - dt.timedelta(days=3)).isoformat()
    d2 = (today - dt.timedelta(days=2)).isoformat()

    def fake_get_json(url, timeout=15):
        if "eodhd.com" in url:
            raise RuntimeError("eodhd 429")
        assert "alphavantage.co" in url
        assert "symbol=BBCA.JK" in url
        return {
            "Time Series (Daily)": {
                d1: {
                    "1. open": "4000", "2. high": "4100", "3. low": "3950",
                    "4. close": "4000", "5. adjusted close": "4000", "6. volume": "50000",
                },
                d2: {
                    "1. open": "4050", "2. high": "4200", "3. low": "4000",
                    "4. close": "4150", "5. adjusted close": "4150", "6. volume": "60000",
                },
            }
        }

    monkeypatch.setattr(data, "_http_get_json", fake_get_json)
    df = data.fetch_history("BBCA", "1mo")
    assert df is not None
    assert df.attrs["provider"] == "alphavantage"
    assert df.attrs["provenance"] == "fallback"
    assert float(df["close"].iloc[-1]) == 4150.0


def test_p25_fallback_tidak_dipakai_untuk_indeks_dan_tanpa_stooq(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(data, "_MAX_RETRIES", 1)
    monkeypatch.setenv("EODHD_API_KEY", "k1")
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "k2")
    monkeypatch.setattr(data.yf, "download", lambda *_a, **_k: pd.DataFrame())
    called = {"n": 0}
    monkeypatch.setattr(
        data, "_http_get_json",
        lambda *_a, **_k: called.__setitem__("n", called["n"] + 1),
    )

    assert data.fetch_history("^JKSE", "2y", raw_symbol=True) is None
    assert called["n"] == 0
    assert "stooq" not in inspect.getsource(data).lower()


def test_load_cache_period_incompatible_rejected_and_unperioded_finds_longest(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(data, "_CACHE_DIR", str(tmp_path))
    short = make_prices([100, 101, 102])
    long_df = make_prices(np.linspace(6500, 7000, 60))

    # Simpan legacy file dengan metadata period="1mo" -> ditolak untuk permintaan "1y"
    data._save_cache("BBRI.JK", short, interval="1d", period=None)
    meta_p = tmp_path / "BBRI.JK.json"
    meta_txt = meta_p.read_text(encoding="utf-8").replace('"period": ""', '"period": "1mo"')
    meta_p.write_text(meta_txt, encoding="utf-8")
    assert data._load_cache("BBRI.JK", "1d", period="1y") is None

    # Simpan ^JKSE_2y.csv -> pemanggil offline _load_cache("^JKSE", "1d") tetap menemukannya
    data._save_cache("^JKSE", long_df, interval="1d", period="2y")
    found = data._load_cache("^JKSE", "1d")
    assert found is not None
    assert len(found) == 60
    assert found.attrs["period"] == "2y"


def test_fetch_quote_name_fast_info_dan_fallback_info(monkeypatch):
    data._name_cache.clear()
    try:
        class FastTicker:
            fast_info = {"shortName": "Bank Rakyat Indonesia"}

            @property
            def info(self):
                raise AssertionError(".info tidak perlu dipanggil")

        monkeypatch.setattr(data.yf, "Ticker", lambda _s: FastTicker())
        assert data.fetch_quote_name("BBRI") == "Bank Rakyat Indonesia"

        data._name_cache.clear()

        class InfoFallbackTicker:
            fast_info = {}

            def get_history_metadata(self):
                return {}

            info = {"longName": "PT Telkom Indonesia Tbk"}

        monkeypatch.setattr(data.yf, "Ticker", lambda _s: InfoFallbackTicker())
        assert data.fetch_quote_name("TLKM") == "PT Telkom Indonesia Tbk"
    finally:
        data._name_cache.clear()


def test_format_provenance_semua_status():
    df_legacy = make_prices([100, 101, 102])
    df_legacy.attrs.update({
        "provider": "yfinance",
        "provenance": "cache-legacy",
        "stale": True,
        "adjusted": None,
        "quality": {"ok": False, "flags": {"stale_days": 8}},
    })
    txt = data_quality.format_provenance(df_legacy)
    assert "cache lama/basi" in txt
    assert "penyesuaian tidak diketahui" in txt
    assert "tertinggal 8 hari bursa" in txt
    assert "WIB" in txt


# -------------------------------------------- 2. Screener metadata & stale ---

def test_screener_scan_persists_quality_and_date_meta(tmp_store):
    live = make_prices(np.linspace(1000, 1300, 120), start="2026-05-01")
    stale = make_prices(np.linspace(1000, 1200, 60), start="2024-01-01")
    lag_2d = make_prices(np.linspace(1000, 1250, 118), start="2026-05-01")

    frames = {f"L{i:02d}": live.copy() for i in range(9)}
    frames["LAG2"] = lag_2d
    frames["OLD1"] = stale

    res = screener.scan(force=True, symbols=list(frames), downloader=lambda _s: frames)
    assert "OLD1" in res["skipped"]
    assert res["meta"]["skipped_stale"] == 1
    assert res["meta"]["trade_date_min"] <= res["meta"]["trade_date"]

    persisted_meta = store.market_scan_meta()
    assert persisted_meta["skipped_stale"] == "1"
    assert persisted_meta["trade_date_min"] <= persisted_meta["trade_date"]


def test_screener_live_mode_rejects_all_stale_vs_today(tmp_store, monkeypatch):
    old_only = {"OLD1": make_prices(np.linspace(1000, 1200, 60), start="2024-01-01")}
    monkeypatch.setattr(screener, "fetch_frames", lambda *a, **k: old_only)
    try:
        screener.scan(force=True, symbols=["OLD1"])
        raise AssertionError("Harusnya gagal karena semua emiten stale")
    except RuntimeError as exc:
        assert "cukup" in str(exc)


# -------------------------------------------- 3. Berita & Docker/CI kontrak --

def test_news_build_query_normalizes_quotes_and_bare_symbols():
    assert news.build_query("LINK", 'PT "Link Net" Tbk') == (
        '"PT Link Net Tbk" OR "LINK.JK" OR "saham LINK"'
    )
    assert news.build_query("WIFI", "") == '"WIFI.JK" OR "saham WIFI"'
    assert news.build_query("HERO.JK", "HERO") == '"HERO.JK" OR "saham HERO"'


def test_pricing_arb_window_override_and_special_board(monkeypatch):
    monkeypatch.setenv("IDX_ARB_WINDOW_END", "2027-03-31")
    d = dt.date(2027, 2, 15)
    assert pricing.arb_floor_price(1000, d) == 930.0
    st = pricing.auto_reject_status(d)
    assert st["asym_arb"] is True
    assert st["expired"] is True  # IDX_ARA_WINDOW_END belum diperpanjang

    risk = pricing.check_arb_gap_risk(1000, 910, dt=d, board="Pemantauan Khusus")
    assert risk["risk"] is True
    assert risk["floor"] == 900.0
    assert risk["deeper_than_7pct"] is True
    assert "IDX_ARA_WINDOW_END" in risk["window_note"]


def test_infra_dockerfile_compose_makefile_ci_env_gitignore():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY handlers/ ./handlers/" in dockerfile
    assert "COPY vendor/ ./vendor/" in dockerfile
    assert "COPY core/ ./core/" in dockerfile

    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "BACKUP_DIR:" in compose

    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "--cov=handlers" in makefile
    assert "handlers tools research" in makefile

    ci_yml = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "--cov=handlers" in ci_yml
    assert "docker-smoke" in ci_yml
    assert "SKIP_MODEL=1" in ci_yml

    env_ex = (ROOT / ".env.example").read_text(encoding="utf-8")
    for var in (
        "BACKUP_DIR", "LOG_JSON", "SAHAMBOT_FUND_TTL", "SAHAMBOT_NEWS_TTL",
        "FULL_SCAN_MAX_STALE_DAYS", "IDX_ARA_WINDOW_END", "IDX_ARB_WINDOW_END",
        "EODHD_API_KEY", "ALPHAVANTAGE_API_KEY",
    ):
        assert var in env_ex

    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".zwork/" in gitignore


def test_yfinance_repair_adaptive_without_scipy_and_fallback_on_importerror(
    tmp_path, monkeypatch
):
    """Tanpa `scipy`, `repair=False` agar `yf.download` tidak gagal `No module named scipy`."""
    monkeypatch.setattr(data, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(data, "_MAX_RETRIES", 1)
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)

    # 1. `find_spec("scipy")` None -> repair=False di data.fetch_history & screener._download_chunk
    orig_find_spec = data.importlib.util.find_spec
    monkeypatch.setattr(
        data.importlib.util, "find_spec",
        lambda name, *a, **k: None if name == "scipy" else orig_find_spec(name, *a, **k),
    )
    assert data.supports_yf_repair() is False

    frames = make_prices([5000, 5100, 5200])
    seen_data_kwargs: list[dict] = []

    def fake_yf_data(*_a, **kwargs):
        seen_data_kwargs.append(dict(kwargs))
        return frames.copy()

    monkeypatch.setattr(data.yf, "download", fake_yf_data)
    out = data.fetch_history("BBCA", "1mo")
    assert out is not None
    assert seen_data_kwargs[-1]["auto_adjust"] is True
    assert seen_data_kwargs[-1]["repair"] is False

    seen_screener_kwargs: list[dict] = []

    def fake_yf_screener(*_a, **kwargs):
        seen_screener_kwargs.append(dict(kwargs))
        return pd.DataFrame()

    monkeypatch.setattr("yfinance.download", fake_yf_screener)
    screener._download_chunk(["BBCA"], "1y")
    assert seen_screener_kwargs[-1]["auto_adjust"] is True
    assert seen_screener_kwargs[-1]["repair"] is False

    # 2. Bila `supports_yf_repair()` True namun `ModuleNotFoundError("scipy")` muncul,
    #    helper mengulang sekali dengan repair=False.
    monkeypatch.setattr(data, "supports_yf_repair", lambda: True)
    calls: list[bool] = []

    def flaky_scipy_download(*_a, **kwargs):
        calls.append(bool(kwargs.get("repair")))
        if kwargs.get("repair"):
            raise ModuleNotFoundError("No module named 'scipy'")
        return frames.copy()

    res = data.download_yf_with_safe_repair(flaky_scipy_download, "BBCA.JK", period="1mo")
    assert res is not None and not res.empty
    assert calls == [True, False]


def test_eodhd_quota_error_triggers_cooldown_and_resets_with_clear_cache(
    tmp_path, monkeypatch
):
    """HTTP 402/429 EODHD mengaktifkan cooldown sementara agar batch tidak spam kuota."""
    monkeypatch.setattr(data, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(data, "_MAX_RETRIES", 1)
    monkeypatch.setattr(data, "_EODHD_COOLDOWN_SEC", 600)
    monkeypatch.setenv("EODHD_API_KEY", "quota-key")
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    monkeypatch.setattr(data.yf, "download", lambda *_a, **_k: pd.DataFrame())

    eodhd_calls = {"n": 0}

    def raise_quota(url, timeout=15):
        eodhd_calls["n"] += 1
        raise urllib.error.HTTPError(
            url, 402, "Payment Required", email.message.Message(), None
        )

    monkeypatch.setattr(data, "_http_get_json", raise_quota)

    assert data.fetch_history("BBCA", "1mo") is None
    assert eodhd_calls["n"] == 1
    assert data._is_fallback_cooled_down("eodhd") is True

    # Panggilan berikutnya tidak lagi memukul EODHD selama masa cooldown aktif
    assert data.fetch_history("BBRI", "1mo") is None
    assert eodhd_calls["n"] == 1

    # clear_cache() membersihkan status cooldown (untuk isolasi / reset manual)
    data.clear_cache()
    assert data._is_fallback_cooled_down("eodhd") is False
    assert data.fetch_history("BMRI", "1mo") is None
    assert eodhd_calls["n"] == 2


def test_alphavantage_premium_or_rate_limit_triggers_cooldown(tmp_path, monkeypatch):
    """Pesan premium/rate-limit Alpha Vantage mengaktifkan cooldown agar kuota tidak spam."""
    monkeypatch.setattr(data, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(data, "_MAX_RETRIES", 1)
    monkeypatch.setattr(data, "_AV_COOLDOWN_SEC", 600)
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "free-av-key")
    monkeypatch.setattr(data.yf, "download", lambda *_a, **_k: pd.DataFrame())

    av_calls = {"n": 0}

    def fake_av_premium(_url, timeout=15):
        av_calls["n"] += 1
        return {
            "Information": "Thank you for using Alpha Vantage! This is a premium endpoint."
        }

    monkeypatch.setattr(data, "_http_get_json", fake_av_premium)

    assert data.fetch_history("BBCA", "1mo") is None
    assert av_calls["n"] == 1
    assert data._is_fallback_cooled_down("alphavantage") is True

    # Panggilan berikutnya melewati Alpha Vantage selama cooldown aktif
    assert data.fetch_history("TLKM", "1mo") is None
    assert av_calls["n"] == 1

    data.clear_cache()
    assert data._is_fallback_cooled_down("alphavantage") is False
