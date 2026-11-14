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
    "rencana": "Ketik kode saham untuk hitung lot &amp; rencana beli (contoh: <code>BBCA</code>, atau dengan anggaran: <code>BBCA 5jt</code>):",
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
        "Ketik kode emiten untuk alarm (contoh: <code>BBCA</code> untuk semua "
        "alarm standar, atau tambah target harga / jenis mis. "
        "<code>BBCA 9500</code>, <code>BBCA &lt;9000</code>, "
        "<code>BBCA oversold</code>).\n"
        "<i>Jenis default: " + ", ".join(sorted(alerts.ALL_KINDS)) + "</i>:"
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
        "👋 Halo! Aku <b>SahamBuddy</b> — asisten pemula untuk mengecek "
        "nilai saham, mencari kandidat beli, dan mengecek saham yang "
        "perlu dijual di BEI.\n\n"
        "Belum tahu mulai dari mana? Tekan <b>Pandu Aku Langkah demi Langkah</b>, "
        "atau langsung pilih tujuanmu di bawah (bisa juga ketik langsung, "
        "mis. <code>BBCA layak beli?</code>):\n\n"
        "• <b>Saham Cocok Dibeli</b>: kandidat dari data pasar (berdasar skor, bukan jaminan untung)\n"
        "• <b>Cek Nilai Saham</b>: mahal/murah, kas aman, tren &amp; status layak beli\n"
        "• <b>Cek Saham Jual</b>: pantau posisi atau saham yang melemah\n\n"
        "⚠️ <i>Alat bantu analisis, bukan ajakan beli/jual &amp; tidak terhubung "
        "ke akun sekuritasmu. Selalu DYOR!</i>"
    )


def main_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧭 Pandu Aku Langkah demi Langkah", callback_data="guide:start")],
        [InlineKeyboardButton("🟢 Saham Cocok Dibeli", callback_data="guide:beli"),
         InlineKeyboardButton("🔴 Cek Saham Jual", callback_data="guide:jual")],
        [InlineKeyboardButton("🔍 Cek Nilai Saham", callback_data="guide:cek"),
         InlineKeyboardButton("💼 Portofolio Saya", callback_data="run:portofolio")],
        [InlineKeyboardButton("🎓 Belajar Pemula", callback_data="run:belajar"),
         InlineKeyboardButton("⚙️ Menu Lengkap", callback_data="sub:more")],
    ])


def home_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")]])


def result_markup(
    action: str, ticker: str, *, allow_buy: bool = True
) -> InlineKeyboardMarkup:
    """Tombol langkah lanjutan kontekstual di bawah hasil analisis/cek."""
    rows: list[list[InlineKeyboardButton]] = []
    if action == "cek":
        if allow_buy:
            rows.append([
                InlineKeyboardButton("💰 Rincian Nilai", callback_data=f"tick:fundamental:{ticker}"),
                InlineKeyboardButton("🎯 Hitung Lot", callback_data=f"tick:rencana:{ticker}"),
            ])
        else:
            rows.append([
                InlineKeyboardButton("💰 Rincian Nilai", callback_data=f"tick:fundamental:{ticker}"),
                InlineKeyboardButton("📈 Grafik & Analisis", callback_data=f"tick:analisis:{ticker}"),
            ])
        rows.append(_action_trade_row(ticker, allow_buy=allow_buy))
    elif action == "fundamental":
        rows.append([
            InlineKeyboardButton("🛡️ Cek Layak Beli", callback_data=f"tick:cek:{ticker}"),
            InlineKeyboardButton("📈 Grafik & Analisis", callback_data=f"tick:analisis:{ticker}"),
        ])
        if allow_buy:
            rows.append([
                InlineKeyboardButton("🎯 Hitung Lot", callback_data=f"tick:rencana:{ticker}"),
                InlineKeyboardButton("📝 Sudah beli di Stockbit", callback_data=f"exec:buy:{ticker}"),
            ])
    elif action == "analisis":
        rows.append([
            InlineKeyboardButton("💰 Rincian Nilai", callback_data=f"tick:fundamental:{ticker}"),
            InlineKeyboardButton("🎯 Hitung Lot", callback_data=f"tick:rencana:{ticker}"),
        ])
        rows.append(_action_trade_row(ticker, allow_buy=allow_buy))
    elif action in ("tanya_beli", "beli", "tanya_umum"):
        rows.append([
            InlineKeyboardButton("🛡️ Cek 6 Poin", callback_data=f"tick:cek:{ticker}"),
            InlineKeyboardButton("🎯 Hitung Lot", callback_data=f"tick:rencana:{ticker}"),
        ])
        rows.append(_action_trade_row(ticker, allow_buy=allow_buy))
    elif action == "rencana":
        rows.append(_action_trade_row(ticker, allow_buy=allow_buy))
    elif action in ("jual", "tanya_jual"):
        rows.append([
            InlineKeyboardButton("📝 Sudah jual di Stockbit", callback_data=f"exec:sell:{ticker}"),
            InlineKeyboardButton("💼 Portofolio", callback_data="run:portofolio"),
        ])
        rows.append([
            InlineKeyboardButton("📈 Grafik & Analisis", callback_data=f"tick:analisis:{ticker}"),
            InlineKeyboardButton("💰 Rincian Nilai", callback_data=f"tick:fundamental:{ticker}"),
        ])
    rows.append([
        InlineKeyboardButton("🔄 Perbarui", callback_data=f"refresh:{action}:{ticker}"),
        InlineKeyboardButton("🏠 Menu Utama", callback_data="menu"),
    ])
    return InlineKeyboardMarkup(rows)


