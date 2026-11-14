"""Menu interaktif & alur ramah pemula (progressive disclosure).

Seluruh markup tombol, teks panduan pemula, submenu terkelompok, kamus
istilah klik-langsung (/belajar), serta router callback dan input teks
dipusatkan di sini. Semua panggilan ke handler & state bot (`ALLOWED_IDS`,
`run_menu_action`, `_do_*`, `_pending_*`, markup) lewat `deps` agar tetap
kompatibel dengan monkeypatch tes pada `bot`.
"""
from __future__ import annotations

import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest

from core import alerts, learn, saran as saranmod, store
from core.reports import esc

POPULAR_TICKERS = ["BBCA", "BBRI", "TLKM", "BMRI", "ANTM", "ASII"]

# Aksi yang meminta user memilih/mengetik kode emiten -> label tombol/header.
NEED_TICKER = {
    "cek": "🛡️ Cek Layak Beli",
    "rencana": "🎯 Hitung Modal & Lot",
    "analisis": "📊 Analisis Lengkap",
    "skor": "⚡ Nilai Cepat",
    "berita": "📰 Sentimen Berita",
    "fundamental": "💰 Rincian Fundamental",
    "backtest": "📈 Uji Histori 5 Tahun",
    "beli": "🤔 Tanya: Layak Beli?",
    "jual": "🤔 Tanya: Layak Jual?",
    "sinyal_detail": "📡 Detail sinyal",
}

_PICK_HINTS = {
    "cek": "Cek 6 poin keamanan (tren, valuasi, asing, berita, jadwal, likuiditas) + kesimpulan LAYAK / TUNGGU / HINDARI.",
    "rencana": "Hitung berapa lot yang aman dibeli sesuai modal tersimpanmu, lengkap dengan batas rugi (SL) & target (TP).",
    "analisis": "Laporan lengkap: grafik harga, skor tren, kesehatan keuangan, dan sentimen berita terbaru.",
    "beli": "Tanyakan apakah kondisi saat ini cocok untuk beli — hanya pertimbangan data, tidak membeli otomatis.",
    "jual": "Tanyakan apakah saham yang kamu pegang sebaiknya ditahan, dijual sebagian, atau cut loss.",
    "skor": "Ringkasan angka cepat kondisi teknikal saham saat ini (-100 s.d. +100).",
    "berita": "7 berita terbaru beserta sentimen positif, netral, atau negatif.",
    "fundamental": "Cek untung rugi, mahal/murah (PER, PBV), efisiensi (ROE), utang (DER), dan dividen.",
    "backtest": "Uji aturan sinyal ke data 5 tahun lalu untuk melihat seberapa sering berhasil.",
    "sinyal_detail": "Lihat skor lengkap, angka entry/SL/TP, dan fundamental 1 saham dari hasil scan.",
}

_MANUAL_PROMPT = {
    "cek": "Ketik kode saham yang mau dicek kelayakannya (contoh: <code>BBCA</code>):",
    "rencana": "Ketik kode saham untuk hitung lot &amp; rencana beli (contoh: <code>BBCA</code>):",
    "modal": "Ketik nominal modal dalam Rupiah (contoh: <code>5000000</code>, <code>5jt</code>, atau <code>2,5jt</code>):",
    "analisis": "Ketik kode emiten untuk dianalisis (contoh: <code>BBCA</code>, atau beberapa dipisah spasi: <code>BBCA TLKM</code>):",
    "skor": "Ketik kode emiten untuk skor cepat (contoh: <code>BBCA TLKM BBRI</code>):",
    "berita": "Ketik kode emiten untuk beritanya (contoh: <code>BBCA</code>):",
    "fundamental": "Ketik kode emiten untuk fundamentalnya (contoh: <code>BBRI</code>):",
    "backtest": "Ketik kode emiten untuk di-backtest (contoh: <code>BBCA</code>):",
    "beli": "Mau cek layak beli saham apa? Ketik kodenya (contoh: <code>BBCA</code>):",
    "jual": "Mau cek saham yang kamu pegang? Ketik kodenya (contoh: <code>TLKM</code>):",
    "sinyal_detail": "Ketik kode emiten untuk detail sinyalnya (contoh: <code>BBCA</code>):",
    "watch_add": "Ketik kode emiten yang mau ditambah ke watchlist (contoh: <code>ANTM</code>):",
    "watch_del": "Ketik kode emiten yang mau dihapus dari watchlist:",
    "alert_add": (
        "Ketik kode emiten untuk alarm (jenis default: "
        + ", ".join(sorted(alerts.ALL_KINDS)) + "):"
    ),
}

