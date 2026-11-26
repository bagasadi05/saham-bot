"""Laporan kondisi pasar BEI (`/pasar` / `/market`, ringkasan pagi & penutupan).

Modul ini murni domain (tanpa Telegram, tanpa jaringan langsung) sehingga 100%
dapat diuji secara offline. Prinsip kejujuran data (`AGENTS.md`):
- Setiap komponen (IHSG/rezim, breadth snapshot `/scan`, rotasi sektor BEI,
  aliran asing opsional, dan sentimen berita pasar) bersifat opsional.
- Bagian yang datanya kosong/gagal ditulis "tidak tersedia" — tidak diisi 0,
  tidak dikarang.
- Sintesis kondisi (`KONSTRUKTIF` / `WASPADA` / `CAMPURAN` / `TIDAK_CUKUP_DATA`)
  bersifat deskriptif atas kondisi pasar saat ini, bukan janji arah esok hari.
"""
from __future__ import annotations

import html
import math
import statistics
from typing import Any

import pandas as pd

from core import news as newsmod, regime as regimemod, sektor as sektormod
from core.reports import esc

CONDITION_CONSTRUCTIVE = "KONSTRUKTIF"
CONDITION_CAUTIOUS = "WASPADA"
CONDITION_MIXED = "CAMPURAN"
CONDITION_NA = "TIDAK_CUKUP_DATA"


def _fmt_num(val: float | None, digits: int = 2, *, sign: bool = False) -> str:
    if val is None or not isinstance(val, (int, float)) or not math.isfinite(val):
        return "—"
    fmt = f"{val:+,.{digits}f}" if sign else f"{val:,.{digits}f}"
    # Format Indonesia: titik ribuan, koma desimal
    return fmt.replace(",", "_").replace(".", ",").replace("_", ".")


def _ret_pct(series: pd.Series, lookback: int) -> float | None:
    if len(series) <= lookback:
        return None
    cur = float(series.iloc[-1])
    prev = float(series.iloc[-(lookback + 1)])
    if not (math.isfinite(cur) and math.isfinite(prev) and prev > 0):
        return None
    return round((cur / prev - 1.0) * 100.0, 2)


def ihsg_snapshot(
    df: pd.DataFrame | None,
    *,
    regime_info: dict | None = None,
    breadth_ma50: float | None = None,
) -> dict[str, Any]:
    """Ringkas level & return IHSG (^JKSE) beserta rezim SMA50/SMA200 + vol/breadth."""
    reg = (
        dict(regime_info)
        if isinstance(regime_info, dict)
        else regimemod.evaluate_ihsg(df, breadth_ma50=breadth_ma50)
    )
    if df is None or df.empty or "close" not in df:
        close = reg.get("close")
        return {
            "ok": close is not None,
            "close": close,
            "ret1d": None,
            "ret5d": None,
            "ret20d": None,
            "as_of": reg.get("as_of"),
            "bars": int(reg.get("bars") or 0),
            "stale": bool(reg.get("stale", False)),
            "regime": reg,
            "reason": None if close is not None else (reg.get("reason") or "data ^JKSE tidak tersedia"),
        }

    close_s = pd.to_numeric(df["close"], errors="coerce").dropna()
    close_s = close_s[close_s > 0]
    if close_s.empty:
        return {
            "ok": False,
            "close": None,
            "ret1d": None,
            "ret5d": None,
            "ret20d": None,
            "as_of": None,
            "bars": 0,
            "stale": bool(getattr(df, "attrs", {}).get("stale", False)),
            "regime": reg,
            "reason": "data ^JKSE tidak memiliki harga penutupan valid",
        }

    as_of = reg.get("as_of")
    if not as_of:
        try:
            as_of = pd.to_datetime(close_s.index[-1]).strftime("%Y-%m-%d")
        except Exception:
            as_of = str(close_s.index[-1])[:10]

    return {
        "ok": True,
        "close": round(float(close_s.iloc[-1]), 2),
        "ret1d": _ret_pct(close_s, 1),
        "ret5d": _ret_pct(close_s, 5),
        "ret20d": _ret_pct(close_s, 20),
        "as_of": as_of,
        "bars": int(len(close_s)),
        "stale": bool(getattr(df, "attrs", {}).get("stale", False) or reg.get("stale", False)),
        "regime": reg,
        "reason": None,
    }


