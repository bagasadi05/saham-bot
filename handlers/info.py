"""Handler info: /start (/menu, /help), /id, /validasi, /status.

Dependensi bot (menu markup, load_validation/format_validasi, STATS,
STARTED_AT, ADMIN_IDS) diterima lewat `deps` agar monkeypatch di tes tetap
bekerja dan tidak ada impor melingkar.
"""
from __future__ import annotations

import asyncio
import html
import logging
import time
from datetime import timedelta

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from core import (data, fundamental, learn, marketcal, news,
                  scorecard, store)
from core.reports import _trend_line, esc, fmt_compact_amount

log = logging.getLogger("sahambuddy")


async def do_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    await update.effective_message.reply_text(
        deps._menu_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=deps.main_menu_markup())


async def do_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    await update.effective_message.reply_text(
        deps._help_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=deps.help_markup())


async def do_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    """[P11] Tampilkan ID Telegram pemakai untuk keperluan ALLOWED_USER_IDS."""
    user = update.effective_user
    uid = user.id if user else update.effective_chat.id
    await update.effective_message.reply_text(
        f"🪪 <b>ID Telegram kamu:</b> <code>{uid}</code>\n"
        "Salin angka ini ke <code>ALLOWED_USER_IDS</code> di file <code>.env</code> "
        "jika ingin membatasi akses bot hanya untuk kamu sendiri.",
        parse_mode=ParseMode.HTML,
    )


