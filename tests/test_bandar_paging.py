"""Tes /bandar: paging berhenti lebih awal saat emiten yang dicari ketemu."""
from __future__ import annotations

from core import bandar


def test_fetch_stops_when_ticker_found(monkeypatch):
    calls: list[int] = []
    pages = [
        {"data": [{"StockCode": "AAAA"}, {"StockCode": "BBBB"}]},
        {"data": [{"StockCode": "BBRI"}, {"StockCode": "CCCC"}]},
        {"data": [{"StockCode": "DDDD"}, {"StockCode": "EEEE"}]},
    ]

    def fake_get(url, scraper, params=None, **kwargs):
        calls.append(params["start"])
        return pages[len(calls) - 1]

    monkeypatch.setattr(bandar, "_scraper", lambda: object())
    monkeypatch.setattr(bandar, "_get", fake_get)
    monkeypatch.setattr(bandar.time, "sleep", lambda seconds: None)

    rows = bandar._fetch_stock_summary(None, max_stocks=1000, page_len=2,
                                       cooldown=0, until="BBRI")

    assert calls == [0, 2]                 # halaman 3 tidak diambil
    assert bandar.find_row(rows, "bbri")["StockCode"] == "BBRI"


def test_fetch_without_until_reads_all_pages(monkeypatch):
    calls: list[int] = []
    pages = [
        {"data": [{"StockCode": "AAAA"}, {"StockCode": "BBBB"}]},
        {"data": [{"StockCode": "BBRI"}]},
    ]

    def fake_get(url, scraper, params=None, **kwargs):
        calls.append(params["start"])
        return pages[len(calls) - 1]

    monkeypatch.setattr(bandar, "_scraper", lambda: object())
    monkeypatch.setattr(bandar, "_get", fake_get)
    monkeypatch.setattr(bandar.time, "sleep", lambda seconds: None)

    bandar._fetch_stock_summary(None, max_stocks=1000, page_len=2, cooldown=0)
    assert calls == [0, 2]                 # berhenti karena halaman terakhir pendek


def test_bandar_disk_cache_fallback_saat_cloudflare_403(tmp_path, monkeypatch):
    monkeypatch.setattr(bandar, "CACHE_DIR", str(tmp_path / "bandar_cache"))
    monkeypatch.setattr(bandar, "_scraper", lambda: object())
    payload = {"data": [{"IndexCode": "COMPOSITE", "Close": 7250.0}]}
    monkeypatch.setattr(bandar, "_get", lambda url, s, params=None: payload)

    first = bandar.get_index_summary(ttl=0)
    assert first == payload["data"]
    assert bandar.last_stale_age() is None

    def _blocked(*_a, **_kw):
        raise bandar.IDXTidakTersedia("HTTP 403")

    monkeypatch.setattr(bandar, "_get", _blocked)
    fallback = bandar.get_index_summary(ttl=0)
    assert fallback == payload["data"]
    assert bandar.last_stale_age() is not None
    assert bandar.last_stale_age() >= 0


def test_bandar_disk_cache_basi_melewati_batas_tetap_error(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setattr(bandar, "CACHE_DIR", str(tmp_path / "bandar_cache"))
    monkeypatch.setattr(bandar, "_STALE_MAX_AGE", 1.0)
    monkeypatch.setattr(bandar, "_scraper", lambda: object())
    payload = {"data": [{"IndexCode": "COMPOSITE", "Close": 7250.0}]}
    monkeypatch.setattr(bandar, "_get", lambda url, s, params=None: payload)
    bandar.get_index_summary(ttl=0)

    # Geser waktu maju melewati batas stale
    orig_time = bandar.time.time
    monkeypatch.setattr(bandar.time, "time", lambda: orig_time() + 10.0)
    monkeypatch.setattr(
        bandar, "_get",
        lambda *_a, **_kw: (_ for _ in ()).throw(bandar.IDXTidakTersedia("HTTP 403")),
    )
    with pytest.raises(bandar.IDXTidakTersedia):
        bandar.get_index_summary(ttl=0)