def breadth_snapshot(
    scan_rows: list[dict] | None,
    scan_meta: dict | None = None,
) -> dict[str, Any]:
    """Hitung breadth (luas pasar) dari snapshot `market_scan` lokal."""
    rows = [r for r in (scan_rows or []) if isinstance(r, dict)]
    if not rows:
        return {
            "ok": False,
            "total": 0,
            "beli": 0,
            "jual": 0,
            "tahan": 0,
            "beli_pct": None,
            "jual_pct": None,
            "advancing": 0,
            "declining": 0,
            "flat": 0,
            "adv_ratio_pct": None,
            "median_ret1d": None,
            "trade_date": (scan_meta or {}).get("trade_date"),
            "scanned_at": (scan_meta or {}).get("scanned_at"),
            "reason": "belum ada snapshot /scan",
        }

    total = len(rows)
    beli = sum(1 for r in rows if str(r.get("signal") or "").upper() == "BELI")
    jual = sum(1 for r in rows if str(r.get("signal") or "").upper() == "JUAL")
    tahan = total - beli - jual

    rets: list[float] = []
    adv = dec = flat = 0
    for r in rows:
        raw_r = r.get("ret1d")
        try:
            val = float(raw_r)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(val):
            continue
        rets.append(val)
        if val > 0.05:
            adv += 1
        elif val < -0.05:
            dec += 1
        else:
            flat += 1

    n_ret = len(rets)
    return {
        "ok": True,
        "total": total,
        "beli": beli,
        "jual": jual,
        "tahan": tahan,
        "beli_pct": round(beli / total * 100.0, 1) if total else None,
        "jual_pct": round(jual / total * 100.0, 1) if total else None,
        "advancing": adv,
        "declining": dec,
        "flat": flat,
        "adv_ratio_pct": round(adv / n_ret * 100.0, 1) if n_ret else None,
        "median_ret1d": round(statistics.median(rets), 2) if rets else None,
        "trade_date": (scan_meta or {}).get("trade_date") or rows[0].get("trade_date"),
        "scanned_at": (scan_meta or {}).get("scanned_at"),
        "reason": None,
    }


def foreign_snapshot(stock_rows: list[dict] | None) -> dict[str, Any]:
    """Agregasi arus asing dari payload BEI (`GetStockSummary`) bila tersedia."""
    rows = [r for r in (stock_rows or []) if isinstance(r, dict)]
    if not rows:
        return {"ok": False, "net_value": None, "buy_value": None, "sell_value": None,
                "n_rows": 0, "reason": "ringkasan perdagangan BEI tidak tersedia"}
    buy_sum = 0.0
    sell_sum = 0.0
    counted = 0
    for r in rows:
        try:
            fb = r.get("ForeignBuy")
            fs = r.get("ForeignSell")
            if fb is None or fs is None:
                continue
            fb_v = float(fb)
            fs_v = float(fs)
            if not (math.isfinite(fb_v) and math.isfinite(fs_v)):
                continue
            # ForeignBuy/ForeignSell di BEI umumnya dalam lembar; kalikan harga bila tersedia
            px = r.get("ClosingPrice") or r.get("Close")
            if px is not None:
                px_v = float(px)
                if math.isfinite(px_v) and px_v > 0:
                    buy_sum += fb_v * px_v
                    sell_sum += fs_v * px_v
                    counted += 1
        except (TypeError, ValueError):
            continue
    if counted == 0:
        return {"ok": False, "net_value": None, "buy_value": None, "sell_value": None,
                "n_rows": 0, "reason": "field ForeignBuy/ForeignSell tidak tersedia"}
    net = buy_sum - sell_sum
    return {
        "ok": True,
        "net_value": round(net),
        "buy_value": round(buy_sum),
        "sell_value": round(sell_sum),
        "n_rows": counted,
        "label": "NET BUY" if net > 0 else ("NET SELL" if net < 0 else "IMBANG"),
        "reason": None,
    }


