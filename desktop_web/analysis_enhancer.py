"""Penguat analisis deterministik & penyederhana bahasa manusia untuk SahamBot Desktop.

Seluruh kalkulasi bersifat murni, deterministik, dan jujur terhadap data kosong
(tidak pernah mengarang angka jika rasio/histori tidak tersedia).
"""
from __future__ import annotations

import os
import re
import statistics
from typing import Any

from core import btcache, fundamental
from core.desktop_helpers import _fmt_rp, _safe_float

_BT_CACHE_DIR = os.path.join("temp", "cache", "bt_desktop")

_CORP_NEWS_RULES: tuple[tuple[str, str, str, str], ...] = (
    (
        "Rights Issue / HMETD",
        "danger",
        r"\b(rights?\s*issue|hmetd|pmhmetd|penambahan\s+modal\s+dengan\s+hak|dilusi)\b",
        "Potensi dilusi saham & tekanan harga jangka pendek — cek harga pelaksanaan & pembeli siaga.",
    ),
    (
        "Private Placement",
        "danger",
        r"\b(private\s+placement|pmthmetd|tanpa\s+hak\s+memesan)\b",
        "Penerbitan saham baru tanpa HMETD — perhatikan potensi dilusi.",
    ),
    (
        "Suspensi / UMA / PKPU",
        "danger",
        r"\b(suspensi|suspend|uma\b|unusual\s+market|delisting|pkpu|pailit|gagal\s+bayar|default)\b",
        "Risiko likuiditas / pengawasan khusus BEI — hindari masuk sebelum status jelas.",
    ),
    (
        "Rebalancing Indeks",
        "danger",
        r"\b(ftse|msci|keluar\s+dari\s+(issi|jii|lq45)|eksklusi|penghapusan\s+indeks|rebalancing)\b",
        "Perubahan konstituen indeks bisa memicu arus dana pasif besar (outflow/inflow).",
    ),
    (
        "Dividen / Cum-Ex Date",
        "info",
        r"\b(cum\s*date|ex\s*date|dividen|dividend\s+yield|bagi\s+dividen)\b",
        "Waspadai dividen trap (harga rawan turun tajam saat ex-date).",
    ),
    (
        "Aksi Korporasi Lain",
        "info",
        r"\b(stock\s*split|reverse\s*split|buyback|tender\s*offer|akuisisi|merger)\b",
        "Aksi korporasi aktif — cek detail keterbukaan informasi BEI.",
    ),
)


def detect_corp_action_news(news_dict: dict[str, Any] | None) -> list[dict[str, str]]:
    """Pindai judul berita emiten untuk mendeteksi aksi korporasi krusial secara otomatis."""
    details = (news_dict or {}).get("details") or []
    found: list[dict[str, str]] = []
    seen_cats: set[str] = set()
    for item in details:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        low = title.lower()
        for cat, sev, pattern, note in _CORP_NEWS_RULES:
            if cat in seen_cats:
                continue
            if re.search(pattern, low):
                seen_cats.add(cat)
                found.append({
                    "category": cat,
                    "severity": sev,
                    "headline": title,
                    "note": note,
                })
                if len(found) >= 5:
                    return found
    return found


