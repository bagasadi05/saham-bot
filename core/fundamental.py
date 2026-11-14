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


def normalize_margin(v) -> float | None:
    """Normalisasi net profit margin (Yahoo fraksi 0.22 -> 22.0%)."""
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


def normalize_payout(v) -> float | None:
    """Normalisasi payout ratio (Yahoo fraksi 0.45 -> 45%, 1.25 -> 125%)."""
    f = _finite_float(v)
    if f is None or f < 0:
        return None
    return f * 100.0 if f <= 5.0 else f


def is_financial_sector(sector: str | None) -> bool:
    """Bank/asuransi/keuangan: DPK dihitung sebagai liabilitas sehingga DER alami tinggi."""
    if not sector:
        return False
    s = str(sector).strip().lower()
    return any(k in s for k in ("financial", "bank", "insurance",
                                "perbankan", "keuangan", "asuransi"))


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

    # Disambiguasi dividendYield Yahoo: versi baru mengirim 0.85 untuk 0.85%,
    # padahal trailingAnnualDividendYield tetap fraksi (0.0085).
    dy_raw = _finite_float(g("dividendYield"))
    trail_dy = _finite_float(g("trailingAnnualDividendYield"))
    if dy_raw is None:
        dy_val = trail_dy
    elif 0 < dy_raw < 1.0 and trail_dy is not None and 0 < trail_dy < dy_raw / 10.0:
        dy_val = dy_raw / 100.0
    else:
        dy_val = dy_raw

    fields = {
        "name": g("longName", "shortName") or symbol.replace(".JK", ""),
        "sector": g("sector", "industry"),
        "market_cap": g("marketCap"),
        "per": _finite_float(g("trailingPE")),
        "pbv": _finite_float(g("priceToBook")),
        "eps": _finite_float(g("trailingEps")),
        "div_yield": dy_val,
        "roe": _finite_float(g("returnOnEquity")),
        "margin": _finite_float(g("profitMargins")),
        "debt_eq": _finite_float(g("debtToEquity")),
        "ocf": _finite_float(g("operatingCashflow")),
        "fcf": _finite_float(g("freeCashflow")),
        "net_income": _finite_float(g("netIncomeToCommon")),
        "total_cash": _finite_float(g("totalCash")),
        "total_debt": _finite_float(g("totalDebt")),
        "current_ratio": _finite_float(g("currentRatio")),
        "payout_ratio": _finite_float(g("payoutRatio")),
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
            def _q_label(q) -> str:
                if hasattr(q, "quarter") and hasattr(q, "year"):
                    return f"Q{q.quarter} {q.year}"
                return str(q)[:10]

            def _series(row, n=4):
                if row is None:
                    return None
                s = row.dropna()
                if len(s) == 0:
                    return None
                s = s.iloc[-n:]
                labels = [_q_label(q) for q in s.index]
                return [(lbl, float(v)) for lbl, v in zip(labels, s.values, strict=True)]
            fields["rev_trend"] = _series(rev_row)
            fields["profit_trend"] = _series(profit_row)
            last_q = max((q for q in qs.columns if hasattr(q, "quarter")), default=None)
            fields["as_of"] = f"Q{last_q.quarter} {last_q.year}" if last_q else None
    except Exception:
        pass  # tren tak tersedia → dikatakan jujur di laporan
    return fields


def analyze_cashflow_quality(f: dict | None) -> dict:
    """Audit forensik arus kas, neraca & jebakan dividen (offline).

    Mengikuti disiplin sektor BEI: bank/asuransi/keuangan tidak dinilai
    dengan aturan FCF, OCF/laba, atau current ratio manufaktur karena
    DPK & penyaluran kredit mendistorsi arus kas operasi. Bagian yang
    datanya kosong tidak diisi nol dan tidak dianggap negatif.
    """
    raw = f or {}
    fin_sector = is_financial_sector(raw.get("sector"))
    ocf = _finite_float(raw.get("ocf"))
    fcf = _finite_float(raw.get("fcf"))
    net_income = _finite_float(raw.get("net_income"))
    total_cash = _finite_float(raw.get("total_cash"))
    total_debt = _finite_float(raw.get("total_debt"))
    current_ratio = _finite_float(raw.get("current_ratio"))
    payout = normalize_payout(raw.get("payout_ratio"))
    div_yield = normalize_div_yield(raw.get("div_yield"))

    has_any = any(
        v is not None
        for v in (ocf, fcf, total_cash, total_debt, current_ratio, payout)
    )
    if not has_any:
        return {
            "available": False,
            "fin_sector": fin_sector,
            "ocf": None,
            "fcf": None,
            "net_cash": None,
            "ocf_to_ni": None,
            "current_ratio": None,
            "payout_pct": None,
            "flags": [],
            "strengths": [],
        }

    flags: list[str] = []
    strengths: list[str] = []
    ocf_to_ni = None
    net_cash = (
        round(total_cash - total_debt)
        if (total_cash is not None and total_debt is not None)
        else None
    )

    if not fin_sector:
        if ocf is not None and net_income is not None and net_income > 0:
            ocf_to_ni = round(ocf / net_income, 2)
            if ocf < 0:
                flags.append(
                    "Arus kas operasi negatif (OCF < 0) meski laba positif — "
                    "waspada kualitas laba akrual"
                )
            elif ocf_to_ni < 0.7:
                flags.append(
                    f"Konversi kas lemah (OCF {ocf_to_ni:.2f}× laba bersih)"
                )
            elif ocf_to_ni >= 1.0:
                strengths.append(
                    f"Laba didukung kas nyata (OCF {ocf_to_ni:.2f}× laba bersih)"
                )
        elif ocf is not None and ocf < 0:
            flags.append("Arus kas operasi (OCF) negatif")

        if fcf is not None:
            if fcf < 0:
                flags.append(
                    "Free cash flow (FCF) negatif (arus kas tersedot operasi/capex)"
                )
            elif fcf > 0:
                strengths.append("Free cash flow (FCF) positif")

        if net_cash is not None:
            if net_cash > 0:
                strengths.append("Posisi kas bersih positif (kas > utang)")
            elif total_cash > 0 and total_debt > total_cash * 2.0:
                flags.append("Utang lebih dari 2× kas yang tersedia")

        if current_ratio is not None:
            if current_ratio < 1.0:
                flags.append(
                    f"Current ratio {current_ratio:.2f}× (< 1,0×) — "
                    "likuiditas jangka pendek ketat"
                )
            elif current_ratio >= 1.5:
                strengths.append(f"Current ratio sehat ({current_ratio:.2f}×)")

    if payout is not None and payout > 100.0:
        flags.append(
            f"Rasio pembagian dividen {payout:.0f}% (> 100% laba) — "
            "risiko dividen tidak berkelanjutan"
        )
    elif (
        not fin_sector
        and div_yield is not None and div_yield >= 5.0
        and fcf is not None and fcf < 0
    ):
        flags.append(
            f"Yield dividen {div_yield:.1f}% tapi FCF negatif — "
            "waspada dividend trap"
        )

    return {
        "available": True,
        "fin_sector": fin_sector,
        "ocf": ocf,
        "fcf": fcf,
        "net_cash": net_cash,
        "ocf_to_ni": ocf_to_ni,
        "current_ratio": current_ratio,
        "payout_pct": round(payout, 1) if payout is not None else None,
        "flags": flags,
        "strengths": strengths,
    }


def score_fundamentals(f: dict) -> dict:
    """Skor valuasi -100..+100 lewat poin per aspek (heuristik standar)."""
    points: list[float] = []
    verdicts: list[str] = []

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
        if pbv <= 0:
            points.append(-15); verdicts.append(f"🔴 PBV {pbv:.2f} negatif/nol (ekuitas negatif)")
        elif pbv < 1:
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

    margin = normalize_margin(f.get("margin"))
    if margin is not None:
        if margin < 0:
            points.append(-5); verdicts.append(f"🔴 Margin bersih {margin:.1f}% negatif")
        elif margin >= 15:
            points.append(5); verdicts.append(f"🟢 Margin bersih {margin:.1f}% — tebal")
        elif margin >= 5:
            points.append(0); verdicts.append(f"🟡 Margin bersih {margin:.1f}% — wajar")
        else:
            points.append(-3); verdicts.append(f"🟡 Margin bersih {margin:.1f}% tipis")

    dy = normalize_div_yield(f.get("div_yield"))
    if dy is not None:
        if dy >= 5:
            points.append(10); verdicts.append(f"🟢 Dividend yield {dy:.1f}% — bagus buat pasif")
        elif dy >= 3:
            points.append(5); verdicts.append(f"🟡 Dividend yield {dy:.1f}%")
        elif dy > 0:
            points.append(0); verdicts.append(f"⚪ Dividend yield {dy:.1f}% rendah")

    de = normalize_der(f.get("debt_eq"))
    fin_sector = is_financial_sector(f.get("sector"))
    if de is not None:
        if de < 0:
            points.append(-5); verdicts.append(f"🔴 DER {de:.0f}% negatif (ekuitas bermasalah)")
        elif fin_sector:
            points.append(0)
            verdicts.append(
                f"🏦 DER {de:.0f}% (sektor keuangan/bank — DPK dihitung sebagai liabilitas)"
            )
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
    cf = analyze_cashflow_quality(f)
    for flg in cf["flags"]:
        verdicts.append(f"⚠️ {flg}")
    return {
        "pct": round(raw),
        "label": label,
        "emoji": emoji,
        "notes": verdicts,
        "forensic": cf,
    }