async def do_validasi(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps,
                      *, from_menu: bool = False):
    """Tampilkan hasil uji statistik yang tersimpan (tanpa hitung ulang)."""
    rm = deps.home_markup() if from_menu else None
    v = await asyncio.to_thread(deps.load_validation)
    if v is None:
        await update.effective_message.reply_text(
            "Belum ada data validasi. Jalankan <code>python research/evaluate.py</code> "
            "lalu <code>python research/publish.py</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=rm)
        return
    await deps.reply_long(update.effective_message, deps.format_validasi(v),
                          reply_markup=rm)


async def do_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    """Kesehatan bot: uptime, error, isi store, cache, status bursa."""
    user = update.effective_user
    if deps.ADMIN_IDS and (not user or user.id not in deps.ADMIN_IDS):
        await update.effective_message.reply_text("❌ /status hanya untuk admin bot.")
        return
    up_min = int((time.time() - deps.STARTED_AT) // 60)
    jam, menit = divmod(up_min, 60)
    try:
        c = await asyncio.to_thread(store.counts)
        h = await asyncio.to_thread(store.scan_history_counts)
        scan_meta = await asyncio.to_thread(store.market_scan_meta)
    except Exception:
        log.exception("status: store gagal dibaca")
        c, h, scan_meta = {}, {}, {}
    cache = data.cache_stats()
    avg = deps.STATS["ms"] / deps.STATS["commands"] if deps.STATS["commands"] else 0.0
    scan_ln = ""
    if scan_meta:
        td = scan_meta.get("trade_date") or "—"
        td_min = scan_meta.get("trade_date_min")
        td_txt = f"{td} (min {td_min})" if td_min and td_min != td else str(td)
        ok_n = scan_meta.get("ok", 0)
        st_n = scan_meta.get("skipped_stale", 0)
        ql_n = scan_meta.get("skipped_quality", 0)
        fl_n = scan_meta.get("quality_flagged", 0)
        scan_ln = (
            f"Snapshot /scan: bar {esc(td_txt)} | lolos {ok_n} "
            f"(dilewati basi {st_n}, kualitas {ql_n}, perbaikan bar {fl_n})\n"
        )
    await update.effective_message.reply_text(
        "🩺 <b>Status SahamBuddy</b>\n"
        f"Uptime: {jam}j {menit}m | perintah: {deps.STATS['commands']} | "
        f"error: {deps.STATS['errors']} | rata-rata {avg:.0f} ms\n"
        f"Cache harga: {cache.get('entries', 0)} emiten "
        f"(hit {cache.get('hit', 0)}, miss {cache.get('miss', 0)}, "
        f"TTL {cache.get('ttl_seconds', 0)} dtk)\n"
        f"Store: {c.get('chats', 0)} chat | {c.get('watchlist', 0)} watchlist | "
        f"{c.get('alerts', 0)} alert | {c.get('paper_open', 0)} posisi terbuka "
        f"dari {c.get('paper_total', 0)}\n"
        f"Histori sinyal: {h.get('total', 0)} baris "
        f"({h.get('pending', 0)} menunggu hasil)\n"
        f"{scan_ln}"
        f"Bursa: {'BUKA' if marketcal.market_open() else 'tutup'} | "
        f"hari ini {'hari bursa' if marketcal.is_trading_day(marketcal.now_wib().date()) else 'libur'}",
        parse_mode=ParseMode.HTML)


async def do_berita(update: Update, ticker: str, deps):
    msg = update.effective_message
    try:
        t = data.normalize_ticker(ticker).replace(".JK", "")
    except ValueError as e:
        await msg.reply_text(f"❌ {esc(e)}", parse_mode=ParseMode.HTML, reply_markup=deps.home_markup())
        return
    await update.effective_chat.send_action(ChatAction.TYPING)
    try:
        items = await asyncio.to_thread(news.fetch_news, t)
    except Exception:
        items = []
    if not items:
        await msg.reply_text(
            f"❌ Berita untuk {esc(t)} tidak tersedia — atau jaringan sedang bermasalah.",
            parse_mode=ParseMode.HTML,
            reply_markup=deps.home_markup(),
        )
        return
    sent = await asyncio.to_thread(news.news_sentiment, items)
    n_ml = sent.get("engine_ml", 0)
    if n_ml == sent["n"] and sent["n"] > 0:
        engine_tag = "ML lokal"
    elif n_ml > 0:
        engine_tag = f"ML {n_ml}/{sent['n']} + lexicon"
    else:
        engine_tag = "lexicon"
    lines = [
        f"📰 <b>Berita {esc(t)}</b> — sentimen {sent['emoji']} "
        f"({sent['pct']:+.0f}%, {sent['n']} berita, mesin: {engine_tag})"
    ]
    for d in sent["details"][:7]:
        har = f" {d['sent']:+.0f}" if d["sent"] else ""
        age = f", {d['age_days']:.0f} hari lalu" if d.get("age_days", 0) >= 1 else ""
        src = f" — <i>{esc(d['source'])}</i>" if d.get("source") else ""
        when = f"{d['date']:%d %b}" if d.get("date") else "—"
        url = html.escape(str(d.get("link") or "https://news.google.com"), quote=True)
        lines.append(
            f"\n{when} · <a href=\"{url}\">{esc(d['title'][:110])}</a>"
            f"{har}{age}{src}"
        )
    lines.append("\n\n🔒 Bukan saran investasi.")
    await deps.reply_long(
        msg,
        "\n".join(lines),
        disable_web_page_preview=True,
        reply_markup=deps.result_markup("berita", t),
    )


async def cmd_berita(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    """Ringkasan berita terkini + skor sentimen lexicon Indonesia."""
    args = deps.tokens(update)
    if not args:
        wl = await asyncio.to_thread(store.get_watchlist,
                                     update.effective_chat.id)
        await update.effective_message.reply_text(
            "📰 Berita — pilih emiten:",
            reply_markup=deps.ticker_pick_markup("berita", wl))
        return
    await do_berita(update, args[0], deps)


async def do_fundamental(update: Update, ticker: str, deps):
    msg = update.effective_message
    try:
        t = data.normalize_ticker(ticker).replace(".JK", "")
    except ValueError as e:
        await msg.reply_text(f"❌ {esc(e)}", parse_mode=ParseMode.HTML, reply_markup=deps.home_markup())
        return
    await update.effective_chat.send_action(ChatAction.TYPING)
    try:
        fund = await asyncio.to_thread(fundamental.fetch_fundamentals, t)
    except ValueError as e:
        await msg.reply_text(f"❌ {esc(e)}", parse_mode=ParseMode.HTML, reply_markup=deps.home_markup())
        return
    except Exception as e:
        log.warning("fundamental gagal %s: %s", t, e)
        await msg.reply_text(
            f"❌ Data fundamental untuk {esc(t)} tidak tersedia — "
            "sumber sedang bermasalah (rate-limit/jaringan). Coba lagi nanti.",
            parse_mode=ParseMode.HTML,
            reply_markup=deps.home_markup(),
        )
        return
    if fund is None:
        await msg.reply_text(
            f"❌ Data fundamental untuk {esc(t)} tidak tersedia.",
            parse_mode=ParseMode.HTML,
            reply_markup=deps.home_markup(),
        )
        return
    sc = await asyncio.to_thread(fundamental.score_fundamentals, fund)
    currency = str(fund.get("currency") or "IDR").strip()
    ccy_upper = currency.upper()
    rows = [f"🏛️ <b>Fundamental {esc(t)}</b> — {esc(fund.get('name'))}"]
    if fund.get("sector"):
        rows.append(f"Sektor: {esc(fund['sector'])}")
    if ccy_upper not in ("IDR", ""):
        rows.append(
            f"Mata uang laporan: <b>{esc(currency)}</b> "
            "<i>(angka asli laporan, tidak dikonversi kurs)</i>"
        )
    rows.append(f"Skor: <b>{sc['pct']:+d}%</b> {sc['emoji']} {esc(sc['label'])}\n")

    def _fmt(key, tpl, suffix=""):
        v = fund.get(key)
        return tpl % v + suffix if v is not None else "—"

    der_v = fundamental.normalize_der(fund.get("debt_eq"))
    der_txt = f"{der_v:.0f}%" if der_v is not None else "—"
    rows.append(f"  PER: {_fmt('per', '%.1f')}   PBV: {_fmt('pbv', '%.2f')}")
    eps_v = fund.get("eps")
    if eps_v is None:
        eps_txt = "—"
    elif ccy_upper in ("IDR", ""):
        eps_txt = f"{eps_v:.0f}"
    elif ccy_upper == "TIDAK TERSEDIA":
        eps_txt = f"{eps_v:.2f}"
    else:
        eps_txt = f"{ccy_upper} {eps_v:.2f}" if abs(float(eps_v)) < 100 else f"{ccy_upper} {eps_v:.0f}"
    rows.append(f"  EPS: {eps_txt}   DER: {der_txt}")
    roe = fundamental.normalize_roe(fund.get("roe"))
    margin = fundamental.normalize_margin(fund.get("margin"))
    roe_parts = []
    if roe is not None:
        roe_parts.append(f"ROE: {roe:.1f}%")
    if margin is not None:
        roe_parts.append(f"Margin: {margin:.1f}%")
    if roe_parts:
        rows.append("  " + "   ".join(roe_parts))
    dy = fundamental.normalize_div_yield(fund.get("div_yield"))
    if dy is not None:
        rows.append(f"  Dividend yield: {dy:.1f}%")
    if fund.get("market_cap"):
        if ccy_upper in ("IDR", ""):
            rows.append(f"  Kap. pasar: Rp{fund['market_cap'] / 1e12:.1f} T")
        else:
            rows.append(f"  Kap. pasar: {esc(fmt_compact_amount(fund['market_cap'], currency=currency))}")

    def _amt_singkat(v: float) -> str:
        if ccy_upper not in ("IDR", ""):
            return fmt_compact_amount(v, currency=currency)
        sign = "-" if v < 0 else ""
        av = abs(v)
        if av >= 1e12:
            return f"{sign}Rp{av / 1e12:.2f} T"
        if av >= 1e9:
            return f"{sign}Rp{av / 1e9:.0f} M"
        return f"{sign}Rp{av / 1e6:.0f} Jt"

    cf = await asyncio.to_thread(fundamental.analyze_cashflow_quality, fund)
    if cf["available"]:
        cf_bits = []
        if cf["ocf"] is not None:
            cf_bits.append(f"OCF {esc(_amt_singkat(cf['ocf']))}")
        if cf["fcf"] is not None:
            cf_bits.append(f"FCF {esc(_amt_singkat(cf['fcf']))}")
        if cf["net_cash"] is not None:
            lbl_nc = "Kas bersih" if cf["net_cash"] >= 0 else "Utang bersih"
            cf_bits.append(f"{lbl_nc} {esc(_amt_singkat(abs(cf['net_cash'])))}")
        if cf["current_ratio"] is not None:
            cf_bits.append(f"CR {cf['current_ratio']:.2f}×")
        if cf["payout_pct"] is not None:
            cf_bits.append(f"Payout {cf['payout_pct']:.0f}%")
        if cf_bits:
            rows.append("  Arus kas &amp; neraca: " + " · ".join(cf_bits))
    else:
        rows.append("  Arus kas &amp; neraca: <i>tidak tersedia</i>")
    filing_note = cf.get("filing_note") or fund.get("filing_note")
    if filing_note:
        rows.append(f"  <i>{esc(str(filing_note))}</i>")

    rows.append(
        f"\nData per: {esc(str(fund.get('as_of') or 'kuartal terakhir yang tercatat'))}"
        " <i>(laporan keuangan via Yahoo, bisa delay 1-2 kuartal)</i>"
    )
    for lbl, key in (("Pendapatan", "rev_trend"), ("Laba bersih", "profit_trend")):
        line = _trend_line(fund.get(key), currency=currency)
        rows.append(f"  {lbl} 4K: {line if line else '<i>tren tidak tersedia</i>'}")

    rows.append("\nCatatan skor:")
    rows.extend(f"  {esc(n)}" for n in sc["notes"])
    for ok_cf in cf["strengths"]:
        rows.append(f"  🟢 {esc(ok_cf)}")
    rows.append("\n🔒 Bukan saran investasi.")
    await deps.reply_long(
        msg,
        "\n".join(rows),
        reply_markup=deps.result_markup("fundamental", t),
    )


async def cmd_fundamental(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    """Rincian fundamental emiten (PER, PBV, ROE, dividen, DER)."""
    args = deps.tokens(update)
    if not args:
        wl = await asyncio.to_thread(store.get_watchlist,
                                     update.effective_chat.id)
        await update.effective_message.reply_text(
            "💰 Fundamental — pilih emiten:",
            reply_markup=deps.ticker_pick_markup("fundamental", wl))
        return
    await do_fundamental(update, args[0], deps)


async def cmd_agenda(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    """[P4] Agenda emiten: ex-date dividen & aksi korporasi terbaru."""
    args = deps.tokens(update)
    if not args:
        await update.effective_message.reply_text(
            "Contoh: <code>/agenda BBCA</code>", parse_mode=ParseMode.HTML)
        return
    t = args[0].upper()
    await update.effective_chat.send_action(ChatAction.TYPING)

    def _job():
        import yfinance as yf
        sym = data.normalize_ticker(t)
        tk = yf.Ticker(sym)
        return sym, (tk.calendar or {}), tk.dividends, tk.splits

    try:
        sym, cal, divs, splits = await asyncio.to_thread(_job)
    except ValueError as e:
        await update.effective_message.reply_text(f"❌ {e}")
        return
    except Exception as e:
        log.warning("agenda gagal %s: %s", t, e)
        await update.effective_message.reply_text(
            f"❌ Agenda {esc(t)} tidak tersedia (sumber bermasalah) — coba lagi nanti.",
            parse_mode=ParseMode.HTML)
        return

    lines = [f"📅 <b>Agenda {esc(sym.replace('.JK', ''))}</b>"]
    # kalender: ex-date dividen / jadwal earnings (Timestamp saja)
    kal = []
    if isinstance(cal, dict):
        for k, v in cal.items():
            if hasattr(v, "strftime"):
                kal.append((str(k), v.strftime("%d %b %Y")))
            elif isinstance(v, (list, tuple)):
                for x in v:
                    if hasattr(x, "strftime"):
                        kal.append((str(k), x.strftime("%d %b %Y")))
                if any(not hasattr(x, "strftime") for x in v):
                    kal.append((str(k), esc(str(v))[:40]))
    if cal is None or not cal:
        # yfinance versi lama: calendar berupa list dict
        if isinstance(cal, list):
            for item in cal[:3]:
                for k, v in (item or {}).items():
                    if hasattr(v, "strftime"):
                        kal.append((str(k), v.strftime("%d %b %Y")))
    if kal:
        lines.append("\n🗓️ Kalender (via Yahoo, bisa berubah):")
        lines.extend(f"  • {esc(k)}: {esc(v)}" for k, v in kal[:6])

    if divs is not None and len(divs):
        last = divs.iloc[-1]
        when = divs.index[-1].strftime("%d %b %Y")
        lines.append(f"\n💵 Dividen terakhir: {float(last):,.1f} /lembar (ex≈{esc(when)})")
        lines.append("  4 terakhir: " + ", ".join(
            f"{float(v):.1f} ({d.strftime('%b %y')})"
            for d, v in list(divs.tail(4).items())))
    else:
        lines.append("\n💵 Dividen: tidak ada agenda tercatat")

    if splits is not None and len(splits):
        lines.append("\n✂️ Split terakhir: " + ", ".join(
            f"ratio {float(v):.0f} ({d.strftime('%d %b %y')})"
            for d, v in list(splits.tail(2).items())))
    else:
        lines.append("✂️ Split: tidak ada agenda tercatat")

    if not kal and (divs is None or len(divs) == 0) and (splits is None or len(splits) == 0):
        lines.append("\nℹ️ Tidak ada agenda tercatat untuk emiten ini "
                     "(atau Yahoo tidak menyimpannya).")
    lines.append("\n🔒 Data Yahoo bisa telat/tidak lengkap — cek juga situs emiten. "
                 "Bukan saran memburu dividen.")
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_jejak(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps,
                    *, from_menu: bool = False):
    """Jejak sinyal: hasil nyata BELI/JUAL setelah horizon hari bursa."""
    rm = deps.home_markup() if from_menu else None
    since = (marketcal.now_wib().date() - timedelta(days=365)).isoformat()
    rows = await asyncio.to_thread(store.closed_scan_history, since)
    counts = await asyncio.to_thread(store.scan_history_counts)
    await deps.reply_long(update.effective_message,
                     scorecard.format_summary(scorecard.summarize(rows), counts),
                     reply_markup=rm)


async def cmd_belajar(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    """[P1] Kamus istilah: /belajar RSI, /belajar PBV, /belajar MACD, ..."""
    args = deps.tokens(update)
    if not args:
        await update.effective_message.reply_text(
            "🎓 <b>Belajar istilah IHSG</b>\n"
            "Ketuk istilah di bawah atau ketik: <code>/belajar &lt;topik&gt;</code>\n"
            "Pilih topik: <code>/belajar RSI</code>, <code>/belajar PBV</code>\n\n"
            f"{learn.list_topics()}",
            parse_mode=ParseMode.HTML,
            reply_markup=deps.belajar_markup())
        return
    txt = learn.learn(args[0])
    if txt is None:
        await update.effective_message.reply_text(
            "❓ Topik tidak dikenal. Kirim <code>/belajar</code> saja "
            "buat lihat daftar topiknya.",
            parse_mode=ParseMode.HTML,
            reply_markup=deps.belajar_markup())
        return
    await update.effective_message.reply_text(
        txt, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
        reply_markup=deps.belajar_detail_markup())
