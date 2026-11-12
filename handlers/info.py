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

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from core import data, fundamental, marketcal, news, store
from core.reports import _trend_line, esc

log = logging.getLogger("sahambuddy")


async def do_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    await update.effective_message.reply_text(
        "👋 Halo! Aku <b>SahamBuddy</b> — bot analisis saham Indonesia "
        "(teknikal + fundamental + sentimen berita + data resmi BEI).\n\n"
        "Menu utama:\n"
        "• <b>/analisis BBCA</b> — PowerScore, tren mingguan, se-sektor, chart & berita\n"
        "• <b>/skor TLKM BBRI</b> — ringkasan cepat\n"
        "• <b>/berita BBCA</b> — 7 berita terbaru + sentimen (ML lokal)\n"
        "• <b>/fundamental BBRI</b> — PER/PBV/ROE/dividen/DER + tren 4 kuartal\n"
        "• <b>/saran</b> — pemindaian harian: kandidat BUY + entry/SL/TP + "
        "ukuran posisi (1% modal), dilacak otomatis; <b>/saran list</b>, "
        "<b>/saran lq45</b>, ubah modal: <b>/modal 10000000</b>\n"
        "• <b>/sinyal</b> — sinyal BELI/JUAL <b>seluruh pasar IDX</b> "
        "(±944 emiten): <b>/sinyal beli</b>, <b>/sinyal beli bagus</b> "
        "(lolos saringan kualitas), <b>/sinyal jual</b>, <b>/sinyal BBCA</b>, "
        "<b>/sinyal csv</b>; perbarui: <b>/scan</b>\n"
        "• <b>/rencana BBCA 5000000</b> — rencana beli <b>satu emiten</b>: "
        "lot yang terjangkau, biaya, % modal, rugi maksimal, RR\n"
        "• <b>/paket</b> — paket beli <b>multi-emiten siap Stockbit</b> dari "
        "hasil <b>/scan</b>: kandidat paling likuid &amp; berkualitas\n"
        "• <b>/cek BBCA</b> — ceklist <b>satu layar</b>: tren, valuasi, "
        "asing, berita, agenda, likuiditas + kesimpulan\n"
        "• <b>/portofolio</b> — posisi <b>uang sungguhan</b>: PnL, bobot, "
        "sektor, risiko SL; catat: <b>/portofolio beli BBCA 5 9000</b>\n"
        "• <b>/bandar</b> — IHSG resmi BEI + top movers; <b>/bandar BBCA</b>\n"
        "• <b>/sektor</b> — rotasi sektor BEI: sektor terkuat & terlemah hari ini\n"
        "• <b>/banding BBCA BBRI</b> — tabel PER/PBV/ROE/return + konteks IHSG\n"
        "• <b>/agenda BBCA</b> — ex-date dividen & aksi korporasi\n"
        "• <b>/backtest BBCA</b> — akurasi statistik + simulasi transaksi\n"
        "• <b>/watch add ANTM</b> / <b>/alert add BBCA jump</b> — alarm otomatis\n"
        "• <b>/belajar RSI</b> — kamus istilah: cara baca + jebakan umumnya\n"
        "• <b>/validasi</b> — hasil uji statistik skor (IC, walk-forward, Kronos)\n"
        "• <b>/jejak</b> — track record nyata: hasil sinyal BELI/JUAL setelah "
        "N hari bursa\n"
        "• <b>/beli BBCA</b> / <b>/jual BBCA</b> — tanya layak beli/jual (+ konteks backtest)\n"
        "• <b>/auto on</b> — ringkasan harian ±08:30 WIB + pengingat band/dividen\n"
        "• <b>/id</b> — lihat ID Telegram kamu (untuk <code>ALLOWED_USER_IDS</code>)\n\n"
        "⚠️ Bukan saran investasi — selalu DYOR!",
        parse_mode=ParseMode.HTML,
        reply_markup=deps.main_menu_markup())


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


async def do_validasi(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    """Tampilkan hasil uji statistik yang tersimpan (tanpa hitung ulang)."""
    v = await asyncio.to_thread(deps.load_validation)
    if v is None:
        await update.effective_message.reply_text(
            "Belum ada data validasi. Jalankan <code>python research/evaluate.py</code> "
            "lalu <code>python research/publish.py</code>.", parse_mode=ParseMode.HTML)
        return
    await update.effective_message.reply_text(deps.format_validasi(v),
                                              parse_mode=ParseMode.HTML)


async def do_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    """Kesehatan bot: uptime, error, isi store, cache, status bursa."""
    user = update.effective_user
    if deps.ADMIN_IDS and (not user or user.id not in deps.ADMIN_IDS):
        await update.effective_message.reply_text("❌ /status hanya untuk admin bot.")
        return
    up_min = int((time.time() - deps.STARTED_AT) // 60)
    jam, menit = divmod(up_min, 60)
    try:
        c = store.counts()
        h = store.scan_history_counts()
    except Exception:
        log.exception("status: store gagal dibaca")
        c, h = {}, {}
    cache = data.cache_stats()
    avg = deps.STATS["ms"] / deps.STATS["commands"] if deps.STATS["commands"] else 0.0
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
        await update.effective_message.reply_text(
            "📰 Berita — pilih emiten:",
            reply_markup=deps.ticker_pick_markup(
                "berita", store.get_watchlist(update.effective_chat.id)))
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
    rows = [f"🏛️ <b>Fundamental {esc(t)}</b> — {esc(fund.get('name'))}"]
    if fund.get("sector"):
        rows.append(f"Sektor: {esc(fund['sector'])}")
    rows.append(f"Skor: <b>{sc['pct']:+d}%</b> {sc['emoji']} {esc(sc['label'])}\n")

    def _fmt(key, tpl, suffix=""):
        v = fund.get(key)
        return tpl % v + suffix if v is not None else "—"

    der_v = fundamental.normalize_der(fund.get("debt_eq"))
    der_txt = f"{der_v:.0f}%" if der_v is not None else "—"
    rows.append(f"  PER: {_fmt('per', '%.1f')}   PBV: {_fmt('pbv', '%.2f')}")
    rows.append(f"  EPS: {_fmt('eps', '%d')}   DER: {der_txt}")
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
        rows.append(f"  Kap. pasar: Rp{fund['market_cap'] / 1e12:.1f} T")

    rows.append(
        f"\nData per: {esc(str(fund.get('as_of') or 'kuartal terakhir yang tercatat'))}"
        " <i>(laporan keuangan via Yahoo, bisa delay 1-2 kuartal)</i>"
    )
    for lbl, key in (("Pendapatan", "rev_trend"), ("Laba bersih", "profit_trend")):
        line = _trend_line(fund.get(key))
        rows.append(f"  {lbl} 4K: {line if line else '<i>tren tidak tersedia</i>'}")

    rows.append("\nCatatan skor:")
    rows.extend(f"  {esc(n)}" for n in sc["notes"])
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
        await update.effective_message.reply_text(
            "💰 Fundamental — pilih emiten:",
            reply_markup=deps.ticker_pick_markup(
                "fundamental", store.get_watchlist(update.effective_chat.id)))
        return
    await do_fundamental(update, args[0], deps)
