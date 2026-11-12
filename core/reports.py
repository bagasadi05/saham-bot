"""Lapisan teks laporan bot: laporan /analisis, konteks pasar, harga.

Dipisah dari `bot.py` supaya handler tidak lagi mencampur orkestrasi jaringan
dengan perakitan HTML. Semua fungsi di sini **murni**: menerima data yang sudah
diambil pemanggil dan mengembalikan string — tidak menyentuh jaringan, store,
atau state global, sehingga bisa diuji tanpa mock jaringan.
"""
from __future__ import annotations

import html

from core import analysis, learn, powerscore, textutil


def esc(s) -> str:
    """Escape teks dinamis untuk pesan HTML Telegram (<, >, &).

    Wajib untuk semua teks dari luar (judul berita, nama emiten, string skor
    seperti 'MA20 < MA50') — kalau tidak, Telegram menolak pesan dengan
    'Can't parse entities: unsupported start tag' dan handler crash.
    """
    return html.escape(str(s))


def fmt_rp(v: float) -> str:
    """Rupiah tanpa desimal — delegasi ke core/textutil.py."""
    return textutil.rupiah(v)


def _rp(v) -> str:
    """Harga ringkas: 8.750 / 1.234,5 kalau pecahan."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{x:,.0f}" if abs(x) >= 1 else f"{x:,.2f}"


def last_macd_cross_note(score: dict) -> str:
    return f"\n⚠️ {esc(score['macd_note'])}" if score.get("macd_note") else ""


def market_context_line(res: dict, mkt: dict | None) -> str:
    """[P2 & P9] Konteks IHSG + pembanding se-sektor (angka, bukan opini)."""
    peers = (mkt.get("peers") if mkt else None) or res.get("sector_ctx")
    if not mkt:
        out = "📊 Konteks pasar: <i>data pembanding tidak tersedia</i>"
    else:
        stock20 = res["snap"].get("ret20d")
        stock1y = res.get("ret1y")
        parts = []
        for label, stock_v, mkt_v in [("20 hari", stock20, mkt.get("ret20d")),
                                      ("1 tahun", stock1y, mkt.get("ret1y"))]:
            if stock_v is None or mkt_v is None:
                parts.append(f"{label}: —")
            else:
                diff = stock_v - mkt_v
                arah = ("unggul" if diff > 0.5
                        else ("tertinggal" if diff < -0.5 else "sejalan"))
                parts.append(f"{label}: {stock_v:+.1f}% vs IHSG {mkt_v:+.1f}% "
                             f"({arah} {diff:+.1f} poin)")
        out = "📊 Konteks pasar (IHSG, data delayed): " + " | ".join(parts)
    if peers and peers.get("peers"):
        stock20 = res["snap"].get("ret20d")
        stock1y = res.get("ret1y")
        peer_lbl = ", ".join(esc(p) for p in peers["peers"])
        sec_txt = f" [{esc(peers['sector'])}]" if peers.get("sector") else ""
        sec_parts = []
        for lbl, sv, pv in [("20 hari", stock20, peers.get("ret20d")),
                            ("1 tahun", stock1y, peers.get("ret1y"))]:
            if sv is None or pv is None:
                sec_parts.append(f"{lbl}: —")
            else:
                diff = sv - pv
                sec_parts.append(f"{lbl}: vs rata² peer {pv:+.1f}% ({diff:+.1f} poin)")
        out += f"\n🏭 Se-sektor ({peer_lbl}){sec_txt}: " + " | ".join(sec_parts)
    return out


def data_age_line(res: dict) -> str:
    """' — data per 06 Nov 2026' supaya angka tidak disangka real-time."""
    as_of = (res.get("snap") or {}).get("as_of")
    return f" — <i>data per {esc(as_of)}</i>" if as_of else ""


def build_report(res: dict, fund: dict | None, news_sent: dict | None,
                 mkt: dict | None = None) -> str:
    snap, sc, sr = res["snap"], res["score"], res["sr"]
    # [P1] tiap sinyal diberi penjelasan 1 baris biar pembelajar paham artinya
    def _sig_line(s: str, mark: str) -> str:
        note = learn.signal_note(s)
        return (f"  {mark} {esc(s)}" +
                (f" — <i>{esc(note)}</i>" if note else ""))

    bull_txt = "\n".join(_sig_line(s, "✅") for s in sc["bull"]) or "  —"
    bear_txt = "\n".join(_sig_line(s, "❌") for s in sc["bear"]) or "  —"
    ma_row = " | ".join(
        f"MA{n}:{snap['sma' + n]}" if snap.get('sma' + n) else f"MA{n}:—"
        for n in ("20", "50", "200"))
    power = powerscore.judge(sc["pct"], fund, news_sent, snap)
    conf = analysis.extra_confirmations(res["ind"])
    conf_txt = ""
    for c in conf:
        note = learn.pro_note(c)
        conf_txt += f"  • {esc(c)}" + (f" — <i>{esc(note)}</i>" if note else "") + "\n"
    conf_txt = conf_txt.rstrip()

    # [P1] penjelasan rating & PowerScore biar tidak terasa seperti tebakan
    rating_note = esc(learn.RATING_NOTES.get(
        sc["rating"], "(penjelasan rating tidak tersedia)"))

    rsi_v = snap.get("rsi")
    if rsi_v is None:
        rsi_txt = "—"
    else:
        zone = " (oversold)" if rsi_v < 30 else (" (overbought)" if rsi_v > 70 else "")
        rsi_txt = f"{rsi_v}{zone}"

    wt = res.get("weekly")
    if wt:
        arrow_w = ("📈" if wt["direction"] == "naik"
                   else ("📉" if wt["direction"] == "turun" else "➖"))
        weekly_txt = (
            f"\n📅 Tren mingguan (1wk vs MA20): {arrow_w} "
            f"<b>{esc(wt['direction'])}</b> ({wt['pct_vs_ma']:+.1f}% vs MA20)"
        )
        if wt.get("warning"):
            weekly_txt += f"\n  ⚠️ <i>{esc(wt['warning'])}</i>"
    else:
        weekly_txt = "\n📅 Tren mingguan: <i>data mingguan tidak tersedia</i>"

    headline_txt = ""
    if news_sent and news_sent.get("details"):
        top = sorted(news_sent["details"], key=lambda d: -abs(d["sent"]))[:2]

        def _headline(d: dict) -> str:
            # URL wajib di-escape juga: '&' mentah di href = can't parse entities
            url = html.escape(str(d.get("link") or "https://finance.yahoo.com"),
                              quote=True)
            return (f"  📰 <a href=\"{url}\">{esc(d['title'][:80])}</a>" +
                    (f" <i>({('%+d' % d['sent'])})</i>" if d["sent"] else ""))

        heads = "\n".join(_headline(d) for d in top)
        headline_txt = (f"\nBerita terkini (sentimen {news_sent['emoji']} "
                        f"{news_sent['pct']:+.0f}%):\n{heads}")

    return (
        f"{res.get('stale_note', '')}"
        f"{res.get('quality_note', '')}"
        f"{res.get('fallback_note', '')}"
        f"{res.get('history_note', '')}"
        f"📊 <b>{esc(res['ticker'])}</b>\n"
        f"Harga: <b>{fmt_rp(snap['close'])}</b> "
        f"({snap['ret1d']:+.2f}% 1d, {snap['ret20d']:+.2f}% 20d)"
        f"{data_age_line(res)}\n\n"
        f"{power}\n\n"
        f"Skor teknikal: <b>{sc['pct']:+.0f}%</b> → {sc['emoji']} <b>{esc(sc['rating'])}</b>\n"
        f"  <i>{rating_note}</i>\n"
        f"  ✅ Sinyal positif:\n{bull_txt}\n"
        f"  ❌ Sinyal negatif:\n{bear_txt}\n"
        f"{last_macd_cross_note(sc)}\n\n"
        f"Indikator:\n"
        f"  RSI 14: {rsi_txt}\n"
        f"  MACD hist: {snap['macd_hist']}\n"
        f"  Volume hari ini: {snap['volume']:,.0f} "
        f"({snap['volume'] / snap['vol_avg20']:.1f}x rata\u00b2 20d)\n"
        f"  {ma_row}\n\n"
        f"Level:\n"
        f"  Rentang 20d: {fmt_rp(sr['low_recent'])} — {fmt_rp(sr['high_recent'])}\n"
        f"{('\nKonfirmasi indikator pro:\n' + conf_txt) if conf_txt else ''}\n"
        f"{headline_txt}{weekly_txt}\n\n"
        f"{market_context_line(res, mkt)}\n"
        f"ℹ️ <i>Skor teknikal = bacaan tren, bukan sinyal beli "
        f"(uji 2017–2026: IC +0,01 ≈ nol). Peringkat antar-emiten ada di /saran.</i>\n"
        f"🔒 <i>Bukan saran investasi. DYOR!</i>"
    )


def _trend_line(trend: list[tuple[str, float]] | None) -> str | None:
    """[P3] Satu baris tren 4 kuartal + perubahan % kuartal terakhir."""
    if not trend or len(trend) < 2:
        return None

    def _rp(v: float) -> str:
        sign = "-" if v < 0 else ""
        av = abs(v)
        tril = av / 1e12
        return f"{sign}Rp{tril:.2f}T" if tril >= 1 else f"{sign}Rp{av / 1e9:.0f}M"

    labels = [lbl for lbl, _ in trend]
    vals = [v for _, v in trend]
    base = "→".join(_rp(v) for v in vals)
    chg = (vals[-1] - vals[-2]) / abs(vals[-2]) * 100 if vals[-2] != 0 else None
    arrow = " 📈" if (chg or 0) > 0 else (" 📉" if (chg or 0) < 0 else "")
    out = f"{base}{arrow}"
    if chg is not None:
        out += f" ({chg:+.1f}% kuartal terakhir)"
    out += f"\n      kuartal: {esc(' → '.join(labels))}"
    return out
