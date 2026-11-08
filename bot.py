"""SahamBuddy — bot Telegram analisis saham Indonesia.

Perintah:
  /start, /menu, /help     - menu utama / panduan
  /beli BBCA / /jual BBCA  - tanya layak beli/jual (atau ketik bebas:
                            "BBCA beli gak?", "jual TLKM?")
  /analisis BBCA           - laporan teknikal lengkap + chart
  /skor ANTM TLKM          - ringkasan skor (tanpa chart)
  /watch add <TICKER>      - tambah saham ke watchlist
  /watch list              - lihat watchlist
  /watch <TICKER>          - sekilas semua/harga watchlist
  /alert add <TICKER> [jenis...]
  /alert list
  /alert remove <TICKER>
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import time
from zoneinfo import ZoneInfo

import pandas as pd
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters)
from telegram.error import BadRequest

import config
import config
from core import alerts, analysis, bandar, data, fundamental, news, powerscore, saran as saranmod, store
from core.chart import build_chart

import backtest as btmod

# [kendala 3, fix permanen] httpx bengkak saat NO_PROXY berisi IPv6 berkurung
# seperti "[::1]" — (langsung) bersihkan sebelum client dibangun
_np = os.environ.get("NO_PROXY", os.environ.get("no_proxy", ""))
if "[::1]" in _np or "[" in _np:
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "localhost,127.0.0.1,::1"

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    level=logging.INFO)
# [kendala 6] token sering tampil di URL httpx — redaksikan (bisa masuk ke log)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
log = logging.getLogger("sahambuddy")

WIB = ZoneInfo("Asia/Jakarta")
TMP_DIR = os.getenv("TMP_DIR", "temp")
os.makedirs(TMP_DIR, exist_ok=True)


# ---------------------------------------------------------------- helpers

def fmt_rp(v: float) -> str:
    return f"Rp{v:,.0f}".replace(",", ".")


def esc(s) -> str:
    """Escape teks dinamis untuk pesan HTML Telegram (<, >, &).

    Wajib untuk semua teks dari luar (judul berita, nama emiten, string skor
    seperti 'MA20 < MA50') — kalau tidak, Telegram menolak pesan dengan
    'Can't parse entities: unsupported start tag' dan handler crash.
    """
    return html.escape(str(s))


def tokens(update: Update) -> list[str]:
    txt = (update.effective_message.text or "").split()
    return txt[1:]


# ---- tanya beli/jual bahasa natural: "BBCA beli gak?", "jual TLKM?" ----
_TANYA_BELI = {"beli", "borong", "buy", "akumulasi", "serok"}
_TANYA_JUAL = {"jual", "lepas", "sell", "cut", "cutloss", "tp", "take",
               "tahan", "hold"}
_TANYA_STOP = {"atau", "gak", "nggak", "ngga", "apa", "apakah", "kah",
               "dong", "ya", "lagi", "sekarang", "kapan", "bagus", "layak",
               "mau", "saya", "aku", "kamu", "untuk", "di", "ke", "dari",
               "yang", "ini", "itu", "sih", "aja", "saja", "kalau", "gimana",
               "bagaimana", "harus", "bisa", "boleh", "mending", "sebaiknya",
               "worth", "nya", "nih", "tolong", "coba", "cek", "saham",
               "emiten", "loss", "profit"}


def parse_tanya(text: str) -> tuple[str, str | None] | None:
    """Kenali pertanyaan beli/jual dari teks bebas.

    Balik (focus, ticker) — focus: 'beli' | 'jual' | 'umum'
    (umum = "beli atau jual"); ticker None kalau tidak disebut.
    None kalau teksnya bukan pertanyaan beli/jual.
    """
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    has_beli = any(w in _TANYA_BELI for w in words)
    has_jual = any(w in _TANYA_JUAL for w in words)
    if not (has_beli or has_jual):
        return None
    tickers = [w.upper() for w in words
               if 2 <= len(w) <= 6 and w.isalpha()
               and w not in _TANYA_BELI and w not in _TANYA_JUAL
               and w not in _TANYA_STOP]
    focus = "umum" if (has_beli and has_jual) else ("beli" if has_beli else "jual")
    return focus, tickers[0] if tickers else None


async def send_typing(update: Update):
    await update.effective_chat.send_action(ChatAction.TYPING)


def last_macd_cross_note(score: dict) -> str:
    return f"\n⚠️ {esc(score['macd_note'])}" if score.get("macd_note") else ""


async def fetch_and_analyze(ticker: str) -> dict:
    df = await asyncio.to_thread(data.fetch_history, ticker)
    if df is None:
        raise ValueError(f"Data untuk '{ticker}' tidak ditemukan (kursor tidak valid atau koneksi gagal).")
    res = await asyncio.to_thread(analysis.analyze, ticker, df)
    res["ticker"] = ticker.upper().replace(".", "").replace("JK", "")
    # [kendala 1] kalau data berasal dari cache (Yahoo 429 dsb), sampaikan jujur
    res["stale_note"] = df.attrs.get("stale_note", "")
    return res


# ---------------------------------------------------------------- report

def build_report(res: dict, fund: dict | None, news_sent: dict | None) -> str:
    snap, sc, sr = res["snap"], res["score"], res["sr"]
    bull_txt = "\n".join(f"  ✅ {esc(s)}" for s in sc["bull"]) or "  —"
    bear_txt = "\n".join(f"  ❌ {esc(s)}" for s in sc["bear"]) or "  —"
    ma_row = " | ".join(
        f"MA{n}:{snap['sma' + n]}" if snap.get('sma' + n) else f"MA{n}:—"
        for n in ("20", "50", "200"))
    power = powerscore.judge(sc["pct"], fund, news_sent, snap)
    conf = analysis.extra_confirmations(res["ind"])
    conf_txt = "\n".join(f"  • {esc(c)}" for c in conf) if conf else ""

    headline_txt = ""
    if news_sent and news_sent.get("details"):
        top = sorted(news_sent["details"], key=lambda d: -abs(d["sent"]))[:2]
        heads = "\n".join(
            f"  📰 <a href=\"{esc(d.get('link') or 'https://finance.yahoo.com')}\">"
            f"{esc(d['title'][:80])}</a>" + (f" <i>({('%+d' % d['sent'])})</i>" if d["sent"] else "")
            for d in top)
        headline_txt = (f"\nBerita terkini (sentimen {news_sent['emoji']} "
                        f"{news_sent['pct']:+.0f}%):\n{heads}")

    return (
        f"{res.get('stale_note', '')}"
        f"📊 <b>{res['ticker']}</b>\n"
        f"Harga: <b>{fmt_rp(snap['close'])}</b> "
        f"({snap['ret1d']:+.2f}% 1d, {snap['ret20d']:+.2f}% 20d)\n\n"
        f"{power}\n\n"
        f"Skor teknikal: <b>{sc['pct']:+.0f}%</b> → {sc['emoji']} <b>{sc['rating']}</b>\n"
        f"  ✅ Sinyal positif:\n{bull_txt}\n"
        f"  ❌ Sinyal negatif:\n{bear_txt}\n"
        f"{last_macd_cross_note(sc)}\n\n"
        f"Indikator:\n"
        f"  RSI 14: {snap['rsi']}{' (oversold)' if snap['rsi'] < 30 else ' (overbought)' if snap['rsi'] > 70 else ''}\n"
        f"  MACD hist: {snap['macd_hist']}\n"
        f"  Volume hari ini: {snap['volume']:,.0f} "
        f"({snap['volume'] / snap['vol_avg20']:.1f}x rata\u00b2 20d)\n"
        f"  {ma_row}\n\n"
        f"Level:\n"
        f"  Rentang 20d: {fmt_rp(sr['low_recent'])} — {fmt_rp(sr['high_recent'])}\n"
        f"{('\nKonfirmasi indikator pro:\n' + conf_txt) if conf_txt else ''}\n"
        f"{headline_txt}\n\n"
        f"🔒 <i>Bukan saran investasi. DYOR!</i>"
    )


def build_quick(res: dict) -> str:
    snap, sc = res["snap"], res["score"]
    # [kendala 1] tanda singkat kalau data dari cache, bukan live
    note = " ⚠️(cache)" if res.get("stale_note") else ""
    return (
        f"{sc['emoji']} <b>{res['ticker']}</b> {fmt_rp(snap['close'])} "
        f"({snap['ret1d']:+.2f}%) — {sc['pct']:+.0f}% {sc['rating']}{note}"
    )


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


def _verdict_jual(ts_pct: float, close: float, support: float) -> tuple[str, str]:
    """(judul, alasan singkat) untuk yang sudah pegang."""
    if ts_pct >= 50:
        return ("🟢 TAHAN",
                "Tren naik — biarkan profit jalan, pasang stop loss di bawah "
                "support.")
    if ts_pct > -20:
        return ("🟡 TAHAN dengan SL ketat",
                "Tren belum jelas — tahan, tapi pasang stop loss supaya profit "
                "tidak berubah jadi rugi.")
    if ts_pct > -50:
        return ("🟠 Pertimbangkan JUAL SEBAGIAN",
                "Tren melemah — amankan sebagian profit/modal, sisanya pasang "
                "stop loss.")
    extra = (" Harga sudah di bawah support "
             f"{fmt_rp(support)}." if close < support else "")
    return ("🔴 Pertimbangkan JUAL / cut loss",
            "Downtrend kuat. Jangan ditahan dengan harapan." + extra)


def build_verdict(res: dict, fund: dict | None, news_sent: dict | None,
                  focus: str) -> str:
    snap, sc, sr = res["snap"], res["score"], res["sr"]
    t = res["ticker"]
    fund_pct = fund["pct"] if fund else None
    news_pct = news_sent["pct"] if news_sent else None
    pw = powerscore.combine(sc["pct"], fund_pct, news_pct)
    close = snap["close"]

    last = res["ind"].iloc[-1]
    atr = float(last["atr14"]) if not pd.isna(last.get("atr14", float("nan"))) else None
    sl_beli = close - atr if atr else sr["support"]
    tp_beli = close + 2 * atr if atr else sr["resistance"]

    beli_judul, beli_alasan = _verdict_beli(pw["pct"])
    jual_judul, jual_alasan = _verdict_jual(sc["pct"], close, sr["support"])

    title = {"beli": f"🛒 <b>Beli {t}?</b>",
             "jual": f"💰 <b>Jual {t}?</b>",
             "umum": f"⚖️ <b>Beli atau jual {t}?</b>"}[focus]

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
        reasons.append(f"Teknikal {sc['pct']:+.0f}% ({esc(sc['rating'])}): {top}")
    elif sc["bull"]:
        top = "; ".join(esc(s) for s in sc["bull"][:2])
        reasons.append(f"Teknikal {sc['pct']:+.0f}% ({esc(sc['rating'])}): {top}")
    else:
        reasons.append(f"Teknikal {sc['pct']:+.0f}% ({esc(sc['rating'])})")
    if fund:
        notes = "; ".join(esc(n) for n in fund.get("notes", [])[:2])
        reasons.append(f"Fundamental {fund['pct']:+.0f}% ({esc(fund['label'])}): "
                       f"{notes}" if notes else
                       f"Fundamental {fund['pct']:+.0f}% ({esc(fund['label'])})")
    if news_sent:
        reasons.append(f"Berita {news_sent['pct']:+.0f}% "
                       f"({news_sent['n']} berita)")
    reasons_txt = "\n".join(f"  • {r}" for r in reasons)

    conds = []
    if snap.get("sma50") and close < snap["sma50"]:
        conds.append(f"Kalau harga tembus ke atas MA50 ({fmt_rp(snap['sma50'])}) "
                     "→ teknikal membaik.")
    elif snap.get("sma20") and close < snap["sma20"]:
        conds.append(f"Kalau harga tembus ke atas MA20 ({fmt_rp(snap['sma20'])}) "
                     "→ momentum jangka pendek membaik.")
    if snap["rsi"] < 30:
        conds.append("RSI sudah oversold — pantau pantulan, tapi jangan tangkap "
                     "pisau jatuh.")
    if abs(close - sr["support"]) / close < 0.03:
        conds.append(f"Harga dekat support {fmt_rp(sr['support'])} — kalau jebol, "
                     "sinyal makin buruk.")
    elif close > sr["resistance"] * 0.97:
        conds.append(f"Kalau tembus resistance {fmt_rp(sr['resistance'])} → "
                     "momentum naik.")
    conds_txt = "\n".join(f"  • {esc(c)}" for c in conds[:2])

    trap = powerscore.trap_warning(snap.get("ret20d"), snap.get("ret1d"))
    trap_txt = f"\n{trap}" if trap else ""

    return (
        f"{res.get('stale_note', '')}{title}\n"
        f"Harga: <b>{fmt_rp(close)}</b> "
        f"({snap['ret1d']:+.2f}% 1d, {snap['ret20d']:+.2f}% 20d) | "
        f"PowerScore <b>{pw['pct']:+d}% {pw['emoji']} {pw['rating']}</b>\n\n"
        f"{headline}{other}\n\n"
        f"<b>Kenapa:</b>\n{reasons_txt}\n\n"
        f"<b>Level penting:</b>\n"
        f"  Support: {fmt_rp(sr['support'])} | Resistance: {fmt_rp(sr['resistance'])}\n"
        f"  Kalau beli — SL: {fmt_rp(sl_beli)} (1×ATR) | "
        f"TP: {fmt_rp(tp_beli)} (2×ATR)\n"
        f"{('<b>Yang bisa mengubah jawaban:</b>\n' + conds_txt + chr(10)) if conds_txt else ''}"
        f"{trap_txt}\n"
        f"🔒 <i>Bukan saran investasi. Skor bot = deskripsi kondisi saat itu, "
        f"bukan ramalan. DYOR!</i>"
    )


# ---------------------------------------------------------------- menu interaktif
# /start menampilkan tombol; semua fitur bisa dipakai tanpa mengetik perintah.
# Alur: tombol aksi -> (pilih emiten dari tombol cepat / ketik manual) -> hasil.
# Pesan teks biasa hanya diproses kalau chat sedang menunggu input emiten
# (PENDING), selebihnya diabaikan supaya tidak mengganggu perintah lain.

POPULAR_TICKERS = ["BBCA", "BBRI", "TLKM", "BMRI", "ANTM", "ASII"]

# aksi yang butuh emiten -> label tombolnya
NEED_TICKER = {
    "analisis": "📊 Analisis",
    "skor": "⚡ Skor",
    "berita": "📰 Berita",
    "fundamental": "💰 Fundamental",
    "backtest": "📈 Backtest",
    "beli": "🛒 Beli",
    "jual": "💰 Jual",
}

# chat_id -> aksi yang sedang menunggu input emiten
PENDING: dict[int, str] = {}
# diisi dari ALLOWED_USER_IDS di main(); kosong = siapa pun boleh
ALLOWED_IDS: set[int] = set()


def main_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Analisis", callback_data="pick:analisis"),
         InlineKeyboardButton("⚡ Skor", callback_data="pick:skor")],
        [InlineKeyboardButton("📰 Berita", callback_data="pick:berita"),
         InlineKeyboardButton("💰 Fundamental", callback_data="pick:fundamental")],
        [InlineKeyboardButton("💡 Saran", callback_data="run:saran"),
         InlineKeyboardButton("📈 Backtest", callback_data="pick:backtest")],
        [InlineKeyboardButton("🛒 Beli", callback_data="pick:beli"),
         InlineKeyboardButton("💰 Jual", callback_data="pick:jual")],
        [InlineKeyboardButton("👀 Watchlist", callback_data="run:watch"),
         InlineKeyboardButton("🔔 Alert", callback_data="run:alert")],
        [InlineKeyboardButton("🏦 Bandar", callback_data="run:bandar"),
         InlineKeyboardButton("❓ Bantuan", callback_data="run:help")],
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


def ticker_pick_markup(action: str, watchlist: list[str] | None = None) -> InlineKeyboardMarkup:
    rows = []
    wl = [t for t in (watchlist or []) if t not in POPULAR_TICKERS][:6]
    if wl:
        for i in range(0, len(wl), 3):
            rows.append([InlineKeyboardButton(f"⭐ {t}", callback_data=f"tick:{action}:{t}")
                         for t in wl[i:i + 3]])
    for i in range(0, len(POPULAR_TICKERS), 3):
        rows.append([InlineKeyboardButton(t, callback_data=f"tick:{action}:{t}")
                     for t in POPULAR_TICKERS[i:i + 3]])
    rows.append([InlineKeyboardButton("✏️ Ketik manual", callback_data=f"manual:{action}")])
    rows.append([InlineKeyboardButton("⬅️ Kembali", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def watch_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Lihat", callback_data="run:watch_list"),
         InlineKeyboardButton("➕ Tambah", callback_data="manual:watch_add")],
        [InlineKeyboardButton("🗑 Hapus", callback_data="manual:watch_del")],
        [InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
    ])


def alert_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Lihat", callback_data="run:alert_list"),
         InlineKeyboardButton("➕ Tambah", callback_data="manual:alert_add")],
        [InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
    ])


_MANUAL_PROMPT = {
    "analisis": "Ketik kode emiten untuk dianalisis (contoh: <code>BBCA</code>, atau beberapa dipisah spasi: <code>BBCA TLKM</code>):",
    "skor": "Ketik kode emiten untuk skor cepat (contoh: <code>BBCA TLKM BBRI</code>):",
    "berita": "Ketik kode emiten untuk beritanya (contoh: <code>BBCA</code>):",
    "fundamental": "Ketik kode emiten untuk fundamentalnya (contoh: <code>BBRI</code>):",
    "backtest": "Ketik kode emiten untuk di-backtest (contoh: <code>BBCA</code>):",
    "beli": "Mau beli emiten apa? Ketik kodenya (contoh: <code>BBCA</code>):",
    "jual": "Mau jual emiten apa? Ketik kodenya (contoh: <code>TLKM</code>):",
    "watch_add": "Ketik kode emiten yang mau ditambah ke watchlist (contoh: <code>ANTM</code>):",
    "watch_del": "Ketik kode emiten yang mau dihapus dari watchlist:",
    "alert_add": "Ketik kode emiten untuk alarm (jenis default: jump, drop, breakout, oversold, overbought, ma_golden, ma_death):",
}


async def _do_analisis(update: Update, tickers: list[str]):
    msg = update.effective_message
    for t in tickers[:5]:
        try:
            res = await fetch_and_analyze(t)
        except ValueError as e:
            await msg.reply_text(f"❌ {e}", reply_markup=home_markup())
            continue
        fund_f = asyncio.to_thread(fundamental.fetch_fundamentals, t)
        news_f = asyncio.to_thread(news.fetch_news, t)
        try:
            fund, items = await asyncio.gather(fund_f, news_f, return_exceptions=True)
        except Exception:
            fund, items = None, None
        if isinstance(fund, Exception) or fund is None:
            fund, fsc = None, None
        else:
            fsc = await asyncio.to_thread(fundamental.score_fundamentals, fund)
        if isinstance(items, Exception) or not items:
            nsent = None
        else:
            nsent = await asyncio.to_thread(news.news_sentiment, items)
        await msg.reply_text(build_report(res, fsc, nsent),
                             parse_mode=ParseMode.HTML,
                             reply_markup=result_markup("analisis", t))
        path = os.path.join(TMP_DIR, f"chart_{int(time.time() * 1000)}.png")
        await asyncio.to_thread(build_chart, res["ticker"].upper(), res["ind"],
                                res["score"]["pct"], path)
        try:
            with open(path, "rb") as f:
                await msg.reply_photo(
                    photo=f, caption=f"📈 Chart {res['ticker']} (6 bulan terakhir)",
                    reply_markup=result_markup("analisis", t))
        finally:
            if os.path.exists(path):
                os.remove(path)


async def _do_skor(update: Update, tickers: list[str]):
    msg = update.effective_message
    args = tickers or store.get_watchlist(update.effective_chat.id) or ["BBCA"]
    lines = []
    for t in args[:8]:
        try:
            res = await fetch_and_analyze(t)
            lines.append(build_quick(res))
        except ValueError as e:
            lines.append(f"❌ {e}")
    await msg.reply_text("\n".join(lines) or "Watchlist kosong.",
                         parse_mode=ParseMode.HTML, reply_markup=home_markup())


async def _do_berita(update: Update, ticker: str):
    msg = update.effective_message
    t = ticker.upper()
    await update.effective_chat.send_action(ChatAction.TYPING)
    try:
        items = await asyncio.to_thread(news.fetch_news, t)
    except Exception:
        items = []
    if not items:
        await msg.reply_text(f"❌ Berita untuk {t} tidak tersedia — atau jaringan sedang bermasalah.",
                             reply_markup=home_markup())
        return
    sent = await asyncio.to_thread(news.news_sentiment, items)
    lines = [f"📰 <b>Berita {t}</b> — sentimen {sent['emoji']} "
             f"({sent['pct']:+.0f}%, {sent['n']} berita)"]
    for d in sent["details"][:7]:
        har = f" {d['sent']:+.0f}" if d["sent"] else ""
        age = f", {d['age_days']:.0f}h lalu" if d["age_days"] >= 1 else ""
        src = f" — <i>{esc(d['source'])}</i>" if d.get("source") else ""
        lines.append(f"\n{d['date']:%d %b} · <a href=\"{esc(d['link'] or 'https://news.google.com')}\">"
                     f"{esc(d['title'][:110])}</a>{har}{age}{src}")
    lines.append("\n\n🔒 Bukan saran investasi.")
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML,
                         disable_web_page_preview=True,
                         reply_markup=result_markup("berita", t))


async def _do_fundamental(update: Update, ticker: str):
    msg = update.effective_message
    t = ticker.upper()
    await update.effective_chat.send_action(ChatAction.TYPING)
    fund = await asyncio.to_thread(fundamental.fetch_fundamentals, t)
    if fund is None:
        await msg.reply_text(f"❌ Data fundamental untuk {t} tidak tersedia.",
                             reply_markup=home_markup())
        return
    sc = await asyncio.to_thread(fundamental.score_fundamentals, fund)
    rows = [f"🏛️ <b>Fundamental {t}</b> — {esc(fund.get('name'))}"]
    if fund.get("sector"):
        rows.append(f"Sektor: {esc(fund['sector'])}")
    rows.append(f"Skor: <b>{sc['pct']:+d}%</b> {sc['emoji']} {sc['label']}\n")

    def _fmt(key, tpl, suffix=""):
        v = fund.get(key)
        return tpl % v + suffix if v is not None else "—"
    rows.append(f"  PER: {_fmt('per', '%.1f')}   PBV: {_fmt('pbv', '%.2f')}")
    rows.append(f"  EPS: {_fmt('eps', '%d')}   DER: {(_fmt('debt_eq', '%.0f%%') if fund.get('debt_eq') is not None else '—')}")
    if fund.get("roe") is not None:
        roe = fund["roe"] * 100 if fund["roe"] < 3 else fund["roe"]
        rows.append(f"  ROE: {roe:.1f}%")
    if fund.get("div_yield") is not None:
        dy = fund["div_yield"] * 100 if fund["div_yield"] < 1 else fund["div_yield"]
        rows.append(f"  Dividend yield: {dy:.1f}%")
    if fund.get("market_cap"):
        rows.append(f"  Kap. pasar: Rp{fund['market_cap'] / 1e12:.1f} T")
    rows.append("\nCatatan skor:")
    rows.extend(f"  {esc(n)}" for n in sc["notes"])
    rows.append("\n🔒 Bukan saran investasi.")
    await msg.reply_text("\n".join(rows), parse_mode=ParseMode.HTML,
                         reply_markup=result_markup("fundamental", t))


async def _do_backtest(update: Update, ticker: str):
    msg = update.effective_message
    t = ticker.upper()
    parts = []
    try:
        bt = await asyncio.to_thread(btmod.backtest, t)
        parts.append(f"🧪 <b>Backtest statistik {t}</b>\n<pre>{btmod.summarize(bt)}</pre>")
    except ValueError as e:
        await msg.reply_text(f"❌ {e}", reply_markup=home_markup())
        return
    try:
        import backtest_real
        stats = await asyncio.to_thread(backtest_real.run_backtest, t)
        parts.append("<b>Simulasi transaksi nyata</b> (buy sinyal BUY, SL 1×ATR, "
                     "TP 2×ATR, biaya 0,25%):")
        parts.append(
            f"<pre>"
            f"Return/thn      : {stats['Return (Ann.) [%]']:+.1f}%\n"
            f"Buy & hold      : {stats['Buy & Hold Return [%]']:+.1f}%\n"
            f"Transaksi       : {stats['# Trades']:.0f}\n"
            f"Win-rate        : {stats['Win Rate [%]']:.1f}%\n"
            f"Profit factor   : {stats['Profit Factor']:.2f}\n"
            f"Max drawdown    : {stats['Max. Drawdown [%]']:.1f}%"
            f"</pre>")
    except ValueError as e:
        parts.append(f"⚠️ Simulasi transaksi dilewati: {e}")
    except Exception:
        log.exception("backtest_real gagal utk %s", t)
        parts.append("⚠️ Simulasi transaksi gagal dijalankan (library backtesting.py).")
    parts.append("⚠️ Skor = deskripsi kondisi saat itu, bukan ramalan. "
                 "Uji dengan beberapa emiten sebelum menarik kesimpulan.")
    await msg.reply_text("\n".join(parts), parse_mode=ParseMode.HTML,
                         disable_web_page_preview=True,
                         reply_markup=result_markup("backtest", t))


async def _do_tanya(update: Update, ticker: str, focus: str):
    """Jawab 'beli/jual gak?' untuk satu emiten (focus: beli/jual/umum)."""
    msg = update.effective_message
    t = ticker.upper()
    try:
        res = await fetch_and_analyze(t)
    except ValueError as e:
        await msg.reply_text(f"❌ {e}", reply_markup=home_markup())
        return
    fund_f = asyncio.to_thread(fundamental.fetch_fundamentals, t)
    news_f = asyncio.to_thread(news.fetch_news, t)
    try:
        fund, items = await asyncio.gather(fund_f, news_f, return_exceptions=True)
    except Exception:
        fund, items = None, None
    if isinstance(fund, Exception) or fund is None:
        fund, fsc = None, None
    else:
        fsc = await asyncio.to_thread(fundamental.score_fundamentals, fund)
    if isinstance(items, Exception) or not items:
        nsent = None
    else:
        nsent = await asyncio.to_thread(news.news_sentiment, items)
    await msg.reply_text(build_verdict(res, fsc, nsent, focus),
                         parse_mode=ParseMode.HTML,
                         reply_markup=result_markup(f"tanya_{focus}", t))


async def _do_saran(update: Update, list_only: bool = False):
    msg = update.effective_message
    chat_id = update.effective_chat.id
    if list_only:
        lines = saranmod.quotes_forgotten(chat_id)
        await msg.reply_text("\n".join(lines) or "Belum ada posisi latihan.",
                             parse_mode=ParseMode.HTML, disable_web_page_preview=True,
                             reply_markup=home_markup())
        return
    wl = store.get_watchlist(chat_id)
    universe = (wl + [t for t in saranmod.DEFAULT_UNIVERSE if t not in wl])[:saranmod.MAX_SCAN]
    await msg.reply_text(
        f"🔍 Memindai {len(universe)} emiten (teknikal+fundamental+berita), 3-4 menit...",
        reply_markup=home_markup())
    analyzed, latest = [], {}
    for t in universe:
        a = await gather_candidate(t)
        if a:
            analyzed.append(a)
            latest[a["res"]["ticker"]] = a["res"]["snap"]["close"]
        await asyncio.sleep(.2)
    if not analyzed:
        await msg.reply_text("❌ Data sumber error, coba lagi nanti.",
                             reply_markup=home_markup())
        return
    result = saranmod.pick(analyzed)
    saranmod.update_paper(chat_id, latest)
    lines = [f"🧭 <b>RINGKASAN SARAN HARIAN — {pd.Timestamp.now(tz=WIB):%d %b %Y}</b>",
             "Skema cais: jual dibeli penutupan | SL 1×ATR | TP 2×ATR\n"]
    if result["picks"]:
        saranmod.open_paper(chat_id, result["picks"], f"{pd.Timestamp.now(tz=WIB):%d %b}")
        lines.append("Kandidat BUY (urut PowerScore, tersimpan sebagai posisi latihan):")
        for c in result["picks"]:
            fund_txt = "—" if c["fund"] is None else f"{c['fund']:+.0f}"
            news_txt = "—" if c["news"] is None else f"{c['news']:+.0f}"
            lines.append(
                f"  {c['emoji']} <b>{c['ticker']}</b> power {c['power']:+d}% "
                f"(tek {c['tech']:+.0f}, fund {fund_txt}, berita {news_txt})\n"
                f"      Entry {saranmod.fmt(c['entry'])} | SL {saranmod.fmt(c['sl'])} | "
                f"TP {saranmod.fmt(c['tp'])}")
    else:
        lines.append("Tidak ada kandidat layak BUY hari ini "
                     "(semua skor tak mencukupi / terkena filter jebakan momentum).")
    lines.append("\n🚫 HINDARI (terlemah):")
    for c in result["avoid"]:
        lines.append(f"  {c['emoji']} <b>{c['ticker']}</b> {c['power']:+d}% — "
                     f"{'jebakan momentum' if c['ret20d'] > 15 else 'teknikal lemah'}")
    lines.append("\n⚠️ Alat bantu latihan, bukan saran formal. DYOR!")
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML,
                         disable_web_page_preview=True, reply_markup=home_markup())


async def _do_watch_list(update: Update):
    msg = update.effective_message
    chat_id = update.effective_chat.id
    wl = store.get_watchlist(chat_id)
    if not wl:
        await msg.reply_text("Watchlist masih kosong. Tambahkan dulu lewat tombol ➕ Tambah.",
                             reply_markup=watch_menu_markup())
        return
    lines = [store_format_quick(wl, chat_id)]
    for t in wl:
        try:
            res = await fetch_and_analyze(t)
            lines.append(build_quick(res))
        except ValueError as e:
            lines.append(f"❌ {e}")
        await asyncio.sleep(.3)
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML,
                         reply_markup=watch_menu_markup())


async def _do_watch_add(update: Update, ticker: str):
    msg = update.effective_message
    ok, wl = store.watchlist_add(update.effective_chat.id, ticker.upper())
    prefix = "✅ Ditambahkan" if ok else "📌 Sudah ada di"
    await msg.reply_text(f"{prefix} watchlist: {', '.join(str(w) for w in wl)}",
                         reply_markup=watch_menu_markup())


async def _do_watch_del(update: Update, ticker: str):
    msg = update.effective_message
    ok, wl = store.watchlist_remove(update.effective_chat.id, ticker.upper())
    prefix = "🗑️ Dihapus" if ok else "❓ Tidak ditemukan di"
    await msg.reply_text(f"{prefix} watchlist. Sisanya: {', '.join(wl) or '(kosong)'}",
                         reply_markup=watch_menu_markup())


async def _do_alert_list(update: Update):
    msg = update.effective_message
    al = store.get_alerts(update.effective_chat.id)
    if al:
        txt = "\n".join(f"  • <b>{s}</b>: {', '.join(sorted(k))}" for s, k in al.items())
        await msg.reply_text(f"Alert kamu:\n{txt}", parse_mode=ParseMode.HTML,
                             reply_markup=alert_menu_markup())
    else:
        await msg.reply_text("Belum ada alert.", reply_markup=alert_menu_markup())


async def _do_alert_add(update: Update, ticker: str):
    msg = update.effective_message
    chat_id = update.effective_chat.id
    symbol = ticker.upper()
    kinds = {"jump", "drop", "breakout", "oversold", "overbought", "ma_golden", "ma_death"}
    saved = store.set_alert(chat_id, symbol, kinds)
    store.watchlist_add(chat_id, symbol)
    await msg.reply_text(
        f"🔔 Alert untuk <b>{symbol}</b> aktif: <code>{', '.join(sorted(saved))}</code>\n"
        "Scanner mengecek tiap 30 menit saat pasar buka.",
        parse_mode=ParseMode.HTML, reply_markup=alert_menu_markup())


async def _do_bandar(update: Update):
    msg = update.effective_message
    await update.effective_chat.send_action(ChatAction.TYPING)

    def _job():
        idx = bandar.get_index_summary()
        rows = bandar.get_stock_summary(bandar.last_trading_date(), max_stocks=200)
        return idx, rows
    try:
        idx, rows = await asyncio.to_thread(_job)
    except bandar.IDXTidakTersedia as e:
        await msg.reply_text(
            "🔒 <b>Data BEI (idx.co.id) kini diblokir Cloudflare</b> "
            "(IP server diperlakukan kurang manis). Coba lagi nanti atau dari server lain.\n"
            "<i>Alternatif cepat: /analisis TICKER (pakai Yahoo Finance, delayed 15 menit).</i>",
            parse_mode=ParseMode.HTML, reply_markup=home_markup())
        log.warning("BEI blocked: %s", e)
        return
    comp = next((x for x in idx if x["IndexCode"] == "COMPOSITE"), None)
    lines = []
    if comp:
        def _num(k):
            return f"{comp.get(k):,.2f}" if isinstance(comp.get(k), (int, float)) else "—"
        lines.append(f"🇮🇩 <b>IHSG</b> (BEI resmi, {comp.get('Date', '')[:10]})")
        for key_lbl, key in [("Penutupan", "IndexClose"), ("Buka", "IndexOpen"),
                             ("Tinggi", "IndexHigh"), ("Rendah", "IndexLow"),
                             ("Perubahan", "IndexChange")]:
            lines.append(f"  {key_lbl}: {_num(key)}")
    up = bandar.top_movers(rows, n=4)
    dn = bandar.top_movers(rows, n=4, asc=True)

    def _mv(rs):
        return "\n".join(
            f"  <b>{r['StockCode']}</b> {bandar.to_pct(r):+.1f}% "
            f"vol {r.get('Volume',0):,.0f}" for r in rs)
    lines.append("\n🔥 Top gainer:\n" + _mv(up))
    lines.append("\n💀 Top loser:\n" + _mv(dn))
    lines.append("\n⚠️ Data BEI resmi (bukan saran investasi)")
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML,
                         disable_web_page_preview=True, reply_markup=home_markup())


async def run_menu_action(update: Update, action: str, tickers: list[str]):
    """Jalankan satu aksi menu. Dipakai tombol maupun input teks."""
    if action == "analisis":
        await _do_analisis(update, tickers)
    elif action == "skor":
        await _do_skor(update, tickers)
    elif action == "berita":
        await _do_berita(update, tickers[0])
    elif action == "fundamental":
        await _do_fundamental(update, tickers[0])
    elif action == "backtest":
        await _do_backtest(update, tickers[0])
    elif action == "beli":
        await _do_tanya(update, tickers[0], "beli")
    elif action == "jual":
        await _do_tanya(update, tickers[0], "jual")
    elif action == "tanya_beli":
        await _do_tanya(update, tickers[0], "beli")
    elif action == "tanya_jual":
        await _do_tanya(update, tickers[0], "jual")
    elif action == "tanya_umum":
        await _do_tanya(update, tickers[0], "umum")
    elif action == "saran":
        await _do_saran(update)
    elif action == "saran_list":
        await _do_saran(update, list_only=True)
    elif action == "watch_list":
        await _do_watch_list(update)
    elif action == "watch_add":
        await _do_watch_add(update, tickers[0])
    elif action == "watch_del":
        await _do_watch_del(update, tickers[0])
    elif action == "alert_list":
        await _do_alert_list(update)
    elif action == "alert_add":
        await _do_alert_add(update, tickers[0])
    elif action == "bandar":
        await _do_bandar(update)
    else:
        await update.effective_message.reply_text(
            "Aksi tidak dikenal. Buka /start untuk menu.",
            reply_markup=home_markup())


async def _safe_edit(msg, text, **kw):
    """Edit pesan menu; abaikan 'Message is not modified' (double-tap jinak)."""
    try:
        await msg.edit_text(text, **kw)
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


async def menu_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if ALLOWED_IDS and q.from_user.id not in ALLOWED_IDS:
        await q.answer("⛔ Bot ini privat.", show_alert=True)
        return
    data = q.data or ""
    chat_id = update.effective_chat.id
    msg = q.message
    if data == "menu":
        PENDING.pop(chat_id, None)
        await _safe_edit(msg, _menu_text(), parse_mode=ParseMode.HTML,
                            reply_markup=main_menu_markup())
        return
    if data.startswith("pick:"):
        action = data.split(":", 1)[1]
        label = NEED_TICKER.get(action, action)
        PENDING.pop(chat_id, None)
        await _safe_edit(msg, f"{label} — pilih emiten:",
                            reply_markup=ticker_pick_markup(
                                action, store.get_watchlist(chat_id)))
        return
    if data.startswith("manual:"):
        action = data.split(":", 1)[1]
        PENDING[chat_id] = action
        await _safe_edit(msg, _MANUAL_PROMPT.get(action, "Ketik kode emiten:"),
                            parse_mode=ParseMode.HTML,
                            reply_markup=InlineKeyboardMarkup(
                                [[InlineKeyboardButton("⬅️ Batal", callback_data="menu")]]))
        return
    if data.startswith("tick:"):
        _, action, ticker = data.split(":")
        PENDING.pop(chat_id, None)
        await _safe_edit(msg, f"⏳ Memproses <b>{ticker}</b>...",
                            parse_mode=ParseMode.HTML, reply_markup=home_markup())
        await run_menu_action(update, action, [ticker])
        return
    if data.startswith("refresh:"):
        _, action, ticker = data.split(":")
        PENDING.pop(chat_id, None)
        await _safe_edit(msg, f"⏳ Memperbarui <b>{ticker}</b>...",
                            parse_mode=ParseMode.HTML, reply_markup=home_markup())
        await run_menu_action(update, action, [ticker])
        return
    if data.startswith("run:"):
        action = data.split(":", 1)[1]
        if action == "watch":
            await _safe_edit(msg, "👀 <b>Watchlist</b> — pilih:",
                                parse_mode=ParseMode.HTML,
                                reply_markup=watch_menu_markup())
        elif action == "alert":
            await _safe_edit(msg, "🔔 <b>Alert</b> — pilih:",
                                parse_mode=ParseMode.HTML,
                                reply_markup=alert_menu_markup())
        elif action == "help":
            await _safe_edit(msg, _menu_text(), parse_mode=ParseMode.HTML,
                                reply_markup=main_menu_markup())
        elif action == "saran":
            await _safe_edit(msg, "🔍 Memindai emiten, 3-4 menit...",
                                reply_markup=home_markup())
            await run_menu_action(update, "saran", [])
        elif action == "bandar":
            await _safe_edit(msg, "⏳ Mengambil data BEI...",
                                reply_markup=home_markup())
            await run_menu_action(update, "bandar", [])
        else:
            await run_menu_action(update, action, [])
        return


async def menu_text_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Teks biasa: hanya diproses kalau chat sedang menunggu input emiten,
    atau kalau teksnya pertanyaan beli/jual ('BBCA beli gak?')."""
    chat_id = update.effective_chat.id
    action = PENDING.get(chat_id)
    if not action:
        if ALLOWED_IDS and update.effective_user.id not in ALLOWED_IDS:
            return
        parsed = parse_tanya(update.effective_message.text or "")
        if parsed:
            focus, ticker = parsed
            if ticker:
                await _do_tanya(update, ticker, focus)
            else:
                pick_action = "beli" if focus != "jual" else "jual"
                label = "🛒 Beli" if pick_action == "beli" else "💰 Jual"
                await update.effective_message.reply_text(
                    f"{label} — pilih emiten:",
                    reply_markup=ticker_pick_markup(
                        pick_action, store.get_watchlist(chat_id)))
        return
    if ALLOWED_IDS and update.effective_user.id not in ALLOWED_IDS:
        PENDING.pop(chat_id, None)
        return
    text = (update.effective_message.text or "").strip().upper()
    tickers = [t for t in text.replace(",", " ").split() if t]
    if not tickers or not all(t.isalnum() and len(t) <= 6 for t in tickers):
        await update.effective_message.reply_text(
            "❌ Kode emiten tidak valid. Contoh: <code>BBCA</code> (atau <code>BBCA TLKM</code>).",
            parse_mode=ParseMode.HTML)
        return
    PENDING.pop(chat_id, None)
    await run_menu_action(update, action, tickers[:5])