def _synthesize_condition(
    ihsg: dict,
    breadth: dict,
    sectors: dict,
    sent: dict | None,
) -> tuple[str, str, list[str]]:
    """Sintesis deskriptif kondisi pasar saat ini (tanpa janji arah esok hari)."""
    signals: list[int] = []
    notes: list[str] = []

    reg = ihsg.get("regime") or {}
    reg_st = reg.get("status")
    if reg_st == "BULL":
        signals.append(1)
        notes.append("rezim IHSG konstruktif (SMA50 ≥ SMA200, eksposur normal 100%)")
    elif reg_st == "DEFENSIVE":
        signals.append(-2)
        notes.append("rezim IHSG defensif (SMA50 < SMA200, batas eksposur dipangkas 50%)")
    elif ihsg.get("ok") and isinstance(ihsg.get("ret20d"), (int, float)):
        if ihsg["ret20d"] >= 2.0:
            signals.append(1)
            notes.append(f"IHSG 20 hari menguat {_fmt_num(ihsg['ret20d'], 1, sign=True)}%")
        elif ihsg["ret20d"] <= -2.0:
            signals.append(-1)
            notes.append(f"IHSG 20 hari melemah {_fmt_num(ihsg['ret20d'], 1, sign=True)}%")

    if breadth.get("ok"):
        adv_r = breadth.get("adv_ratio_pct")
        if adv_r is not None:
            if adv_r >= 55.0:
                signals.append(1)
                notes.append(f"luas pasar positif ({_fmt_num(adv_r, 0)}% emiten naik)")
            elif adv_r <= 42.0:
                signals.append(-1)
                notes.append(f"luas pasar tertekan ({_fmt_num(adv_r, 0)}% emiten naik)")

    if sectors.get("ok"):
        n_pos = int(sectors.get("n_positive") or 0)
        n_neg = int(sectors.get("n_negative") or 0)
        if n_pos > n_neg + 2:
            signals.append(1)
            notes.append(f"mayoritas sektor hijau ({n_pos}/{sectors['n']})")
        elif n_neg > n_pos + 2:
            signals.append(-1)
            notes.append(f"mayoritas sektor merah ({n_neg}/{sectors['n']})")

    if sent and sent.get("n"):
        lbl = sent.get("label")
        if lbl == "POSITIF":
            signals.append(1)
            notes.append(f"sentimen berita pasar positif ({sent['pct']:+.0f}%)")
        elif lbl == "NEGATIF":
            signals.append(-1)
            notes.append(f"sentimen berita pasar negatif ({sent['pct']:+.0f}%)")
        elif lbl == "CAMPURAN":
            notes.append("sentimen berita pasar campuran (positif & negatif berimbang)")

    if not signals:
        if not (ihsg.get("ok") or breadth.get("ok") or sectors.get("ok") or (sent and sent.get("n"))):
            return CONDITION_NA, "❔", ["tidak cukup data pasar untuk menyusun ringkasan"]
        return CONDITION_MIXED, "🟡", notes or ["sinyal pasar cenderung datar / belum dominan"]

    total = sum(signals)
    has_pos = any(s > 0 for s in signals)
    has_neg = any(s < 0 for s in signals)
    if reg_st == "DEFENSIVE" or (total <= -2) or (has_neg and not has_pos):
        return CONDITION_CAUTIOUS, "🔴", notes
    if has_pos and not has_neg:
        return CONDITION_CONSTRUCTIVE, "🟢", notes
    return CONDITION_MIXED, "🟡", notes


