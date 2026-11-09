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


def test_fetch_history_mengembalikan_none_tanpa_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(data, "_MAX_RETRIES", 1)
    monkeypatch.setattr(data.yf, "download", lambda *_a, **_k: pd.DataFrame())
    assert data.fetch_history("ZZZZ") is None
