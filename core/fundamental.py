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
from pathlib import Path
import time

import yfinance as yf

from core import data as datamod

_FUND_TTL = int(os.getenv("SAHAMBOT_FUND_TTL", "21600") or 0)  # 6 jam
CACHE_VERSION = 2
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


def normalize_currency(v) -> str:
    """Normalisasi kode mata uang laporan keuangan ('IDR', 'USD', atau 'tidak tersedia')."""
    if v is None:
        return "tidak tersedia"
    s = str(v).strip().upper()
    return s if s else "tidak tersedia"


def normalize_roe(v, unit: str | None = None) -> float | None:
    """Normalisasi ROE ke skala persen.

    - ``unit`` eksplisit ``"pct"`` / ``"percent"`` / ``"%"`` → sudah persen,
      TIDAK dikali 100 lagi (melindungi ROE mikro seperti 0.2% atau 2.5%).
    - ``unit`` eksplisit ``"fraction"`` / ``"ratio"`` / ``"decimal"`` → ×100.
    - ``unit is None`` (dict/cache lama tanpa tag unit) → heuristik kompatibel
      ``abs(f) < 3.0`` dianggap fraksi Yahoo (0.18 → 18%).
    """
    f = _finite_float(v)
    if f is None:
        return None
    if unit is not None:
        u = str(unit).strip().lower()
        if u in ("pct", "percent", "%"):
            return f
        if u in ("fraction", "ratio", "decimal", "dec"):
            return f * 100.0
    return f * 100.0 if abs(f) < 3.0 else f


def build_filing_note(f: dict | None) -> str:
    """Catatan jujur keterlambatan laporan keuangan (POJK 14 / TTM vs tahunan)."""
    raw = f or {}
    filing_date = raw.get("filing_date")
    as_of = raw.get("as_of")
    if filing_date:
        return f"Tanggal rilis/filing: {filing_date} (data agregator Yahoo Finance)"
    if as_of:
        return (
            f"Periode {as_of} (TTM/ringkasan Yahoo Finance; tanggal rilis filing BEI / "
            "POJK 14 tidak tersedia — cek keterlambatan laporan resmi)"
        )
    return (
        "Tanggal filing/periode laporan tidak tersedia (data TTM vs tahunan Yahoo bisa "
        "tertinggal dari rilis BEI/POJK 14)"
    )


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



_FX_CACHE: dict[str, tuple[float, float]] = {}


def _get_usdidr_rate(fallback: float = 18364.0) -> float:
    """Ambil kurs USD/IDR dari Yahoo (cache 1 jam, fallback konservatif bila offline)."""
    now = time.time()
    cached = _FX_CACHE.get("USDIDR")
    if cached and (now - cached[0]) < 3600:
        return cached[1]
    rate = None
    try:
        hist = yf.Ticker("USDIDR=X").history(period="5d")
        if hist is not None and not hist.empty and "Close" in hist.columns:
            v = _finite_float(hist["Close"].dropna().iloc[-1])
            if v is not None and 10000.0 <= v <= 30000.0:
                rate = v
    except Exception:
        pass
    final = rate if rate is not None else fallback
    _FX_CACHE["USDIDR"] = (now, final)
    return final



