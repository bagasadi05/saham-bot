"""Handler info: /start (/menu, /help), /id, /validasi, /status.

Dependensi bot (menu markup, load_validation/format_validasi, STATS,
STARTED_AT, ADMIN_IDS) diterima lewat `deps` agar monkeypatch di tes tetap
bekerja dan tidak ada impor melingkar.
"""
from __future__ import annotations

import asyncio
import logging
import time

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core import data, marketcal, store

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
