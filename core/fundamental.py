"""Data fundamental (PER, PBV, ROE, dividen) + skor valuations sederhana.
Sumber: info Yahoo Finance (data dari laporan keuangan & agregator).

Hasil dicache 6 jam: laporan kuartalan tidak berubah dalam hitungan menit,
padahal `.info` + `quarterly_income_stmt` termasuk panggilan termahal ke Yahoo.
Tanpa cache, satu `/saran` 12 emiten = 24 permintaan jaringan yang sama persis.
"""
from __future__ import annotations

import math
import os
import threading
import time

import yfinance as yf

from core import data as datamod

_FUND_TTL = int(os.getenv("SAHAMBOT_FUND_TTL", "21600") or 0)  # 6 jam
_fund_cache: dict[str, tuple[float, dict]] = {}
_fund_lock = threading.Lock()


def _finite_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def normalize_roe(v) -> float | None:
    """Normalisasi ROE (yfinance kadang fraksi 0.18, kadang persen 18.0)."""
    f = _finite_float(v)
    if f is None:
        return None
    return f * 100.0 if abs(f) < 3.0 else f


def normalize_div_yield(v) -> float | None:
    """Normalisasi dividend yield (fraksi 0.04 -> 4.0%)."""
    f = _finite_float(v)
    if f is None:
        return None
    return f * 100.0 if 0 <= f < 1.0 else f


def normalize_der(v) -> float | None:
    """DER dari Yahoo `debtToEquity` sudah berbentuk persen (mis. 45.2 = 45.2%)."""
    return _finite_float(v)


def fetch_fundamentals(ticker: str) -> dict | None:
    """Return dict fundamental atau None kalau data tidak tersedia (cache 6 jam)."""
    symbol = datamod.normalize_ticker(ticker)
    if _FUND_TTL > 0:
        with _fund_lock:
            hit = _fund_cache.get(symbol)
            if hit and time.time() - hit[0] < _FUND_TTL:
                return dict(hit[1])
    fields = _fetch_uncached(symbol)
    if fields is not None and _FUND_TTL > 0:
        with _fund_lock:
            _fund_cache[symbol] = (time.time(), fields)
    return fields


def clear_cache():
    """Kosongkan cache fundamental (dipakai test / manual)."""
    with _fund_lock:
        _fund_cache.clear()


def _fetch_uncached(symbol: str) -> dict | None:
    yft = yf.Ticker(symbol)
    info = yft.info or {}

    def g(*keys):
        for k in keys:
            v = info.get(k)
            if v is not None:
                return v
        return None

    fields = {
        "name": g("longName", "shortName") or symbol.replace(".JK", ""),
        "sector": g("sector", "industry"),
        "market_cap": g("marketCap"),
        "per": _finite_float(g("trailingPE")),
        "pbv": _finite_float(g("priceToBook")),
        "eps": _finite_float(g("trailingEps")),
        "div_yield": _finite_float(g("dividendYield")),
        "roe": _finite_float(g("returnOnEquity")),
        "margin": _finite_float(g("profitMargins")),
        "debt_eq": _finite_float(g("debtToEquity")),
    }
    if all(fields[k] is None for k in ("per", "pbv", "div_yield", "roe")):
        return None

    # [P3] tren 4 kuartal terakhir: pendapatan & laba bersih (quarterly financials)
    fields["as_of"] = None
    fields["rev_trend"] = None   # [(kuartal, revenue), ...] terbaru di akhir
    fields["profit_trend"] = None
    try:
        qs = yft.quarterly_income_stmt
        if qs is not None and len(qs.columns) >= 2:
            # urutkan kronologis — Yahoo kadang kirim kolom tidak berurutan
            cols = sorted(qs.columns, key=lambda q: str(q))
            rev_row = None
            profit_row = None
            for key in ("Total Revenue", "Operating Revenue", "Revenues"):
                if key in qs.index:
                    rev_row = qs.loc[key, cols]
                    break
            for key in ("Net Income Common Stockholders", "Net Income",
                        "Net Income Including Noncontrolling Interests"):
                if key in qs.index:
                    profit_row = qs.loc[key, cols]
                    break
            def _series(row, n=4):
                if row is None:
                    return None
                s = row.dropna()
                s = s[s != 0]
                if len(s) == 0:
                    return None
                s = s.iloc[-n:]
                labels = [f"Q{q.quarter} {q.year}" for q in s.index]
                return [(lbl, float(v)) for lbl, v in zip(labels, s.values, strict=True)]
            fields["rev_trend"] = _series(rev_row)
            fields["profit_trend"] = _series(profit_row)
            last_q = max((q for q in qs.columns if hasattr(q, "quarter")), default=None)
            fields["as_of"] = f"Q{last_q.quarter} {last_q.year}" if last_q else None
    except Exception:
        pass  # tren tak tersedia → dikatakan jujur di laporan
    return fields