def _menu_text() -> str:
    return (
        "👋 Halo! Aku <b>SahamBuddy</b> — bot analisis saham Indonesia "
        "(teknikal + fundamental + sentimen berita + data resmi BEI).\n\n"
        "Pilih dari tombol di bawah — atau ketik perintah langsung, mis. "
        "<code>/analisis BBCA</code>.\n\n"
        "⚠️ Bukan saran investasi — selalu DYOR!")


async def _post_init(app: Application):
    """Daftarkan daftar perintah di menu '/' Telegram."""
    from telegram import BotCommand
    await app.bot.set_my_commands([
        BotCommand("start", "Menu utama (tombol interaktif)"),
        BotCommand("menu", "Menu utama (tombol interaktif)"),
        BotCommand("analisis", "Analisis emiten, mis. /analisis BBCA"),
        BotCommand("skor", "Ringkasan skor cepat"),
        BotCommand("berita", "Berita + sentimen emiten"),
        BotCommand("fundamental", "PER/PBV/ROE/dividen/DER"),
        BotCommand("saran", "Kandidat BUY harian + paper trading"),
        BotCommand("beli", "Tanya: layak beli? mis. /beli BBCA"),
        BotCommand("jual", "Tanya: layak jual? mis. /jual BBCA"),
        BotCommand("bandar", "Data resmi BEI / IHSG"),
        BotCommand("backtest", "Backtest 5 tahun emiten"),
        BotCommand("watch", "Kelola watchlist"),
        BotCommand("alert", "Kelola alarm harga"),
        BotCommand("help", "Panduan"),
    ])