_MODAL_PRESETS = (2_000_000, 5_000_000, 10_000_000, 25_000_000)

_BELAJAR_BUTTONS = (
    ("PER (Mahal/Murah)", "per", "PBV (Nilai Buku)", "pbv"),
    ("ROE (Profitabilitas)", "roe", "Dividen Yield", "dividend"),
    ("RSI (Jenuh Beli/Jual)", "rsi", "MACD (Momentum)", "macd"),
    ("MA50 (Tren Menengah)", "ma50", "IHSG (Pasar Umum)", "ihsg"),
    ("ATR (Stop Loss)", "atr", "PowerScore Bot", "powerscore"),
)


def menu_text() -> str:
    return (
        "👋 Halo! Aku <b>SahamBuddy</b> — asisten untuk cek, hitung modal, "
        "dan belajar saham Indonesia (BEI).\n\n"
        "Baru mulai? Pilih tujuanmu lewat tombol di bawah, atau ketik langsung "
        "pakai bahasa sehari-hari (mis. <code>BBCA layak beli?</code>).\n\n"
        "• <b>Cek Layak Beli</b>: ringkasan 6 poin aman/tidak sebelum beli\n"
        "• <b>Hitung Modal &amp; Lot</b>: tahu berapa lot yang pas &amp; batas rugi\n"
        "• <b>Belajar Istilah</b>: arti RSI, PER, PBV, MA50 pakai bahasa santai\n\n"
        "⚠️ <i>Alat bantu analisis, bukan ajakan beli/jual &amp; tidak terhubung "
        "ke akun sekuritasmu. Selalu DYOR!</i>"
    )


def main_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡️ Cek Layak Beli", callback_data="pick:cek"),
         InlineKeyboardButton("📊 Analisis Lengkap", callback_data="pick:analisis")],
        [InlineKeyboardButton("💡 Ide & Sinyal", callback_data="sub:ide"),
         InlineKeyboardButton("🎯 Hitung Modal & Lot", callback_data="sub:rencana")],
        [InlineKeyboardButton("👀 Watchlist & Alarm", callback_data="sub:pantau"),
         InlineKeyboardButton("💼 Portofolio Saya", callback_data="run:portofolio")],
        [InlineKeyboardButton("🎓 Belajar Istilah", callback_data="run:belajar"),
         InlineKeyboardButton("❓ Panduan Pemula", callback_data="run:help")],
        [InlineKeyboardButton("⚙️ Fitur Lanjutan & Perintah", callback_data="sub:pro")],
    ])


def home_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")]])