def _action_trade_row(
    ticker: str, *, allow_buy: bool
) -> list[InlineKeyboardButton]:
    """Baris cepat catat beli Stockbit (jika layak) & cek layak jual."""
    btns: list[InlineKeyboardButton] = []
    if allow_buy:
        btns.append(
            InlineKeyboardButton("📝 Sudah beli di Stockbit", callback_data=f"exec:buy:{ticker}")
        )
    btns.append(
        InlineKeyboardButton("🤔 Cek layak jual?", callback_data=f"tick:jual:{ticker}")
    )
    return btns


def ticker_pick_markup(
    action: str,
    watchlist: list[str] | None = None,
    *,
    show_guide_back: bool = False,
) -> InlineKeyboardMarkup:
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
    if show_guide_back:
        rows.append([
            InlineKeyboardButton("⬅️ Panduan", callback_data="guide:start"),
            InlineKeyboardButton("🏠 Menu Utama", callback_data="menu"),
        ])
    else:
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
        "Angka ini dipakai untuk <b>sizing posisi asli</b> (<code>/rencana</code>, "
        "<code>/paket</code>, <code>/portofolio</code>) sekaligus <b>simulasi "
        "paper trading</b> (<code>/saran</code>) dengan batas risiko aman "
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
                [InlineKeyboardButton("📒 Jurnal Eksekusi", callback_data="run:jurnal"),
                 InlineKeyboardButton("📋 Semua Perintah (/)", callback_data="sub:allcmd")],
                [InlineKeyboardButton("⬅️ Menu Utama", callback_data="menu")],
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
            "• <code>/saran</code> · <code>/saran lq45</code> · <code>/saran list</code> — kandidat harian &amp; posisi latihan\n"
            "• <code>/paket</code> — paket multi-saham siap Stockbit\n"
            "• <code>/sinyal</code> · <code>/sinyal beli bagus</code> — sinyal seluruh IDX\n"
            "• <code>/sektor</code> — rotasi kekuatan sektor BEI\n"
            "• <code>/modal 5000000</code> — atur modal sizing posisi asli &amp; paper\n"
            "• <code>/portofolio</code> — posisi nyata (<code>/portofolio beli BBCA 5 9000</code>, "
            "<code>/portofolio jual BBCA 5 9200</code>, <code>/portofolio batas 20 35</code>)\n"
            "• <code>/jurnal</code> — slippage Stockbit &amp; diagnostik realisasi "
            "(<code>/jurnal beli</code>, <code>/jurnal jual</code>, <code>/jurnal riwayat</code>)\n\n"
            "<b>Pantau, Riset &amp; Lainnya:</b>\n"
            "• <code>/watch</code> · <code>/alert BBCA 9500</code> · <code>/auto on</code> · <code>/auto off</code> — pantau otomatis\n"
            "• <code>/skor</code> · <code>/berita</code> · <code>/fundamental</code> · "
            "<code>/banding BBCA BBRI</code> · <code>/agenda BBCA</code>\n"
            "• <code>/scan</code> · <code>/backtest BBCA</code> · <code>/bandar</code> · "
            "<code>/validasi</code> · <code>/jejak</code> · <code>/id</code> · <code>/status</code>",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("📖 Panduan Pemula", callback_data="run:help"),
                 InlineKeyboardButton("⬅️ Menu Utama", callback_data="menu")],
            ]),
        )
    if group == "more":
        return (
            "⚙️ <b>Menu Lengkap SahamBuddy</b>\n\n"
            "Semua alat analisis, simulasi, dan fitur lanjutan ada di sini:",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🛡️ Cek Layak Beli", callback_data="pick:cek"),
                 InlineKeyboardButton("💰 Rincian Nilai", callback_data="pick:fundamental")],
                [InlineKeyboardButton("📊 Analisis Lengkap", callback_data="pick:analisis"),
                 InlineKeyboardButton("🎯 Hitung Modal & Lot", callback_data="sub:rencana")],
                [InlineKeyboardButton("💡 Ide & Sinyal", callback_data="sub:ide"),
                 InlineKeyboardButton("👀 Watchlist & Alarm", callback_data="sub:pantau")],
                [InlineKeyboardButton("📖 Panduan & Contoh", callback_data="run:help"),
                 InlineKeyboardButton("🧪 Fitur Lanjutan & Riset", callback_data="sub:pro")],
                [InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
            ]),
        )
    return None