def build_market_report(
    *,
    ihsg_df: pd.DataFrame | None = None,
    regime_info: dict | None = None,
    scan_rows: list[dict] | None = None,
    scan_meta: dict | None = None,
    index_rows: list[dict] | None = None,
    index_error: str | None = None,
    stock_rows: list[dict] | None = None,
    news_items: list[dict] | None = None,
    news_sent: dict | None = None,
    as_of_label: str | None = None,
) -> dict[str, Any]:
    """Susun laporan kondisi pasar BEI terstruktur dari seluruh bahan yang tersedia."""
    b_ma50 = regimemod.compute_breadth_ma50(scan_rows) if regime_info is None else None
    ihsg = ihsg_snapshot(ihsg_df, regime_info=regime_info, breadth_ma50=b_ma50)
    breadth = breadth_snapshot(scan_rows, scan_meta)

    if index_error:
        sectors: dict[str, Any] = {"ok": False, "sectors": [], "reason": index_error}
    elif index_rows:
        sectors = sektormod.summarize(index_rows)
    else:
        sectors = {"ok": False, "sectors": [], "reason": "data indeks sektor BEI tidak tersedia"}

    foreign = foreign_snapshot(stock_rows) if stock_rows is not None else {
        "ok": False, "net_value": None, "reason": "tidak diminta"
    }

    if news_sent is not None:
        sent = news_sent
    elif news_items:
        sent = newsmod.news_sentiment(news_items)
    else:
        sent = None

    missing: list[str] = []
    if not ihsg.get("ok"):
        missing.append("IHSG")
    if not breadth.get("ok"):
        missing.append("breadth (/scan)")
    if not sectors.get("ok"):
        missing.append("sektor BEI")
    if not (sent and sent.get("n")):
        missing.append("berita pasar")

    condition, cond_emoji, cond_notes = _synthesize_condition(ihsg, breadth, sectors, sent)
    return {
        "as_of_label": as_of_label or ihsg.get("as_of") or breadth.get("trade_date") or "",
        "condition": condition,
        "condition_emoji": cond_emoji,
        "condition_notes": cond_notes,
        "ihsg": ihsg,
        "breadth": breadth,
        "sectors": sectors,
        "foreign": foreign,
        "news": sent,
        "missing": missing,
    }


def _fmt_compact_idr(val: float) -> str:
    sign = "+" if val > 0 else ("-" if val < 0 else "")
    av = abs(val)
    if av >= 1e12:
        return f"{sign}Rp{_fmt_num(av / 1e12, 2)}T"
    if av >= 1e9:
        return f"{sign}Rp{_fmt_num(av / 1e9, 1)}M"
    return f"{sign}Rp{_fmt_num(av / 1e6, 0)}jt"