def score_fundamentals(f: dict) -> dict:
    """Skor valuasi -100..+100 lewat poin per aspek (heuristik standar)."""
    points = []
    verdicts = []

    per = _finite_float(f.get("per"))
    if per is not None:
        if per < 0:
            points.append(-15); verdicts.append("🔴 PER negatif (labanya rugi)")
        elif per < 10:
            points.append(15); verdicts.append(f"🟢 PER {per:.1f} — murah")
        elif per < 20:
            points.append(8); verdicts.append(f"🟡 PER {per:.1f} — wajar")
        elif per < 35:
            points.append(0); verdicts.append(f"🟡 PER {per:.1f} — agak mahal")
        else:
            points.append(-8); verdicts.append(f"🔴 PER {per:.1f} — mahal")

    pbv = _finite_float(f.get("pbv"))
    if pbv is not None:
        if pbv < 1:
            points.append(10); verdicts.append(f"🟢 PBV {pbv:.2f} — di bawah nilai buku")
        elif pbv < 3:
            points.append(5); verdicts.append(f"🟡 PBV {pbv:.2f} — wajar")
        else:
            points.append(-5); verdicts.append(f"🔴 PBV {pbv:.2f} — premium tinggi")

    roe = normalize_roe(f.get("roe"))
    if roe is not None:
        if roe < 0:
            points.append(-10); verdicts.append(f"🔴 ROE {roe:.1f}% negatif")
        elif roe > 15:
            points.append(15); verdicts.append(f"🟢 ROE {roe:.1f}% — profitabilitas bagus")
        elif roe > 8:
            points.append(5); verdicts.append(f"🟡 ROE {roe:.1f}% — cukup")
        else:
            points.append(-5); verdicts.append(f"🔴 ROE {roe:.1f}% lemah")

    dy = normalize_div_yield(f.get("div_yield"))
    if dy is not None:
        if dy >= 5:
            points.append(10); verdicts.append(f"🟢 Dividend yield {dy:.1f}% — bagus buat pasif")
        elif dy >= 3:
            points.append(5); verdicts.append(f"🟡 Dividend yield {dy:.1f}%")
        elif dy > 0:
            points.append(0); verdicts.append(f"⚪ Dividend yield {dy:.1f}% rendah")

    de = normalize_der(f.get("debt_eq"))
    if de is not None:
        if de < 0:
            points.append(-5); verdicts.append(f"🔴 DER {de:.0f}% negatif (ekuitas bermasalah)")
        elif de < 50:
            points.append(5); verdicts.append(f"🟢 DER {de:.0f}% — sehat")
        elif de < 100:
            points.append(0); verdicts.append(f"🟡 DER {de:.0f}% biasa")
        else:
            points.append(-5); verdicts.append(f"🔴 DER {de:.0f}% tinggi")

    raw = max(-40, min(40, sum(points))) * 2.5  # skala -100..100
    if raw >= 50:
        label, emoji = "FUNDAMENTAL KUAT", "💪"
    elif raw >= 15:
        label, emoji = "CUKUP", "🙂"
    elif raw > -15:
        label, emoji = "MIX", "🤷"
    else:
        label, emoji = "LEMAH", "🫤"
    return {"pct": round(raw), "label": label, "emoji": emoji, "notes": verdicts}
