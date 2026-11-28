"""Analisator Pro Multi-Faktor untuk SahamBot (murni, deterministik, offline-testable).

Menambahkan tiga lapisan analisis di atas `core/analysis.py`, `core/fundamental.py`,
dan `core/cek.py` tanpa mengarang data:
1. Struktur Pasar & Volatility Contraction (VCP / Bollinger Squeeze + Breakout Volume).
2. Matriks Konfluensi 6 Pilar (Harian, Mingguan, Aliran Dana, Fundamental/4Q,
   Valuasi/MoS, Sentimen/Rezim IHSG) beserta deteksi konflik antar-sinyal.
3. Proyeksi 3 Skenario (Bear / Base / Bull) berbasis level teknikal/ATR/Fair Value
   dan daftar Kill Criteria terukur (syarat batal tesis).
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from core import analysis as analysismod, pricing


def _sf(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def detect_market_structure(ind: pd.DataFrame | None) -> dict[str, Any]:
    """Deteksi struktur pasar (HH/HL vs LH/LL), VCP/Bollinger Squeeze, & kualitas breakout."""
    if ind is None or not isinstance(ind, pd.DataFrame) or len(ind) < 35:
        return {
            "available": False,
            "phase": "TIDAK TERSEDIA",
            "tone": "slate",
            "structure": "data kurang",
            "squeeze": False,
            "squeeze_label": "tidak tersedia",
            "bb_width_pctile": None,
            "atr_contraction": None,
            "vol_ratio": None,
            "breakout_state": "tidak tersedia",
            "summary": "Data historis kurang dari 35 bar untuk analisis struktur pasar.",
        }

    close_s = pd.to_numeric(ind["close"], errors="coerce").dropna()
    if len(close_s) < 35:
        return {
            "available": False,
            "phase": "TIDAK TERSEDIA",
            "tone": "slate",
            "structure": "data kurang",
            "squeeze": False,
            "squeeze_label": "tidak tersedia",
            "bb_width_pctile": None,
            "atr_contraction": None,
            "vol_ratio": None,
            "breakout_state": "tidak tersedia",
            "summary": "Data penutupan valid kurang dari 35 bar.",
        }

    last = ind.iloc[-1]
    px = _sf(last.get("close")) or float(close_s.iloc[-1])
    sma20 = _sf(last.get("sma20"))
    sma50 = _sf(last.get("sma50"))
    sma200 = _sf(last.get("sma200"))

    # 1. Struktur pivot fractal (HH/HL vs LH/LL)
    piv_lows, piv_highs = analysismod._fractal_pivots(ind, window=min(80, len(ind)), order=2)
    if len(piv_highs) >= 2 and len(piv_lows) >= 2:
        hh = piv_highs[-1] > piv_highs[-2]
        hl = piv_lows[-1] > piv_lows[-2]
        lh = piv_highs[-1] < piv_highs[-2]
        ll = piv_lows[-1] < piv_lows[-2]
        if hh and hl:
            structure = "HH + HL (Uptrend Sehat)"
        elif lh and ll:
            structure = "LH + LL (Downtrend)"
        elif hl and not hh:
            structure = "HL + LH (Segitiga / Kontraksi)"
        elif hh and not hl:
            structure = "HH + LL (Volatil / Melebar)"
        else:
            structure = "Sideways / Konsolidasi"
    else:
        structure = "Pivot terbatas (Konsolidasi)"

    # 2. Bollinger Band Width Percentile & ATR Contraction (VCP / Squeeze)
    bb_pctile: float | None = None
    if "bb_upper" in ind.columns and "bb_lower" in ind.columns:
        mid = pd.to_numeric(ind.get("sma20", close_s), errors="coerce")
        bw = (pd.to_numeric(ind["bb_upper"], errors="coerce") - pd.to_numeric(ind["bb_lower"], errors="coerce")) / mid.replace(0, np.nan)
        bw_valid = bw.dropna().tail(120)
        if len(bw_valid) >= 20:
            cur_bw = float(bw_valid.iloc[-1])
            bb_pctile = round(float((bw_valid <= cur_bw).mean() * 100.0), 1)

    atr_ratio: float | None = None
    if "atr14" in ind.columns:
        atr_s = pd.to_numeric(ind["atr14"], errors="coerce").dropna()
        if len(atr_s) >= 20:
            atr_short = float(atr_s.tail(5).mean())
            atr_long = float(atr_s.tail(60).mean())
            if atr_long > 0:
                atr_ratio = round(atr_short / atr_long, 2)

    is_squeeze = bool(
        (bb_pctile is not None and bb_pctile <= 25.0)
        or (atr_ratio is not None and atr_ratio <= 0.75)
    )
    if is_squeeze:
        squeeze_label = f"VCP / Squeeze Aktif (BB pct {bb_pctile if bb_pctile is not None else '—'}%, ATR {atr_ratio if atr_ratio is not None else '—'}x)"
    else:
        squeeze_label = f"Normal (BB pct {bb_pctile if bb_pctile is not None else '—'}%)"

    # 3. Volume ratio & status breakout terhadap high 20 hari sebelumnya
    vol_ratio: float | None = None
    if "volume" in ind.columns:
        vol_s = pd.to_numeric(ind["volume"], errors="coerce").dropna()
        if len(vol_s) >= 21:
            avg20_vol = float(vol_s.iloc[-21:-1].mean())
            if avg20_vol > 0:
                vol_ratio = round(float(vol_s.iloc[-1]) / avg20_vol, 2)

    high_s = pd.to_numeric(ind["high"], errors="coerce").dropna() if "high" in ind.columns else close_s
    prev_high20 = float(high_s.iloc[-21:-1].max()) if len(high_s) >= 21 else float(high_s.max())
    prev_low20 = float(pd.to_numeric(ind["low"], errors="coerce").dropna().iloc[-21:-1].min()) if "low" in ind.columns and len(ind) >= 21 else float(close_s.min())

    if prev_high20 > 0 and px >= prev_high20 * 0.995:
        if vol_ratio is not None and vol_ratio >= 1.5:
            breakout_state = f"Breakout Terkonfirmasi Volume ({vol_ratio:.1f}x)"
        else:
            breakout_state = f"Uji/Breakout Resistance 20h (Volume {vol_ratio:.1f}x — waspada fakeout)" if vol_ratio is not None else "Uji Resistance 20h"
    elif prev_low20 > 0 and px <= prev_low20 * 1.005:
        breakout_state = "Uji/Breakdown Support 20h (Risiko Jebol)"
    elif prev_high20 > 0 and px >= prev_high20 * 0.96:
        breakout_state = "Mendekati Resistance 20h (<4%)"
    else:
        breakout_state = "Di dalam rentang 20 hari"

    # 4. Klasifikasi Fase Pasar (Wyckoff-inspired)
    above_50 = sma50 is not None and px > sma50
    above_200 = sma200 is not None and px > sma200
    above_20 = sma20 is not None and px > sma20

    if above_50 and (sma200 is None or above_200) and is_squeeze:
        phase = "KONSOLIDASI VCP (SIAP BREAKOUT)"
        tone = "emerald"
    elif above_20 and above_50 and (sma200 is None or above_200):
        phase = "MARKUP (UPTREND)"
        tone = "emerald"
    elif (not above_50) and sma200 is not None and (not above_200) and (sma50 is not None and sma50 < sma200):
        phase = "MARKDOWN (DOWNTREND)"
        tone = "rose"
    elif above_20 and not above_50:
        phase = "AKUMULASI / REBOUND AWAL"
        tone = "sky"
    elif (not above_20) and above_50:
        phase = "PULLBACK / DISTRIBUSI RINGAN"
        tone = "amber"
    else:
        phase = "TRANSISI / SIDEWAYS"
        tone = "slate"

    summary = f"Fase {phase.lower()} · Struktur {structure} · {breakout_state}."
    return {
        "available": True,
        "phase": phase,
        "tone": tone,
        "structure": structure,
        "squeeze": is_squeeze,
        "squeeze_label": squeeze_label,
        "bb_width_pctile": bb_pctile,
        "atr_contraction": atr_ratio,
        "vol_ratio": vol_ratio,
        "breakout_state": breakout_state,
        "summary": summary,
    }


def build_confluence_matrix(
    detail: dict[str, Any],
    fair_value: dict[str, Any] | None = None,
    structure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bangun matriks konfluensi 6 pilar independen & deteksi konflik antar-sinyal."""
    res = detail.get("res") or {}
    snap = res.get("snap") or {}
    score_tech = res.get("score") or {}
    weekly = res.get("weekly") or {}
    fund = detail.get("fund") or {}
    news = detail.get("news") or {}
    regime = detail.get("market_regime") or detail.get("regime") or {}
    cek_out = detail.get("cek") or {}

    pillars: list[dict[str, Any]] = []

    # Pilar 1: Struktur & Tren Harian
    tech_pct = _sf(score_tech.get("pct"))
    st_phase = (structure or {}).get("phase", "")
    if tech_pct is None:
        pillars.append({"key": "trend", "label": "Tren & Struktur Harian", "status": "NA", "tone": "slate", "detail": "Data teknikal tidak tersedia"})
    elif tech_pct >= 25 and "MARKDOWN" not in st_phase:
        pillars.append({"key": "trend", "label": "Tren & Struktur Harian", "status": "BULLISH", "tone": "emerald", "detail": f"Skor teknikal {tech_pct:+.0f}% · {st_phase or 'Uptrend'}"})
    elif tech_pct <= -20 or "MARKDOWN" in st_phase:
        pillars.append({"key": "trend", "label": "Tren & Struktur Harian", "status": "BEARISH", "tone": "rose", "detail": f"Skor teknikal {tech_pct:+.0f}% · {st_phase or 'Downtrend'}"})
    else:
        pillars.append({"key": "trend", "label": "Tren & Struktur Harian", "status": "NETRAL", "tone": "amber", "detail": f"Skor teknikal {tech_pct:+.0f}% · {st_phase or 'Sideways'}"})

    # Pilar 2: Tren Mingguan (Multi-Timeframe)
    w_dir = str(weekly.get("direction") or "").strip().lower()
    if not w_dir:
        pillars.append({"key": "weekly", "label": "Tren Mingguan (MTF)", "status": "NA", "tone": "slate", "detail": "Data mingguan tidak tersedia"})
    elif w_dir == "naik":
        pillars.append({"key": "weekly", "label": "Tren Mingguan (MTF)", "status": "BULLISH", "tone": "emerald", "detail": "Tren mingguan mengarah NAIK (selaras)"})
    elif w_dir == "turun":
        pillars.append({"key": "weekly", "label": "Tren Mingguan (MTF)", "status": "BEARISH", "tone": "rose", "detail": "Tren mingguan TURUN (awan resistensi)"})
    else:
        pillars.append({"key": "weekly", "label": "Tren Mingguan (MTF)", "status": "NETRAL", "tone": "amber", "detail": f"Tren mingguan {w_dir}"})

    # Pilar 3: Momentum & Aliran Dana (CMF20 + RSI Divergence)
    cmf20 = _sf(detail.get("cmf20"))
    rsi_div = detail.get("rsi_divergence") or {}
    div_type = str(rsi_div.get("type") or "").lower()
    rsi_v = _sf(snap.get("rsi"))
    if cmf20 is None and rsi_v is None:
        pillars.append({"key": "flow", "label": "Aliran Dana & Momentum", "status": "NA", "tone": "slate", "detail": "CMF20/RSI tidak tersedia"})
    elif div_type == "bearish" or (cmf20 is not None and cmf20 < -0.08):
        pillars.append({"key": "flow", "label": "Aliran Dana & Momentum", "status": "BEARISH", "tone": "rose", "detail": f"CMF20 {cmf20:+.2f}" if cmf20 is not None else "Divergensi RSI Bearish"})
    elif div_type == "bullish" or (cmf20 is not None and cmf20 > 0.08):
        pillars.append({"key": "flow", "label": "Aliran Dana & Momentum", "status": "BULLISH", "tone": "emerald", "detail": f"Akumulasi CMF20 {cmf20:+.2f}" if cmf20 is not None else "Divergensi RSI Bullish"})
    else:
        pillars.append({"key": "flow", "label": "Aliran Dana & Momentum", "status": "NETRAL", "tone": "amber", "detail": f"CMF20 {cmf20:+.2f} · RSI {rsi_v:.0f}" if (cmf20 is not None and rsi_v is not None) else "Aliran dana seimbang"})

    # Pilar 4: Fundamental & Forensik 4Q
    f_pct = _sf(fund.get("pct"))
    f_flags = list(fund.get("forensic_flags") or [])
    if f_pct is None:
        pillars.append({"key": "fundamental", "label": "Fundamental & Forensik 4Q", "status": "NA", "tone": "slate", "detail": "Data fundamental tidak tersedia"})
    elif f_pct >= 25 and not f_flags:
        pillars.append({"key": "fundamental", "label": "Fundamental & Forensik 4Q", "status": "BULLISH", "tone": "emerald", "detail": f"Skor fundamental {f_pct:+.0f}% · bersih dari bendera forensik"})
    elif f_pct <= -15 or len(f_flags) >= 2:
        pillars.append({"key": "fundamental", "label": "Fundamental & Forensik 4Q", "status": "BEARISH", "tone": "rose", "detail": f"Skor {f_pct:+.0f}% · {len(f_flags)} bendera forensik"})
    else:
        pillars.append({"key": "fundamental", "label": "Fundamental & Forensik 4Q", "status": "NETRAL", "tone": "amber", "detail": f"Skor {f_pct:+.0f}% · {len(f_flags)} catatan forensik"})

    # Pilar 5: Valuasi & Margin of Safety
    fv = fair_value or {}
    mos = _sf(fv.get("mos_pct")) if fv.get("available") else None
    if mos is None:
        pillars.append({"key": "valuation", "label": "Valuasi & Nilai Wajar", "status": "NA", "tone": "slate", "detail": "Estimasi nilai wajar belum tersedia"})
    elif mos >= 15.0:
        pillars.append({"key": "valuation", "label": "Valuasi & Nilai Wajar", "status": "BULLISH", "tone": "emerald", "detail": f"Undervalued (MoS {mos:+.1f}%)"})
    elif mos <= -10.0:
        pillars.append({"key": "valuation", "label": "Valuasi & Nilai Wajar", "status": "BEARISH", "tone": "rose", "detail": f"Overvalued (MoS {mos:+.1f}%)"})
    else:
        pillars.append({"key": "valuation", "label": "Valuasi & Nilai Wajar", "status": "NETRAL", "tone": "amber", "detail": f"Harga wajar (MoS {mos:+.1f}%)"})

    # Pilar 6: Sentimen Berita & Rezim IHSG
    n_pct = _sf(news.get("pct"))
    n_cnt = int(news.get("n") or 0)
    reg_label = str(regime.get("regime") or regime.get("status") or regime.get("label") or "").upper()
    reg_defensive = "DEFENSIVE" in reg_label or "BEAR" in reg_label or "RISK_OFF" in reg_label
    if n_cnt == 0 and not reg_label:
        pillars.append({"key": "macro", "label": "Sentimen & Rezim IHSG", "status": "NA", "tone": "slate", "detail": "Sentimen berita & rezim belum tersedia"})
    elif reg_defensive or (n_pct is not None and n_pct <= -25):
        pillars.append({"key": "macro", "label": "Sentimen & Rezim IHSG", "status": "BEARISH", "tone": "rose", "detail": f"Rezim {reg_label or '—'} · Sentimen {n_pct:+.0f}%" if n_pct is not None else f"Rezim IHSG {reg_label}"})
    elif (not reg_defensive) and (n_pct is not None and n_pct >= 20):
        pillars.append({"key": "macro", "label": "Sentimen & Rezim IHSG", "status": "BULLISH", "tone": "emerald", "detail": f"Rezim {reg_label or 'kondusif'} · Sentimen {n_pct:+.0f}%"})
    else:
        pillars.append({"key": "macro", "label": "Sentimen & Rezim IHSG", "status": "NETRAL", "tone": "amber", "detail": f"Rezim {reg_label or 'netral'} · Berita {n_cnt} item"})

    valid = [p for p in pillars if p["status"] != "NA"]
    n_bull = sum(1 for p in valid if p["status"] == "BULLISH")
    n_bear = sum(1 for p in valid if p["status"] == "BEARISH")
    n_net = sum(1 for p in valid if p["status"] == "NETRAL")
    n_valid = len(valid)

    # Skor konfluensi 0-100: BULLISH=1.0, NETRAL=0.5, BEARISH=0.0
    score = round(((n_bull + 0.5 * n_net) / n_valid) * 100.0, 1) if n_valid > 0 else None

    conflicts: list[str] = []
    by_key = {p["key"]: p for p in pillars}
    if by_key["trend"]["status"] == "BULLISH" and by_key["flow"]["status"] == "BEARISH":
        conflicts.append("Harga menguat, tetapi aliran dana (CMF20/RSI) menunjukkan distribusi/divergensi bearish.")
    if by_key["trend"]["status"] == "BULLISH" and by_key["fundamental"]["status"] == "BEARISH":
        conflicts.append("Momentum teknikal positif, namun fundamental/forensik 4Q memiliki risiko tinggi.")
    if by_key["valuation"]["status"] == "BULLISH" and by_key["trend"]["status"] == "BEARISH":
        conflicts.append("Valuasi murah (undervalued), tetapi tren masih turun — risiko value trap jika masuk terlalu cepat.")
    if by_key["trend"]["status"] == "BULLISH" and by_key["weekly"]["status"] == "BEARISH":
        conflicts.append("Rebound harian melawan tren mingguan yang masih turun — rawan tertahan resistance.")
    if cek_out.get("vetoes"):
        conflicts.append(f"Ada {len(cek_out['vetoes'])} veto aktif di /cek yang membatasi agresivitas beli.")

    if score is None:
        conviction = "DATA TERBATAS"
        conv_tone = "slate"
    elif n_bull >= 4 and n_bear == 0 and not cek_out.get("vetoes"):
        conviction = "KEYAKINAN TINGGI (SELARAS)"
        conv_tone = "emerald"
    elif n_bear >= 3 or (cek_out.get("vetoes") and n_bear >= 2):
        conviction = "RISIKO TINGGI (HINDARI)"
        conv_tone = "rose"
    elif conflicts:
        conviction = "SINYAL KONFLIK (SELEKTIF)"
        conv_tone = "amber"
    elif score >= 60:
        conviction = "KEYAKINAN SEDANG (KONSTRUKTIF)"
        conv_tone = "sky"
    else:
        conviction = "NETRAL / TUNGGU KONFIRMASI"
        conv_tone = "amber"

    return {
        "score": score,
        "conviction": conviction,
        "tone": conv_tone,
        "counts": {"bullish": n_bull, "neutral": n_net, "bearish": n_bear, "valid": n_valid, "total": len(pillars)},
        "pillars": pillars,
        "conflicts": conflicts,
    }