def format_market_report(
    report: dict[str, Any],
    *,
    title: str | None = None,
    disclaimer: str | None = None,
) -> str:
    """Format laporan kondisi pasar lengkap dalam HTML aman Telegram."""
    head = title or "🌐 <b>LAPORAN KONDISI PASAR BEI</b>"
    as_of = report.get("as_of_label")
    lines = [head]
    if as_of:
        lines.append(f"<i>Acuan data: {esc(as_of)}</i>")

    cond = report.get("condition") or CONDITION_NA
    c_em = report.get("condition_emoji") or "❔"
    c_notes = report.get("condition_notes") or []
    lines += [
        "",
        f"{c_em} <b>Ringkasan kondisi: {esc(cond)}</b>",
    ]
    for n in c_notes[:3]:
        lines.append(f"  • {esc(n)}")

    # 1) IHSG & Rezim
    ihsg = report.get("ihsg") or {}
    lines += ["", "📈 <b>1. IHSG &amp; Rezim Pasar (^JKSE)</b>"]
    if ihsg.get("ok"):
        bits = [f"<b>{_fmt_num(ihsg['close'], 2)}</b>"]
        if ihsg.get("ret1d") is not None:
            bits.append(f"1h {_fmt_num(ihsg['ret1d'], 2, sign=True)}%")
        if ihsg.get("ret5d") is not None:
            bits.append(f"5h {_fmt_num(ihsg['ret5d'], 2, sign=True)}%")
        if ihsg.get("ret20d") is not None:
            bits.append(f"20h {_fmt_num(ihsg['ret20d'], 2, sign=True)}%")
        stale_tag = " ⚠️(cache lama)" if ihsg.get("stale") else ""
        lines.append(f"  IHSG: {' · '.join(bits)}{stale_tag}")
        reg = ihsg.get("regime") or {}
        if reg.get("status") in ("BULL", "DEFENSIVE"):
            sma50 = _fmt_num(reg.get("sma50"), 0)
            sma200 = _fmt_num(reg.get("sma200"), 0)
            gap = _fmt_num(reg.get("gap_pct"), 2, sign=True)
            scale_pct = int(round(float(reg.get("exposure_scale", 1.0)) * 100))
            lines.append(
                f"  Rezim: <b>{esc(reg.get('label') or reg['status'])}</b> "
                f"(SMA50 {sma50} vs SMA200 {sma200}, selisih {gap}% · eksposur {scale_pct}%)"
            )
        else:
            lines.append(
                f"  Rezim: <i>tidak tersedia</i> "
                f"({esc(reg.get('reason') or 'data <200 hari bursa')})"
            )
    else:
        lines.append(
            f"  <i>Data IHSG tidak tersedia</i> ({esc(ihsg.get('reason') or 'gagal dimuat')})."
        )

    # 2) Breadth / Luas Pasar (snapshot lokal)
    br = report.get("breadth") or {}
    lines += ["", "📡 <b>2. Luas Pasar (Snapshot /scan)</b>"]
    if br.get("ok"):
        dt_txt = f" ({esc(br['trade_date'])})" if br.get("trade_date") else ""
        lines.append(
            f"  Emiten terpantau: <b>{br['total']}</b>{dt_txt} — "
            f"🟢 BELI <b>{br['beli']}</b> ({_fmt_num(br['beli_pct'], 1)}%) · "
            f"🔴 JUAL <b>{br['jual']}</b> ({_fmt_num(br['jual_pct'], 1)}%) · "
            f"⚪ TAHAN <b>{br['tahan']}</b>"
        )
        if br.get("adv_ratio_pct") is not None:
            med_txt = (
                f" · median 1h {_fmt_num(br['median_ret1d'], 2, sign=True)}%"
                if br.get("median_ret1d") is not None
                else ""
            )
            lines.append(
                f"  Pergerakan 1h: <b>{br['advancing']}</b> naik / "
                f"<b>{br['declining']}</b> turun / {br['flat']} datar "
                f"({_fmt_num(br['adv_ratio_pct'], 0)}% naik{med_txt})"
            )
    else:
        lines.append(
            f"  <i>Breadth tidak tersedia</i> ({esc(br.get('reason') or 'belum ada snapshot')}). "
            "Jalankan <code>/scan</code> untuk memperbarui."
        )

    # 3) Rotasi Sektor BEI
    sec = report.get("sectors") or {}
    lines += ["", "🏭 <b>3. Rotasi Sektor BEI</b>"]
    if sec.get("ok"):
        stale_age = report.get("index_stale_age")
        if stale_age is not None:
            jam = max(1, int(round(float(stale_age) / 3600)))
            lines.append(
                f"  ⚠️ <i>idx.co.id diblokir — memakai cache terakhir (±{jam} jam lalu).</i>"
            )
        all_secs = sec.get("sectors") or []
        lines.append(
            f"  {sec['n']} sektor terbaca: <b>{sec['n_positive']} naik</b> / "
            f"<b>{sec['n_negative']} turun</b>"
        )
        top_s = all_secs[:3]
        top_keys = {s.get("code") or s.get("name") for s in top_s}
        bot_s = [
            s for s in reversed(all_secs)
            if (s.get("code") or s.get("name")) not in top_keys
        ][:3]
        if top_s:
            lines.append(
                "  💪 Terkuat: "
                + " · ".join(
                    f"<b>{esc(s['name'])}</b> {_fmt_num(s['pct'], 2, sign=True)}%"
                    for s in top_s
                )
            )
        if bot_s:
            lines.append(
                "  🩸 Terlemah: "
                + " · ".join(
                    f"<b>{esc(s['name'])}</b> {_fmt_num(s['pct'], 2, sign=True)}%"
                    for s in bot_s
                )
            )
    else:
        lines.append(
            f"  <i>Data sektor BEI tidak tersedia</i> "
            f"({esc(sec.get('reason') or 'tidak terbaca')})."
        )

    # 3b) Arus asing bila disediakan
    fgn = report.get("foreign") or {}
    if fgn.get("ok"):
        lines.append(
            f"  🌍 Estimasi arus asing BEI ({fgn['n_rows']} emiten): "
            f"<b>{esc(fgn['label'])} {_fmt_compact_idr(fgn['net_value'])}</b>"
        )

    # 4) Sentimen Berita Pasar
    ns = report.get("news")
    lines += ["", "📰 <b>4. Sentimen Berita Pasar</b>"]
    if ns and ns.get("n"):
        cov = (ns.get("engine_coverage") or {}).get("summary") or "lexicon"
        lines.append(
            f"  Sentimen: {ns['emoji']} <b>{esc(ns['label'])}</b> "
            f"({ns['pct']:+.0f}%, {ns['n']} berita — "
            f"+{ns.get('n_pos', 0)}/-{ns.get('n_neg', 0)}/={ns.get('n_neu', 0)}, "
            f"mesin: {esc(cov)})"
        )
        for d in (ns.get("details") or [])[:4]:
            url = html.escape(str(d.get("link") or "https://news.google.com"), quote=True)
            src = f" — <i>{esc(d['source'])}</i>" if d.get("source") else ""
            sc_tag = f" ({d['sent']:+.1f})" if d.get("sent") else ""
            lines.append(
                f"  • <a href=\"{url}\">{esc(str(d.get('title') or '')[:95])}</a>"
                f"{sc_tag}{src}"
            )
    else:
        lines.append("  <i>Berita pasar tidak tersedia saat ini.</i>")

    missing = report.get("missing") or []
    if missing:
        lines += [
            "",
            f"ℹ️ <i>Bagian tidak tersedia: {esc(', '.join(missing))} — "
            "tidak diisi nol dan tidak dikarang.</i>",
        ]

    if disclaimer:
        lines += ["", disclaimer]
    else:
        lines += [
            "",
            "🔒 <i>Ringkasan kondisi deskriptif dari data pasar, bukan ramalan atau ajakan beli/jual.</i>",
        ]
    return "\n".join(lines)


