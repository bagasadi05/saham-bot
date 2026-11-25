"""Menu interaktif & alur ramah pemula (progressive disclosure).

Seluruh markup tombol, teks panduan pemula, submenu terkelompok, kamus
istilah klik-langsung (/belajar), serta router callback dan input teks
dipusatkan di sini. Semua panggilan ke handler & state bot (`ALLOWED_IDS`,
`run_menu_action`, `_do_*`, `_pending_*`, markup) lewat `deps` agar tetap
kompatibel dengan monkeypatch tes pada `bot`.
"""
from __future__ import annotations

from core import syariah

import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest

import config
from core import alerts, learn, saran as saranmod, store, textutil
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
    "onboard_modal": (
        "Ketik nominal modal dalam Rupiah untuk Tahap 2 "
        "(contoh: <code>500000</code>, <code>1jt</code>, atau <code>5jt</code>):"
    ),
    "onboard_ticker": (
        "Ketik kode saham untuk Tahap 4 (contoh: <code>BBCA</code>):"
    ),
}

_MODAL_PRESETS = (500_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000, 25_000_000)

_BELAJAR_BUTTONS = (
    ("PER (Mahal/Murah)", "per", "PBV (Nilai Buku)", "pbv"),
    ("ROE (Profitabilitas)", "roe", "Dividen Yield", "dividend"),
    ("RSI (Jenuh Beli/Jual)", "rsi", "MACD (Momentum)", "macd"),
    ("MA50 (Tren Menengah)", "ma50", "IHSG (Pasar Umum)", "ihsg"),
    ("ATR (Stop Loss)", "atr", "PowerScore Bot", "powerscore"),
)


def _safe_mode(mode: str | None) -> str:
    try:
        return store.normalize_user_mode(mode or store.DEFAULT_USER_MODE)
    except ValueError:
        return store.DEFAULT_USER_MODE


def _render_menu_text(deps, mode: str) -> str:
    try:
        return deps._menu_text(mode=mode)
    except TypeError:
        return deps._menu_text()


def _render_main_menu_markup(
    deps, mode: str, *, syariah_only: bool = False
) -> InlineKeyboardMarkup:
    try:
        return deps.main_menu_markup(mode=mode, syariah_only=syariah_only)
    except TypeError:
        try:
            return deps.main_menu_markup(mode=mode)
        except TypeError:
            return deps.main_menu_markup()


def menu_text(mode: str = "pemula") -> str:
    brand = esc(getattr(config, "BOT_DISPLAY_NAME", "SahamBuddy") or "SahamBuddy")
    if _safe_mode(mode) == "expert":
        return (
            f"⚡ <b>{brand}</b> · <i>Mode Expert</i>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Terminal cepat analisis teknikal, kualitas fundamental, "
            "sizing risiko, portofolio &amp; jurnal eksekusi BEI.\n\n"
            "⌘ <code>/cek</code> · <code>/analisis</code> · <code>/rencana</code> · "
            "<code>/sinyal</code> · <code>/saran</code> · <code>/jurnal</code>\n\n"
            "<blockquote>⚠️ Alat bantu analisis berbasis data, bukan ajakan beli/jual "
            "&amp; tidak terhubung ke akun sekuritas. Selalu DYOR.</blockquote>"
        )
    return (
        f"✨ <b>{brand}</b> · <i>Asisten Saham BEI</i>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Cek valuasi, cari kandidat beli, atau pantau saham yang kamu pegang "
        "— bisa juga ketik langsung, mis. <code>BBCA layak beli?</code>\n\n"
        "🟢 <b>Saham Cocok Dibeli</b> — kandidat teratas berbasis data\n"
        "🔍 <b>Cek Nilai Saham</b> — mahal/murah, kas aman &amp; tren\n"
        "🔴 <b>Cek Saham Jual</b> — evaluasi posisi &amp; batas risiko\n\n"
        "<blockquote>💡 Baru mulai? Tekan <b>Pandu Aku Langkah demi Langkah</b>.\n"
        "⚠️ Alat bantu analisis, bukan ajakan beli/jual &amp; tidak terhubung ke akun sekuritas. Selalu DYOR.</blockquote>"
    )


def main_menu_markup(mode: str = "pemula", syariah_only: bool = False) -> InlineKeyboardMarkup:
    sy_btn = InlineKeyboardButton(
        "☪️ Saham: Syariah" if syariah_only else "🌐 Saham: Semua",
        callback_data="syariah:menu",
    )
    if _safe_mode(mode) == "expert":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🛡️ Cek 6 Poin", callback_data="pick:cek"),
             InlineKeyboardButton("📊 Analisis", callback_data="pick:analisis")],
            [InlineKeyboardButton("📡 Sinyal IDX", callback_data="run:sinyal"),
             InlineKeyboardButton("💡 Ide Harian", callback_data="run:saran")],
            [InlineKeyboardButton("🎯 Hitung Lot", callback_data="sub:rencana"),
             InlineKeyboardButton("💼 Portofolio", callback_data="run:portofolio")],
            [InlineKeyboardButton("🌐 Pasar", callback_data="run:pasar"),
             InlineKeyboardButton("📒 Jurnal", callback_data="run:jurnal")],
            [sy_btn,
             InlineKeyboardButton("⚙️ Lainnya", callback_data="sub:more")],
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Cari Saham Beli", callback_data="guide:beli"),
         InlineKeyboardButton("🔍 Cek Saham", callback_data="guide:cek")],
        [InlineKeyboardButton("🔴 Cek Jual", callback_data="guide:jual"),
         InlineKeyboardButton("💼 Portofolio", callback_data="run:portofolio")],
        [sy_btn,
         InlineKeyboardButton("⚙️ Lainnya", callback_data="sub:more")],
    ])