def load_corp_action_override(symbol: str | None) -> dict | None:
    """Muat catatan aksi korporasi & rasio bank terverifikasi dari data/corp_action_overrides.json."""
    if not symbol:
        return None
    code = str(symbol).strip().upper().replace(".JK", "")
    p = Path(__file__).resolve().parent.parent / "data" / "corp_action_overrides.json"
    if not p.is_file():
        return None
    try:
        import json
        raw = json.loads(p.read_text(encoding="utf-8"))
        val = raw.get(code)
        return val if isinstance(val, dict) else None
    except Exception:
        return None


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

    raw_roe = _finite_float(g("returnOnEquity"))
    roe_pct = raw_roe * 100.0 if raw_roe is not None else None
    fin_currency = normalize_currency(g("financialCurrency", "currency"))
    px_currency = normalize_currency(g("currency"))
    raw_pbv = _finite_float(g("priceToBook"))
    fx_usdidr = None
    if fin_currency == "USD" and px_currency == "IDR":
        fx_usdidr = _get_usdidr_rate()
        if raw_pbv is not None and raw_pbv > 100.0:
            bvps = _finite_float(g("bookValue"))
            cur_px = _finite_float(g("currentPrice", "regularMarketPrice"))
            if bvps is not None and bvps > 0 and cur_px is not None and cur_px > 0:
                raw_pbv = round(cur_px / (bvps * fx_usdidr), 4)
            else:
                raw_pbv = round(raw_pbv / fx_usdidr, 4)

    fields = {
        "_v": CACHE_VERSION,
        "symbol": symbol.replace(".JK", "").upper(),
        "name": g("longName", "shortName") or symbol.replace(".JK", ""),
        "sector": g("sector", "industry"),
        "currency": fin_currency,
        "price_currency": px_currency,
        "fx_usdidr": fx_usdidr,
        "market_cap": g("marketCap"),
        "per": _finite_float(g("trailingPE")),
        "pbv": raw_pbv,
        "eps": _finite_float(g("trailingEps")),
        "div_yield": dy_val,
        "roe": roe_pct,
        "roe_unit": "pct",
        "margin": _finite_float(g("profitMargins")),
        "debt_eq": _finite_float(g("debtToEquity")),
        "ocf": _finite_float(g("operatingCashflow")),
        "fcf": _finite_float(g("freeCashflow")),
        "net_income": _finite_float(g("netIncomeToCommon")),
        "total_cash": _finite_float(g("totalCash")),
        "total_debt": _finite_float(g("totalDebt")),
        "current_ratio": _finite_float(g("currentRatio")),
        "payout_ratio": _finite_float(g("payoutRatio")),
        "ev_ebitda": _finite_float(g("enterpriseToEbitda")),
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
    fields["filing_note"] = build_filing_note(fields)
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
    filing_note = build_filing_note(raw)
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
            "forensic_flags": [],
            "forensic_penalty": 0,
            "filing_note": filing_note,
        }

    flags: list[str] = []
    strengths: list[str] = []
    forensic_flags: list[str] = []
    raw_penalty = 0
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
                forensic_flags.append("ocf_negatif_vs_laba")
                raw_penalty += 25
            elif ocf_to_ni < 0.7:
                flags.append(
                    f"Konversi kas lemah (OCF {ocf_to_ni:.2f}× laba bersih)"
                )
                forensic_flags.append("konversi_kas_lemah")
                raw_penalty += 15
            elif ocf_to_ni >= 1.0:
                strengths.append(
                    f"Laba didukung kas nyata (OCF {ocf_to_ni:.2f}× laba bersih)"
                )
        elif ocf is not None and ocf < 0:
            flags.append("Arus kas operasi (OCF) negatif")
            forensic_flags.append("ocf_negatif")
            raw_penalty += 15

        if fcf is not None:
            if fcf < 0:
                flags.append(
                    "Free cash flow (FCF) negatif (arus kas tersedot operasi/capex)"
                )
                forensic_flags.append("fcf_negatif")
                raw_penalty += 15
            elif fcf > 0:
                strengths.append("Free cash flow (FCF) positif")

        if net_cash is not None:
            if net_cash > 0:
                strengths.append("Posisi kas bersih positif (kas > utang)")
            elif (total_cash <= 0 and total_debt > 0) or (
                total_cash > 0 and total_debt > total_cash * 2.0
            ):
                flags.append("Utang lebih dari 2× kas yang tersedia")
                forensic_flags.append("utang_gt_2x_kas")
                raw_penalty += 15

        if current_ratio is not None:
            if current_ratio < 1.0:
                flags.append(
                    f"Current ratio {current_ratio:.2f}× (< 1,0×) — "
                    "likuiditas jangka pendek ketat"
                )
                forensic_flags.append("current_ratio_lt_1")
                raw_penalty += 12
            elif current_ratio >= 1.5:
                strengths.append(f"Current ratio sehat ({current_ratio:.2f}×)")

    if payout is not None and payout > 100.0:
        flags.append(
            f"Rasio pembagian dividen {payout:.0f}% (> 100% laba) — "
            "risiko dividen tidak berkelanjutan"
        )
        forensic_flags.append("payout_gt_100")
        raw_penalty += 20
    elif (
        not fin_sector
        and div_yield is not None and div_yield >= 5.0
        and fcf is not None and fcf < 0
    ):
        flags.append(
            f"Yield dividen {div_yield:.1f}% tapi FCF negatif — "
            "waspada dividend trap"
        )
        forensic_flags.append("dividend_trap_fcf_negatif")
        raw_penalty += 15

    forensic_penalty = min(90, raw_penalty)

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
        "forensic_flags": forensic_flags,
        "forensic_penalty": forensic_penalty,
        "filing_note": filing_note,
    }



# Patokan median kasar PER & PBV per sektor BEI untuk konteks valuasi relatif.
SECTOR_BENCHMARKS: dict[str, tuple[float, float]] = {
    "financial services": (12.0, 1.8),
    "energy": (9.0, 1.4),
    "basic materials": (14.0, 1.6),
    "consumer defensive": (18.0, 3.0),
    "consumer cyclical": (16.0, 2.0),
    "communication services": (16.0, 2.4),
    "industrials": (12.0, 1.5),
    "healthcare": (22.0, 3.5),
    "real estate": (12.0, 0.9),
    "utilities": (14.0, 1.5),
    "technology": (25.0, 3.0),
}


