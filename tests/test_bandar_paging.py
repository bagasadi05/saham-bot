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