def mode_text(cur_mode: str = "pemula") -> str:
    cur = _safe_mode(cur_mode)
    label = "🌱 Pemula" if cur == "pemula" else "⚡ Expert"
    return (
        "🎚️ <b>Level Tampilan Menu</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Aktif saat ini: <b>{label}</b>\n\n"
        "🌱 <b>Pemula</b> — menu ringkas (≤8 tombol), bahasa sederhana, "
        "+ panduan bertahap <b>Tahap 1→5</b>.\n"
        "⚡ <b>Expert</b> — tampilan padat &amp; grid akses cepat ke semua alat pro "
        "(<code>/sinyal</code>, <code>/paket</code>, <code>/saran</code>, <code>/jurnal</code>).\n\n"
        "<blockquote>Ganti kapan saja lewat tombol di bawah atau ketik "
        "<code>/mode pemula</code> / <code>/mode expert</code>.</blockquote>"
    )


def mode_menu_markup(cur_mode: str = "pemula") -> InlineKeyboardMarkup:
    cur = _safe_mode(cur_mode)
    lbl_pemula = "✅ Mode Pemula (Aktif)" if cur == "pemula" else "🌱 Mode Pemula"
    lbl_expert = "✅ Mode Expert (Aktif)" if cur == "expert" else "⚡ Mode Expert"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(lbl_pemula, callback_data="mode:set:pemula"),
         InlineKeyboardButton(lbl_expert, callback_data="mode:set:expert")],
        [InlineKeyboardButton("🧭 Panduan Bertahap (Tahap 1→5)", callback_data="onboard:s1")],
        [InlineKeyboardButton("☪️ Filter Saham Syariah vs Biasa", callback_data="syariah:menu")],
        [InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
    ])


def syariah_text(enabled: bool = False) -> str:
    meta = syariah.metadata()
    status = "☪️ Saham Syariah Saja (ISSI)" if enabled else "🌐 Semua Saham (Biasa + Syariah)"
    return (
        "☪️ <b>Filter Jenis Saham (Syariah vs Biasa)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Aktif saat ini: <b>{status}</b>\n\n"
        f"☪️ <b>Saham Syariah Saja</b> — menyaring <code>/sinyal</code>, <code>/saran</code>, "
        f"dan <code>/paket</code> hanya ke konstituen <b>ISSI / DES OJK</b> ({meta['count']} emiten, "
        f"acuan <code>{esc(meta['announcement'])}</code> efektif {esc(meta['effective_date'])}).\n"
        "🌐 <b>Semua Saham (Biasa)</b> — menampilkan seluruh emiten BEI termasuk perbankan konvensional.\n\n"
        "• Cek status satu emiten: <code>/syariah cek TLKM</code> atau <code>/syariah cek BBCA</code>\n"
        "• Ganti cepat: <code>/syariah on</code> atau <code>/syariah off</code>\n\n"
        "<blockquote>ℹ️ DES OJK direview tiap Mei &amp; November. Selalu pastikan status syariah "
        "terbaru di aplikasi sekuritas syariahmu sebelum transaksi.</blockquote>"
    )


def syariah_menu_markup(enabled: bool = False) -> InlineKeyboardMarkup:
    lbl_on = "✅ ☪️ Syariah Saja (Aktif)" if enabled else "☪️ Saham Syariah Saja"
    lbl_off = "✅ 🌐 Semua Saham (Aktif)" if not enabled else "🌐 Semua Saham (Biasa)"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(lbl_on, callback_data="syariah:set:on"),
         InlineKeyboardButton(lbl_off, callback_data="syariah:set:off")],
        [InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
    ])


def _mode_switched_view(mode: str, deps) -> tuple[str, InlineKeyboardMarkup]:
    norm = _safe_mode(mode)
    if norm == "pemula":
        text = (
            "🌱 <b>Mode Pemula diaktifkan!</b>\n\n"
            "Menu utama disederhanakan dan kamu bisa mengikuti alur terpandu "
            "langkah demi langkah (Tahap 1→5) dari pilih tujuan sampai hitung lot.\n\n"
            "Pilih langkah selanjutnya di bawah:"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Mulai Panduan Bertahap (Tahap 1→5)", callback_data="onboard:s1")],
            [InlineKeyboardButton("🏠 Buka Menu Pemula", callback_data="menu")],
        ])
        return text, markup
    text = "⚡ <b>Mode Expert diaktifkan!</b>\n\n" + _render_menu_text(deps, "expert")
    return text, _render_main_menu_markup(deps, "expert")


