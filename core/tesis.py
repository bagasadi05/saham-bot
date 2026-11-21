"""Pelacak tesis posisi portofolio asli (`GUGUR` / `MELEMAH` / `MENGUAT` / `TETAP`).

Mengevaluasi apakah alasan memegang sebuah posisi masih utuh berdasarkan data
yang benar-benar tersedia (harga vs SL/Chandelier, tren teknikal & MA50,
peringkat relatif lintas-pasar terhadap pita tahan 60%, time-stop, berita,
forensik fundamental, dan rezim IHSG).

Prinsip kejujuran (`AGENTS.md`):
- Bila harga tidak tersedia (`close` kosong/<=0), status dikembalikan sebagai
  `TIDAK_TERSEDIA` (`data_ok=False`), tidak diisi nol dan tidak memicu alert.
- Alert exit/perubahan tesis hanya berbunyi saat status berubah, tidak
  diulang-ulang selama status bertahan.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from core import analysis, saran
from core.reports import esc

GUGUR = "GUGUR"
MELEMAH = "MELEMAH"
TETAP = "TETAP"
MENGUAT = "MENGUAT"
TIDAK_TERSEDIA = "TIDAK_TERSEDIA"

VALID_STATUSES = (GUGUR, MELEMAH, TETAP, MENGUAT)

_STATUS_META: dict[str, tuple[str, str, int]] = {
    GUGUR: ("🔴", "GUGUR (tesis batal)", 3),
    MELEMAH: ("🟠", "MELEMAH (perlu tinjau)", 2),
    TETAP: ("⚪", "TETAP (tesis terjaga)", 1),
    MENGUAT: ("🟢", "MENGUAT (tesis terkonfirmasi)", 0),
    TIDAK_TERSEDIA: ("❔", "tidak tersedia", -1),
}


def _num(val) -> float | None:
    try:
        x = float(val)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _extract_tech(
    df: pd.DataFrame | None,
    ind: pd.DataFrame | None,
    tech_score: dict | None,
) -> tuple[float | None, float | None, float | None]:
    """Ambil (tech_pct, sma20, sma50) dari argumen atau hitung dari DataFrame."""
    use_ind = ind
    if use_ind is None and isinstance(df, pd.DataFrame) and len(df) >= 20:
        try:
            use_ind = analysis.compute_indicators(df)
        except Exception:
            use_ind = None

    tech_pct = _num((tech_score or {}).get("pct"))
    if tech_pct is None and isinstance(use_ind, pd.DataFrame) and not use_ind.empty:
        try:
            tech_pct = _num(analysis.score_stock(use_ind).get("pct"))
        except Exception:
            tech_pct = None

    sma20 = sma50 = None
    if isinstance(use_ind, pd.DataFrame) and not use_ind.empty:
        last = use_ind.iloc[-1]
        sma20 = _num(last.get("sma20")) if "sma20" in use_ind.columns else None
        sma50 = _num(last.get("sma50")) if "sma50" in use_ind.columns else None
    return tech_pct, sma20, sma50


def evaluate_position(
    ticker: str,
    avg_price: float | None,
    close: float | None,
    *,
    low: float | None = None,
    df: pd.DataFrame | None = None,
    ind: pd.DataFrame | None = None,
    tech_score: dict | None = None,
    sl: float | None = None,
    tp: float | None = None,
    trailing_sl: float | None = None,
    trailing_hit: bool = False,
    days_held: int | None = None,
    time_stop_flag: bool = False,
    pnl_net_pct: float | None = None,
    rel_pct: float | None = None,
    fund: dict | None = None,
    news_sent: dict | None = None,
    market_regime: dict | None = None,
) -> dict[str, Any]:
    """Nilai status tesis satu posisi: `GUGUR` / `MELEMAH` / `MENGUAT` / `TETAP` / `TIDAK_TERSEDIA`."""
    sym = str(ticker or "").strip().upper().removesuffix(".JK")
    c_val = _num(close)
    avg_val = _num(avg_price)
    if c_val is None or c_val <= 0:
        emoji, lbl, sev = _STATUS_META[TIDAK_TERSEDIA]
        return {
            "ticker": sym,
            "status": TIDAK_TERSEDIA,
            "emoji": emoji,
            "label": lbl,
            "score": 0,
            "reasons": ["data harga tidak tersedia — status tesis tidak dinilai"],
            "signals": {},
            "severity": sev,
            "data_ok": False,
        }

    low_val = _num(low)
    sl_val = _num(sl)
    tp_val = _num(tp)
    trail_val = _num(trailing_sl)
    rel_val = _num(rel_pct)

    pnl_val = _num(pnl_net_pct)
    if pnl_val is None and avg_val is not None and avg_val > 0:
        pnl_val = round(saran.pnl_pct(avg_val, c_val), 2)

    tech_pct, sma20, sma50 = _extract_tech(df, ind, tech_score)
    fund_pct = _num((fund or {}).get("pct"))
    forensic_pen = _num((fund or {}).get("forensic_penalty")) or 0.0
    news_pct = _num((news_sent or {}).get("pct")) if (news_sent and news_sent.get("n")) else None
    reg_status = (market_regime or {}).get("status") if isinstance(market_regime, dict) else None

    gugur_reasons: list[str] = []
    pos_reasons: list[str] = []
    neg_reasons: list[str] = []
    score = 0

    # --- Kondisi batal keras (GUGUR) ---
    sl_breached = sl_val is not None and sl_val > 0 and (
        c_val <= sl_val or (low_val is not None and low_val <= sl_val)
    )
    trail_breached = bool(trailing_hit) or (
        trail_val is not None and trail_val > 0 and c_val <= trail_val
    )
    if sl_breached:
        gugur_reasons.append(f"harga menembus batas SL ({saran.fmt(sl_val)})")
    if trail_breached:
        t_txt = f" ({saran.fmt(trail_val)})" if trail_val else ""
        gugur_reasons.append(f"tembus Chandelier trailing stop{t_txt}")
    if pnl_val is not None and pnl_val <= -8.0 and tech_pct is not None and tech_pct <= -25:
        gugur_reasons.append(
            f"rugi bersih {pnl_val:+.1f}% disertai tren teknikal rusak ({tech_pct:+.0f}%)"
        )
    if rel_val is not None and rel_val < 25.0 and tech_pct is not None and tech_pct <= -30:
        gugur_reasons.append(
            f"peringkat relatif jatuh ke persentil {rel_val:.0f}% & tren negatif"
        )
    if forensic_pen >= 25.0 and tech_pct is not None and tech_pct < 0:
        gugur_reasons.append(
            f"bendera forensik kas/fundamental berat (-{forensic_pen:.0f}) & tren turun"
        )

    # --- Sinyal penguat vs pelemah ---
    if tech_pct is not None:
        if tech_pct >= 25:
            score += 2
            pos_reasons.append(f"tren teknikal kuat ({tech_pct:+.0f}%)")
        elif tech_pct <= -20:
            score -= 2
            neg_reasons.append(f"tren teknikal berbalik negatif ({tech_pct:+.0f}%)")

    if sma50 is not None and sma50 > 0:
        if c_val < sma50:
            score -= 1
            neg_reasons.append("harga di bawah MA50")
        elif sma20 is None or c_val >= sma20:
            score += 1
            ma_lbl = "MA20/MA50" if sma20 is not None else "MA50"
            pos_reasons.append(f"harga bertahan di atas {ma_lbl}")

    if tp_val is not None and tp_val > 0 and c_val >= tp_val:
        score += 2
        pos_reasons.append(f"mencapai target TP ({saran.fmt(tp_val)})")
    elif pnl_val is not None:
        if pnl_val >= 5.0:
            score += 1
            pos_reasons.append(f"untung bersih berjalan {pnl_val:+.1f}%")
        elif pnl_val <= -4.0:
            score -= 1
            neg_reasons.append(f"terkoreksi {pnl_val:+.1f}% dari harga rata-rata")

    if rel_val is not None:
        if rel_val >= 70.0:
            score += 1
            pos_reasons.append(f"peringkat relatif kuat (persentil {rel_val:.0f}%)")
        elif rel_val < saran.HOLD_BAND_PCT:
            score -= 2
            neg_reasons.append(
                f"peringkat relatif turun ke {rel_val:.0f}% (< pita tahan {saran.HOLD_BAND_PCT:.0f}%)"
            )

    if time_stop_flag:
        score -= 2
        d_txt = f" ({days_held} hari bursa)" if days_held is not None else ""
        neg_reasons.append(f"melewati batas tahan horizontal{d_txt}")

    if news_pct is not None:
        if news_pct >= 25.0:
            score += 1
            pos_reasons.append(f"sentimen berita positif ({news_pct:+.0f}%)")
        elif news_pct <= -25.0:
            score -= 1
            neg_reasons.append(f"sentimen berita negatif ({news_pct:+.0f}%)")

    if forensic_pen >= 15.0:
        score -= 2
        neg_reasons.append(f"penalti forensik fundamental (-{forensic_pen:.0f})")
    elif fund_pct is not None:
        if fund_pct >= 20.0:
            score += 1
            pos_reasons.append(f"fundamental mendukung ({fund_pct:+.0f}%)")
        elif fund_pct <= -25.0:
            score -= 1
            neg_reasons.append(f"fundamental lemah ({fund_pct:+.0f}%)")

    if reg_status == "DEFENSIVE" and pnl_val is not None and pnl_val < 0:
        score -= 1
        neg_reasons.append("rezim IHSG defensif saat posisi masih rugi")

    # --- Klasifikasi akhir ---
    if gugur_reasons:
        status = GUGUR
        reasons = gugur_reasons + neg_reasons
    elif score <= -2 or time_stop_flag or (rel_val is not None and rel_val < saran.HOLD_BAND_PCT and score < 0):
        status = MELEMAH
        reasons = neg_reasons or ["indikator pendukung melemah"]
    elif (score >= 2 and not neg_reasons) or score >= 3:
        status = MENGUAT
        reasons = pos_reasons
    else:
        status = TETAP
        reasons = (pos_reasons[:1] + neg_reasons[:1]) or ["tesis awal masih terjaga"]

    emoji, lbl, sev = _STATUS_META[status]
    return {
        "ticker": sym,
        "status": status,
        "emoji": emoji,
        "label": lbl,
        "score": int(score),
        "reasons": reasons[:3],
        "signals": {
            "close": c_val,
            "pnl_net_pct": pnl_val,
            "tech_pct": tech_pct,
            "sma20": sma20,
            "sma50": sma50,
            "rel_pct": rel_val,
            "sl_breached": sl_breached,
            "trail_breached": trail_breached,
            "time_stop": bool(time_stop_flag),
            "regime": reg_status,
        },
        "severity": sev,
        "data_ok": True,
    }


def evaluate_transition(prev_status: str | None, cur_status: str) -> dict[str, Any]:
    """Tentukan apakah status tesis berubah dan apakah alert exit perlu dikirim.

    Aturan anti-spam:
    - Bila data tidak tersedia (`TIDAK_TERSEDIA`): jangan alert.
    - Bila status sama dengan sebelumnya (`prev == cur`): `changed=False`,
      `should_alert=False` (tidak berisik berulang).
    - Bila posisi baru pertama kali dipantau (`prev is None`): alert hanya jika
      langsung `GUGUR` atau `MELEMAH`.
    - Bila status berubah (`prev != cur`):
      - Berubah ke `GUGUR` atau `MELEMAH`: `should_alert=True` (perlu tinjau/keluar).
      - Pulih dari `GUGUR`/`MELEMAH` ke `TETAP`/`MENGUAT`: `should_alert=True`.
      - Pergeseran sehat `TETAP <-> MENGUAT`: `changed=True`, `should_alert=False`.
    """
    prev = prev_status if prev_status in VALID_STATUSES else None
    if cur_status not in VALID_STATUSES:
        return {
            "prev": prev,
            "cur": cur_status,
            "changed": False,
            "should_alert": False,
            "direction": "na",
        }
    if prev is None:
        deteriorated = cur_status in (GUGUR, MELEMAH)
        return {
            "prev": None,
            "cur": cur_status,
            "changed": False,
            "should_alert": deteriorated,
            "direction": "memburuk" if deteriorated else "awal",
        }
    if prev == cur_status:
        return {
            "prev": prev,
            "cur": cur_status,
            "changed": False,
            "should_alert": False,
            "direction": "tetap",
        }
    prev_sev = _STATUS_META[prev][2]
    cur_sev = _STATUS_META[cur_status][2]
    direction = "memburuk" if cur_sev > prev_sev else "membaik"
    should_alert = (
        cur_status in (GUGUR, MELEMAH)
        or (prev in (GUGUR, MELEMAH) and cur_status in (TETAP, MENGUAT))
    )
    return {
        "prev": prev,
        "cur": cur_status,
        "changed": True,
        "should_alert": should_alert,
        "direction": direction,
    }


def format_portfolio_line(
    tesis_res: dict[str, Any],
    *,
    prev_status: str | None = None,
) -> str:
    """Satu baris HTML ringkas status tesis untuk ditampilkan di `/portofolio`."""
    st = tesis_res.get("status") or TIDAK_TERSEDIA
    em = tesis_res.get("emoji") or _STATUS_META.get(st, ("❔", "", -1))[0]
    reasons = tesis_res.get("reasons") or []
    reason_txt = f" — {esc('; '.join(reasons[:2]))}" if reasons else ""
    if st == TIDAK_TERSEDIA:
        return f"{em} Tesis: <i>tidak tersedia</i>{reason_txt}"
    prev_clean = prev_status if prev_status in VALID_STATUSES and prev_status != st else None
    delta_txt = f" (dari {esc(prev_clean)})" if prev_clean else ""
    return f"{em} Tesis: <b>{esc(st)}</b>{delta_txt}{reason_txt}"


def format_alert_line(tesis_res: dict[str, Any], transition: dict[str, Any]) -> str:
    """Satu baris HTML alert perubahan tesis untuk `_scan_real_position_exits`."""
    st = tesis_res.get("status") or TETAP
    em = tesis_res.get("emoji") or "⚪"
    prev = transition.get("prev")
    prev_txt = f" (sebelumnya {esc(prev)})" if prev else ""
    reasons = tesis_res.get("reasons") or []
    reason_txt = esc("; ".join(reasons[:2])) if reasons else "perubahan kondisi indikator"
    if st == GUGUR:
        return (
            f"{em} <b>Tesis Posisi GUGUR</b>{prev_txt}: {reason_txt} "
            "— evaluasi keluar disiplin sesuai rencana."
        )
    if st == MELEMAH:
        return (
            f"{em} <b>Tesis Posisi MELEMAH</b>{prev_txt}: {reason_txt} "
            "— tinjau kembali posisi &amp; batas SL."
        )
    return (
        f"{em} <b>Tesis Posisi Pulih → {esc(st)}</b>{prev_txt}: {reason_txt}."
    )
