"""Bandarmology — data resmi BEI (idx.co.id): ringkasan perdagangan, aliran
asing, IHSG & sektor. Tahan hujan: Cloudflare kadang melempar tantangan 403,
tiap permintaan di-retry berselang; kalau tetap gagal, caller diberi tahu
dengan jujur agar pesan bot tidak berbohong."""
from __future__ import annotations

import copy
import os
import threading
import time

BASE = "https://www.idx.co.id/primary"

# Ringkasan bursa BEI hanya berubah sekali sehari, tetapi satu /bandar memicu
# 2 permintaan ke idx.co.id (di balik Cloudflare). Beberapa pengguna beruntun
# dulu bisa memicu blokir; cache memori 10 menit menghemat kuota.
# Set BANDAR_TTL=0 untuk mematikan.
_TTL_SECONDS = float(os.getenv("BANDAR_TTL", "600") or 0)
_cache: dict[tuple, tuple[float, object]] = {}
_cache_lock = threading.Lock()


def clear_cache():
    with _cache_lock:
        _cache.clear()


def cache_stats() -> dict:
    with _cache_lock:
        return {"entries": len(_cache), "ttl_seconds": _TTL_SECONDS}


def _cached(key: tuple, producer, ttl: float | None = None):
    """Nilai dari cache memori bila masih segar, kalau tidak panggil producer().

    Salinan (deepcopy) dikembalikan agar pemanggil tidak bisa merusak isi cache.
    """
    ttl = _TTL_SECONDS if ttl is None else ttl
    if ttl > 0:
        with _cache_lock:
            hit = _cache.get(key)
            if hit is not None and time.time() - hit[0] <= ttl:
                return copy.deepcopy(hit[1])
    value = producer()
    if ttl > 0:
        with _cache_lock:
            _cache[key] = (time.time(), copy.deepcopy(value))
    return value
HDR = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.idx.co.id/en/market-data/trading-summary/stocks-summary",
}


class IDXTidakTersedia(Exception):
    """Semua cara melewati Cloudflare gagal — jangan bohong ke user, laporkan."""


def _get(url: str, scraper, params: dict | None = None, tries: int = 3, pause: float = 4.0):
    last_exc: Exception | None = None
    for attempt in range(tries):
        try:
            r = scraper.get(url, params=params, headers=HDR, timeout=45)
            if r.status_code == 200 and (r.text or "").strip().startswith("{"):
                return r.json()
            last_exc = ValueError(f"HTTP {r.status_code}")
        except Exception as e:
            last_exc = e
        if attempt < tries - 1:
            time.sleep(pause)
    raise IDXTidakTersedia(f"BEI terblokir Cloudflare ({type(last_exc).__name__}: {last_exc})") from last_exc


def _scraper():
    import cloudscraper
    return cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "linux"})


def get_stock_summary(trade_date: str | None = None, max_stocks: int = 1000,
                      page_len: int = 100, cooldown: float = 2.0,
                      ttl: float | None = None) -> list[dict]:
    """Ringkasan perdagangan harian seluruh emiten (OHLC, volume, nilai, asing).

    Hasil di-cache per (tanggal bursa, jumlah emiten) — lihat `_TTL_SECONDS`.
    """
    return _cached(("stock", trade_date, max_stocks, page_len),
                   lambda: _fetch_stock_summary(trade_date, max_stocks, page_len, cooldown),
                   ttl)


def _fetch_stock_summary(trade_date: str | None, max_stocks: int,
                         page_len: int, cooldown: float) -> list[dict]:
    s = _scraper()
    params = {"start": 0, "length": page_len, "language": "en-us"}
    if trade_date:
        params["date"] = trade_date
    rows, start = [], 0
    while start < max_stocks:
        data = _get(f"{BASE}/TradingSummary/GetStockSummary", s,
                    params={"start": start, **params})
        d = data.get("data", [])
        rows.extend(d)
        if len(d) < page_len:
            break
        start += page_len
        time.sleep(cooldown)
    return rows


def get_index_summary(ttl: float | None = None) -> list[dict]:
    """IHSG + indeks sektor (COMPOSITE, LQ45, dst) langsung dari BEI."""
    return _cached(("index",), _fetch_index_summary, ttl)


def _fetch_index_summary() -> list[dict]:
    return _get(f"{BASE}/TradingSummary/GetIndexSummary", _scraper()).get("data", [])


def foreign_fields(row: dict) -> dict:
    return {k: v for k, v in row.items() if "fgn" in k.lower() or "foreign" in k.lower()}


def find_row(rows: list[dict], code: str) -> dict | None:
    c = code.upper().replace(".JK", "")
    return next((r for r in rows if r.get("StockCode", "").upper() == c), None)


def to_pct(row: dict) -> float:
    try:
        prev = float(row.get("PreviousPrice") or 0)
        close = float(row.get("ClosingPrice") or 0)
        return (close / prev - 1) * 100 if prev else 0.0
    except (TypeError, ValueError):
        return 0.0


def top_movers(rows: list[dict], n: int = 5, asc: bool = False) -> list[dict]:
    return sorted(rows, key=to_pct, reverse=not asc)[:n]


def last_trading_date() -> str:
    """Tanggal bursa terakhir (BEI membalas kosong untuk hari libur)."""
    from core import marketcal
    return marketcal.last_trading_day().isoformat()