def format_market_brief(report: dict[str, Any]) -> str:
    """Ringkasan kondisi pasar ringkas (3–5 baris) untuk disisipkan di `morning_scan`."""
    ihsg = report.get("ihsg") or {}
    br = report.get("breadth") or {}
    ns = report.get("news")
    if not (ihsg.get("ok") or br.get("ok") or (ns and ns.get("n"))):
        return ""

    cond = report.get("condition") or CONDITION_NA
    c_em = report.get("condition_emoji") or "❔"
    lines = [f"🌐 <b>Kondisi Pasar</b> — {c_em} <b>{esc(cond)}</b>"]

    if ihsg.get("ok"):
        reg = ihsg.get("regime") or {}
        reg_lbl = reg.get("label") if reg.get("status") in ("BULL", "DEFENSIVE") else "rezim tdk tersedia"
        ret1 = f" ({_fmt_num(ihsg['ret1d'], 2, sign=True)}% 1h)" if ihsg.get("ret1d") is not None else ""
        lines.append(f"  • IHSG: <b>{_fmt_num(ihsg['close'], 2)}</b>{ret1} · {esc(reg_lbl)}")
    else:
        lines.append("  • IHSG: <i>tidak tersedia</i>")

    if br.get("ok"):
        lines.append(
            f"  • Snapshot pasar ({br['total']} emiten): "
            f"BELI {br['beli']} · JUAL {br['jual']} · TAHAN {br['tahan']}"
        )

    if ns and ns.get("n"):
        lines.append(
            f"  • Berita pasar: {ns['emoji']} <b>{esc(ns['label'])}</b> "
            f"({ns['pct']:+.0f}%, {ns['n']} berita)"
        )
    lines.append("  <i>Detail lengkap: <code>/pasar</code></i>")
    return "\n".join(lines)