def home_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")]])


def _clean_cb_ticker(ticker: str) -> str:
    raw = (ticker or "").strip().upper()
    clean = _clean_ticker_tok(raw)
    if clean:
        return clean
    stripped = raw[:-3] if raw.endswith(".JK") else raw
    alnum = "".join(ch for ch in stripped if ch.isalnum())
    return alnum[:10] or "BBCA"


def result_markup(
    action: str, ticker: str, *, allow_buy: bool = True
) -> InlineKeyboardMarkup:
    """Tombol langkah lanjutan kontekstual di bawah hasil analisis/cek."""
    t = _clean_cb_ticker(ticker)
    rows: list[list[InlineKeyboardButton]] = []
    if action == "cek":
        rows.append([
            InlineKeyboardButton("💰 Rincian Nilai", callback_data=f"tick:fundamental:{t}"),
            InlineKeyboardButton("📈 Grafik & Analisis", callback_data=f"tick:analisis:{t}"),
        ])
        if allow_buy:
            rows.append([
                InlineKeyboardButton("🎯 Hitung Lot", callback_data=f"tick:rencana:{t}"),
                InlineKeyboardButton("📝 Sudah beli di Stockbit", callback_data=f"exec:buy:{t}"),
            ])
        rows.append([
            InlineKeyboardButton("🤔 Cek layak jual?", callback_data=f"tick:jual:{t}"),
        ])
    elif action == "fundamental":
        rows.append([
            InlineKeyboardButton("🛡️ Cek Layak Beli", callback_data=f"tick:cek:{t}"),
            InlineKeyboardButton("📈 Grafik & Analisis", callback_data=f"tick:analisis:{t}"),
        ])
        if allow_buy:
            rows.append([
                InlineKeyboardButton("🎯 Hitung Lot", callback_data=f"tick:rencana:{t}"),
                InlineKeyboardButton("📝 Sudah beli di Stockbit", callback_data=f"exec:buy:{t}"),
            ])
    elif action == "analisis":
        rows.append([
            InlineKeyboardButton("🛡️ Cek Layak Beli", callback_data=f"tick:cek:{t}"),
            InlineKeyboardButton("💰 Rincian Nilai", callback_data=f"tick:fundamental:{t}"),
        ])
        if allow_buy:
            rows.append([
                InlineKeyboardButton("🎯 Hitung Lot", callback_data=f"tick:rencana:{t}"),
                InlineKeyboardButton("📝 Sudah beli di Stockbit", callback_data=f"exec:buy:{t}"),
            ])
        rows.append([
            InlineKeyboardButton("🤔 Cek layak jual?", callback_data=f"tick:jual:{t}"),
        ])
    elif action in ("tanya_beli", "beli", "tanya_umum"):
        rows.append([
            InlineKeyboardButton("🛡️ Cek 6 Poin", callback_data=f"tick:cek:{t}"),
            InlineKeyboardButton("💰 Rincian Nilai", callback_data=f"tick:fundamental:{t}"),
        ])
        row2 = [InlineKeyboardButton("📈 Grafik & Analisis", callback_data=f"tick:analisis:{t}")]
        if allow_buy:
            row2.append(InlineKeyboardButton("🎯 Hitung Lot", callback_data=f"tick:rencana:{t}"))
        rows.append(row2)
        if action == "tanya_umum":
            trade_btns: list[InlineKeyboardButton] = []
            if allow_buy:
                trade_btns.append(
                    InlineKeyboardButton("📝 Sudah beli di Stockbit", callback_data=f"exec:buy:{t}")
                )
            trade_btns.append(
                InlineKeyboardButton("📝 Sudah jual di Stockbit", callback_data=f"exec:sell:{t}")
            )
            rows.append(trade_btns)
            rows.append([
                InlineKeyboardButton("🤔 Cek layak jual?", callback_data=f"tick:jual:{t}"),
            ])
        else:
            rows.append(_action_trade_row(t, allow_buy=allow_buy))
    elif action == "rencana":
        rows.append([
            InlineKeyboardButton("🛡️ Cek 6 Poin", callback_data=f"tick:cek:{t}"),
            InlineKeyboardButton("💰 Rincian Nilai", callback_data=f"tick:fundamental:{t}"),
        ])
        rows.append([
            InlineKeyboardButton("💵 Ubah Modal", callback_data="sub:modal"),
            InlineKeyboardButton("✏️ Hitung Budget Lain", callback_data="manual:rencana"),
        ])
        if allow_buy:
            rows.append([
                InlineKeyboardButton("📈 Grafik & Analisis", callback_data=f"tick:analisis:{t}"),
            ])
            rows.append(_action_trade_row(t, allow_buy=True))
        else:
            rows.append([
                InlineKeyboardButton("📈 Grafik & Analisis", callback_data=f"tick:analisis:{t}"),
                InlineKeyboardButton("🤔 Cek layak jual?", callback_data=f"tick:jual:{t}"),
            ])
    elif action in ("jual", "tanya_jual"):
        rows.append([
            InlineKeyboardButton("📝 Sudah jual di Stockbit", callback_data=f"exec:sell:{t}"),
            InlineKeyboardButton("💼 Portofolio", callback_data="run:portofolio"),
        ])
        rows.append([
            InlineKeyboardButton("📈 Grafik & Analisis", callback_data=f"tick:analisis:{t}"),
            InlineKeyboardButton("💰 Rincian Nilai", callback_data=f"tick:fundamental:{t}"),
        ])
    elif action in ("skor", "berita", "backtest"):
        rows.append([
            InlineKeyboardButton("🛡️ Cek 6 Poin", callback_data=f"tick:cek:{t}"),
            InlineKeyboardButton("📈 Grafik & Analisis", callback_data=f"tick:analisis:{t}"),
        ])
        if allow_buy:
            rows.append([
                InlineKeyboardButton("💰 Rincian Nilai", callback_data=f"tick:fundamental:{t}"),
                InlineKeyboardButton("🎯 Hitung Lot", callback_data=f"tick:rencana:{t}"),
            ])
            rows.append(_action_trade_row(t, allow_buy=True))
        else:
            rows.append([
                InlineKeyboardButton("💰 Rincian Nilai", callback_data=f"tick:fundamental:{t}"),
                InlineKeyboardButton("🤔 Cek layak jual?", callback_data=f"tick:jual:{t}"),
            ])
    rows.append([
        InlineKeyboardButton("🔄 Perbarui", callback_data=f"refresh:{action}:{t}"),
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
    extra_tickers: list[str] | None = None,
) -> InlineKeyboardMarkup:
    rows = []
    extra: list[str] = []
    for raw in (extra_tickers or []):
        clean = _clean_ticker_tok(str(raw).strip().upper())
        if clean and clean not in extra:
            extra.append(clean)
    extra = extra[:6]
    if extra:
        for i in range(0, len(extra), 3):
            rows.append([
                InlineKeyboardButton(f"📡 {t}", callback_data=f"tick:{action}:{t}")
                for t in extra[i:i + 3]
            ])
    wl: list[str] = []
    for raw in (watchlist or []):
        clean = _clean_ticker_tok(str(raw).strip().upper())
        if clean and clean not in extra and clean not in POPULAR_TICKERS and clean not in wl:
            wl.append(clean)
    wl = wl[:6]
    if wl:
        for i in range(0, len(wl), 3):
            rows.append([InlineKeyboardButton(f"⭐ {t}", callback_data=f"tick:{action}:{t}")
                         for t in wl[i:i + 3]])
    pop = [t for t in POPULAR_TICKERS if t not in extra]
    for i in range(0, len(pop), 3):
        rows.append([InlineKeyboardButton(t, callback_data=f"tick:{action}:{t}")
                     for t in pop[i:i + 3]])
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
        [InlineKeyboardButton("Rp 500 Rb", callback_data=f"setmodal:{_MODAL_PRESETS[0]}"),
         InlineKeyboardButton("Rp 1 Juta", callback_data=f"setmodal:{_MODAL_PRESETS[1]}")],
        [InlineKeyboardButton("Rp 2 Juta", callback_data=f"setmodal:{_MODAL_PRESETS[2]}"),
         InlineKeyboardButton("Rp 5 Juta", callback_data=f"setmodal:{_MODAL_PRESETS[3]}")],
        [InlineKeyboardButton("Rp 10 Juta", callback_data=f"setmodal:{_MODAL_PRESETS[4]}"),
         InlineKeyboardButton("Rp 25 Juta", callback_data=f"setmodal:{_MODAL_PRESETS[5]}")],
        [InlineKeyboardButton("✏️ Ketik Angka Sendiri", callback_data="manual:modal")],
        [InlineKeyboardButton("⬅️ Kembali", callback_data="sub:rencana"),
         InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
    ])