def _guide_start_view() -> tuple[str, InlineKeyboardMarkup]:
    """Langkah awal panduan interaktif untuk pemula."""
    text = (
        "🧭 <b>Panduan Pemula — Mulai dari Mana?</b>\n\n"
        "Pilih situasi kamu sekarang, aku arahkan langkah demi langkah:\n\n"
        "1️⃣ <b>Cari saham cocok dibeli</b> — belum punya incaran, mau lihat kandidat dari data pasar\n"
        "2️⃣ <b>Cek nilai 1 saham</b> — sudah punya nama saham, mau cek mahal/murah &amp; layak beli\n"
        "3️⃣ <b>Cek saham untuk dijual</b> — sudah pegang saham, bingung tahan atau jual\n"
        "4️⃣ <b>Panduan 3 langkah</b> — baru pertama kali pakai bot ini"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("1️⃣ Cari Saham Cocok Dibeli", callback_data="guide:beli")],
        [InlineKeyboardButton("2️⃣ Cek Nilai 1 Saham", callback_data="guide:cek")],
        [InlineKeyboardButton("3️⃣ Cek Saham Cocok Dijual", callback_data="guide:jual")],
        [InlineKeyboardButton("4️⃣ Panduan 3 Langkah Pemula", callback_data="guide:pemula")],
        [InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
    ])
    return text, markup