def compute_fcf_yield(f: dict | None) -> float | None:
    """FCF Yield (%) = Free Cash Flow / Market Cap × 100 (ditolak bila beda mata uang USD vs IDR)."""
    raw = f or {}
    fin_cur = normalize_currency(raw.get("currency"))
    px_cur = normalize_currency(raw.get("price_currency")) if raw.get("price_currency") else "IDR"
    fx = _finite_float(raw.get("fx_usdidr"))
    fcf = _finite_float(raw.get("fcf"))
    if fin_cur.upper() != "TIDAK TERSEDIA" and px_cur.upper() != "TIDAK TERSEDIA" and fin_cur.upper() != px_cur.upper():
        if fin_cur.upper() == "USD" and px_cur.upper() == "IDR" and fx is not None and fx > 0 and fcf is not None:
            fcf = fcf * fx
        else:
            return None
    mcap = _finite_float(raw.get("market_cap"))
    if fcf is None or mcap is None or mcap <= 0:
        return None
    return round(fcf / mcap * 100.0, 2)


def sector_relative_valuation(f: dict | None) -> dict | None:
    """Bandingkan PER & PBV emiten terhadap patokan sektor BEI."""
    raw = f or {}
    sec = str(raw.get("sector") or "").strip().lower()
    if not sec:
        return None
    bench = None
    for key, pair in SECTOR_BENCHMARKS.items():
        if key in sec:
            bench = (key, pair[0], pair[1])
            break
    if bench is None:
        return None
    sec_key, bench_per, bench_pbv = bench
    per = _finite_float(raw.get("per"))
    pbv = _finite_float(raw.get("pbv"))
    out: dict = {"sector": sec_key, "bench_per": bench_per, "bench_pbv": bench_pbv}
    if per is not None and per > 0:
        out["per_diff_pct"] = round((per / bench_per - 1.0) * 100.0, 1)
    if pbv is not None and pbv > 0:
        out["pbv_diff_pct"] = round((pbv / bench_pbv - 1.0) * 100.0, 1)
    return out if ("per_diff_pct" in out or "pbv_diff_pct" in out) else None