async def cmd_berita(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ringkasan berita terkini + skor sentimen lexicon Indonesia."""
    args = tokens(update)
    if not args:
        await update.effective_message.reply_text(
            "📰 Berita — pilih emiten:",
            reply_markup=ticker_pick_markup(
                "berita", store.get_watchlist(update.effective_chat.id)))
        return
    t = args[0].upper()
    await update.effective_chat.send_action(ChatAction.TYPING)
    try:
        items = await asyncio.to_thread(news.fetch_news, t)
    except Exception:
        items = []
    if not items:
        await update.effective_message.reply_text(
            f"❌ Berita untuk {t} tidak tersedia — atau jaringan sedang bermasalah.")
        return
    sent = await asyncio.to_thread(news.news_sentiment, items)
    lines = [f"📰 <b>Berita {t}</b> — sentimen {sent['emoji']} "
             f"({sent['pct']:+.0f}%, {sent['n']} berita)"]
    for d in sent["details"][:7]:
        har = f" {d['sent']:+.0f}" if d["sent"] else ""
        age = f", {d['age_days']:.0f}h lalu" if d["age_days"] >= 1 else ""
        src = f" — <i>{esc(d['source'])}</i>" if d.get("source") else ""
        lines.append(f"\n{d['date']:%d %b} · <a href=\"{esc(d['link'] or 'https://news.google.com')}\">"
                     f"{esc(d['title'][:110])}</a>{har}{age}{src}")
    lines.append("\n\n🔒 Bukan saran investasi.")
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML,
        disable_web_page_preview=True)


async def cmd_fundamental(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Rincian fundamental emiten (PER, PBV, ROE, dividen, DER)."""
    args = tokens(update)
    if not args:
        await update.effective_message.reply_text(
            "💰 Fundamental — pilih emiten:",
            reply_markup=ticker_pick_markup(
                "fundamental", store.get_watchlist(update.effective_chat.id)))
        return
    t = args[0].upper()
    await update.effective_chat.send_action(ChatAction.TYPING)
    fund = await asyncio.to_thread(fundamental.fetch_fundamentals, t)
    if fund is None:
        await update.effective_message.reply_text(
            f"❌ Data fundamental untuk {t} tidak tersedia.")
        return
    sc = await asyncio.to_thread(fundamental.score_fundamentals, fund)
    rows = [
        f"🏛️ <b>Fundamental {t}</b> — {esc(fund.get('name'))}",
    ]
    if fund.get("sector"):
        rows.append(f"Sektor: {esc(fund['sector'])}")
    rows.append(f"Skor: <b>{sc['pct']:+d}%</b> {sc['emoji']} {sc['label']}\n")
    def _fmt(key, tpl, suffix=""):
        v = fund.get(key)
        return tpl % v + suffix if v is not None else "—"
    rows.append(f"  PER: {_fmt('per', '%.1f')}   PBV: {_fmt('pbv', '%.2f')}")
    rows.append(f"  EPS: {_fmt('eps', '%d')}   DER: {(_fmt('debt_eq', '%.0f%%') if fund.get('debt_eq') is not None else '—')}")
    if fund.get("roe") is not None:
        roe = fund["roe"] * 100 if fund["roe"] < 3 else fund["roe"]
        rows.append(f"  ROE: {roe:.1f}%")
    if fund.get("div_yield") is not None:
        dy = fund["div_yield"] * 100 if fund["div_yield"] < 1 else fund["div_yield"]
        rows.append(f"  Dividend yield: {dy:.1f}%")
    if fund.get("market_cap"):
        cap = fund["market_cap"] / 1e12
        rows.append(f"  Kap. pasar: Rp{cap:.1f} T")
    rows.append("\nCatatan skor:")
    rows.extend(f"  {esc(n)}" for n in sc["notes"])
    rows.append("\n🔒 Bukan saran investasi.")
    await update.effective_message.reply_text(
        "\n".join(rows), parse_mode=ParseMode.HTML)


async def gather_candidate(t: str) -> dict | None:
    """Analisis gabungan satu emiten: teknikal + fundamental + berita (threaded)."""
    try:
        res = await fetch_and_analyze(t)
    except ValueError:
        return None
    try:
        fund = await asyncio.to_thread(fundamental.fetch_fundamentals, t)
        fsc = await asyncio.to_thread(fundamental.score_fundamentals, fund) if fund else None
    except Exception:
        fund, fsc = None, None
    try:
        items = await asyncio.to_thread(news.fetch_news, t)
        nsent = await asyncio.to_thread(news.news_sentiment, items) if items else None
    except Exception:
        nsent = None
    return {"res": res, "fund": fsc, "nsent": nsent}


async def cmd_saran(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Pemindaian harian: kandidat BUY berline entry/SL/TP + daftar HINDARI."""
    args = tokens(update)
    sub = args[0].lower() if args else ""

    if sub in ("list", "posisi"):
        lines = saranmod.quotes_forgotten(update.effective_chat.id)
        await update.effective_message.reply_text(
            "\n".join(lines) or "Belum ada posisi latihan.",
            parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        return

    wl = store.get_watchlist(update.effective_chat.id)
    universe = (wl + [t for t in saranmod.DEFAULT_UNIVERSE if t not in wl])[:saranmod.MAX_SCAN]
    await update.effective_message.reply_text(
        f"🔍 Memindai {len(universe)} emiten (teknikal+fundamental+berita), 3-4 menit...")
    analyzed, latest = [], {}
    for t in universe:
        a = await gather_candidate(t)
        if a:
            analyzed.append(a)
            latest[a["res"]["ticker"]] = a["res"]["snap"]["close"]
        await asyncio.sleep(.2)
    if not analyzed:
        await update.effective_message.reply_text("❌ Data sumber error, coba lagi nanti.")
        return

    result = saranmod.pick(analyzed)
    saranmod.update_paper(update.effective_chat.id, latest)

    lines = [f"🧭 <b>RINGKASAN SARAN HARIAN — {pd.Timestamp.now(tz=WIB):%d %b %Y}</b>",
             "Skema cais: jual dibeli penutupan | SL 1×ATR | TP 2×ATR\n"]
    if result["picks"]:
        saranmod.open_paper(
            update.effective_chat.id, result["picks"],
            f"{pd.Timestamp.now(tz=WIB):%d %b}")
        lines.append("Kandidat BUY (urut PowerScore, tersimpan sebagai posisi latihan):")
        for c in result["picks"]:
            fund_txt = "—" if c["fund"] is None else f"{c['fund']:+.0f}"
            news_txt = "—" if c["news"] is None else f"{c['news']:+.0f}"
            lines.append(
                f"  {c['emoji']} <b>{c['ticker']}</b> power {c['power']:+d}% "
                f"(tek {c['tech']:+.0f}, fund {fund_txt}, berita {news_txt})\n"
                f"      Entry {saranmod.fmt(c['entry'])} | SL {saranmod.fmt(c['sl'])} | "
                f"TP {saranmod.fmt(c['tp'])}")
    else:
        lines.append("Tidak ada kandidat layak BUY hari ini "
                     "(semua skor tak mencukupi / terkena filter jebakan momentum).")
    lines.append("\n🚫 HINDARI (terlemah):")
    for c in result["avoid"]:
        lines.append(f"  {c['emoji']} <b>{c['ticker']}</b> {c['power']:+d}% — "
                     f"{'jebakan momentum' if c['ret20d'] > 15 else 'teknikal lemah'}")
    lines.append("\n⚠️ Alat bantu latihan, bukan saran formal. DYOR!")
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_bandar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Data resmi BEI: IHSG & sektor, plus info emiten (aliran asing bila tersedia)."""
    args = tokens(update)
    await update.effective_chat.send_action(ChatAction.TYPING)
    def _job():
        idx = bandar.get_index_summary()
        ticker = args[0] if args else None
        rows = bandar.get_stock_summary(bandar.last_trading_date(), max_stocks=200)
        return idx, ticker, rows
    try:
        idx, ticker, rows = await asyncio.to_thread(_job)
    except bandar.IDXTidakTersedia as e:
        # CF kadang boikot sementara — coba tetapkan jujur tanpa bohong
        await update.effective_message.reply_text(
            "🔒 <b>Data BEI (idx.co.id) kini diblokir Cloudflare</b> "
            "(IP server diperlakukan kurang manis). Coba lagi nanti atau dari server lain.\n"
            "<i>Alternatif cepat: /analisis TICKER (pakai Yahoo Finance, delayed 15 menit).</i>",
            parse_mode=ParseMode.HTML)
        log.warning("BEI blocked: %s", e)
        return

    comp = next((x for x in idx if x["IndexCode"] == "COMPOSITE"), None)
    lines = []
    if comp:
        def _num(k):
            return f"{comp.get(k):,.2f}" if isinstance(comp.get(k), (int, float)) else "—"
        lines.append(f"🇮🇩 <b>IHSG</b> (BEI resmi, {comp.get('Date', '')[:10]})")
        for key_lbl, key in [("Penutupan", "IndexClose"), ("Buka", "IndexOpen"),
                             ("Tinggi", "IndexHigh"), ("Rendah", "IndexLow"),
                             ("Perubahan", "IndexChange")]:
            lines.append(f"  {key_lbl}: {_num(key)}")
    if ticker:
        row = bandar.find_row(rows, ticker)
        if row:
            pct = bandar.to_pct(row)
            lines.append(f"\n📊 <b>{row['StockCode']}</b> — {esc(row.get('StockName',''))}")
            for lbl, key in [("Buka","OpenPrice"),("Tertinggi","HighestPrice"),
                             ("Terendah","LowestPrice"),("Tutup","ClosingPrice"),
                             ("Volume","Volume"),("Nilai","Value")]:
                v = row.get(key)
                lines.append(f"  {lbl}: {v:,.0f}" if isinstance(v, (int, float)) else f"  {lbl}: —")
            lines.append(f"  Perubahan: {pct:+.2f}%")
            fg = bandar.foreign_fields(row)
            if fg:
                lines.append(f"  Asing: {fg}")
        else:
            lines.append(f"\n❓ {ticker.upper()} tidak ditemukan di ringkasan "
                         "(dapat berlangsung: tidak jual-beli hari ini)")
    else:
        up = bandar.top_movers(rows, n=4)
        dn = bandar.top_movers(rows, n=4, asc=True)
        def _mv(rs):
            return "\n".join(
                f"  <b>{r['StockCode']}</b> {bandar.to_pct(r):+.1f}% "
                f"vol {r.get('Volume',0):,.0f}" for r in rs)
        lines.append("\n🔥 Top gainer:\n" + _mv(up))
        lines.append("\n💀 Top loser:\n" + _mv(dn))
    lines.append("\n⚠️ Data BEI resmi (bukan saran investasi)")
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


# ---------------------------------------------------------------- handlers

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        _menu_text(), parse_mode=ParseMode.HTML,
        reply_markup=main_menu_markup())


async def cmd_analisis(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = tokens(update)
    if not args:
        await update.effective_message.reply_text(
            "📊 Analisis — pilih emiten:",
            reply_markup=ticker_pick_markup(
                "analisis", store.get_watchlist(update.effective_chat.id)))
        return

    for t in args[:5]:  # max 5 emiten per permintaan
        try:
            res = await fetch_and_analyze(t)
        except ValueError as e:
            await update.effective_message.reply_text(f"❌ {e}", reply_markup=home_markup())
            continue

        # data pendukung fundamental & berita (diparalel, gagal = abaikan)
        fund_f = asyncio.to_thread(fundamental.fetch_fundamentals, t)
        news_f = asyncio.to_thread(news.fetch_news, t)
        try:
            fund, items = await asyncio.gather(fund_f, news_f, return_exceptions=True)
        except Exception:
            fund, items = None, None
        if isinstance(fund, Exception) or fund is None:
            fund, fsc = None, None
        else:
            fsc = await asyncio.to_thread(fundamental.score_fundamentals, fund)
        if isinstance(items, Exception) or not items:
            nsent = None
        else:
            nsent = await asyncio.to_thread(news.news_sentiment, items)

        await update.effective_message.reply_text(
            build_report(res, fsc, nsent), parse_mode=ParseMode.HTML,
            reply_markup=result_markup("analisis", t))

        path = os.path.join(TMP_DIR, f"chart_{int(time.time() * 1000)}.png")
        await asyncio.to_thread(build_chart, res["ticker"].upper(), res["ind"],
                                res["score"]["pct"], path)
        try:
            with open(path, "rb") as f:
                await update.effective_message.reply_photo(
                    photo=f, caption=f"📈 Chart {res['ticker']} (6 bulan terakhir)",
                    reply_markup=result_markup("analisis", t))
        finally:
            if os.path.exists(path):
                os.remove(path)


async def cmd_skor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = tokens(update)
    if not args:
        await update.effective_message.reply_text(
            "⚡ Skor — pilih emiten:",
            reply_markup=ticker_pick_markup(
                "skor", store.get_watchlist(update.effective_chat.id)))
        return
    lines = []
    for t in args[:8]:
        try:
            res = await fetch_and_analyze(t)
            lines.append(build_quick(res))
        except ValueError as e:
            lines.append(f"❌ {e}")
    await update.effective_message.reply_text(
        "\n".join(lines) or "Watchlist kosong.", parse_mode=ParseMode.HTML)


async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = tokens(update)
    chat_id = update.effective_chat.id
    if not args:
        # tampilkan watchlist ringkas
        wl = store.get_watchlist(chat_id)
        if not wl:
            await update.effective_message.reply_text(
                "👀 Watchlist masih kosong — pilih:",
                parse_mode=ParseMode.HTML, reply_markup=watch_menu_markup())
            return
        lines = [store_format_quick(wl, chat_id)]
        lines.append("Ganti detail dengan /skor nama-saham")
        await update.effective_message.reply_text(
            "\n".join(l for l in lines if l), parse_mode=ParseMode.HTML,
            reply_markup=watch_menu_markup())
        return

    sub, *rest = args
    sub = sub.lower()
    if sub in ("add", "tambah") and rest:
        ok, wl = store.watchlist_add(chat_id, rest[0].upper())
        prefix = "✅ Ditambahkan" if ok else "📌 Sudah ada di"
        await update.effective_message.reply_text(
            f"{prefix} watchlist: {', '.join(str(w) for w in wl)}")
    elif sub in ("remove", "hapus") and rest:
        ok, wl = store.watchlist_remove(chat_id, rest[0].upper())
        prefix = "🗑️ Dihapus" if ok else "❓ Tidak ditemukan di"
        await update.effective_message.reply_text(
            f"{prefix} watchlist. Sisanya: {', '.join(wl) or '(kosong)'}")
    elif sub in ("list",):
        wl = store.get_watchlist(chat_id)
        if wl:
            for t in wl:
                try:
                    res = await fetch_and_analyze(t)
                    await update.effective_message.reply_text(
                        build_quick(res), parse_mode=ParseMode.HTML)
                    await asyncio.sleep(.3)  # rate limit
                except ValueError as e:
                    await update.effective_message.reply_text(f"❌ {e}")
        else:
            await update.effective_message.reply_text("Watchlist kosong.")
    else:
        await update.effective_message.reply_text(
            "Format: /watch add TICKER — /watch list — /watch remove TICKER")


def store_format_quick(tickers: list[str], chat_id) -> str:
    return "Daftar watchlist kamu:\n" + "\n".join(f"  • {t}" for t in tickers)


async def cmd_alert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = tokens(update)
    chat_id = update.effective_chat.id
    if not args:
        await update.effective_message.reply_text(
            "🔔 Alert — pilih di bawah.\n"
            "Jenis: jump, drop, breakout, breakdown, oversold, overbought, "
            "ma_golden, ma_death, vol_spike.",
            reply_markup=alert_menu_markup())
        return

    sub, *rest = args
    sub = sub.lower()
    if sub in ("add", "buat") and rest:
        symbol = rest[0].upper()
        kinds = {k.lower() for k in rest[1:]} or {"jump", "drop", "breakout",
                                                   "oversold", "overbought",
                                                   "ma_golden", "ma_death"}
        invalid = kinds - alerts.ALL_KINDS
        if invalid:
            await update.effective_message.reply_text(
                f"❌ Jenis tidak dikenal: {', '.join(sorted(invalid))}. "
                f"Coba /alert untuk lihat daftar.")
            return
        saved = store.set_alert(chat_id, symbol, kinds)
        # sekalian pidahkan ke watchlist kalau belum
        store.watchlist_add(chat_id, symbol)
        kind_txt = ", ".join(sorted(saved))
        await update.effective_message.reply_text(
            f"🔔 Alert untuk <b>{symbol}</b> aktif: <code>{kind_txt}</code>\n"
            "Scanner mengecek tiap 30 menit saat pasar buka.",
            parse_mode=ParseMode.HTML)
    elif sub in ("remove", "hapus") and rest:
        ok = store.remove_alert(chat_id, rest[0].upper())
        await update.effective_message.reply_text(
            "🗑️ Alert dihapus." if ok else "❓ Alert untuk emiten itu tidak ada.")
    elif sub in ("list",):
        al = store.get_alerts(chat_id)
        if al:
            txt = "\n".join(
                f"  • <b>{s}</b>: {', '.join(sorted(k))}" for s, k in al.items())
            await update.effective_message.reply_text(
                f"Alert kamu:\n{txt}", parse_mode=ParseMode.HTML)
        else:
            await update.effective_message.reply_text("Belum ada alert.")
    else:
        await update.effective_message.reply_text("Format salah. Coba <code>/alert</code>.",
                                                  parse_mode=ParseMode.HTML)


async def cmd_backtest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = tokens(update)
    if not args:
        await update.effective_message.reply_text(
            "📈 Backtest — pilih emiten:\n"
            "Tiap hari 5 tahun terakhir bot berteori, lalu dicek hasil 20 hari kemudian.",
            reply_markup=ticker_pick_markup(
                "backtest", store.get_watchlist(update.effective_chat.id)))
        return
    await update.effective_message.reply_text(
        "⏳ Menghitung backtest 5 tahun + simulasi transaksi, tunggu 1-2 menit...")
    t = args[0].upper()
    parts = []
    try:
        bt = await asyncio.to_thread(btmod.backtest, t)
        parts.append(f"🧪 <b>Backtest statistik {t}</b>\n<pre>{btmod.summarize(bt)}</pre>")
    except ValueError as e:
        await update.effective_message.reply_text(f"❌ {e}")
        return
    try:
        import backtest_real
        stats = await asyncio.to_thread(backtest_real.run_backtest, t)
        parts.append("<b>Simulasi transaksi nyata</b> (buy sinyal BUY, SL 1×ATR, "
                     "TP 2×ATR, biaya 0,25%):")
        parts.append(
            f"<pre>"
            f"Return/thn      : {stats['Return (Ann.) [%]']:+.1f}%\n"
            f"Buy & hold      : {stats['Buy & Hold Return [%]']:+.1f}%\n"
            f"Transaksi       : {stats['# Trades']:.0f}\n"
            f"Win-rate        : {stats['Win Rate [%]']:.1f}%\n"
            f"Profit factor   : {stats['Profit Factor']:.2f}\n"
            f"Max drawdown    : {stats['Max. Drawdown [%]']:.1f}%"
            f"</pre>")
    except ValueError as e:
        parts.append(f"⚠️ Simulasi transaksi dilewati: {e}")
    except Exception:
        log.exception("backtest_real gagal utk %s", t)
        parts.append("⚠️ Simulasi transaksi gagal dijalankan (library backtesting.py).")
    parts.append("⚠️ Skor = deskripsi kondisi saat itu, bukan ramalan. "
                 "Uji dengan beberapa emiten sebelum menarik kesimpulan.")
    await update.effective_message.reply_text(
        "\n".join(parts), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_beli(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Jawab 'layak beli gak?' untuk satu emiten."""
    args = tokens(update)
    if not args:
        await update.effective_message.reply_text(
            "🛒 Beli — pilih emiten:",
            reply_markup=ticker_pick_markup(
                "beli", store.get_watchlist(update.effective_chat.id)))
        return
    await _do_tanya(update, args[0], "beli")


async def cmd_jual(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Jawab 'layak jual gak?' untuk satu emiten (sudut pandang yang pegang)."""
    args = tokens(update)
    if not args:
        await update.effective_message.reply_text(
            "💰 Jual — pilih emiten:",
            reply_markup=ticker_pick_markup(
                "jual", store.get_watchlist(update.effective_chat.id)))
        return
    await _do_tanya(update, args[0], "jual")




# ---------------------------------------------------------------- scanner

async def scan_alerts(app: Application):
    """Job berkala: periksa semua alert aktif di semua chat."""
    all_store = store._load()
    dt = pd.Timestamp.now(tz=WIB)
    if not alerts.market_hours(dt.hour, dt.minute, dt.weekday()):
        return  # hanya jam bursa

    cache: dict[str, dict] = {}
    for chat_id, c in all_store.items():
        for symbol, kinds in c.get("alerts", {}).items():
            try:
                if symbol not in cache:
                    df = await asyncio.to_thread(data.fetch_history, symbol)
                    if df is None:
                        continue
                    cache[symbol] = await asyncio.to_thread(analysis.compute_indicators, df)
            except Exception:
                log.exception("scan error %s", symbol)
                continue
            ind = cache[symbol]
            triggered = alerts.check_triggers(ind)
            hit = triggered & set(kinds)
            if hit:
                snap = analysis.last_snapshot(ind)
                sc = analysis.score_stock(ind)
                try:
                    await app.bot.send_message(
                        int(chat_id),
                        alerts.build_alert_message(symbol, sorted(hit), snap, sc),
                        parse_mode=ParseMode.HTML)
                except Exception:
                    log.exception("gagal kirim alert ke chat %s", chat_id)
                await asyncio.sleep(.3)
        # paper trades (/saran) — posisi latihan: cek SL/TP
        if c.get("paper"):
            paper_tickers = [t for t, r in c.get("paper", {}).items() if r.get("status") == "open"]
            latest = {}
            for t in paper_tickers:
                try:
                    df = await asyncio.to_thread(data.fetch_history, t, "3mo")
                    if df is not None:
                        latest[t] = float(df["close"].iloc[-1])
                except Exception:
                    log.exception("paper fetch gagal %s", t)
            for msg in saranmod.update_paper(chat_id, latest):
                try:
                    await app.bot.send_message(int(chat_id), msg, parse_mode=ParseMode.HTML)
                except Exception:
                    log.exception("gagal kirim update paper ke %s", chat_id)


# ---------------------------------------------------------------- main

async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    """[kendala 4] setiap crash handler: log penuh + balasan ramah ke user."""
    err = ctx.error
    # double-tap tombol / pesan sudah diubah: jinak, jangan ganggu user
    if isinstance(err, BadRequest) and "message is not modified" in str(err).lower():
        return
    log.error("handler kena error: %r", err,
              exc_info=err if isinstance(err, BaseException) else None)
    if update and getattr(update, "effective_message", None):
        try:
            await ctx.bot.send_message(
                update.effective_chat.id,
                "⚠️ Ups, ada kesalahan internal saat memproses permintaan ini. "
                "Coba ulangi; kalau sering terjadi lihat log — pemilik bot tahu.")
        except Exception:
            pass


def main():
    """Entry point: python bot.py"""
    token = config.BOT_TOKEN or os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "BOT_TOKEN belum diset.\n"
            "  1. Chat @BotFather di Telegram\n"
            "  2. Kirim /newbot, ikuti isian\n"
            "  3. Jalankan:  BOT_TOKEN=123:abc python bot.py")
    app = Application.builder().token(token).post_init(_post_init).build()

    # [kendala 5] kalau ALLOWED_USER_IDS diisi, hanya user itu yang boleh memakai bot
    user_ids: list[int] = []
    for part in (os.getenv("ALLOWED_USER_IDS", "") or "").replace(" ", "").split(","):
        if part:
            try:
                user_ids.append(int(part))
            except ValueError:
                raise SystemExit(
                    f"ALLOWED_USER_IDS berisi '{part}' — harus berupa angka id user "
                    "Telegram dipisah koma (cek id-mu dengan @userinfobot)")
    user_filter = filters.User(user_id=user_ids) if user_ids else None
    if not user_ids:
        log.warning("ALLOWED_USER_IDS kosong — bot bisa dipakai siapa saja. "
                    "Isi env ALLOWED_USER_IDS (dipisah koma) untuk membatasi.")

    reg = lambda names, fn: app.add_handler(CommandHandler(names, fn, filters=user_filter))
    reg(["start", "menu"], cmd_start)
    reg("help", cmd_start)
    reg(["analisa", "analysis", "analisis"], cmd_analisis)
    reg("skor", cmd_skor)
    reg(["berita", "news"], cmd_berita)
    reg(["fundamental", "fundi"], cmd_fundamental)
    reg("backtest", cmd_backtest)
    reg("watch", cmd_watch)
    reg(["saran", "recommendasi"], cmd_saran)
    reg(["beli", "buy"], cmd_beli)
    reg(["jual", "sell"], cmd_jual)
    reg(["bandar", "ihsg"], cmd_bandar)
    reg("alert", cmd_alert)
    ALLOWED_IDS.update(user_ids)
    app.add_handler(CallbackQueryHandler(menu_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_text_input))

    job_queue = app.job_queue
    job_queue.run_repeating(scan_alerts, interval=config.ALERT_SCAN_MINUTES * 60, first=10)

    log.info("Bot jalan. Tekan Ctrl+C untuk berhenti.")
    app.add_error_handler(on_error)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