def _guide_beli_view(
    rows: list[dict], meta: dict, modal: int
) -> tuple[str, InlineKeyboardMarkup]:
    """Alur terpandu mencari kandidat beli tanpa memanggil jaringan."""
    if not rows:
        text = (
            "🟢 <b>Cari Saham Cocok Dibeli</b>\n\n"
            "Saat ini <b>belum ada hasil scan seluruh pasar</b> tersimpan di server.\n\n"
            "Kamu tetap bisa mencari kandidat tanpa menunggu lama:\n"
            "• <b>Ide Harian (Cepat)</b>: langsung cari dari watchlist/LQ45 + hitung lot\n"
            "• <b>Scan 944 Saham</b>: pindai semua saham BEI (±3–6 menit di latar belakang)\n"
            "• <b>Cek Saham Populer</b>: langsung cek BBCA, BBRI, TLKM, dll.\n\n"
            f"Modal tersimpan: <b>{saranmod.fmt(modal)}</b>."
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("💡 Ide Harian (Cepat)", callback_data="run:saran"),
             InlineKeyboardButton("📡 Scan 944 Saham", callback_data="sg:scan")],
            [InlineKeyboardButton("🔍 Cek Saham Populer", callback_data="guide:cek"),
             InlineKeyboardButton("💵 Atur Modal", callback_data="sub:modal")],
            [InlineKeyboardButton("⬅️ Kembali ke Panduan", callback_data="guide:start"),
             InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
        ])
        return text, markup

    buy_rows = [r for r in rows if r.get("signal") == "BELI"]
    top_buy = sorted(
        buy_rows, key=lambda r: float(r.get("rel_pct") or 0), reverse=True
    )[:3]
    top_tickers = [str(r.get("ticker", "")).upper() for r in top_buy if r.get("ticker")]
    scanned = str(meta.get("scanned_at") or "").strip()[:16]
    scan_note = f" (scan: <code>{esc(scanned)}</code>)" if scanned else ""
    text = (
        "🟢 <b>Cari Saham Cocok Dibeli</b>\n\n"
        f"Data scan terakhir memuat <b>{len(rows)}</b> emiten dengan "
        f"<b>{len(buy_rows)}</b> sinyal BELI{scan_note}.\n\n"
        "⚠️ <i>Sinyal BELI = peringkat skor relatif riset, bukan jaminan untung. "
        "Selalu cek 6 poin keamanannya dulu sebelum beli.</i>\n\n"
        "• <b>Pilihan BELI Berkualitas</b>: paling cocok untuk pemula (disaring laba &amp; arus kas)\n"
        "• <b>Cek cepat kandidat teratas</b>: ketuk tombol saham di bawah untuk langsung cek kelayakan\n"
        f"• Modal tersimpan saat ini: <b>{saranmod.fmt(modal)}</b>"
    )
    kb: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("✨ Pilihan BELI Berkualitas", callback_data="run:sinyal_bagus"),
         InlineKeyboardButton("💡 Ide Harian + Hitung Lot", callback_data="run:saran")],
    ]
    if top_tickers:
        kb.append([
            InlineKeyboardButton(f"🔍 Cek {t}", callback_data=f"tick:cek:{t}")
            for t in top_tickers
        ])
    kb.extend([
        [InlineKeyboardButton("📦 Paket Multi-Saham", callback_data="run:paket"),
         InlineKeyboardButton("📡 Ringkasan Scan", callback_data="run:sinyal")],
        [InlineKeyboardButton("💵 Atur Modal", callback_data="sub:modal")],
        [InlineKeyboardButton("⬅️ Kembali ke Panduan", callback_data="guide:start"),
         InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
    ])
    return text, InlineKeyboardMarkup(kb)


def _guide_jual_view(
    positions: list[dict], wl: list[str], rows: list[dict]
) -> tuple[str, InlineKeyboardMarkup]:
    """Alur terpandu meninjau saham yang layak ditahan atau dijual."""
    held = [str(p["ticker"]).upper() for p in positions if p.get("ticker")][:4]
    has_sell = any(r.get("signal") == "JUAL" for r in rows)
    extra = [t for t in dict.fromkeys([*wl, *POPULAR_TICKERS]) if t not in held][:4]

    if held:
        held_note = (
            "Kamu punya posisi aktif tersimpan: <b>"
            + ", ".join(esc(t) for t in held)
            + "</b>.\nKetuk salah satu di bawah untuk cek apakah sebaiknya "
            "ditahan, ambil untung sebagian, atau cut loss:"
        )
    else:
        held_note = (
            "Belum ada posisi aktif tersimpan di portofoliomu. Pilih saham di "
            "bawah atau ketik kode saham yang kamu pegang di Stockbit "
            "(mis. <code>TLKM jual gak?</code>):"
        )

    text = (
        "🔴 <b>Cek Saham untuk Dijual</b>\n\n"
        f"{held_note}\n\n"
        "ℹ️ <i>Catatan: sinyal JUAL hanya panduan untuk pemegang saham "
        "(ambil untung / batasi rugi), bukan instruksi short selling.</i>"
    )
    kb: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(held), 2):
        kb.append([
            InlineKeyboardButton(f"💼 Cek Jual {t}", callback_data=f"tick:jual:{t}")
            for t in held[i:i + 2]
        ])
    for i in range(0, len(extra), 2):
        kb.append([
            InlineKeyboardButton(f"🔍 Cek {t}", callback_data=f"tick:jual:{t}")
            for t in extra[i:i + 2]
        ])
    kb.append([
        InlineKeyboardButton("✏️ Ketik kode lain", callback_data="manual:jual"),
        InlineKeyboardButton("💼 Portofolio Saya", callback_data="run:portofolio"),
    ])
    if has_sell:
        kb.append([
            InlineKeyboardButton("🔴 Daftar Sinyal JUAL Pasar", callback_data="sg:sell")
        ])
    kb.append([
        InlineKeyboardButton("⬅️ Kembali ke Panduan", callback_data="guide:start"),
        InlineKeyboardButton("🏠 Menu Utama", callback_data="menu"),
    ])
    return text, InlineKeyboardMarkup(kb)


