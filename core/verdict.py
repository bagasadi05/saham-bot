"""Verdict tanya beli/jual berbasis data (PowerScore + teknikal + fundamental + berita).

Dipindahkan dari `bot.py` agar entrypoint tetap ramping; seluruh fungsi tetap
di-re-export dari `bot.py` untuk kompatibilitas mundur.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from core import (analysis, btcache, data_quality, marketcal, powerscore, pricing,
                  reports as core_reports, saran as saranmod)
from core.reports import esc, fmt_rp

# ---------------------------------------------------------------- tanya beli/jual
# Jawaban atas "BBCA beli gak?" / "/beli BBCA" / "/jual TLKM": BUKAN perintah
# beli/jual — melainkan verdict berbasis data (PowerScore + teknikal +
# fundamental + berita) beserta level SL/TP dan kondisi yang bisa mengubah
# jawaban. Skor bot = deskripsi kondisi saat itu, bukan ramalan.

def _verdict_beli(pw_pct: float) -> tuple[str, str]:
    """(judul, alasan singkat) untuk yang belum punya."""
    if pw_pct >= 50:
        return ("✅ Layak dipertimbangkan BELI",
                "Sinyal kuat: teknikal, fundamental, dan berita searah. "
                "Tetap beli bertahap, jangan all-in.")
    if pw_pct >= 20:
        return ("⚠️ Boleh BELI BERTAHAP",
                "Sinyal positif tapi belum kuat — cicil, dan tunggu konfirmasi "
                "(mis. harga tembus MA50).")
    if pw_pct > -20:
        return ("⏳ TUNGGU dulu",
                "Belum ada sinyal beli yang jelas. Pantau dari watchlist, "
                "jangan kejar harga.")
    if pw_pct > -50:
        return ("🛑 JANGAN BELI dulu",
                "Tren dan/atau fundamental belum mendukung. Beli sekarang "
                "berarti lawan tren.")
    return ("🔻 HINDARI beli",
            "Downtrend kuat. Beli sekarang berisiko nyangkut — tunggu tren "
            "berbalik.")


def _verdict_jual(
    ts_pct: float,
    close: float,
    support: float,
    *,
    held_pos: dict | None = None,
    exit_guidance: dict | None = None,
    rel_pct: float | None = None,
    forensic_penalty: float = 0.0,
    forensic_flags: list[str] | None = None,
    market_regime: dict | None = None,
) -> tuple[str, str]:
    """(judul, alasan singkat) untuk yang sudah pegang atau meninjau keluar posisi."""
    extra_notes: list[str] = []

    if isinstance(exit_guidance, dict):
        eff_sl = exit_guidance.get("effective_sl") or exit_guidance.get("sl_initial")
        trail = exit_guidance.get("chandelier") or {}
        scale_out = exit_guidance.get("scale_out") or {}
        time_stop = exit_guidance.get("time_stop") or {}

        if held_pos and eff_sl and close <= float(eff_sl):
            return (
                "🔴 SAATNYA JUAL / CUT LOSS (SL TERSENTUH)",
                f"Harga berjalan {fmt_rp(close)} sudah menyentuh/menembus batas SL efektif "
                f"{fmt_rp(float(eff_sl))}. Disiplin batasi kerugian.",
            )
        if held_pos and trail.get("hit") and trail.get("stop"):
            return (
                "🟠 Pertimbangkan JUAL / amankan profit (Trailing Stop jebol)",
                f"Harga {fmt_rp(close)} turun menembus Chandelier trailing stop "
                f"{fmt_rp(float(trail['stop']))} (puncak 22h "
                f"{fmt_rp(float(trail.get('high_22') or close))} − 3×ATR).",
            )
        if held_pos and scale_out.get("tp1_reached") and scale_out.get("tp1"):
            bep_sl = scale_out.get("bep_sl") or exit_guidance.get("bep_sl")
            bep_txt = (
                f" dan geser SL sisa lot ke BEP+0,40% fee ({fmt_rp(float(bep_sl))})"
                if bep_sl
                else ""
            )
            extra_notes.append(
                f"Target TP1 ({fmt_rp(float(scale_out['tp1']))}) sudah tercapai — "
                f"pertimbangkan scale-out jual 50% lot{bep_txt}."
            )
        if held_pos and time_stop.get("triggered"):
            days_h = time_stop.get("days_held")
            extra_notes.append(
                f"Time-stop aktif: posisi sudah dipegang {days_h} hari bursa "
                f"(>= {time_stop.get('max_hold_days', 20)} hari horizon model)."
            )

    if rel_pct is not None:
        if float(rel_pct) < saranmod.HOLD_BAND_PCT:
            extra_notes.append(
                f"Skor relatif lintas-pasar ({float(rel_pct):.0f}%) turun di bawah "
                f"pita tahan {saranmod.HOLD_BAND_PCT:.0f}% (keluar hold-band)."
            )
        else:
            extra_notes.append(
                f"Skor relatif ({float(rel_pct):.0f}%) masih di dalam pita tahan "
                f">= {saranmod.HOLD_BAND_PCT:.0f}%."
            )

    pen_mag = abs(float(forensic_penalty or 0.0))
    if pen_mag > 0 or forensic_flags:
        fl = "; ".join(str(x) for x in (forensic_flags or [])[:2])
        extra_notes.append(
            f"Waspada kualitas kas/forensik (-{pen_mag:.0f} poin"
            + (f": {fl}" if fl else "")
            + ")."
        )

    if isinstance(market_regime, dict) and market_regime.get("status") == "DEFENSIVE":
        extra_notes.append("Rezim IHSG defensif (SMA50 < SMA200) — perketat trailing stop.")

    tail = (" " + " ".join(extra_notes)) if extra_notes else ""
    out_of_band = rel_pct is not None and float(rel_pct) < saranmod.HOLD_BAND_PCT
    time_hit = bool(
        held_pos
        and isinstance(exit_guidance, dict)
        and (exit_guidance.get("time_stop") or {}).get("triggered")
    )
    tp1_hit = bool(
        held_pos
        and isinstance(exit_guidance, dict)
        and (exit_guidance.get("scale_out") or {}).get("tp1_reached")
    )

    if ts_pct <= -50:
        extra = (" Harga sudah di bawah support "
                 f"{fmt_rp(support)}." if close < support else "")
        return ("🔴 Pertimbangkan JUAL / cut loss",
                "Downtrend kuat. Jangan ditahan dengan harapan." + extra + tail)
    if ts_pct <= -20 or out_of_band or time_hit:
        return ("🟠 Pertimbangkan JUAL SEBAGIAN",
                "Tren melemah — amankan sebagian profit/modal, sisanya pasang "
                "stop loss." + tail)
    if tp1_hit:
        return ("🟡 AMBIL UNTUNG SEBAGIAN (Scale-out TP1)",
                "Harga sudah mencapai area TP1 — amankan 50% posisi dan naikkan "
                "stop loss ke titik impas bersih." + tail)
    if ts_pct >= 50 and pen_mag < 15:
        return ("🟢 TAHAN",
                "Tren naik — biarkan profit jalan, pasang stop loss di bawah "
                "support." + tail)
    return ("🟡 TAHAN dengan SL ketat",
            "Tren belum jelas — tahan, tapi pasang stop loss supaya profit "
            "tidak berubah jadi rugi." + tail)


def _weekly_from_daily(ind: pd.DataFrame, score_pct: float) -> dict | None:
    """Hitung tren mingguan dari data harian (tanpa panggilan Yahoo ekstra bila cukup >=20 pekan)."""
    if ind is None or "close" not in ind.columns or not isinstance(ind.index, pd.DatetimeIndex):
        return None
    w_close = ind["close"].resample("W-FRI").last().dropna()
    if len(w_close) < 20:
        return None
    return analysis.weekly_trend(w_close.to_frame(name="close"), score_pct)


def build_verdict(
    res: dict,
    fund: dict | None,
    news_sent: dict | None,
    focus: str = "umum",
    bt_summary: dict | None = None,
    weekly: dict | None = None,
    *,
    positions: list[dict] | None = None,
    open_execs: dict[str, dict] | None = None,
    rel_pct: float | None = None,
    market_regime: dict | None = None,
) -> str:
    snap, sc, sr = res["snap"], res["score"], res["sr"]
    t = res["ticker"]
    forensic_pen = float((fund or {}).get("forensic_penalty") or 0.0)
    forensic_flags = list((fund or {}).get("forensic_flags") or [])
    fund_input = fund.get("base_pct", fund.get("pct")) if fund else None
    news_pct = news_sent["pct"] if news_sent else None
    pw = powerscore.combine(sc["pct"], fund_input, news_pct, forensic_penalty=forensic_pen)
    pw_label = pw.get("trend_label") or analysis.describe_rating(pw.get("rating"))
    sc_label = sc.get("trend_label") or analysis.describe_rating(sc.get("rating"))
    close = float(snap["close"])

    ind = res.get("ind")
    last = ind.iloc[-1] if ind is not None and not ind.empty else pd.Series(dtype=float)
    raw_atr = float(last["atr14"]) if not pd.isna(last.get("atr14", float("nan"))) else None
    atr = raw_atr if (raw_atr is not None and raw_atr > 0) else None
    sl_beli = float(saranmod.stop_price(close, atr)) if atr else float(pricing.round_tick(sr["support"]))
    tp_beli = float(saranmod.target_price(close, atr)) if atr else float(pricing.round_tick(sr["resistance"]))

    t_up = str(t).upper()
    held_pos = None
    if positions:
        for p in positions:
            if str(p.get("ticker") or "").upper() == t_up and int(p.get("lots") or 0) > 0:
                held_pos = p
                break
    exec_info = (open_execs or {}).get(t_up) if isinstance(open_execs, dict) else None

    entry_ref = float(held_pos.get("avg_price") or close) if held_pos else close
    lots_ref = int(held_pos.get("lots") or 0) if held_pos else 0
    stored_sl = float(exec_info["sl"]) if exec_info and exec_info.get("sl") else None
    stored_tp = float(exec_info["tp"]) if exec_info and exec_info.get("tp") else None
    entry_date_raw = (
        str((exec_info or {}).get("trade_date") or (held_pos or {}).get("updated_at") or "")[:10]
        or None
    )
    as_of_raw = str(res.get("as_of") or snap.get("as_of") or "")[:10]
    try:
        ref_today = date.fromisoformat(as_of_raw) if as_of_raw else marketcal.now_wib().date()
    except ValueError:
        ref_today = marketcal.now_wib().date()

    prev_close = (
        float(ind["close"].iloc[-2])
        if ind is not None and len(ind) >= 2 and "close" in ind.columns
        else None
    )
    exit_g = saranmod.build_exit_guidance(
        t_up,
        entry_ref,
        lots=lots_ref,
        atr=atr,
        sl=stored_sl,
        tp=stored_tp,
        close=close,
        prev_close=prev_close,
        df=ind,
        entry_date=entry_date_raw,
        today=ref_today,
    )
    days_held = (exit_g.get("time_stop") or {}).get("days_held")

    beli_judul, beli_alasan = _verdict_beli(pw["pct"])
    jual_judul, jual_alasan = _verdict_jual(
        sc["pct"],
        close,
        sr["support"],
        held_pos=held_pos,
        exit_guidance=exit_g,
        rel_pct=rel_pct,
        forensic_penalty=forensic_pen,
        forensic_flags=forensic_flags,
        market_regime=market_regime,
    )

    sym_html = esc(t)
    title = {"beli": f"🛒 <b>Beli {sym_html}?</b>",
             "jual": f"💰 <b>Jual {sym_html}?</b>",
             "umum": f"⚖️ <b>Beli atau jual {sym_html}?</b>"}.get(
                 focus, f"⚖️ <b>Beli atau jual {sym_html}?</b>"
             )

    if focus == "beli":
        headline = f"<b>Jawaban singkat: {beli_judul}</b>\n{beli_alasan}"
        other = f"\nKalau sudah pegang: {jual_judul}"
    elif focus == "jual":
        headline = f"<b>Jawaban singkat: {jual_judul}</b>\n{jual_alasan}"
        other = f"\nKalau belum punya: {beli_judul}"
    else:
        headline = (f"<b>Kalau belum punya: {beli_judul}</b>\n{beli_alasan}\n"
                    f"<b>Kalau sudah pegang: {jual_judul}</b>\n{jual_alasan}")
        other = ""

    reasons = []
    if sc["pct"] < 0 and sc["bear"]:
        top = "; ".join(esc(s) for s in sc["bear"][:2])
        reasons.append(f"Teknikal {sc['pct']:+.0f}% ({esc(str(sc_label))}): {top}")
    elif sc["bull"]:
        top = "; ".join(esc(s) for s in sc["bull"][:2])
        reasons.append(f"Teknikal {sc['pct']:+.0f}% ({esc(str(sc_label))}): {top}")
    else:
        reasons.append(f"Teknikal {sc['pct']:+.0f}% ({esc(str(sc_label))})")
    if fund:
        notes = "; ".join(esc(n) for n in fund.get("notes", [])[:2])
        reasons.append(f"Fundamental {fund['pct']:+.0f}% ({esc(fund['label'])}): "
                       f"{notes}" if notes else
                       f"Fundamental {fund['pct']:+.0f}% ({esc(fund['label'])})")
        pen_abs = abs(float(forensic_pen or 0.0))
        if pen_abs > 0 or forensic_flags:
            fl_txt = "; ".join(esc(str(x)) for x in forensic_flags[:2])
            reasons.append(
                f"Forensik kas/dividen (-{pen_abs:.0f} poin)"
                + (f": {fl_txt}" if fl_txt else "")
            )
    if news_sent:
        reasons.append(f"Berita {news_sent['pct']:+.0f}% "
                       f"({news_sent['n']} berita)")
    if rel_pct is not None:
        reasons.append(
            f"Peringkat relatif lintas-pasar: persentil {float(rel_pct):.0f}% "
            f"(pita tahan >= {saranmod.HOLD_BAND_PCT:.0f}%)"
        )
    if isinstance(market_regime, dict) and market_regime.get("status") in ("BULL", "DEFENSIVE"):
        reasons.append(
            f"Rezim IHSG: {esc(str(market_regime.get('label') or market_regime.get('status')))}"
        )
    if weekly:
        arrow_w = ("📈" if weekly["direction"] == "naik"
                   else ("📉" if weekly["direction"] == "turun" else "➖"))
        reasons.append(
            f"Tren mingguan: {arrow_w} {esc(weekly['direction'])} "
            f"({weekly['pct_vs_ma']:+.1f}% vs MA20)"
        )
        if weekly.get("warning"):
            reasons.append(f"⚠️ {esc(weekly['warning'])}")
    reasons_txt = "\n".join(f"  • {r}" for r in reasons)

    conds = []
    if snap.get("sma50") and close < snap["sma50"]:
        conds.append(f"Kalau harga tembus ke atas MA50 ({fmt_rp(snap['sma50'])}) "
                     "→ teknikal membaik.")
    elif snap.get("sma20") and close < snap["sma20"]:
        conds.append(f"Kalau harga tembus ke atas MA20 ({fmt_rp(snap['sma20'])}) "
                     "→ momentum jangka pendek membaik.")
    if snap.get("rsi") is not None and snap["rsi"] < 30:
        conds.append("RSI sudah oversold — pantau pantulan, tapi jangan tangkap "
                     "pisau jatuh.")
    if close > 0 and abs(close - sr["support"]) / close < 0.03:
        conds.append(f"Harga dekat support {fmt_rp(sr['support'])} — kalau jebol, "
                     "sinyal makin buruk.")
    elif close > sr["resistance"] * 0.97:
        conds.append(f"Kalau tembus resistance {fmt_rp(sr['resistance'])} → "
                     "momentum naik.")
    conds_txt = "\n".join(f"  • {esc(c)}" for c in conds[:2])

    arb_warns = list((exit_g.get("arb_risk") or {}).get("warnings") or [])
    level_lines = [
        f"  Support: {fmt_rp(sr['support'])} | Resistance: {fmt_rp(sr['resistance'])}"
    ]
    if focus == "jual":
        if held_pos:
            lots_h = int(held_pos.get("lots") or 0)
            net_pct = float(exit_g.get("pnl_net_pct") or 0.0)
            days_txt = f" · pegang {days_held} hari bursa" if days_held is not None else ""
            level_lines.append(
                f"  Posisi tercatat: <b>{lots_h} lot</b> @ {fmt_rp(entry_ref)} "
                f"(PnL bersih {net_pct:+.2f}% setelah fee BEI 0,40%{days_txt})"
            )
        eff_sl = float(exit_g.get("effective_sl") or exit_g.get("sl_initial") or sl_beli)
        init_sl = float(exit_g.get("sl_initial") or sl_beli)
        sl_src_lbl = "SL eksekusi" if stored_sl else "1×ATR"
        trail = exit_g.get("chandelier") or {}
        trail_sl = trail.get("stop")
        trail_txt = (
            f" | Chandelier trailing SL: {fmt_rp(float(trail_sl))} "
            f"(puncak 22h {fmt_rp(float(trail.get('high_22') or close))} − 3×ATR)"
            if trail_sl
            else ""
        )
        level_lines.append(
            f"  Batas keluar (SL efektif): <b>{fmt_rp(eff_sl)}</b> "
            f"(SL awal {fmt_rp(init_sl)} [{sl_src_lbl}]{trail_txt})"
        )
        so = exit_g.get("scale_out") or {}
        if so.get("tp1"):
            bep_sl = float(so.get("bep_sl") or exit_g.get("bep_sl") or entry_ref)
            tp2_px = float(so.get("tp_full") or exit_g.get("tp_target") or tp_beli)
            level_lines.append(
                f"  Scale-out TP1 (jual 50% lot): <b>{fmt_rp(float(so['tp1']))}</b> "
                f"→ geser SL sisa ke BEP+0,40% fee BEI <b>{fmt_rp(bep_sl)}</b> | "
                f"TP penuh: <b>{fmt_rp(tp2_px)}</b>"
            )
        ts = exit_g.get("time_stop") or {}
        level_lines.append(
            f"  Time-stop: evaluasi keluar bila pegang >= {ts.get('max_hold_days', 20)} hari bursa "
            f"({esc(str(ts.get('reason') or ''))})"
        )
        for w in arb_warns:
            level_lines.append(f"  ⚠️ {esc(str(w))}")
    elif focus == "beli":
        level_lines.append(
            f"  Kalau beli — SL: {fmt_rp(sl_beli)} (1×ATR) | "
            f"TP: {fmt_rp(tp_beli)} (2×ATR)"
        )
        for w in arb_warns:
            level_lines.append(f"  ⚠️ {esc(str(w))}")
    else:
        level_lines.append(
            f"  Kalau beli — SL: {fmt_rp(sl_beli)} (1×ATR) | "
            f"TP: {fmt_rp(tp_beli)} (2×ATR)"
        )
        eff_sl = float(exit_g.get("effective_sl") or exit_g.get("sl_initial") or sl_beli)
        so = exit_g.get("scale_out") or {}
        tp1_px = float(so.get("tp1") or tp_beli)
        bep_sl = float(so.get("bep_sl") or exit_g.get("bep_sl") or entry_ref)
        level_lines.append(
            f"  Kalau sudah pegang — SL efektif: {fmt_rp(eff_sl)} | "
            f"Scale-out TP1 (50%): {fmt_rp(tp1_px)} → SL BEP+fee {fmt_rp(bep_sl)}"
        )
        for w in arb_warns:
            level_lines.append(f"  ⚠️ {esc(str(w))}")
    levels_txt = "\n".join(level_lines)

    prov_line = core_reports.provenance_line(res)
    if not prov_line and res.get("as_of"):
        prov_line = f"🔎 <i>Data per {esc(str(res['as_of']))} WIB</i>\n"
    qn = data_quality.plain_note(res.get("quality_note"))
    qual_note = f"⚠️ <i>{esc(qn)}</i>\n" if qn else ""

    bt_line = btcache.format_verdict_backtest(bt_summary, t)
    trap = powerscore.trap_warning(snap.get("ret20d"), snap.get("ret1d"))
    trap_txt = f"\n{trap}" if trap else ""

    return (
        f"{res.get('stale_note', '')}{prov_line}{qual_note}{title}\n"
        f"Harga: <b>{fmt_rp(close)}</b> "
        f"({snap['ret1d']:+.2f}% 1d, {snap['ret20d']:+.2f}% 20d) | "
        f"PowerScore <b>{pw['pct']:+d}% {pw['emoji']} {esc(str(pw_label))}</b>\n\n"
        f"{headline}{other}\n\n"
        f"<b>Kenapa:</b>\n{reasons_txt}\n\n"
        f"<b>Level penting:</b>\n"
        f"{levels_txt}\n"
        f"{bt_line}\n"
        f"{('<b>Yang bisa mengubah jawaban:</b>\n' + conds_txt + chr(10)) if conds_txt else ''}"
        f"{trap_txt}\n"
        f"🔒 <i>Bukan saran investasi. Skor bot = deskripsi kondisi saat itu, "
        f"bukan ramalan. DYOR!</i>"
    )