def _modal_saved_text(cur_modal: int, new_modal: int) -> str:
    """Ringkasan konfirmasi perubahan modal beserta batas risiko & slot."""
    return (
        f"💵 Modal diperbarui: {saranmod.fmt(cur_modal)} → <b>{saranmod.fmt(new_modal)}</b>\n"
        f"<i>Risiko maks normal (1%): {saranmod.fmt(new_modal * 0.01)} per posisi · "
        f"Batas slot 25%: {saranmod.fmt(new_modal * 0.25)}.</i>"
    )


def _modal_saved_markup() -> InlineKeyboardMarkup:
    """Tombol lanjutan setelah modal disimpan agar pengguna langsung bertindak."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 Hitung 1 Saham Sekarang", callback_data="pick:rencana")],
        [InlineKeyboardButton("🔎 Cari Kandidat Beli", callback_data="guide:beli"),
         InlineKeyboardButton("📈 Beli Bagus (Fundamental)", callback_data="run:sinyal_bagus")],
        [InlineKeyboardButton("⬅️ Atur Rencana & Risiko", callback_data="sub:rencana"),
         InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
    ])


def _submenu_content(group: str, modal: int) -> tuple[str, InlineKeyboardMarkup] | None:
    if group == "ide":
        return (
            "💡 <b>Ide Saham &amp; Sinyal Pasar</b>\n\n"
            "• <b>Kondisi Pasar</b>: ringkasan IHSG, rezim, luas pasar, sektor &amp; berita.\n"
            "• <b>Ide Hari Ini</b>: scan kandidat teratas + ukuran lot aman.\n"
            "• <b>Beli Berkualitas</b>: khusus sinyal BELI yang labanya sehat.\n"
            "• <b>Sinyal Pasar</b>: rangkuman sinyal seluruh BEI (±944 emiten).\n"
            "• <b>Sektor Terkuat</b>: lihat sektor yang sedang banyak diminati.",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🌐 Kondisi Pasar", callback_data="run:pasar"),
                 InlineKeyboardButton("💡 Ide Hari Ini", callback_data="run:saran")],
                [InlineKeyboardButton("✨ Beli Berkualitas", callback_data="run:sinyal_bagus"),
                 InlineKeyboardButton("📡 Sinyal Pasar", callback_data="run:sinyal")],
                [InlineKeyboardButton("🏭 Sektor Terkuat", callback_data="run:sektor"),
                 InlineKeyboardButton("⬅️ Menu Utama", callback_data="menu")],
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
            "• <code>/mode pemula</code> / <code>/mode expert</code> — ganti tampilan menu (simpel / pro)\n"
            "• <code>/syariah</code> (<code>on</code>/<code>off</code>/<code>cek TLKM</code>) — filter saham syariah ISSI vs semua\n"
            "• <code>/help</code> — panduan 3 langkah mulai\n"
            "• <code>/cek BBCA</code> — ceklist 6 bagian + kesimpulan\n"
            "• <code>/analisis BBCA</code> — grafik &amp; analisis lengkap\n"
            "• <code>/rencana BBCA 5jt</code> — hitung lot, SL, TP sesuai budget\n"
            "• <code>/beli BBCA</code> / <code>/jual BBCA</code> — tanya layak beli/jual\n"
            "• <code>/belajar RSI</code> — kamus istilah saham\n\n"
            "<b>Ide Pasar, Modal &amp; Portofolio:</b>\n"
            "• <code>/pasar</code> (alias <code>/market</code>) — laporan kondisi pasar BEI (IHSG, rezim, breadth, sektor &amp; berita)\n"
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
                 InlineKeyboardButton("🌐 Kondisi Pasar", callback_data="run:pasar")],
                [InlineKeyboardButton("👀 Watchlist & Alarm", callback_data="sub:pantau"),
                 InlineKeyboardButton("📖 Panduan & Contoh", callback_data="run:help")],
                [InlineKeyboardButton("🧭 Panduan Pemula", callback_data="guide:start"),
                 InlineKeyboardButton("🎓 Belajar Istilah", callback_data="run:belajar")],
                [InlineKeyboardButton("🧪 Fitur Lanjutan & Riset", callback_data="sub:pro"),
                 InlineKeyboardButton("🎚️ Ganti Level Menu", callback_data="mode:menu")],
                [InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
            ]),
        )
    return None



from handlers.menu_guide import (  # noqa: E402, F401
    _ONBOARD_GOALS,
    _evaluate_beli_candidates,
    _format_beli_candidate_line,
    _guide_beli_view,
    _guide_content,
    _guide_jual_view,
    _guide_pemula_view,
    _guide_start_view,
    _load_guide_beli_data,
    _load_guide_jual_data,
    _onboard_done_sync,
    _onboard_done_view,
    _onboard_load_s3_sync,
    _onboard_load_state_sync,
    _onboard_s1_view,
    _onboard_s2_view,
    _onboard_s3_payload_sync,
    _onboard_s3_view,
    _onboard_s4_view,
    _onboard_s5_view,
    _onboard_set_modal_sync,
    _onboard_set_stage_sync,
    _resolve_onboard_goal,
)

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
    elif action == "pasar":
        await deps._do_pasar(update, [], from_menu=True)
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
    elif action == "pasar":
        await deps._safe_edit(
            msg, "⏳ Menyusun laporan kondisi pasar BEI...",
            reply_markup=deps.home_markup(),
        )
        await deps.run_menu_action(update, "pasar", [])
    elif action == "scan":
        await deps._do_scan(update, [], ctx.job_queue, from_menu=True)
    else:
        await deps.run_menu_action(update, action, [])


def _cache_dir(deps) -> str:
    return getattr(deps, "TMP_DIR", config.TMP_DIR)


def _load_pick_data(chat_id: int, action: str) -> tuple[list[str], list[str]]:
    """Muat watchlist & (jika sinyal_detail) ticker teratas dari snapshot scan."""
    wl = store.get_watchlist(chat_id)
    if action != "sinyal_detail":
        return wl, []
    rows = store.load_market_scan()
    ranked = sorted(
        (r for r in rows if r.get("ticker")),
        key=lambda r: (
            0 if r.get("signal") == "BELI" else (1 if r.get("signal") == "JUAL" else 2),
            -float(r.get("rel_pct") or 0),
        ),
    )
    extra: list[str] = []
    for r in ranked:
        t = _clean_cb_ticker(str(r["ticker"]))
        if t and t not in extra:
            extra.append(t)
        if len(extra) >= 6:
            break
    return wl, extra


def _onboard_tick_markup(
    ob_state: dict, ticker: str, deps
) -> InlineKeyboardMarkup:
    """Kembalikan tombol navigasi Stage 4/5 saat aksi dipicu dari panduan bertahap."""
    stage = ob_state.get("stage")
    clean = _clean_cb_ticker(ticker)
    if stage not in ("s4", "s5") or ob_state.get("ticker") != clean:
        return deps.home_markup()
    if stage == "s4":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(f"➡️ Lanjut Tahap 5 ({clean})", callback_data=f"onboard:s5:{clean}")],
            [InlineKeyboardButton("⬅️ Kembali Tahap 4", callback_data=f"onboard:s4:{clean}"),
             InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Kembali Tahap 5", callback_data=f"onboard:s5:{clean}"),
         InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
    ])


async def _edit_current_menu(msg, chat_id: int, deps) -> None:
    mode = await asyncio.to_thread(store.get_user_mode, chat_id)
    syariah_only = await asyncio.to_thread(store.get_syariah_mode, chat_id)
    await deps._safe_edit(
        msg,
        _render_menu_text(deps, mode),
        parse_mode=ParseMode.HTML,
        reply_markup=_render_main_menu_markup(deps, mode, syariah_only=syariah_only),
    )


async def _handle_onboard_cb(msg, chat_id: int, step_raw: str, deps) -> None:
    """Tangani seluruh callback panduan bertahap `onboard:*`."""
    if step_raw == "s1":
        data = await asyncio.to_thread(_onboard_set_stage_sync, chat_id, "s1")
        txt, rm = _onboard_s1_view(data["state"])
        await deps._safe_edit(msg, txt, parse_mode=ParseMode.HTML, reply_markup=rm)
        return

    if step_raw.startswith("goal:"):
        goal = step_raw.split(":", 1)[1]
        if goal not in _ONBOARD_GOALS:
            await deps._safe_edit(
                msg, "❌ Tujuan panduan tidak dikenal.", reply_markup=deps.home_markup(),
            )
            return
        data = await asyncio.to_thread(_onboard_set_stage_sync, chat_id, "s2", goal=goal)
        txt, rm = _onboard_s2_view(data["state"], data["modal"], data["max_pos"])
        await deps._safe_edit(msg, txt, parse_mode=ParseMode.HTML, reply_markup=rm)
        return

    if step_raw == "s2":
        data = await asyncio.to_thread(_onboard_set_stage_sync, chat_id, "s2")
        txt, rm = _onboard_s2_view(data["state"], data["modal"], data["max_pos"])
        await deps._safe_edit(msg, txt, parse_mode=ParseMode.HTML, reply_markup=rm)
        return

    if step_raw.startswith("modal:"):
        raw_val = step_raw.split(":", 1)[1]
        try:
            val = int(raw_val)
        except ValueError:
            await deps._safe_edit(
                msg, "❌ Nominal modal tidak valid.", reply_markup=deps.home_markup(),
            )
            return
        if not (100_000 <= val <= 10_000_000_000):
            await deps._safe_edit(
                msg, "❌ Modal harus antara Rp100 ribu dan Rp10 miliar.",
                reply_markup=deps.home_markup(),
            )
            return
        payload = await asyncio.to_thread(_onboard_set_modal_sync, chat_id, val)
        txt, rm = _onboard_s3_view(payload, cache_dir=_cache_dir(deps))
        await deps._safe_edit(msg, txt, parse_mode=ParseMode.HTML, reply_markup=rm)
        return

    if step_raw == "s3":
        payload = await asyncio.to_thread(_onboard_load_s3_sync, chat_id)
        txt, rm = _onboard_s3_view(payload, cache_dir=_cache_dir(deps))
        await deps._safe_edit(msg, txt, parse_mode=ParseMode.HTML, reply_markup=rm)
        return

    if step_raw == "s4" or step_raw.startswith("s4:") or step_raw == "s5" or step_raw.startswith("s5:"):
        stage = step_raw[:2]
        explicit = step_raw.split(":", 1)[1] if ":" in step_raw else ""
        if explicit:
            ticker = _clean_ticker_tok(explicit.strip().upper())
            if not ticker:
                await deps._safe_edit(
                    msg, "❌ Kode saham tidak valid.", reply_markup=deps.home_markup(),
                )
                return
        else:
            cur_data = await asyncio.to_thread(_onboard_load_state_sync, chat_id)
            saved_t = str(cur_data["state"].get("ticker") or "").strip().upper()
            ticker = _clean_ticker_tok(saved_t)
            if not ticker:
                payload = await asyncio.to_thread(_onboard_load_s3_sync, chat_id)
                txt, rm = _onboard_s3_view(payload, cache_dir=_cache_dir(deps))
                await deps._safe_edit(msg, txt, parse_mode=ParseMode.HTML, reply_markup=rm)
                return
        data = await asyncio.to_thread(_onboard_set_stage_sync, chat_id, stage, ticker=ticker)
        view_fn = _onboard_s4_view if stage == "s4" else _onboard_s5_view
        txt, rm = view_fn(data["state"], data["modal"], ticker)
        await deps._safe_edit(msg, txt, parse_mode=ParseMode.HTML, reply_markup=rm)
        return

    if step_raw == "done":
        prev = await asyncio.to_thread(_onboard_done_sync, chat_id)
        txt, rm = _onboard_done_view(prev)
        await deps._safe_edit(msg, txt, parse_mode=ParseMode.HTML, reply_markup=rm)
        return

    await deps._safe_edit(
        msg, "❌ Langkah panduan tidak dikenal.", reply_markup=deps.home_markup(),
    )


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
        await _edit_current_menu(msg, chat_id, deps)
        return

    if cb_data.startswith("mode:"):
        deps._pending_clear(chat_id)
        sub_mode = cb_data.split(":", 1)[1]
        if sub_mode == "menu":
            cur_mode = await asyncio.to_thread(store.get_user_mode, chat_id)
            await deps._safe_edit(
                msg,
                mode_text(cur_mode),
                parse_mode=ParseMode.HTML,
                reply_markup=mode_menu_markup(cur_mode),
            )
            return
        if sub_mode.startswith("set:"):
            raw_target = sub_mode.split(":", 1)[1]
            try:
                norm_mode = store.normalize_user_mode(raw_target)
            except ValueError:
                await deps._safe_edit(
                    msg, "❌ Pilihan level menu tidak valid.",
                    reply_markup=deps.home_markup(),
                )
                return
            saved_mode = await asyncio.to_thread(store.set_user_mode, chat_id, norm_mode)
            txt, rm = _mode_switched_view(saved_mode, deps)
            await deps._safe_edit(msg, txt, parse_mode=ParseMode.HTML, reply_markup=rm)
            return
        await deps._safe_edit(
            msg, "❌ Tombol level menu tidak dikenal.", reply_markup=deps.home_markup(),
        )
        return

    if cb_data.startswith("syariah:"):
        deps._pending_clear(chat_id)
        sub_sy = cb_data.split(":", 1)[1]
        if sub_sy == "menu":
            cur_sy = await asyncio.to_thread(store.get_syariah_mode, chat_id)
            await deps._safe_edit(
                msg,
                syariah_text(cur_sy),
                parse_mode=ParseMode.HTML,
                reply_markup=syariah_menu_markup(cur_sy),
            )
            return
        if sub_sy in ("set:on", "set:off"):
            target_sy = sub_sy == "set:on"
            saved_sy = await asyncio.to_thread(store.set_syariah_mode, chat_id, target_sy)
            banner = (
                "☪️ <b>Filter Saham Syariah AKTIF!</b>\n"
                "Sekarang <code>/sinyal</code>, <code>/saran</code>, dan <code>/paket</code> "
                "hanya menampilkan emiten ISSI / DES OJK.\n\n"
                if saved_sy
                else "🌐 <b>Mode Semua Saham (Biasa) AKTIF!</b>\n"
                "Sekarang seluruh emiten BEI (syariah &amp; non-syariah) ditampilkan.\n\n"
            )
            await deps._safe_edit(
                msg,
                banner + syariah_text(saved_sy),
                parse_mode=ParseMode.HTML,
                reply_markup=syariah_menu_markup(saved_sy),
            )
            return
        await deps._safe_edit(
            msg, "❌ Tombol filter syariah tidak dikenal.", reply_markup=deps.home_markup(),
        )
        return

    if cb_data.startswith("onboard:"):
        deps._pending_clear(chat_id)
        step_raw = cb_data.split(":", 1)[1]
        await _handle_onboard_cb(msg, chat_id, step_raw, deps)
        return

    if cb_data.startswith("sub:"):
        deps._pending_clear(chat_id)
        group = cb_data.split(":", 1)[1]
        modal = await asyncio.to_thread(store.get_modal, chat_id)
        sub = _submenu_content(group, modal)
        if not sub:
            await _edit_current_menu(msg, chat_id, deps)
            return
        text, markup = sub
        await deps._safe_edit(msg, text, parse_mode=ParseMode.HTML, reply_markup=markup)
        return

    if cb_data.startswith("guide:"):
        deps._pending_clear(chat_id)
        step = cb_data.split(":", 1)[1]
        guide = await _guide_content(step, chat_id, cache_dir=_cache_dir(deps))
        if not guide:
            await _edit_current_menu(msg, chat_id, deps)
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
            _modal_saved_text(cur, val),
            parse_mode=ParseMode.HTML,
            reply_markup=_modal_saved_markup(),
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
        wl, extra = await asyncio.to_thread(_load_pick_data, chat_id, action)
        try:
            rm = deps.ticker_pick_markup(action, wl, extra_tickers=extra)
        except TypeError:
            rm = deps.ticker_pick_markup(action, wl)
        await deps._safe_edit(msg, head, parse_mode=ParseMode.HTML, reply_markup=rm)
        return

    if cb_data.startswith("manual:"):
        action = cb_data.split(":", 1)[1]
        uid = update.effective_user.id if update.effective_user else None
        try:
            deps._pending_set(chat_id, action, user_id=uid)
        except TypeError:
            deps._pending_set(chat_id, action)
        if action == "onboard_modal":
            cancel_kb = [[
                InlineKeyboardButton("⬅️ Kembali Tahap 2", callback_data="onboard:s2"),
                InlineKeyboardButton("🏠 Menu Utama", callback_data="menu"),
            ]]
        elif action == "onboard_ticker":
            cancel_kb = [[
                InlineKeyboardButton("⬅️ Kembali Tahap 3", callback_data="onboard:s3"),
                InlineKeyboardButton("🏠 Menu Utama", callback_data="menu"),
            ]]
        else:
            cancel_kb = [[InlineKeyboardButton("⬅️ Batal", callback_data="menu")]]
        await deps._safe_edit(
            msg,
            _MANUAL_PROMPT.get(action, "Ketik kode emiten:"),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(cancel_kb),
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
        ob_state = (
            await asyncio.to_thread(store.get_onboard_state, chat_id)
            if kind == "tick" else {}
        )
        await deps._safe_edit(
            msg,
            f"⏳ {verb} <b>{esc(ticker)}</b>...",
            parse_mode=ParseMode.HTML,
            reply_markup=_onboard_tick_markup(ob_state, ticker, deps),
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
        if action == "rencana":
            if len(parts) == 1:
                return [head]
            if len(parts) == 2:
                return [head, parts[1]] if textutil.parse_rupiah(parts[1]) is not None else []
            # Gabungkan multi-token seperti `BBCA Rp 500.000` atau `BBCA 5 jt`.
            has_rp = any(p == "RP" or p.startswith("RP") for p in parts[1:])
            has_unit = parts[-1] in ("JUTA", "JT", "RIBU", "RB", "K")
            if not (has_rp or has_unit):
                return []
            joined = "".join(parts[1:])
            return [head, joined] if textutil.parse_rupiah(joined) is not None else []
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
    if not update.effective_chat or not update.effective_user:
        return
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
        return
    owner = getattr(deps, "_pending_owner", lambda _c: None)(chat_id)
    if owner is not None and update.effective_user.id != owner:
        return

    raw_text = (update.effective_message.text or "").strip()
    if await deps.jurnal_handler.handle_pending_exec(
        update, chat_id, action, raw_text, deps
    ):
        return
    if action in ("modal", "onboard_modal"):
        val = deps._parse_rupiah(raw_text)
        if val is None or not (100_000 <= val <= 10_000_000_000):
            await update.effective_message.reply_text(
                "❌ Nominal tidak valid (wajar Rp100 ribu – Rp10 miliar).\n"
                "Contoh: <code>5000000</code>, <code>5jt</code>, atau <code>2,5jt</code>.",
                parse_mode=ParseMode.HTML,
            )
            return
        deps._pending_clear(chat_id)
        iv = int(round(val))
        if action == "onboard_modal":
            payload = await asyncio.to_thread(_onboard_set_modal_sync, chat_id, iv)
            txt, rm = _onboard_s3_view(payload, cache_dir=_cache_dir(deps))
            await update.effective_message.reply_text(
                txt, parse_mode=ParseMode.HTML, reply_markup=rm,
            )
            return
        cur = await asyncio.to_thread(store.get_modal, chat_id)
        await asyncio.to_thread(store.set_modal, chat_id, iv)
        await update.effective_message.reply_text(
            _modal_saved_text(cur, iv),
            parse_mode=ParseMode.HTML,
            reply_markup=_modal_saved_markup(),
        )
        return

    if action == "onboard_ticker":
        tokens = _parse_menu_tokens("cek", raw_text)
        if len(tokens) != 1:
            await update.effective_message.reply_text(
                "❌ Ketik tepat 1 kode emiten yang valid (contoh: <code>BBCA</code>).",
                parse_mode=ParseMode.HTML,
            )
            return
        deps._pending_clear(chat_id)
        t = tokens[0]
        data = await asyncio.to_thread(_onboard_set_stage_sync, chat_id, "s4", ticker=t)
        txt, rm = _onboard_s4_view(data["state"], data["modal"], t)
        await update.effective_message.reply_text(
            txt, parse_mode=ParseMode.HTML, reply_markup=rm,
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