def _guide_pemula_view() -> tuple[str, InlineKeyboardMarkup]:
    """Ringkasan 3 langkah praktis menggunakan bot untuk pemula."""
    text = (
        "🎓 <b>Panduan 3 Langkah untuk Pemula</b>\n\n"
        "<b>Langkah 1 — Cari kandidat dulu</b>\n"
        "Ketuk <b>🟢 Saham Cocok Dibeli</b> untuk melihat saham berdasar skor riset "
        "(bukan tebakan, dan tidak ada jaminan untung).\n\n"
        "<b>Langkah 2 — Cek nilai &amp; keamanannya</b>\n"
        "Buka <b>🔍 Cek Nilai Saham</b> lalu pilih/ketik kode saham (mis. BBCA). "
        "Bot cek valuasi mahal/murah, kas, tren &amp; memberi status "
        "<b>LAYAK / TUNGGU / HINDARI</b>.\n\n"
        "<b>Langkah 3 — Hitung lot &amp; pantau jual</b>\n"
        "Jika layak, tekan <b>🎯 Hitung Lot</b> sesuai modalmu. Setelah transaksi manual "
        "di Stockbit, catat di <b>💼 Portofolio Saya</b> lalu gunakan "
        "<b>🔴 Cek Saham Jual</b> saat ragu.\n\n"
        "💡 <i>Bisa juga chat langsung, mis. <code>BBCA beli gak?</code> atau "
        "<code>BBRI fundamentalnya bagus?</code></i>"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Cari Saham Cocok Dibeli", callback_data="guide:beli"),
         InlineKeyboardButton("🛡️ Coba Cek BBCA", callback_data="tick:cek:BBCA")],
        [InlineKeyboardButton("🎓 Kamus Istilah Pemula", callback_data="run:belajar"),
         InlineKeyboardButton("⬅️ Panduan", callback_data="guide:start")],
        [InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
    ])
    return text, markup


def _load_guide_beli_data(chat_id: int) -> tuple[list[dict], dict, int]:
    """Muat snapshot scan & modal dalam satu panggilan store sekuensial."""
    return (
        store.load_market_scan(),
        store.market_scan_meta(),
        store.get_modal(chat_id),
    )


def _load_guide_jual_data(chat_id: int) -> tuple[list[dict], list[str], list[dict]]:
    """Muat posisi, watchlist & snapshot scan dalam satu panggilan store."""
    return (
        store.real_positions(chat_id),
        store.get_watchlist(chat_id),
        store.load_market_scan(),
    )


async def _guide_content(
    step: str, chat_id: int
) -> tuple[str, InlineKeyboardMarkup] | None:
    """Bangun konten & tombol panduan interaktif langkah demi langkah."""
    if step == "start":
        return _guide_start_view()
    if step == "beli":
        rows, meta, modal = await asyncio.to_thread(_load_guide_beli_data, chat_id)
        return _guide_beli_view(rows, meta, modal)
    if step == "cek":
        wl = await asyncio.to_thread(store.get_watchlist, chat_id)
        text = (
            "🔍 <b>Cek Nilai &amp; Keamanan Saham</b>\n\n"
            "Bot mengecek kondisi saham secara menyeluruh:\n"
            "• <b>Nilai &amp; Fundamental</b>: mahal/murah (PER, PBV), untung (ROE), dividen &amp; kas\n"
            "• <b>Tren &amp; Asing</b>: harga menguat/melemah serta aliran dana asing\n"
            "• <b>Berita &amp; Likuiditas</b>: sentimen berita dan kemudahan transaksi\n\n"
            "Hasil akhirnya jujur: <b>LAYAK / TUNGGU / HINDARI</b>, plus rincian nilai "
            "&amp; tombol hitung lot kalau layak.\n\n"
            "<i>Pilih saham di bawah, atau ketik kode saham lain:</i>"
        )
        return text, ticker_pick_markup("cek", wl, show_guide_back=True)
    if step == "jual":
        positions, wl, rows = await asyncio.to_thread(_load_guide_jual_data, chat_id)
        return _guide_jual_view(positions, wl, rows)
    if step == "pemula":
        return _guide_pemula_view()
    return None


def _is_media_message(msg) -> bool:
    """True bila pesan adalah media (foto/dokumen/video) tanpa body teks."""
    return any(
        bool(getattr(msg, attr, None))
        for attr in ("photo", "document", "video", "animation")
    )


