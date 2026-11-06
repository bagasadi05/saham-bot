"""Data fundamental (PER, PBV, ROE, dividen) + skor valuations sederhana.
Sumber: info Yahoo Finance (data dari laporan keuangan & agregator)."""
from __future__ import annotations

import yfinance as yf

from core import data as datamod


def fetch_fundamentals(ticker: str) -> dict | None:
    """Return dict fundamental atau None kalau data tidak tersedia."""
    symbol = datamod.normalize_ticker(ticker)
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
        "per": g("trailingPE"),
        "pbv": g("priceToBook"),
        "eps": g("trailingEps"),
        "div_yield": g("dividendYield"),
        "roe": g("returnOnEquity"),
        "margin": g("profitMargins"),
        "debt_eq": g("debtToEquity"),
    }
    if all(fields[k] is None for k in ("per", "pbv", "div_yield", "roe")):
        return None
    return fields


def score_fundamentals(f: dict) -> dict:
    """Skor valuasi -100..+100 lewat poin per aspek (heuristik standar)."""
    points = []
    verdicts = []

    per = f.get("per")
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

    pbv = f.get("pbv")
    if pbv is not None:
        if pbv < 1:
            points.append(10); verdicts.append(f"🟢 PBV {pbv:.2f} — di bawah nilai buku")
        elif pbv < 3:
            points.append(5); verdicts.append(f"🟡 PBV {pbv:.2f} — wajar")
        else:
            points.append(-5); verdicts.append(f"🔴 PBV {pbv:.2f} — premium tinggi")

    roe = f.get("roe")
    if roe is not None:
        roe *= 100 if roe < 3 else 1  # yfinance kadang persen, kadang fraksi
        if roe < 0:
            points.append(-10); verdicts.append(f"🔴 ROE {roe:.1f}% negatif")
        elif roe > 15:
            points.append(15); verdicts.append(f"🟢 ROE {roe:.1f}% — profitabilitas bagus")
        elif roe > 8:
            points.append(5); verdicts.append(f"🟡 ROE {roe:.1f}% — cukup")
        else:
            points.append(-5); verdicts.append(f"🔴 ROE {roe:.1f}% lemah")

    dy = f.get("div_yield")
    if dy is not None:
        dy = dy * 100 if dy < 1 else dy  # konsisten: 0.03 -> 3%
        if dy >= 5:
            points.append(10); verdicts.append(f"🟢 Dividend yield {dy:.1f}% — bagus buat pasif")
        elif dy >= 3:
            points.append(5); verdicts.append(f"🟡 Dividend yield {dy:.1f}%")
        elif dy > 0:
            points.append(0); verdicts.append(f"⚪ Dividend yield {dy:.1f}% rendah")

    d = f.get("debt_eq")
    if d is not None:
        de = d if d > 5 else d * 100  # fraksi vs persen
        if de < 50:
            points.append(5); verdicts.append(f"🟢 DER {de:.0f}% - hati-hati sehat")
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