def result_markup(action: str, ticker: str) -> InlineKeyboardMarkup:
    """Tombol di bawah hasil: perbarui data + kembali ke menu."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Perbarui", callback_data=f"refresh:{action}:{ticker}")],
        [InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
    ])


def ticker_pick_markup(action: str,
                       watchlist: list[str] | None = None) -> InlineKeyboardMarkup:
    rows = []
    wl = [t for t in (watchlist or []) if t not in POPULAR_TICKERS][:6]
    if wl:
        for i in range(0, len(wl), 3):
            rows.append([InlineKeyboardButton(f"⭐ {t}", callback_data=f"tick:{action}:{t}")
                         for t in wl[i:i + 3]])
    for i in range(0, len(POPULAR_TICKERS), 3):
        rows.append([InlineKeyboardButton(t, callback_data=f"tick:{action}:{t}")
                     for t in POPULAR_TICKERS[i:i + 3]])
    rows.append([InlineKeyboardButton("✏️ Ketik kode lain", callback_data=f"manual:{action}")])
    rows.append([InlineKeyboardButton("⬅️ Kembali", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def watch_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Lihat Watchlist", callback_data="run:watch_list"),
         InlineKeyboardButton("➕ Tambah Saham", callback_data="manual:watch_add")],
        [InlineKeyboardButton("🗑 Hapus Saham", callback_data="manual:watch_del")],
        [InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
    ])


def alert_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Lihat Alarm", callback_data="run:alert_list"),
         InlineKeyboardButton("➕ Pasang Alarm", callback_data="manual:alert_add")],
        [InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
    ])


def belajar_markup() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(lbl1, callback_data=f"belajar:{k1}"),
            InlineKeyboardButton(lbl2, callback_data=f"belajar:{k2}"),
        ]
        for lbl1, k1, lbl2, k2 in _BELAJAR_BUTTONS
    ]
    rows.append([InlineKeyboardButton("⬅️ Menu Utama", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def belajar_detail_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎓 Istilah Lain", callback_data="run:belajar"),
         InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
    ])


def help_text() -> str:
    return (
        "📖 <b>Panduan Pemula — 3 Langkah Mulai</b>\n\n"
        "<b>1. Cek dulu sebelum beli</b>\n"
        "Ketuk <b>🛡️ Cek Layak Beli</b> lalu pilih saham (mis. BBCA). Bot cek "
        "6 poin (tren, mahal/murah, aliran asing, berita, jadwal dividen, "
        "likuiditas) dan memberi kesimpulan <b>LAYAK / TUNGGU / HINDARI</b>.\n\n"
        "<b>2. Hitung lot sesuai modalmu</b>\n"
        "Ketuk <b>🎯 Hitung Modal &amp; Lot</b> supaya tahu berapa lot yang aman "
        "dibeli, harga maksimal, batas rugi (Stop Loss), dan target untung — "
        "tanpa menebak pakai feeling.\n\n"
        "<b>3. Pantau &amp; catat</b>\n"
        "Simpan saham incaran di <b>Watchlist</b>, pasang <b>Alarm</b> harga, "
        "dan catat transaksi nyatamu di <b>Portofolio Saya</b>.\n\n"
        "💡 <i>Tips: kamu juga bisa chat biasa, misalnya: "
        "<code>BBCA layak beli?</code> atau <code>TLKM jual gak?</code></i>\n\n"
        "⚠️ <i>Bot tidak terhubung ke Stockbit/sekuritas &amp; tidak pernah "
        "menaruh order otomatis.</i>"
    )


def help_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡️ Coba Cek BBCA", callback_data="tick:cek:BBCA"),
         InlineKeyboardButton("🎯 Hitung Lot BBCA", callback_data="tick:rencana:BBCA")],
        [InlineKeyboardButton("🎓 Kamus Istilah", callback_data="run:belajar"),
         InlineKeyboardButton("📋 Semua Perintah (/)", callback_data="sub:allcmd")],
        [InlineKeyboardButton("⬅️ Menu Utama", callback_data="menu")],
    ])


def modal_text(cur_modal: int) -> str:
    return (
        f"💵 <b>Atur Modal Rencana &amp; Latihan</b>\n\n"
        f"Modal tersimpan saat ini: <b>{saranmod.fmt(cur_modal)}</b>\n\n"
        "Angka ini dipakai untuk menghitung batas risiko aman per saham "
        "(maks 1% modal per posisi, atau 0,5% saat pasar sedang defensif).\n\n"
        "Pilih nominal cepat di bawah atau ketik angka sendiri:"
    )


def modal_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Rp 2 Juta", callback_data=f"setmodal:{_MODAL_PRESETS[0]}"),
         InlineKeyboardButton("Rp 5 Juta", callback_data=f"setmodal:{_MODAL_PRESETS[1]}")],
        [InlineKeyboardButton("Rp 10 Juta", callback_data=f"setmodal:{_MODAL_PRESETS[2]}"),
         InlineKeyboardButton("Rp 25 Juta", callback_data=f"setmodal:{_MODAL_PRESETS[3]}")],
        [InlineKeyboardButton("✏️ Ketik Angka Sendiri", callback_data="manual:modal")],
        [InlineKeyboardButton("⬅️ Kembali", callback_data="sub:rencana"),
         InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
    ])


def _submenu_content(group: str, modal: int) -> tuple[str, InlineKeyboardMarkup] | None:
    if group == "ide":
        return (
            "💡 <b>Ide Saham &amp; Sinyal Pasar</b>\n\n"
            "• <b>Ide Hari Ini</b>: scan kandidat teratas + ukuran lot aman.\n"
            "• <b>Beli Berkualitas</b>: khusus sinyal BELI yang labanya sehat.\n"
            "• <b>Sinyal Pasar</b>: rangkuman sinyal seluruh BEI (±944 emiten).\n"
            "• <b>Sektor Terkuat</b>: lihat sektor yang sedang banyak diminati.",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("💡 Ide Hari Ini", callback_data="run:saran"),
                 InlineKeyboardButton("✨ Beli Berkualitas", callback_data="run:sinyal_bagus")],
                [InlineKeyboardButton("📡 Sinyal Pasar", callback_data="run:sinyal"),
                 InlineKeyboardButton("🏭 Sektor Terkuat", callback_data="run:sektor")],
                [InlineKeyboardButton("⬅️ Menu Utama", callback_data="menu")],
            ]),
        )
    if group == "rencana":
        return (
            f"🎯 <b>Hitung Modal, Lot &amp; Tanya Beli/Jual</b>\n\n"
            f"Modal tersimpan saat ini: <b>{saranmod.fmt(modal)}</b>\n"
            "<i>1 lot = 100 lembar saham.</i>\n\n"
            "• <b>Hitung 1 Saham</b>: tahu berapa lot pas + SL/TP untuk 1 emiten.\n"
            "• <b>Paket Multi-Saham</b>: beberapa saham siap Stockbit hasil scan.\n"
            "• <b>Tanya Beli/Jual</b>: pertimbangan data (tidak beli otomatis).",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🎯 Hitung 1 Saham", callback_data="pick:rencana"),
                 InlineKeyboardButton("📦 Paket Multi-Saham", callback_data="run:paket")],
                [InlineKeyboardButton("🤔 Tanya: Layak Beli?", callback_data="pick:beli"),
                 InlineKeyboardButton("🤔 Tanya: Layak Jual?", callback_data="pick:jual")],
                [InlineKeyboardButton("💵 Ubah Modal", callback_data="sub:modal"),
                 InlineKeyboardButton("⬅️ Menu Utama", callback_data="menu")],
            ]),
        )
    if group == "modal":
        return modal_text(modal), modal_menu_markup()
    if group == "pantau":
        return (
            "👀 <b>Watchlist, Alarm &amp; Ringkasan Pagi</b>\n\n"
            "• <b>Watchlist</b>: daftar saham incaranmu agar mudah dicek.\n"
            "• <b>Alarm</b>: peringatan otomatis jika harga bergerak tajam.\n"
            "• <b>Auto Pagi</b>: kirim ringkasan rutin ±08:30 WIB tiap hari bursa.",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("⭐ Lihat Watchlist", callback_data="run:watch_list"),
                 InlineKeyboardButton("➕ Tambah Saham", callback_data="manual:watch_add")],
                [InlineKeyboardButton("🗑 Hapus Saham", callback_data="manual:watch_del"),
                 InlineKeyboardButton("🔔 Lihat Alarm", callback_data="run:alert_list")],
                [InlineKeyboardButton("➕ Pasang Alarm", callback_data="manual:alert_add"),
                 InlineKeyboardButton("🌅 Nyalakan Auto Pagi", callback_data="run:auto_on")],
                [InlineKeyboardButton("⬅️ Menu Utama", callback_data="menu")],
            ]),
        )
    if group == "pro":
        return (
            "⚙️ <b>Fitur Lanjutan &amp; Riset</b>\n\n"
            "Perlu cek lebih dalam atau lihat rekam jejak statistik bot?",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("⚡ Nilai Cepat", callback_data="pick:skor"),
                 InlineKeyboardButton("📰 Sentimen Berita", callback_data="pick:berita")],
                [InlineKeyboardButton("💰 Fundamental", callback_data="pick:fundamental"),
                 InlineKeyboardButton("📈 Backtest 5 Thn", callback_data="pick:backtest")],
                [InlineKeyboardButton("🏦 Data BEI / IHSG", callback_data="run:bandar"),
                 InlineKeyboardButton("🔄 Scan ±944 Saham", callback_data="run:scan")],
                [InlineKeyboardButton("🔬 Validasi Riset", callback_data="run:validasi"),
                 InlineKeyboardButton("🧾 Jejak Sinyal", callback_data="run:jejak")],
                [InlineKeyboardButton("📋 Semua Perintah (/)", callback_data="sub:allcmd"),
                 InlineKeyboardButton("⬅️ Menu Utama", callback_data="menu")],
            ]),
        )
    if group == "allcmd":
        return (
            "📋 <b>Daftar Lengkap Perintah (/)</b>\n\n"
            "<b>Untuk Pemula &amp; Cek Cepat:</b>\n"
            "• <code>/start</code>, <code>/menu</code> — buka menu tombol\n"
            "• <code>/help</code> — panduan 3 langkah mulai\n"
            "• <code>/cek BBCA</code> — ceklist 6 bagian + kesimpulan\n"
            "• <code>/analisis BBCA</code> — grafik &amp; analisis lengkap\n"
            "• <code>/rencana BBCA 5jt</code> — hitung lot, SL, TP sesuai budget\n"
            "• <code>/beli BBCA</code> / <code>/jual BBCA</code> — tanya layak beli/jual\n"
            "• <code>/belajar RSI</code> — kamus istilah saham\n\n"
            "<b>Ide Pasar, Modal &amp; Portofolio:</b>\n"
            "• <code>/saran</code> — kandidat harian + sizing latihan\n"
            "• <code>/paket</code> — paket multi-saham siap Stockbit\n"
            "• <code>/sinyal</code> · <code>/sinyal beli bagus</code> — sinyal seluruh IDX\n"
            "• <code>/sektor</code> — rotasi kekuatan sektor BEI\n"
            "• <code>/modal 5000000</code> — atur modal rencana/latihan\n"
            "• <code>/portofolio</code> — catat posisi nyata (<code>/portofolio beli BBCA 5 9000</code>)\n\n"
            "<b>Pantau, Riset &amp; Lainnya:</b>\n"
            "• <code>/watch</code> · <code>/alert</code> · <code>/auto on</code> — pantau otomatis\n"
            "• <code>/skor</code> · <code>/berita</code> · <code>/fundamental</code> · "
            "<code>/banding BBCA BBRI</code> · <code>/agenda BBCA</code>\n"
            "• <code>/scan</code> · <code>/backtest BBCA</code> · <code>/bandar</code> · "
            "<code>/validasi</code> · <code>/jejak</code> · <code>/id</code> · <code>/status</code>",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("📖 Panduan Pemula", callback_data="run:help"),
                 InlineKeyboardButton("⬅️ Menu Utama", callback_data="menu")],
            ]),
        )
    return None


async def safe_edit(msg, text: str, **kw):
    """Edit pesan menu; abaikan 'Message is not modified' (double-tap jinak)."""
    try:
        await msg.edit_text(text, **kw)
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


async def run_menu_action(update: Update, action: str, tickers: list[str], deps):
    """Jalankan satu aksi menu. Dipakai tombol maupun input teks."""
    if action == "analisis":
        await deps._do_analisis(update, tickers)
    elif action == "cek":
        await deps._do_cek(update, [tickers[0]], from_menu=True)
    elif action == "rencana":
        await deps._do_rencana(update, [tickers[0]], from_menu=True)
    elif action == "skor":
        await deps._do_skor(update, tickers, from_menu=True)
    elif action == "berita":
        await deps._do_berita(update, tickers[0])
    elif action == "fundamental":
        await deps._do_fundamental(update, tickers[0])
    elif action == "backtest":
        await deps._do_backtest(update, tickers[0], from_menu=True)
    elif action in ("beli", "tanya_beli"):
        await deps._do_tanya(update, tickers[0], "beli")
    elif action in ("jual", "tanya_jual"):
        await deps._do_tanya(update, tickers[0], "jual")
    elif action == "tanya_umum":
        await deps._do_tanya(update, tickers[0], "umum")
    elif action == "saran":
        await deps._do_saran(update, from_menu=True)
    elif action == "saran_list":
        await deps._do_saran(update, list_only=True, from_menu=True)
    elif action == "watch_list":
        await deps._do_watch_list(update)
    elif action == "watch_add":
        await deps._do_watch_add(update, tickers[0], from_menu=True)
    elif action == "watch_del":
        await deps._do_watch_del(update, tickers[0], from_menu=True)
    elif action == "alert_list":
        await deps._do_alert_list(update, from_menu=True)
    elif action == "alert_add":
        await deps._do_alert_add(update, tickers[0], from_menu=True)
    elif action == "bandar":
        await deps._do_bandar(update, from_menu=True)
    elif action == "portofolio":
        await deps._do_portofolio(update, [], from_menu=True)
    elif action == "sektor":
        await deps._do_sektor(update, [], from_menu=True)
    elif action == "sinyal":
        await deps._do_sinyal(update, [], from_menu=True)
    elif action == "sinyal_bagus":
        await deps._do_sinyal(update, ["beli", "bagus"], from_menu=True)
    elif action == "sinyal_detail":
        await deps._do_sinyal(update, tickers[:1], from_menu=True)
    elif action == "paket":
        await deps._do_paket(update, [], from_menu=True)
    elif action == "validasi":
        await deps._do_validasi(update, from_menu=True)
    elif action == "jejak":
        await deps._do_jejak(update, from_menu=True)
    elif action == "auto_on":
        await asyncio.to_thread(store.set_auto, update.effective_chat.id, True)
        await update.effective_message.reply_text(
            "🌅 <b>Auto-scan pagi: AKTIF</b>\n"
            "Setiap hari bursa sekitar 08:30 WIB bot akan mengirim ringkasan "
            "saham watchlist/unggulanmu. Matikan kapan saja: <code>/auto off</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=deps.home_markup(),
        )
    else:
        await update.effective_message.reply_text(
            "Aksi tidak dikenal. Buka /start untuk menu.",
            reply_markup=deps.home_markup(),
        )


async def _handle_run(update: Update, msg, ctx, action: str, deps):
    if action == "watch":
        await deps._safe_edit(
            msg,
            "👀 <b>Watchlist</b> — pilih aksi:",
            parse_mode=ParseMode.HTML,
            reply_markup=deps.watch_menu_markup(),
        )
    elif action == "alert":
        await deps._safe_edit(
            msg,
            "🔔 <b>Alert</b> — pilih aksi:",
            parse_mode=ParseMode.HTML,
            reply_markup=deps.alert_menu_markup(),
        )
    elif action == "help":
        await deps._safe_edit(
            msg,
            help_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=help_markup(),
        )
    elif action == "belajar":
        await deps._safe_edit(
            msg,
            "🎓 <b>Kamus Istilah Saham Pemula</b>\n\n"
            "Pilih istilah di bawah untuk tahu arti, cara baca, dan jebakan "
            "umumnya (atau ketik <code>/belajar topik</code>):",
            parse_mode=ParseMode.HTML,
            reply_markup=belajar_markup(),
        )
    elif action == "saran":
        await deps._safe_edit(
            msg,
            "🔍 Memindai ide saham hari ini (±1-2 menit)...",
            reply_markup=deps.home_markup(),
        )
        await deps.run_menu_action(update, "saran", [])
    elif action == "bandar":
        await deps._safe_edit(
            msg, "⏳ Mengambil data BEI...", reply_markup=deps.home_markup(),
        )
        await deps.run_menu_action(update, "bandar", [])
    elif action == "sektor":
        await deps._safe_edit(
            msg, "⏳ Mengambil kekuatan sektor BEI...",
            reply_markup=deps.home_markup(),
        )
        await deps.run_menu_action(update, "sektor", [])
    elif action == "scan":
        await deps._do_scan(update, [], ctx.job_queue, from_menu=True)
    else:
        await deps.run_menu_action(update, action, [])


async def menu_callback(update: Update, ctx, deps):
    q = update.callback_query
    if deps.ALLOWED_IDS and (not q.from_user or q.from_user.id not in deps.ALLOWED_IDS):
        await q.answer("⛔ Bot ini privat.", show_alert=True)
        return
    await q.answer()
    cb_data = q.data or ""
    msg = q.message
    if not msg:
        return
    chat_id = update.effective_chat.id

    if cb_data == "menu":
        deps._pending_clear(chat_id)
        await deps._safe_edit(
            msg, deps._menu_text(), parse_mode=ParseMode.HTML,
            reply_markup=deps.main_menu_markup(),
        )
        return

    if cb_data.startswith("sub:"):
        deps._pending_clear(chat_id)
        group = cb_data.split(":", 1)[1]
        modal = await asyncio.to_thread(store.get_modal, chat_id)
        sub = _submenu_content(group, modal)
        if not sub:
            await deps._safe_edit(
                msg, deps._menu_text(), parse_mode=ParseMode.HTML,
                reply_markup=deps.main_menu_markup(),
            )
            return
        text, markup = sub
        await deps._safe_edit(msg, text, parse_mode=ParseMode.HTML, reply_markup=markup)
        return

    if cb_data.startswith("setmodal:"):
        deps._pending_clear(chat_id)
        raw = cb_data.split(":", 1)[1]
        try:
            val = int(raw)
        except ValueError:
            await deps._safe_edit(
                msg, "❌ Nominal tidak valid.", reply_markup=deps.home_markup(),
            )
            return
        if not (100_000 <= val <= 10_000_000_000):
            await deps._safe_edit(
                msg, "❌ Modal harus antara Rp100 ribu dan Rp10 miliar.",
                reply_markup=deps.home_markup(),
            )
            return
        cur = await asyncio.to_thread(store.get_modal, chat_id)
        await asyncio.to_thread(store.set_modal, chat_id, val)
        await deps._safe_edit(
            msg,
            f"💵 Modal diperbarui: {saranmod.fmt(cur)} → <b>{saranmod.fmt(val)}</b>\n"
            f"<i>Risiko maks normal (1%): {saranmod.fmt(val * 0.01)} per posisi.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎯 Hitung 1 Saham Sekarang", callback_data="pick:rencana")],
                [InlineKeyboardButton("⬅️ Kembali", callback_data="sub:rencana"),
                 InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
            ]),
        )
        return

    if cb_data.startswith("belajar:"):
        deps._pending_clear(chat_id)
        topic = cb_data.split(":", 1)[1]
        txt = learn.learn(topic)
        if txt is None:
            await deps._safe_edit(
                msg, "❓ Topik tidak ditemukan.", reply_markup=belajar_markup(),
            )
            return
        await deps._safe_edit(
            msg, txt, parse_mode=ParseMode.HTML,
            disable_web_page_preview=True, reply_markup=belajar_detail_markup(),
        )
        return

    if cb_data.startswith("pick:"):
        action = cb_data.split(":", 1)[1]
        label = NEED_TICKER.get(action, action)
        hint = _PICK_HINTS.get(action)
        head = f"<b>{esc(label)}</b>"
        if hint:
            head += f"\n<i>{esc(hint)}</i>"
        head += "\n\nPilih saham di bawah:"
        deps._pending_clear(chat_id)
        await deps._safe_edit(
            msg,
            head,
            parse_mode=ParseMode.HTML,
            reply_markup=deps.ticker_pick_markup(
                action, await asyncio.to_thread(store.get_watchlist, chat_id),
            ),
        )
        return

    if cb_data.startswith("manual:"):
        action = cb_data.split(":", 1)[1]
        deps._pending_set(chat_id, action)
        await deps._safe_edit(
            msg,
            _MANUAL_PROMPT.get(action, "Ketik kode emiten:"),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Batal", callback_data="menu")]]
            ),
        )
        return

    if cb_data.startswith("tick:") or cb_data.startswith("refresh:"):
        kind, rest = cb_data.split(":", 1)
        parts = rest.split(":", 1)
        if len(parts) != 2 or not parts[1]:
            await deps._safe_edit(
                msg, "❌ Tombol tidak valid — buka ulang menu.",
                reply_markup=deps.home_markup(),
            )
            return
        action, ticker = parts
        deps._pending_clear(chat_id)
        verb = "Memperbarui" if kind == "refresh" else "Memproses"
        await deps._safe_edit(
            msg,
            f"⏳ {verb} <b>{esc(ticker)}</b>...",
            parse_mode=ParseMode.HTML,
            reply_markup=deps.home_markup(),
        )
        await deps.run_menu_action(update, action, [ticker])
        return

    if cb_data.startswith("sg:"):
        deps._pending_clear(chat_id)
        await deps.sinyal_handler.nav(update, cb_data, deps, ctx.job_queue)
        return

    if cb_data.startswith("run:"):
        action = cb_data.split(":", 1)[1]
        await _handle_run(update, msg, ctx, action, deps)


async def menu_text_input(update: Update, ctx, deps):
    """Teks biasa: proses PENDING input emiten/modal, atau pertanyaan bebas."""
    chat_id = update.effective_chat.id
    action = deps._pending_get(chat_id)
    if not action:
        if deps.ALLOWED_IDS and update.effective_user.id not in deps.ALLOWED_IDS:
            return
        parsed = deps.parse_tanya(update.effective_message.text or "")
        if parsed:
            focus, ticker = parsed
            if ticker:
                await deps._do_tanya(update, ticker, focus)
            else:
                pick_action = "beli" if focus != "jual" else "jual"
                label = "🤔 Tanya: Layak Beli?" if pick_action == "beli" else "🤔 Tanya: Layak Jual?"
                wl = await asyncio.to_thread(store.get_watchlist, chat_id)
                await update.effective_message.reply_text(
                    f"{label} — pilih saham:",
                    reply_markup=deps.ticker_pick_markup(pick_action, wl),
                )
        return

    if deps.ALLOWED_IDS and update.effective_user.id not in deps.ALLOWED_IDS:
        deps._pending_clear(chat_id)
        return

    raw_text = (update.effective_message.text or "").strip()
    if action == "modal":
        val = deps._parse_rupiah(raw_text)
        if val is None or not (100_000 <= val <= 10_000_000_000):
            await update.effective_message.reply_text(
                "❌ Nominal tidak valid (wajar Rp100 ribu – Rp10 miliar).\n"
                "Contoh: <code>5000000</code>, <code>5jt</code>, atau <code>2,5jt</code>.",
                parse_mode=ParseMode.HTML,
            )
            return
        deps._pending_clear(chat_id)
        cur = await asyncio.to_thread(store.get_modal, chat_id)
        iv = int(round(val))
        await asyncio.to_thread(store.set_modal, chat_id, iv)
        await update.effective_message.reply_text(
            f"💵 Modal diperbarui: {saranmod.fmt(cur)} → <b>{saranmod.fmt(iv)}</b>\n"
            f"<i>Risiko maks normal (1%): {saranmod.fmt(iv * 0.01)} per posisi.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎯 Hitung 1 Saham Sekarang", callback_data="pick:rencana")],
                [InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
            ]),
        )
        return

    raw_tokens = [t for t in raw_text.upper().replace(",", " ").split() if t]
    tickers = []
    for tok in raw_tokens:
        clean = tok[:-3] if tok.endswith(".JK") else tok
        if clean.isalnum() and 2 <= len(clean) <= 6:
            tickers.append(clean)
        else:
            tickers = []
            break
    if not tickers:
        await update.effective_message.reply_text(
            "❌ Kode emiten tidak valid. Contoh: <code>BBCA</code> (atau <code>BBCA TLKM</code>).",
            parse_mode=ParseMode.HTML,
        )
        return
    deps._pending_clear(chat_id)
    await deps.run_menu_action(update, action, tickers[:5])