async def safe_edit(msg, text: str, *, send_fn=None, **kw):
    """Edit pesan menu; jatuh ke reply_text bila pesan berupa media atau tak bisa diedit."""
    async def _run(call):
        return await send_fn(call) if send_fn is not None else await call()

    if _is_media_message(msg):
        await _run(lambda: msg.reply_text(text, **kw))
        return
    try:
        await _run(lambda: msg.edit_text(text, **kw))
    except BadRequest as e:
        low = str(e).lower()
        if "message is not modified" in low:
            return
        if "no text in the message" in low or "message can't be edited" in low:
            await _run(lambda: msg.reply_text(text, **kw))
            return
        raise


async def run_menu_action(update: Update, action: str, tickers: list[str], deps):
    """Jalankan satu aksi menu. Dipakai tombol maupun input teks."""
    if action == "analisis":
        await deps._do_analisis(update, tickers)
    elif action == "cek":
        await deps._do_cek(update, [tickers[0]], from_menu=True)
    elif action == "rencana":
        await deps._do_rencana(update, tickers[:2], from_menu=True)
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
        kinds = {k.lower() for k in tickers[1:]} or None
        await deps._do_alert_add(update, tickers[0], kinds=kinds, from_menu=True)
    elif action == "bandar":
        await deps._do_bandar(update, from_menu=True)
    elif action == "portofolio":
        await deps._do_portofolio(update, [], from_menu=True)
    elif action == "jurnal":
        await deps._do_jurnal(update, [], from_menu=True)
    elif action == "jurnal_history":
        await deps._do_jurnal(update, ["riwayat"], from_menu=True)
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

    if cb_data.startswith("guide:"):
        deps._pending_clear(chat_id)
        step = cb_data.split(":", 1)[1]
        guide = await _guide_content(step, chat_id)
        if not guide:
            await deps._safe_edit(
                msg, deps._menu_text(), parse_mode=ParseMode.HTML,
                reply_markup=deps.main_menu_markup(),
            )
            return
        text, markup = guide
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

    if cb_data.startswith("exec:"):
        await deps.jurnal_handler.handle_exec_callback(update, cb_data, deps)
        return

    if cb_data.startswith("run:"):
        deps._pending_clear(chat_id)
        action = cb_data.split(":", 1)[1]
        await _handle_run(update, msg, ctx, action, deps)
        return

    await deps._safe_edit(
        msg, "❌ Tombol menu tidak dikenal — kembali ke Menu Utama.",
        reply_markup=deps.home_markup(),
    )


def _clean_ticker_tok(tok: str) -> str | None:
    """Normalkan 1 token kode emiten (`BBCA.JK` -> `BBCA`) atau None bila tidak valid."""
    clean = tok[:-3] if tok.endswith(".JK") else tok
    return clean if (clean.isalnum() and 2 <= len(clean) <= 6) else None


def _parse_menu_tokens(action: str, raw_text: str) -> list[str]:
    """Urai input teks bebas setelah ketuk `manual:*` menjadi argumen aksi."""
    up = raw_text.upper()
    if action in ("alert_add", "rencana"):
        # Pertahankan koma internal pada nominal/target (mis. `9,500` atau `5,5jt`).
        parts = [t.strip(",") for t in up.split() if t.strip(",")]
        if len(parts) == 1 and "," in parts[0]:
            parts = [p.strip() for p in parts[0].split(",") if p.strip()]
        if not parts:
            return []
        head = _clean_ticker_tok(parts[0])
        if not head:
            return []
        if action == "rencana" and len(parts) > 2:
            return []
        return [head, *parts[1:12]]
    out: list[str] = []
    for tok in up.replace(",", " ").split():
        clean = _clean_ticker_tok(tok)
        if not clean:
            return []
        out.append(clean)
    return out[:5]


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
    if await deps.jurnal_handler.handle_pending_exec(
        update, chat_id, action, raw_text, deps
    ):
        return
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

    tickers = _parse_menu_tokens(action, raw_text)
    if not tickers:
        await update.effective_message.reply_text(
            "❌ Kode emiten tidak valid. Contoh: <code>BBCA</code> (atau <code>BBCA TLKM</code>).",
            parse_mode=ParseMode.HTML,
        )
        return
    deps._pending_clear(chat_id)
    await deps.run_menu_action(update, action, tickers)
