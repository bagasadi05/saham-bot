"""Test pengambilan data & normalisasi ticker (tanpa jaringan)."""
from __future__ import annotations

import pandas as pd
import pytest

from core import data


def test_normalize_ticker_menambahkan_suffix():
    assert data.normalize_ticker("bbca") == "BBCA.JK"
    assert data.normalize_ticker(" BBCA ") == "BBCA.JK"
    assert data.normalize_ticker("BBCA.JK") == "BBCA.JK"


@pytest.mark.parametrize("buruk", ["", "   ", "BBCA.US", "BB CA", "BB-CA"])
def test_normalize_ticker_menolak_input_tidak_valid(buruk):
    with pytest.raises(ValueError):
        data.normalize_ticker(buruk)


def test_fetch_quote_name_tidak_crash_untuk_ticker_invalid():
    # Regresi: dulu `symbol` belum terisi saat normalize_ticker gagal
    # -> UnboundLocalError, bukan pesan yang jelas.
    assert data.fetch_quote_name("BBCA.US") == "BBCA.US"


def test_fetch_quote_name_fallback_saat_yahoo_gagal(monkeypatch):
    class TickerGagal:
        def __init__(self, *_a, **_k):
            raise RuntimeError("yahoo mati")

    monkeypatch.setattr(data.yf, "Ticker", TickerGagal)
    assert data.fetch_quote_name("bbca") == "BBCA"


def test_fetch_history_pakai_cache_saat_sumber_gagal(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(data, "_MAX_RETRIES", 1)

    cached = pd.DataFrame(
        {"open": [1.0], "high": [1.1], "low": [0.9], "close": [1.0], "volume": [10.0]},
        index=pd.to_datetime(["2026-01-02"]),
    )
    cached.to_csv(tmp_path / "BBCA.JK.csv")

    def download_gagal(*_a, **_k):
        raise RuntimeError("429 Too Many Requests")

    monkeypatch.setattr(data.yf, "download", download_gagal)
    df = data.fetch_history("BBCA")

    assert df is not None
    assert df.attrs.get("stale") is True
    assert "cache terakhir" in df.attrs.get("stale_note", "")
    assert "WIB" in df.attrs.get("stale_note", "")
    assert "UTC" not in df.attrs.get("stale_note", "")


def test_fetch_history_mengembalikan_none_tanpa_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(data, "_MAX_RETRIES", 1)
    monkeypatch.setattr(data.yf, "download", lambda *_a, **_k: pd.DataFrame())
    assert data.fetch_history("ZZZZ") is None


def test_cache_disk_memisahkan_period_1y_dan_1mo(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(data, "_MAX_RETRIES", 1)
    monkeypatch.setattr(data, "_TTL_SECONDS", 0)

    df_1y = pd.DataFrame(
        {"open": [100.0] * 60, "high": [101.0] * 60, "low": [99.0] * 60,
         "close": [100.0] * 60, "volume": [1000.0] * 60},
        index=pd.bdate_range("2026-01-02", periods=60),
    )
    df_1mo = df_1y.tail(15).copy()

    def fake_dl(symbol, period="1y", **_k):
        return df_1mo.copy() if period == "1mo" else df_1y.copy()

    monkeypatch.setattr(data.yf, "download", fake_dl)
    got_1y = data.fetch_history("BBCA", "1y")
    got_1mo = data.fetch_history("BBCA", "1mo")
    assert len(got_1y) == 60
    assert len(got_1mo) == 15
    assert (tmp_path / "BBCA.JK_1y.csv").exists()
    assert (tmp_path / "BBCA.JK_1mo.csv").exists()

    # Saat Yahoo 429, permintaan 1y tetap membaca cache 60 bar (tidak tertimpa 1mo)
    monkeypatch.setattr(data.yf, "download", lambda *_a, **_k: pd.DataFrame())
    cached_1y = data.fetch_history("BBCA", "1y")
    assert cached_1y is not None
    assert len(cached_1y) == 60
    assert cached_1y.attrs["stale"] is True


def test_fetch_quote_name_utamakan_metadata_ringan_sebelum_info(monkeypatch):
    data._name_cache.clear()

    class FakeTicker:
        def __init__(self, _sym):
            self.fast_info = {"currency": "IDR"}

        def get_history_metadata(self):
            return {"longName": "PT Bank Central Asia Tbk", "shortName": "BCA"}

        @property
        def info(self):
            raise AssertionError(".info berat tidak boleh dipanggil bila metadata ringan cukup")

    monkeypatch.setattr(data.yf, "Ticker", FakeTicker)
    try:
        assert data.fetch_quote_name("BBCA") == "PT Bank Central Asia Tbk"
    finally:
        data._name_cache.clear()