def analyze_quarterly_trend(f: dict | None) -> dict:
    """Evaluasi momentum pendapatan & laba 2–4 kuartal terakhir (offline, jujur bila NA)."""
    raw = f or {}
    rev_raw = raw.get("rev_trend") or []
    prof_raw = raw.get("profit_trend") or []

    def _vals(series) -> list[float]:
        out: list[float] = []
        for item in series:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                v = _finite_float(item[1])
                if v is not None:
                    out.append(v)
        return out

    revs = _vals(rev_raw)
    profs = _vals(prof_raw)
    if len(revs) < 2 and len(profs) < 2:
        return {
            "available": False,
            "flags": [],
            "strengths": [],
            "forensic_flags": [],
            "penalty": 0,
            "rev_growth_pct": None,
            "profit_growth_pct": None,
        }

    flags: list[str] = []
    strengths: list[str] = []
    forensic_flags: list[str] = []
    penalty = 0
    rev_growth_pct = None
    profit_growth_pct = None

    if len(profs) >= 2:
        first_p, last_p = profs[0], profs[-1]
        if first_p > 0:
            profit_growth_pct = round((last_p - first_p) / abs(first_p) * 100.0, 1)
        declines = sum(1 for i in range(1, len(profs)) if profs[i] < profs[i - 1])
        if last_p < 0:
            flags.append("Kuartal terakhir mencatat rugi bersih — waspada tren laba memburuk")
            forensic_flags.append("kuartal_terakhir_rugi")
            penalty += 20
        elif declines >= 2 and last_p < first_p:
            flags.append(f"Laba kuartalan menyusut beruntun ({len(profs)}Q terakhir)")
            forensic_flags.append("laba_turun_beruntun")
            penalty += 10
        elif all(p > 0 for p in profs) and last_p > first_p * 1.05 and profs[-1] >= profs[-2]:
            pct_str = f" ({profit_growth_pct:+.0f}%)" if profit_growth_pct is not None else ""
            strengths.append(f"Laba kuartalan tumbuh konsisten{pct_str}")

    if len(revs) >= 2:
        first_r, last_r = revs[0], revs[-1]
        if first_r > 0:
            rev_growth_pct = round((last_r - first_r) / abs(first_r) * 100.0, 1)
        declines_r = sum(1 for i in range(1, len(revs)) if revs[i] < revs[i - 1])
        if declines_r >= 2 and last_r < first_r:
            flags.append(f"Pendapatan kuartalan menyusut beruntun ({len(revs)}Q terakhir)")
            forensic_flags.append("pendapatan_menyusut")
            penalty += 5
        elif all(r > 0 for r in revs) and last_r > first_r * 1.05 and revs[-1] >= revs[-2]:
            pct_str = f" ({rev_growth_pct:+.0f}%)" if rev_growth_pct is not None else ""
            strengths.append(f"Pendapatan kuartalan tumbuh konsisten{pct_str}")

    return {
        "available": True,
        "flags": flags,
        "strengths": strengths,
        "forensic_flags": forensic_flags,
        "penalty": min(30, penalty),
        "rev_growth_pct": rev_growth_pct,
        "profit_growth_pct": profit_growth_pct,
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

    roe = normalize_roe(f.get("roe"), unit=f.get("roe_unit"))
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

    base_raw = max(-40, min(40, sum(points))) * 2.5  # skala -100..100
    cf = analyze_cashflow_quality(f)
    qt = analyze_quarterly_trend(f)
    ov = f.get("corp_override") or load_corp_action_override(f.get("symbol"))
    ov_flags = list((ov or {}).get("corp_action_flags") or [])
    ov_pen = int((ov or {}).get("extra_penalty") or 0)
    forensic_penalty = min(90, int(cf.get("forensic_penalty") or 0) + int(qt.get("penalty") or 0) + ov_pen)
    raw = max(-100.0, min(100.0, base_raw - forensic_penalty))
    if raw >= 50:
        label, emoji = "FUNDAMENTAL KUAT", "💪"
    elif raw >= 15:
        label, emoji = "CUKUP", "🙂"
    elif raw > -15:
        label, emoji = "MIX", "🤷"
    else:
        label, emoji = "LEMAH", "🫤"
    for flg in cf["flags"] + qt["flags"] + ov_flags:
        verdicts.append(f"⚠️ {flg}")
    if ov and ov.get("bank_ratios"):
        br = ov["bank_ratios"]
        verdicts.append(
            f"🏦 Rasio Bank ({ov.get('as_of', 'lokal')}): NIM {br.get('nim_pct', 'n/a')}% | "
            f"NPF {br.get('npf_gross_pct', 'n/a')}% | CAR {br.get('car_pct', 'n/a')}% | CASA {br.get('casa_pct', 'n/a')}%"
        )
    for ok_q in qt["strengths"]:
        verdicts.append(f"📈 {ok_q}")
    if forensic_penalty > 0:
        verdicts.append(
            f"📉 Penalti forensik kas/neraca: -{forensic_penalty} poin "
            f"(skor dasar {round(base_raw):+d}% → {round(raw):+d}%)"
        )
    sector_rel = sector_relative_valuation(f)
    if sector_rel:
        parts = []
        if "per_diff_pct" in sector_rel:
            d = sector_rel["per_diff_pct"]
            tag = "diskon" if d < -10 else ("premium" if d > 15 else "sejalan")
            parts.append(f"PER {tag} {d:+.0f}% vs patokan ~{sector_rel['bench_per']:.0f}x")
        if "pbv_diff_pct" in sector_rel:
            d = sector_rel["pbv_diff_pct"]
            tag = "diskon" if d < -10 else ("premium" if d > 15 else "sejalan")
            parts.append(f"PBV {tag} {d:+.0f}% vs patokan ~{sector_rel['bench_pbv']:.1f}x")
        if parts:
            verdicts.append(f"🏷️ Relatif sektor ({sector_rel['sector'].title()}): " + ", ".join(parts))

    fcf_yield = compute_fcf_yield(f)
    if fcf_yield is not None and not fin_sector:
        verdicts.append(f"💵 FCF Yield: {fcf_yield:+.1f}%")
    ev_ebitda = _finite_float(f.get("ev_ebitda"))
    if ev_ebitda is not None and ev_ebitda > 0 and not fin_sector:
        verdicts.append(f"📐 EV/EBITDA: {ev_ebitda:.1f}x")

    filing_note = cf.get("filing_note") or build_filing_note(f)
    return {
        "pct": round(raw),
        "base_pct": round(base_raw),
        "label": label,
        "emoji": emoji,
        "notes": verdicts,
        "forensic": cf,
        "quarterly": qt,
        "forensic_penalty": forensic_penalty,
        "forensic_flags": list(cf.get("forensic_flags") or []) + list(qt.get("forensic_flags") or []) + (["corp_action_override"] if ov_flags else []),
        "corp_override": ov,
        "currency": normalize_currency(f.get("currency")),
        "filing_note": filing_note,
        "sector_rel": sector_rel,
        "fcf_yield": fcf_yield,
        "ev_ebitda": ev_ebitda,
    }