def build_scenarios_and_kill_criteria(
    detail: dict[str, Any],
    fair_value: dict[str, Any] | None = None,
    structure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Susun 3 skenario deterministik (Bear / Base / Bull) dan daftar Kill Criteria."""
    res = detail.get("res") or {}
    snap = res.get("snap") or {}
    plan = detail.get("plan") or {}
    px = _sf(snap.get("close")) or _sf(plan.get("entry"))
    atr = _sf(snap.get("atr14")) or _sf(plan.get("atr"))
    sl = _sf(plan.get("sl"))
    tp = _sf(plan.get("tp"))
    sma50 = _sf(snap.get("sma50"))
    sma200 = _sf(snap.get("sma200"))
    fv_val = _sf((fair_value or {}).get("fair_value")) if (fair_value or {}).get("available") else None

    if px is None or px <= 0:
        return {"available": False, "scenarios": [], "kill_criteria": []}

    # Bear target: SL terverifikasi atau px - 2*ATR
    bear_px = pricing.round_tick(sl if (sl and sl < px) else max(1.0, px - (2.0 * atr if atr else px * 0.07)))
    # Base target: TP1/TP rencana atau rata-rata dengan fair value bila wajar
    base_raw = tp if (tp and tp > px) else (px + (2.0 * atr if atr else px * 0.08))
    if fv_val and fv_val > px * 1.03:
        base_raw = min(max(base_raw, px * 1.05), (base_raw + fv_val) / 2.0)
    base_px = pricing.round_tick(max(px + pricing.tick_size(px), base_raw))
    # Bull target: ekstensi +1.5x jarak base atau fair value (mana lebih tinggi, dibatasi realistis +60%)
    bull_raw = max(base_px + (base_px - px) * 0.7, fv_val or 0.0)
    bull_raw = min(bull_raw, px * 1.60)
    bull_px = pricing.round_tick(max(base_px + pricing.tick_size(base_px), bull_raw))

    def _pct(target: float) -> float:
        return round((target - px) / px * 100.0, 1)

    scenarios = [
        {
            "case": "Bear Case",
            "tone": "rose",
            "price": bear_px,
            "pct": _pct(bear_px),
            "note": "Batas risiko / breakdown support atau SL tersentuh — disiplin cut loss.",
        },
        {
            "case": "Base Case",
            "tone": "sky",
            "price": base_px,
            "pct": _pct(base_px),
            "note": "Target realistis berbasis TP rencana & nilai wajar konservatif.",
        },
        {
            "case": "Bull Case",
            "tone": "emerald",
            "price": bull_px,
            "pct": _pct(bull_px),
            "note": "Skenario akselerasi momentum / breakout volume menuju valuasi penuh.",
        },
    ]

    kill_criteria: list[str] = [
        f"Penutupan harian di bawah Rp{int(round(bear_px)):,} ({_pct(bear_px):+.1f}%) — keluar tanpa tawar-menawar."
    ]
    if sma50 and sma200:
        if sma50 >= sma200:
            kill_criteria.append(f"Terjadi Death Cross (SMA50 < SMA200 di Rp{int(round(sma200)):,}) atau penutupan gagal bertahan di atas SMA50.")
        else:
            kill_criteria.append(f"Selama harga masih di bawah SMA50 (Rp{int(round(sma50)):,}) & SMA200 (Rp{int(round(sma200)):,}), hindari entry agresif (falling knife).")
    kill_criteria.append("Aliran dana berbalik distribusi kuat (CMF20 < -0,15) disertai volume jual di atas rata-rata 20 hari.")
    if (detail.get("fund") or {}).get("forensic_flags"):
        kill_criteria.append("Bendera forensik kas/laba kuartalan memburuk pada rilis laporan keuangan berikutnya.")
    if (structure or {}).get("squeeze"):
        kill_criteria.append("Breakout dari pola VCP/Squeeze gagal bertahan (false breakout kembali masuk rentang dengan volume besar).")

    return {
        "available": True,
        "current_price": px,
        "scenarios": scenarios,
        "kill_criteria": kill_criteria,
    }


def analyze_pro(
    detail: dict[str, Any],
    fair_value: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Jalankan paket Analisator Pro lengkap untuk satu emiten."""
    ind = (detail.get("res") or {}).get("ind")
    structure = detect_market_structure(ind)
    confluence = build_confluence_matrix(detail, fair_value=fair_value, structure=structure)
    scenarios = build_scenarios_and_kill_criteria(detail, fair_value=fair_value, structure=structure)
    return {
        "structure": structure,
        "confluence": confluence,
        "scenarios": scenarios,
    }


def score_candidate_precision(
    row: dict[str, Any],
    *,
    fund: dict[str, Any] | None = None,
    nsent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Hitung Skor Presisi (0-100) & Grade (A+/A/B/C/HINDARI) untuk kandidat /sinyal & /paket.

    Tidak mengubah bobot beku `core/relatif.py`, melainkan menggabungkan
    peringkat relatif OOS dengan filter kualitas tren, anti-death-cross,
    anti-chase, likuiditas, fundamental/forensik, dan sentimen berita.
    """
    score = 35.0
    notes: list[str] = []

    rel_pct = _sf(row.get("rel_pct"))
    if rel_pct is not None:
        if rel_pct >= 80:
            score += 25.0
            notes.append(f"Relatif kuat ({rel_pct:.0f}%)")
        elif rel_pct >= 70:
            score += 18.0
        elif rel_pct >= 50:
            score += 8.0
        elif rel_pct < 30:
            score -= 15.0
            notes.append("Peringkat relatif lemah")

    px = _sf(row.get("price")) or _sf(row.get("entry"))
    sma50 = _sf(row.get("sma50"))
    sma200 = _sf(row.get("sma200"))
    if px and sma50:
        if sma200 is not None and sma50 < sma200:
            score -= 25.0
            notes.append("Death Cross (SMA50 < SMA200)")
        elif px > sma50 and (sma200 is None or px > sma200):
            score += 18.0
            notes.append("Uptrend di atas SMA50/200")
        elif px < sma50:
            score -= 15.0
            notes.append("Harga di bawah SMA50")

    rsi_v = _sf(row.get("rsi"))
    macd_h = _sf(row.get("macd_hist"))
    if rsi_v is not None:
        if 42.0 <= rsi_v <= 68.0 and (macd_h is None or macd_h >= 0):
            score += 12.0
        elif rsi_v >= 75.0:
            score -= 15.0
            notes.append(f"Overbought ekstrem (RSI {rsi_v:.0f})")

    ret20 = _sf(row.get("ret20d"))
    if ret20 is not None and ret20 > 15.0:
        score -= 15.0
        notes.append(f"Chase trap (+{ret20:.1f}% 20h)")

    val20 = _sf(row.get("value_avg20"))
    if val20 is not None:
        if val20 >= 10_000_000_000:
            score += 12.0
        elif val20 >= 2_000_000_000:
            score += 6.0
        elif val20 < 1_000_000_000:
            score -= 20.0
            notes.append("Likuiditas tipis (<Rp1M/hari)")

    board = str(row.get("board") or "").lower()
    if "pemantauan" in board or "fca" in board:
        score -= 30.0
        notes.append("Papan Pemantauan Khusus (FCA)")

    if row.get("quality_ok") is False:
        score -= 15.0
        notes.append("Flag kualitas data")

    if isinstance(fund, dict):
        fp = _sf(fund.get("pct"))
        flags = list(fund.get("forensic_flags") or [])
        if fp is not None:
            if fp >= 25.0 and not flags:
                score += 12.0
                notes.append("Fundamental kuat & bersih")
            elif fp < -15.0:
                score -= 15.0
                notes.append("Fundamental lemah")
        if flags:
            score -= min(25.0, 10.0 * len(flags))
            notes.append(f"{len(flags)} bendera forensik")

    if isinstance(nsent, dict) and int(nsent.get("n") or 0) > 0:
        npct = _sf(nsent.get("pct"))
        if npct is not None:
            if npct >= 20.0:
                score += 6.0
            elif npct <= -25.0:
                score -= 10.0
                notes.append("Sentimen berita negatif")

    final_score = round(max(0.0, min(100.0, score)), 1)
    if final_score >= 82.0:
        grade, tone = "A+", "emerald"
    elif final_score >= 70.0:
        grade, tone = "A", "emerald"
    elif final_score >= 55.0:
        grade, tone = "B", "sky"
    elif final_score >= 40.0:
        grade, tone = "C", "amber"
    else:
        grade, tone = "HINDARI", "rose"

    return {
        "score": final_score,
        "grade": grade,
        "tone": tone,
        "note": "; ".join(notes[:3]) if notes else "Konfluensi standar",
    }


def format_pro_summary_html(pro_out: dict[str, Any] | None) -> str:
    """Format ringkas Analisator Pro dalam HTML aman (Telegram/teks)."""
    from core.reports import esc

    if not isinstance(pro_out, dict):
        return ""
    st = pro_out.get("structure") or {}
    cf = pro_out.get("confluence") or {}
    sc = pro_out.get("scenarios") or {}
    cnt = cf.get("counts") or {}
    lines = ["🧭 <b>ANALISATOR PRO (6 PILAR &amp; STRUKTUR)</b>"]
    if cf.get("score") is not None:
        lines.append(
            f"• Konfluensi: <b>{cf['score']}/100</b> — <b>{esc(cf.get('conviction') or '—')}</b> "
            f"({cnt.get('bullish', 0)}B / {cnt.get('neutral', 0)}N / {cnt.get('bearish', 0)}R)"
        )
    if st.get("available"):
        sq = " · <b>VCP/SQUEEZE</b>" if st.get("squeeze") else ""
        lines.append(f"• Fase: <b>{esc(st.get('phase') or '—')}</b>{sq} ({esc(st.get('structure') or '—')})")
    if sc.get("available") and sc.get("scenarios"):
        parts = [
            f"{esc(x['case'].split()[0])} Rp{int(round(x['price'])):,} ({x['pct']:+.1f}%)"
            for x in sc["scenarios"]
        ]
        lines.append("• Skenario: " + " | ".join(parts))
    for c in (cf.get("conflicts") or [])[:2]:
        lines.append(f"• ⚠️ {esc(c)}")
    return "\n".join(lines)