def build_prebuy_checklist(
    detail: dict[str, Any],
    news_corp_alerts: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Checklist Pra-Beli 5 Detik (Anti-FOMO) berbasis aturan objektif."""
    fund = detail.get("fund") or {}
    plan = detail.get("plan") or {}
    ov = fund.get("corp_override") or {}
    alerts = news_corp_alerts or []
    danger_alerts = [a for a in alerts if a.get("severity") == "danger"]
    info_alerts = [a for a in alerts if a.get("severity") != "danger"]

    # 1. Aksi Korporasi & Forensik
    pen = int(fund.get("forensic_penalty") or 0)
    if ov.get("corp_action_flags") or danger_alerts:
        c1_st = "bad"
        c1_det = (
            ov["corp_action_flags"][0]
            if ov.get("corp_action_flags")
            else f"{danger_alerts[0]['category']}: {danger_alerts[0]['headline'][:70]}"
        )
    elif pen >= 20 or info_alerts:
        c1_st = "warn"
        c1_det = (
            f"Penalti forensik kas/4Q (-{pen})"
            if pen >= 20
            else f"{info_alerts[0]['category']} terdeteksi di berita"
        )
    else:
        c1_st = "ok"
        c1_det = "Tidak ada aksi korporasi negatif atau bendera kas berat."

    # 2. Jarak SL & Batas ARB 7%
    sl_dist = _safe_float(plan.get("sl_dist_pct"))
    if plan.get("arb_gap_risk") or (sl_dist is not None and sl_dist > 7.0):
        c2_st = "bad"
        c2_det = f"Jarak SL {sl_dist:.1f}% (> 7% ARB harian — rawan terkunci gap)." if sl_dist else "Risiko gap ARB aktif."
    elif sl_dist is not None and sl_dist > 0:
        c2_st = "ok"
        c2_det = f"Jarak SL {sl_dist:.1f}% (aman di dalam batas ARB 7%)."
    else:
        c2_st = "warn"
        c2_det = "Level Stop-Loss belum terbentuk."

    # 3. Sizing & Veto Risiko
    vetoes = plan.get("vetoes") or []
    if vetoes:
        c3_st = "bad"
        c3_det = f"Veto aktif: {vetoes[0]}"
    elif not plan.get("ok"):
        c3_st = "warn"
        c3_det = plan.get("skip_reason") or "Melebihi batas risiko/modal saat ini (0 lot)."
    else:
        c3_st = "ok"
        c3_det = f"Ukuran posisi aman: {plan.get('lots', 0)} lot ({plan.get('pct_modal', 0):.1f}% modal)."

    # 4. Konfluensi Tren & Bandar (diperbarui live oleh Stockbit MCP di UI)
    c4_st = "pending"
    c4_det = "Menunggu konfirmasi Orderbook & Broker Summary (Stockbit MCP)…"

    return [
        {"key": "corp", "label": "Aksi Korporasi & Kas", "status": c1_st, "detail": c1_det},
        {"key": "arb", "label": "Jarak SL ≤ 7% (ARB)", "status": c2_st, "detail": c2_det},
        {"key": "sizing", "label": "Batas Modal & Veto", "status": c3_st, "detail": c3_det},
        {"key": "bandar", "label": "Selaras Tren & Bandar", "status": c4_st, "detail": c4_det},
    ]


def compute_fair_value(
    close: float | None,
    fund_raw: dict[str, Any] | None,
    fund_scored: dict[str, Any] | None,
    sector: str | None = None,
) -> dict[str, Any]:
    """Estimasi Nilai Wajar (Fair Value) & Margin of Safety secara konservatif.

    - Bank/Keuangan: model PBV–ROE (hurdle rate BEI 12%, fair PBV 0,6x–3,5x).
    - Non-Keuangan: median konservatif dari PER wajar sektoral (maks 15x) dan PBV–ROE,
      dengan penalti kehati-hatian jika ada bendera forensik/aksi korporasi.
    """
    px = _safe_float(close)
    raw = fund_raw or {}
    scored = fund_scored or {}
    if not px or px <= 0:
        return {"available": False, "reason": "Harga penutupan tidak tersedia."}

    is_bank = fundamental.is_financial_sector(sector or raw.get("sector"))
    pbv = _safe_float(raw.get("pbv"))
    per = _safe_float(raw.get("per"))
    roe = fundamental.normalize_roe(raw.get("roe"), unit=raw.get("roe_unit"))
    # Gunakan override ROE bank lokal bila tersedia & lebih segar
    ov = scored.get("corp_override") or {}
    if is_bank and isinstance(ov.get("bank_ratios"), dict) and ov["bank_ratios"].get("roe_pct"):
        roe = _safe_float(ov["bank_ratios"].get("roe_pct")) or roe

    estimates: list[float] = []
    methods: list[str] = []

    if pbv and pbv > 0 and roe is not None and roe > 0:
        bvps = px / pbv
        max_pbv = 3.5 if is_bank else 3.0
        fair_pbv = max(0.6, min(max_pbv, roe / 12.0))
        estimates.append(bvps * fair_pbv)
        methods.append(f"PBV–ROE (wajar {fair_pbv:.2f}x)")

    if not is_bank and per and per > 0:
        eps = px / per
        bench_per = _safe_float((scored.get("sector_rel") or {}).get("bench_per")) or 12.0
        fair_per = max(6.0, min(15.0, bench_per))
        estimates.append(eps * fair_per)
        methods.append(f"PER Sektoral ({fair_per:.0f}x)")

    if not estimates:
        return {
            "available": False,
            "reason": "Data PER/PBV/ROE positif belum lengkap untuk menghitung nilai wajar.",
        }

    fv = float(statistics.median(estimates))
    penalty = int(scored.get("forensic_penalty") or 0)
    discount_haircut = min(0.20, penalty / 200.0)  # maks potongan konservatif 20%
    if discount_haircut > 0:
        fv *= (1.0 - discount_haircut)

    fv = round(fv)
    mos_pct = round((fv - px) / fv * 100.0, 1) if fv > 0 else 0.0

    if mos_pct >= 15.0:
        label, tone = "DISKON MENARIK", "emerald"
        note = f"Harga pasar ({_fmt_rp(px)}) berada ~{mos_pct:.1f}% di bawah estimasi nilai wajar ({_fmt_rp(fv)})."
    elif mos_pct >= 5.0:
        label, tone = "SEDIKIT DISKON", "sky"
        note = f"Harga pasar sedikit di bawah estimasi nilai wajar ({_fmt_rp(fv)}, diskon {mos_pct:.1f}%)."
    elif mos_pct >= -10.0:
        label, tone = "HARGA WAJAR", "amber"
        note = f"Harga pasar sudah dekat dengan estimasi nilai wajar ({_fmt_rp(fv)})."
    else:
        label, tone = "PREMIUM / MAHAL", "rose"
        note = f"Harga pasar ~{abs(mos_pct):.1f}% di atas estimasi nilai wajar ({_fmt_rp(fv)}) — margin keamanan tipis."

    return {
        "available": True,
        "fair_value": fv,
        "mos_pct": mos_pct,
        "label": label,
        "tone": tone,
        "method": " + ".join(methods),
        "haircut_pct": round(discount_haircut * 100, 1),
        "note": note,
    }


def build_simple_summary(
    detail: dict[str, Any],
    fair_value: dict[str, Any],
) -> dict[str, Any]:
    """Ringkas seluruh metrik kompleks menjadi 4 pilar bahasa sehari-hari."""
    snap = (detail.get("res") or {}).get("snap") or {}
    cek = detail.get("cek") or {}
    fund = detail.get("fund") or {}
    plan = detail.get("plan") or {}

    close = _safe_float(snap.get("close"))
    ma20 = _safe_float(snap.get("sma20"))
    ma50 = _safe_float(snap.get("sma50"))
    rsi = _safe_float(snap.get("rsi"))

    # Pilar 1: Tren Harga
    if close and ma20 and ma50:
        if close > ma20 and close > ma50:
            t_status, t_tone = "Tren Naik", "emerald"
            t_text = "Harga bertahan di atas rata-rata 20 & 50 hari."
        elif close > ma20 and close <= ma50:
            t_status, t_tone = "Rebound Awal", "amber"
            t_text = f"Baru pulih di atas MA20 ({_fmt_rp(ma20)}), tapi masih tertahan di bawah MA50 ({_fmt_rp(ma50)})."
        else:
            t_status, t_tone = "Tren Turun", "rose"
            t_text = "Harga masih di bawah MA20 & MA50 — momentum belum kuat."
    else:
        t_status, t_tone, t_text = "Belum Jelas", "slate", "Data rata-rata harga belum lengkap."
    if rsi is not None and rsi >= 70:
        t_text += f" RSI {rsi:.0f} (mulai jenuh beli, rawan koreksi singkat)."

    # Pilar 2: Bisnis & Harga Wajar
    f_pct = _safe_float(fund.get("pct"))
    pen = int(fund.get("forensic_penalty") or 0)
    ov = fund.get("corp_override") or {}
    if f_pct is not None and f_pct >= 60 and pen == 0:
        b_status, b_tone = "Bisnis Sehat", "emerald"
    elif f_pct is not None and f_pct >= 30:
        b_status, b_tone = "Cukup / Campuran", "amber"
    else:
        b_status, b_tone = "Perlu Hati-hati", "rose"

    b_parts: list[str] = []
    if fair_value.get("available"):
        mos = fair_value["mos_pct"]
        b_parts.append(
            f"Estimasi wajar {_fmt_rp(fair_value['fair_value'])} ({'diskon' if mos >= 0 else 'premium'} {abs(mos):.0f}%)."
        )
    if ov.get("corp_action_flags"):
        b_parts.append("Ada catatan aksi korporasi penting (cek banner merah).")
    elif pen > 0:
        b_parts.append(f"Ada catatan forensik kas/kuartalan (penalti -{pen}).")
    elif f_pct is not None:
        b_parts.append(f"Skor fundamental {round(f_pct)}%.")
    b_text = " ".join(b_parts) or "Data fundamental belum lengkap."

    # Pilar 3: Risiko & Sizing
    vetoes = plan.get("vetoes") or []
    verdict = str(cek.get("verdict") or "TUNGGU").upper()
    if vetoes:
        r_status, r_tone = "Ada Veto", "rose"
        r_text = f"Terkena aturan pengaman: {vetoes[0]}."
    elif not plan.get("ok") and plan.get("skip_reason"):
        r_status, r_tone = "Batas Risiko", "amber"
        r_text = "Sizing otomatis 0 lot karena melebihi batas risiko/porsi modal saat ini."
    elif plan.get("arb_gap_risk"):
        r_status, r_tone = "Risiko Gap ARB", "amber"
        r_text = "Jarak Stop-Loss > 7% (rawan terkunci ARB 1 hari)."
    else:
        r_status, r_tone = "Risiko Terukur", "emerald"
        r_text = f"Jarak SL {plan.get('sl_dist_pct', 0):.1f}% dengan R:R 1:{plan.get('rr', 0):.1f}."

    # Pilar 4: Kesimpulan Bahasa Manusia
    entry_s = _fmt_rp(plan.get("entry_pullback") or plan.get("entry"))
    sl_s = _fmt_rp(plan.get("sl"))
    tp_s = _fmt_rp(plan.get("tp"))
    if verdict == "LAYAK" and not vetoes:
        a_tone = "emerald"
        a_head = "LAYAK DIPANTAU / BELI TERUKUR"
        a_det = f"Tren, bisnis, dan risiko cukup selaras. Area beli ideal dekat {entry_s}, jaga Stop-Loss di {sl_s}, target {tp_s}."
    elif verdict == "HINDARI" or vetoes:
        a_tone = "rose"
        a_head = "SEBAIKNYA HINDARI / TUNGGU DULU"
        a_det = f"Masih ada faktor risiko besar ({vetoes[0] if vetoes else 'tren/fundamental belum mendukung'}). Tidak perlu dipaksakan masuk."
    else:
        a_tone = "amber"
        a_head = "TUNGGU KONFIRMASI"
        a_det = f"Sebagian indikator bagus tapi belum kompak. Sabar menunggu harga mendekati support {entry_s} atau breakout bersih; SL disiplin di {sl_s}."

    return {
        "trend": {"status": t_status, "tone": t_tone, "text": t_text},
        "business": {"status": b_status, "tone": b_tone, "text": b_text},
        "risk": {"status": r_status, "tone": r_tone, "text": r_text},
        "action": {"headline": a_head, "detail": a_det, "tone": a_tone},
    }


def run_backtest_summary(ticker: str) -> dict[str, Any]:
    """Jalankan/baca cache backtest 5 tahun (horizon 20 hari, net fee BEI 0,40%)."""
    sym = ticker.strip().upper().replace(".JK", "")
    cached = btcache.load_summary(_BT_CACHE_DIR, sym)
    if cached and cached.get("ok"):
        return cached

    import backtest as bt_mod

    try:
        df_bt = bt_mod.backtest(sym, horizon=20, period="5y")
    except Exception as exc:
        return {"ok": False, "ticker": sym, "error": str(exc)}

    if df_bt is None or df_bt.empty:
        return {"ok": False, "ticker": sym, "error": "Data historis tidak mencukupi untuk backtest 5 tahun."}

    base_avg = round(float(df_bt["fwd_ret"].mean()), 2)
    buy_grp = df_bt[df_bt["rating"].isin(["STRONG BUY", "BUY"])]
    sell_grp = df_bt[df_bt["rating"].isin(["STRONG SELL", "SELL"])]

    buy_n = int(len(buy_grp))
    buy_avg = round(float(buy_grp["fwd_ret"].mean()), 2) if buy_n else None
    buy_wr = round(float((buy_grp["fwd_ret"] > 0).mean() * 100.0), 1) if buy_n else None
    buy_worst = round(float(buy_grp["fwd_ret"].min()), 2) if buy_n else None

    sell_n = int(len(sell_grp))
    sell_avg = round(float(sell_grp["fwd_ret"].mean()), 2) if sell_n else None

    if buy_n < 10:
        plain = f"Sinyal BELI pada {sym} jarang muncul dalam 5 tahun terakhir ({buy_n} kali) — sampel terlalu kecil untuk disimpulkan."
    elif buy_avg is not None and buy_avg > base_avg and (buy_wr or 0) >= 50:
        plain = f"Secara historis 5 tahun, sinyal BELI pada {sym} rata-rata memberi {buy_avg:+.2f}% per 20 hari (win rate {buy_wr:.0f}%), lebih baik dari beli acak ({base_avg:+.2f}%)."
    else:
        plain = f"Secara historis 5 tahun, sinyal teknikal saja pada {sym} kurang unggul (rata-rata BELI {buy_avg:+.2f}% vs acak {base_avg:+.2f}%) — wajib konfirmasi fundamental & bandar."

    summary = {
        "ok": True,
        "ticker": sym,
        "horizon_days": 20,
        "total_obs": int(len(df_bt)),
        "baseline_avg_pct": base_avg,
        "buy_trades": buy_n,
        "buy_avg_pct": buy_avg,
        "buy_win_rate_pct": buy_wr,
        "buy_worst_pct": buy_worst,
        "sell_trades": sell_n,
        "sell_avg_pct": sell_avg,
        "plain_note": plain,
    }
    btcache.save_summary(_BT_CACHE_DIR, summary)
    return summary


def simulate_equity_backtest(
    ticker: str,
    *,
    period: str = "3y",
    modal: float = 10_000_000.0,
    sl_pct: float = 5.0,
    tp_pct: float = 10.0,
    max_hold: int = 20,
    strategy: str = "signal",
    df_override: Any = None,
) -> dict[str, Any]:
    """Simulasi backtest kausal (zero look-ahead) dengan kurva ekuitas vs Buy & Hold.

    Aturan realistis BEI:
    - Sinyal dievaluasi di penutupan hari ke-i, dieksekusi di harga pembukaan hari ke-(i+1).
    - Sizing kelipatan 1 lot (100 saham) dengan biaya BEI (beli 0,15%, jual 0,25%).
    - Prioritas exit konservatif: bila low <= SL dan high >= TP pada bar sama, SL didahulukan.
    """
    from core import analysis, data as datamod
    from core.saran import BUY_FEE, SELL_FEE

    sym = ticker.strip().upper().replace(".JK", "")
    per = period if period in ("1y", "2y", "3y", "5y") else "3y"
    init_cash = max(1_000_000.0, float(modal or 10_000_000.0))
    sl_r = max(1.0, min(30.0, float(sl_pct or 5.0))) / 100.0
    tp_r = max(1.0, min(100.0, float(tp_pct or 10.0))) / 100.0
    hold_lim = max(3, min(120, int(max_hold or 20)))

    df = df_override if df_override is not None else datamod.fetch_history(sym, period=per)
    if df is None or len(df) < 90:
        return {
            "ok": False,
            "ticker": sym,
            "error": f"Data historis '{sym}' tidak cukup panjang untuk simulasi backtest {per}.",
        }

    ind = analysis.compute_indicators(df)
    n = len(ind)
    warmup = min(60, max(30, n // 5))
    if n <= warmup + 5:
        return {"ok": False, "ticker": sym, "error": "Jumlah bar terlalu sedikit setelah warmup."}

    dates = [idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10] for idx in ind.index]
    opens = ind["open"].to_numpy(dtype=float)
    highs = ind["high"].to_numpy(dtype=float)
    lows = ind["low"].to_numpy(dtype=float)
    closes = ind["close"].to_numpy(dtype=float)
    sma20 = ind["sma20"].to_numpy(dtype=float) if "sma20" in ind.columns else closes
    sma50 = ind["sma50"].to_numpy(dtype=float) if "sma50" in ind.columns else closes
    rsi_arr = ind["rsi"].to_numpy(dtype=float) if "rsi" in ind.columns else [50.0] * n

    def _entry_signal(idx: int) -> bool:
        if strategy == "ma_trend":
            c, m20, m50 = closes[idx], sma20[idx], sma50[idx]
            return bool(c == c and m20 == m20 and m50 == m50 and c > m20 > m50)
        if strategy == "rsi_pullback":
            c, m50, r = closes[idx], sma50[idx], rsi_arr[idx]
            return bool(c == c and m50 == m50 and r == r and c > m50 and r < 45.0)
        s = analysis.score_from_rows(ind.iloc[idx], ind.iloc[idx - 1])
        return s.get("rating") in ("STRONG BUY", "BUY")

    # Buy & Hold benchmark (100% beli di pembukaan setelah warmup)
    bh_px0 = float(opens[warmup]) if opens[warmup] > 0 else float(closes[warmup])
    bh_lots = int(init_cash // (bh_px0 * 100.0 * (1.0 + BUY_FEE)))
    bh_cash = init_cash - bh_lots * 100.0 * bh_px0 * (1.0 + BUY_FEE)

    cash = init_cash
    pos_lots = 0
    entry_px = 0.0
    entry_idx = 0
    entry_cost = 0.0
    sl_px = 0.0
    tp_px = 0.0
    pending_entry = False

    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    peak_eq = init_cash
    max_dd = 0.0

    for i in range(warmup, n):
        # 1. Eksekusi entry tertunda pada open hari ini (kausal: sinyal dari i-1)
        if pending_entry and pos_lots == 0:
            px_in = float(opens[i]) if opens[i] > 0 else float(closes[i])
            lot_cost = px_in * 100.0 * (1.0 + BUY_FEE)
            lots = int(cash // lot_cost)
            if lots > 0:
                pos_lots = lots
                entry_px = px_in
                entry_idx = i
                entry_cost = lots * lot_cost
                cash -= entry_cost
                sl_px = px_in * (1.0 - sl_r)
                tp_px = px_in * (1.0 + tp_r)
            pending_entry = False

        # 2. Cek kondisi exit bila sedang pegang posisi
        if pos_lots > 0:
            days_held = i - entry_idx
            exit_px: float | None = None
            exit_reason: str | None = None
            if lows[i] <= sl_px:
                exit_px = min(float(opens[i]), sl_px) if opens[i] > 0 else sl_px
                exit_reason = "SL"
            elif highs[i] >= tp_px:
                exit_px = max(float(opens[i]), tp_px) if opens[i] > 0 else tp_px
                exit_reason = "TP"
            elif days_held >= hold_lim:
                exit_px = float(closes[i])
                exit_reason = "TIME"
            elif i == n - 1:
                exit_px = float(closes[i])
                exit_reason = "END"

            if exit_px is not None and exit_reason is not None:
                proceeds = pos_lots * 100.0 * exit_px * (1.0 - SELL_FEE)
                net_rp = proceeds - entry_cost
                net_pct = (net_rp / entry_cost * 100.0) if entry_cost > 0 else 0.0
                cash += proceeds
                trades.append({
                    "entry_date": dates[entry_idx],
                    "exit_date": dates[i],
                    "entry_price": round(entry_px, 1),
                    "exit_price": round(exit_px, 1),
                    "lots": pos_lots,
                    "hold_days": days_held,
                    "exit_reason": exit_reason,
                    "net_pnl_rp": round(net_rp),
                    "net_pnl_pct": round(net_pct, 2),
                })
                pos_lots = 0
                entry_px = 0.0
                entry_cost = 0.0

        # 3. Evaluasi sinyal entry di penutupan hari i untuk eksekusi open i+1
        if pos_lots == 0 and i < n - 1:
            if _entry_signal(i):
                pending_entry = True

        # 4. Catat kurva ekuitas harian
        cur_eq = cash + (pos_lots * 100.0 * float(closes[i]) * (1.0 - SELL_FEE) if pos_lots > 0 else 0.0)
        bh_eq = bh_cash + bh_lots * 100.0 * float(closes[i]) * (1.0 - SELL_FEE)
        if cur_eq > peak_eq:
            peak_eq = cur_eq
        dd = (cur_eq - peak_eq) / peak_eq * 100.0 if peak_eq > 0 else 0.0
        if dd < max_dd:
            max_dd = dd
        equity_curve.append({
            "time": dates[i],
            "strategy": round(cur_eq),
            "buyhold": round(bh_eq),
        })

    final_eq = equity_curve[-1]["strategy"] if equity_curve else round(init_cash)
    bh_final = equity_curve[-1]["buyhold"] if equity_curve else round(init_cash)
    strat_ret = round((final_eq - init_cash) / init_cash * 100.0, 2)
    bh_ret = round((bh_final - init_cash) / init_cash * 100.0, 2)
    alpha = round(strat_ret - bh_ret, 2)

    wins = [t for t in trades if t["net_pnl_rp"] > 0]
    losses = [t for t in trades if t["net_pnl_rp"] <= 0]
    n_trades = len(trades)
    win_rate = round(len(wins) / n_trades * 100.0, 1) if n_trades else None
    gross_win = sum(t["net_pnl_rp"] for t in wins)
    gross_loss = abs(sum(t["net_pnl_rp"] for t in losses))
    pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_win > 0 else None)
    avg_win = round(sum(t["net_pnl_pct"] for t in wins) / len(wins), 2) if wins else None
    avg_loss = round(sum(t["net_pnl_pct"] for t in losses) / len(losses), 2) if losses else None
    avg_hold = round(sum(t["hold_days"] for t in trades) / n_trades, 1) if n_trades else 0.0

    if n_trades == 0:
        plain = f"Tidak ada sinyal transaksi yang memenuhi aturan pada {sym} selama periode {per}."
    elif alpha >= 0 and strat_ret > 0:
        plain = (
            f"Strategi menghasilkan {strat_ret:+.1f}% (modal akhir {_fmt_rp(final_eq)}), unggul {alpha:+.1f}% "
            f"dibanding Buy & Hold ({bh_ret:+.1f}%) dengan {n_trades} transaksi (Win Rate {win_rate:.0f}%, Max DD {max_dd:.1f}%)."
        )
    elif strat_ret > 0:
        plain = (
            f"Strategi tetap untung {strat_ret:+.1f}% ({_fmt_rp(final_eq)}) dengan drawdown lebih terjaga ({max_dd:.1f}%), "
            f"meski masih di bawah Buy & Hold ({bh_ret:+.1f}%)."
        )
    else:
        plain = (
            f"Strategi ini kurang cocok untuk {sym} pada periode {per} ({strat_ret:+.1f}% vs Buy & Hold {bh_ret:+.1f}%, "
            f"Win Rate {win_rate or 0:.0f}%). Coba ubah strategi atau perlebar SL/TP."
        )

    return {
        "ok": True,
        "ticker": sym,
        "period": per,
        "strategy": strategy,
        "metrics": {
            "initial_capital": round(init_cash),
            "final_capital": final_eq,
            "strategy_ret_pct": strat_ret,
            "buyhold_final": bh_final,
            "buyhold_ret_pct": bh_ret,
            "alpha_pct": alpha,
            "max_drawdown_pct": round(max_dd, 2),
            "trades_count": n_trades,
            "win_rate_pct": win_rate,
            "profit_factor": pf,
            "avg_win_pct": avg_win,
            "avg_loss_pct": avg_loss,
            "avg_hold_days": avg_hold,
            "plain_note": plain,
        },
        "equity_curve": equity_curve,
        "trades": list(reversed(trades))[:100],
    }
