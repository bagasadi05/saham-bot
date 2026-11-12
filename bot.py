"""SahamBuddy — bot Telegram analisis saham Indonesia.

Perintah:
  /start, /menu, /help     - menu utama / panduan
  /beli BBCA / /jual BBCA  - tanya layak beli/jual (atau ketik bebas:
                            "BBCA beli gak?", "jual TLKM?")
  /analisis BBCA           - laporan teknikal lengkap + chart
  /sinyal [beli|jual|KODE] - sinyal BELI/JUAL seluruh pasar IDX
  /scan                    - pindai ulang seluruh pasar (±2–3 menit)
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
import functools
import html
import json
import logging
import os
import re
import sys
import time
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (AIORateLimiter, Application, CallbackQueryHandler,
                          CommandHandler, ContextTypes, MessageHandler,
                          filters)
from telegram.error import BadRequest, NetworkError, TimedOut

import config
from core import (alerts, analysis, bandar, banding, btcache, data,
                  logsetup,
                  data_quality, fundamental, learn,
                  marketcal, news,
                  powerscore,
                  scorecard, screener, saran as saranmod, store)  # noqa: F401 (dipatch tes)
from core.chart import build_chart
from core import textutil
from core.textutil import split_message
from core.guards import (_pending_clear,
                        _pending_get, _pending_set,
                        cooldown_ok)
from core import guards as core_guards
# Alias state yang sama: tes memanipulasi bot.PENDING / bot._last_used.
PENDING = core_guards.PENDING
PENDING_TTL = core_guards.PENDING_TTL
_pending_prune = core_guards._pending_prune
_pending_ts = core_guards._pending_ts
_last_used = core_guards._last_used
from core.reports import _trend_line, build_report, esc, fmt_rp

import backtest as btmod
from handlers import cek as cek_handler
from handlers import market as market_handler
from handlers import sektor as sektor_handler
from handlers import sinyal as sinyal_handler
from handlers import portfolio as portfolio_handler

# [kendala 3, fix permanen] httpx bengkak saat NO_PROXY berisi IPv6 berkurung
# seperti "[::1]" — (langsung) bersihkan sebelum client dibangun
_np = os.environ.get("NO_PROXY", os.environ.get("no_proxy", ""))
if "[::1]" in _np or "[" in _np:
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "localhost,127.0.0.1,::1"

# Log teks (default) atau JSON satu baris saat LOG_JSON=1 — lihat core/logsetup.py
logsetup.setup_logging()
log = logging.getLogger("sahambuddy")

WIB = ZoneInfo("Asia/Jakarta")

# Statistik ringan untuk /status (memori saja — hilang saat restart)
STATS = {"commands": 0, "errors": 0, "ms": 0.0}
STARTED_AT = time.time()

# Admin /status: env ADMIN_IDS; kosong = semua pemakai bot (bot pribadi)
ADMIN_IDS = {int(x) for x in (os.getenv("ADMIN_IDS", "") or "").replace(" ", "").split(",")
             if x.isdigit()}
TMP_DIR = config.TMP_DIR
os.makedirs(TMP_DIR, exist_ok=True)

# Batas watchlist per chat — membatasi biaya pemindaian /saran
MAX_WATCH = 30

# [Tier B] Pemindaian /saran paralel berbatas: satu emiten = 3-4 permintaan
# jaringan (harga, fundamental, berita). 4 sekaligus memangkas LQ45 dari ±5
# menit jadi ±1,5 menit tanpa memicu rate-limit Yahoo.
SCAN_CONCURRENCY = max(1, int(os.getenv("SARAN_CONCURRENCY", "4") or 1))
SCAN_PAUSE = 0.15


# ---------------------------------------------------------------- helpers


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


# --------------------------------------------------- kirim pesan yang tahan blip
# Proxy/VPS bisa memutus satu permintaan Telegram tanpa memutus sesi. Tanpa
# retry, laporan panjang hilang di tengah dan user hanya melihat "kesalahan
# internal". Hanya gangguan jaringan yang diulang: `BadRequest` (mis. HTML
# salah) TIDAK akan berubah kalau dikirim ulang — dan di python-telegram-bot
# `BadRequest` adalah subclass `NetworkError`, jadi harus dikecualikan
# eksplisit.
_SEND_RETRY_ERRORS = (NetworkError, TimedOut)
_SEND_FINAL_ERRORS = (BadRequest,)


async def send_with_retry(call, *, tries: int = 3, base: float = 0.7,
                          what: str = "kirim pesan"):
    """Jalankan `call()` (callable → awaitable) dengan retry backoff ringan.

    `call` harus MEMBUAT permintaan baru tiap percobaan (bukan coroutine yang
    sama), supaya unggahan berkas ikut diulang dengan file yang masih terbuka.
    """
    last: Exception | None = None
    for i in range(max(1, tries)):
        try:
            return await call()
        except _SEND_RETRY_ERRORS as e:
            if isinstance(e, _SEND_FINAL_ERRORS):
                raise
            last = e
            if i + 1 >= tries:
                break
            delay = base * (2 ** i)
            log.warning("%s gagal (%s) — ulang dalam %.1f dtk (%d/%d)",
                        what, e, delay, i + 2, tries)
            await asyncio.sleep(delay)
    assert last is not None
    raise last


async def reply_long(message, text: str, **kwargs) -> None:
    """Kirim pesan panjang: Telegram menolak pesan >4096 karakter.

    Dibagi per baris; kalau satu bagian ditolak parser HTML, bagian itu dikirim
    ulang sebagai teks polos supaya laporan panjang (/saran lq45) tetap sampai.
    Setiap bagian diulang saat NetworkError/TimedOut (P12).
    """
    kwargs.setdefault("parse_mode", ParseMode.HTML)
    for part in split_message(text):
        try:
            await send_with_retry(
                lambda p=part: message.reply_text(p, **kwargs),
                what="kirim laporan")
        except Exception:
            log.warning("bagian pesan ditolak parser HTML — dikirim sebagai teks polos")
            await send_with_retry(
                lambda p=part: message.reply_text(re.sub(r"<[^>]+>", "", p)),
                what="kirim laporan (teks polos)")


def timed(fn):
    """Catat durasi untuk /status; saat LOG_JSON=1 tulis satu baris terstruktur."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        t0 = time.monotonic()
        STATS["commands"] += 1
        ok = True
        try:
            return await fn(*args, **kwargs)
        except Exception:
            ok = False
            raise
        finally:
            dur = (time.monotonic() - t0) * 1000
            STATS["ms"] += dur
            if logsetup.json_enabled():
                update = args[0] if args else None
                chat = getattr(getattr(update, "effective_chat", None), "id",
                               None)
                log.info("command", extra={"cmd": fn.__name__,
                                           "chat_id": chat,
                                           "duration_ms": round(dur, 1),
                                           "ok": ok})

    return wrapper


# ------------------------------------------------------- rate limit per user
# Command mahal (pemindaian puluhan emiten, backtest 5 tahun) tidak boleh
# dipicu beruntun oleh satu akun: bot bisa dipakai siapa saja kalau
# ALLOWED_USER_IDS kosong, dan tiap panggilan memakan menit CPU + puluhan
# permintaan jaringan.
async def fetch_and_analyze(ticker: str) -> dict:
    df = await asyncio.to_thread(data.fetch_history, ticker)
    if df is None:
        raise ValueError(f"Data untuk '{ticker}' tidak ditemukan (kursor tidak valid atau koneksi gagal).")
    res = await asyncio.to_thread(analysis.analyze, ticker, df)
    # ticker yang tampil di laporan: kode emiten saja ("BBCA.JK" -> "BBCA")
    res["ticker"] = data.normalize_ticker(ticker).replace(".JK", "")
    # [kendala 1] kalau data berasal dari cache (Yahoo 429 dsb), sampaikan jujur
    res["stale_note"] = df.attrs.get("stale_note", "")
    # cache bisa berumur hari-an (libur panjang / Yahoo mati) — sebutkan umurnya
    as_of = df.attrs.get("as_of") or ""
    if as_of:
        try:
            gap = marketcal.trading_days_between(
                date.fromisoformat(as_of), marketcal.now_wib().date())
        except ValueError:
            gap = 0
        if gap > 5:
            res["stale_note"] += (
                f"⚠️ <i>Data terakhir {esc(as_of)} ({gap} hari bursa lalu) — "
                "angka mungkin sudah tidak berlaku.</i>\n")
    res["quality_note"] = data_quality.summary_line(df.attrs.get("quality"))
    notes = res.get("fallbacks") or []
    res["fallback_note"] = (
        f"🧩 <i>Indikator cadangan: {esc('; '.join(notes))}.</i>\n" if notes else "")
    return res


# ---------------------------------------------------------------- report

def build_quick(res: dict) -> str:
    snap, sc = res["snap"], res["score"]
    # [kendala 1] tanda singkat kalau data dari cache, bukan live
    note = " ⚠️(cache)" if res.get("stale_note") else ""
    return (
        f"{sc['emoji']} <b>{esc(res['ticker'])}</b> {fmt_rp(snap['close'])} "
        f"({snap['ret1d']:+.2f}%) — {sc['pct']:+.0f}% {esc(sc['rating'])}{note}"
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


def _weekly_from_daily(ind: pd.DataFrame, score_pct: float) -> dict | None:
    """Hitung tren mingguan dari data harian (tanpa panggilan Yahoo ekstra bila cukup >=20 pekan)."""
    if ind is None or "close" not in ind.columns or not isinstance(ind.index, pd.DatetimeIndex):
        return None
    w_close = ind["close"].resample("W-FRI").last().dropna()
    if len(w_close) < 20:
        return None
    return analysis.weekly_trend(w_close.to_frame(name="close"), score_pct)


def build_verdict(res: dict, fund: dict | None, news_sent: dict | None,
                  focus: str, bt_summary: dict | None = None,
                  weekly: dict | None = None) -> str:
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

    sym_html = esc(t)
    title = {"beli": f"🛒 <b>Beli {sym_html}?</b>",
             "jual": f"💰 <b>Jual {sym_html}?</b>",
             "umum": f"⚖️ <b>Beli atau jual {sym_html}?</b>"}[focus]

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

    bt_line = btcache.format_verdict_backtest(bt_summary, t)
    trap = powerscore.trap_warning(snap.get("ret20d"), snap.get("ret1d"))
    trap_txt = f"\n{trap}" if trap else ""

    return (
        f"{res.get('stale_note', '')}{title}\n"
        f"Harga: <b>{fmt_rp(close)}</b> "
        f"({snap['ret1d']:+.2f}% 1d, {snap['ret20d']:+.2f}% 20d) | "
        f"PowerScore <b>{pw['pct']:+d}% {pw['emoji']} {esc(pw['rating'])}</b>\n\n"
        f"{headline}{other}\n\n"
        f"<b>Kenapa:</b>\n{reasons_txt}\n\n"
        f"<b>Level penting:</b>\n"
        f"  Support: {fmt_rp(sr['support'])} | Resistance: {fmt_rp(sr['resistance'])}\n"
        f"  Kalau beli — SL: {fmt_rp(sl_beli)} (1×ATR) | "
        f"TP: {fmt_rp(tp_beli)} (2×ATR)\n"
        f"{bt_line}\n"
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
        [InlineKeyboardButton("📡 Sinyal", callback_data="run:sinyal"),
         InlineKeyboardButton("🎯 Paket Beli", callback_data="run:paket"),
         InlineKeyboardButton("🔄 Scan", callback_data="run:scan")],
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
    "alert_add": "Ketik kode emiten untuk alarm:",
}

# Daftar jenis alert di prompt selalu mengikuti core/alerts.py.
_MANUAL_PROMPT["alert_add"] = (
    "Ketik kode emiten untuk alarm (jenis default: "
    + ", ".join(sorted(alerts.ALL_KINDS)) + "):")


async def _do_analisis(update: Update, tickers: list[str]):
    if not await cooldown_ok(update, "analisis"):
        return
    msg = update.effective_message
    chat_id = update.effective_chat.id if update.effective_chat else None
    wl = store.get_watchlist(chat_id) if chat_id is not None else []
    mkt = await asyncio.to_thread(banding.market_context)
    for t in tickers[:5]:
        try:
            res = await fetch_and_analyze(t)
        except ValueError as e:
            await msg.reply_text(f"❌ {esc(e)}", parse_mode=ParseMode.HTML, reply_markup=home_markup())
            continue
        sym = res["ticker"]
        fund_f = asyncio.to_thread(fundamental.fetch_fundamentals, t)
        news_f = asyncio.to_thread(news.fetch_news, t)
        weekly_f = asyncio.to_thread(data.fetch_weekly, t)
        try:
            fund, items, weekly_df = await asyncio.gather(
                fund_f, news_f, weekly_f, return_exceptions=True)
        except Exception:
            fund, items, weekly_df = None, None, None
        if isinstance(fund, Exception) or fund is None:
            fund, fsc = None, None
        else:
            fsc = await asyncio.to_thread(fundamental.score_fundamentals, fund)
        if isinstance(items, Exception) or not items:
            nsent = None
        else:
            nsent = await asyncio.to_thread(news.news_sentiment, items)
        if isinstance(weekly_df, Exception) or weekly_df is None:
            res["weekly"] = None
        else:
            res["weekly"] = analysis.weekly_trend(weekly_df, res["score"]["pct"])
        try:
            res["ret1y"] = banding.year_return(res["ind"]["close"])
        except Exception:
            res["ret1y"] = None
        try:
            sector_ctx = await asyncio.to_thread(
                banding.sector_context, sym, (fund or {}).get("sector"), wl)
        except Exception:
            sector_ctx = None
        res["sector_ctx"] = sector_ctx
        mkt_ctx = {**mkt, "peers": sector_ctx} if (mkt and sector_ctx) else mkt
        res["mkt"] = mkt_ctx
        await reply_long(
            msg,
            build_report(res, fsc, nsent, mkt_ctx),
            reply_markup=result_markup("analisis", sym),
        )
        os.makedirs(TMP_DIR, exist_ok=True)
        path = os.path.join(TMP_DIR, f"chart_{sym}_{time.time_ns()}.png")
        try:
            await asyncio.to_thread(
                build_chart, sym.upper(), res["ind"], res["score"]["pct"], path)
            def _send_chart(_path=path, _sym=sym):
                # buka ulang tiap percobaan: handle yang sudah terpakai tidak
                # bisa dikirim ulang oleh retry
                with open(_path, "rb") as f:
                    return msg.reply_photo(
                        photo=f,
                        caption=f"📈 Chart {_sym} (6 bulan terakhir)",
                        reply_markup=result_markup("analisis", _sym),
                    )

            await send_with_retry(_send_chart, what="kirim chart")
        except Exception:
            log.exception("chart gagal utk %s", sym)
        finally:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass


async def _do_skor(update: Update, tickers: list[str], from_menu: bool = False):
    msg = update.effective_message
    args = tickers or store.get_watchlist(update.effective_chat.id) or ["BBCA"]
    lines = []
    for t in args[:8]:
        try:
            res = await fetch_and_analyze(t)
            lines.append(build_quick(res))
        except ValueError as e:
            lines.append(f"❌ {esc(e)}")
    await reply_long(
        msg,
        "\n".join(lines) or "Watchlist kosong.",
        reply_markup=home_markup() if from_menu else None,
    )


async def _do_berita(update: Update, ticker: str):
    msg = update.effective_message
    try:
        t = data.normalize_ticker(ticker).replace(".JK", "")
    except ValueError as e:
        await msg.reply_text(f"❌ {esc(e)}", parse_mode=ParseMode.HTML, reply_markup=home_markup())
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
            reply_markup=home_markup(),
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
    await reply_long(
        msg,
        "\n".join(lines),
        disable_web_page_preview=True,
        reply_markup=result_markup("berita", t),
    )


async def _do_fundamental(update: Update, ticker: str):
    msg = update.effective_message
    try:
        t = data.normalize_ticker(ticker).replace(".JK", "")
    except ValueError as e:
        await msg.reply_text(f"❌ {esc(e)}", parse_mode=ParseMode.HTML, reply_markup=home_markup())
        return
    await update.effective_chat.send_action(ChatAction.TYPING)
    try:
        fund = await asyncio.to_thread(fundamental.fetch_fundamentals, t)
    except ValueError as e:
        await msg.reply_text(f"❌ {esc(e)}", parse_mode=ParseMode.HTML, reply_markup=home_markup())
        return
    except Exception as e:
        log.warning("fundamental gagal %s: %s", t, e)
        await msg.reply_text(
            f"❌ Data fundamental untuk {esc(t)} tidak tersedia — "
            "sumber sedang bermasalah (rate-limit/jaringan). Coba lagi nanti.",
            parse_mode=ParseMode.HTML,
            reply_markup=home_markup(),
        )
        return
    if fund is None:
        await msg.reply_text(
            f"❌ Data fundamental untuk {esc(t)} tidak tersedia.",
            parse_mode=ParseMode.HTML,
            reply_markup=home_markup(),
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
    await reply_long(
        msg,
        "\n".join(rows),
        reply_markup=result_markup("fundamental", t),
    )


async def _do_backtest(update: Update, ticker: str, from_menu: bool = False):
    if not await cooldown_ok(update, "backtest"):
        return
    msg = update.effective_message
    try:
        t = data.normalize_ticker(ticker).replace(".JK", "")
    except ValueError as e:
        await msg.reply_text(f"❌ {esc(e)}", parse_mode=ParseMode.HTML, reply_markup=home_markup())
        return
    if not from_menu:
        await msg.reply_text(
            "⏳ Menghitung backtest 5 tahun + simulasi transaksi, tunggu 1-2 menit..."
        )
    parts = []
    try:
        bt = await asyncio.to_thread(btmod.backtest, t)
        parts.append(
            f"🧪 <b>Backtest statistik {esc(t)}</b>\n"
            f"<pre>{esc(btmod.summarize(bt))}</pre>"
        )
    except ValueError as e:
        await msg.reply_text(f"❌ {esc(e)}", parse_mode=ParseMode.HTML, reply_markup=home_markup())
        return
    try:
        import backtest_real
        stats = await asyncio.to_thread(backtest_real.run_backtest, t)
        summary = backtest_real.summarize_stats(t, stats)
        await asyncio.to_thread(btcache.save_summary, TMP_DIR, summary)

        def _f(v, tpl):
            return "—" if v is None else tpl.format(v)

        bh_note = (
            f" (total {summary['bh_total']:+.1f}%)"
            if summary.get("bh_total") is not None else ""
        )
        parts.append(
            f"<b>Simulasi transaksi nyata</b> (buy sinyal BUY, SL 1×ATR, "
            f"TP 2×ATR, biaya beli {backtest_real.BUY_FEE * 100:.2f}% / "
            f"jual {backtest_real.SELL_FEE * 100:.2f}%):"
        )
        parts.append(
            "<pre>"
            f"Return/thn      : {_f(summary['ret_ann'], '{:+.1f}%')}\n"
            f"Buy &amp; hold/thn  : {_f(summary['bh_ann'], '{:+.1f}%')}{bh_note}\n"
            f"Transaksi       : {summary['trades']}\n"
            f"Win-rate        : {_f(summary['win_rate'], '{:.1f}%')}\n"
            f"Profit factor   : {_f(summary['profit_factor'], '{:.2f}')}\n"
            f"Expectancy/trade: {_f(summary.get('expectancy'), '{:+.2f}%')}\n"
            f"Max drawdown    : {_f(summary['max_dd'], '{:.1f}%')}"
            "</pre>"
        )
    except ValueError as e:
        parts.append(f"⚠️ Simulasi transaksi dilewati: {esc(e)}")
    except Exception:
        log.exception("backtest_real gagal utk %s", t)
        parts.append("⚠️ Simulasi transaksi gagal dijalankan (library backtesting.py).")
    parts.append(
        "⚠️ Skor = deskripsi kondisi saat itu, bukan ramalan. "
        "Uji dengan beberapa emiten sebelum menarik kesimpulan."
    )
    await reply_long(
        msg,
        "\n".join(parts),
        disable_web_page_preview=True,
        reply_markup=result_markup("backtest", t),
    )


async def _do_tanya(update: Update, ticker: str, focus: str):
    """Jawab 'beli/jual gak?' untuk satu emiten (focus: beli/jual/umum)."""
    msg = update.effective_message
    try:
        res = await fetch_and_analyze(ticker)
    except ValueError as e:
        await msg.reply_text(f"❌ {esc(e)}", parse_mode=ParseMode.HTML, reply_markup=home_markup())
        return
    t = res["ticker"]
    fund_f = asyncio.to_thread(fundamental.fetch_fundamentals, t)
    news_f = asyncio.to_thread(news.fetch_news, t)
    bt_f = asyncio.to_thread(btcache.load_summary, TMP_DIR, t)
    try:
        fund, items, bt_sum = await asyncio.gather(
            fund_f, news_f, bt_f, return_exceptions=True)
    except Exception:
        fund, items, bt_sum = None, None, None
    if isinstance(fund, Exception) or fund is None:
        fund, fsc = None, None
    else:
        fsc = await asyncio.to_thread(fundamental.score_fundamentals, fund)
    if isinstance(items, Exception) or not items:
        nsent = None
    else:
        nsent = await asyncio.to_thread(news.news_sentiment, items)
    if isinstance(bt_sum, Exception):
        bt_sum = None
    weekly = _weekly_from_daily(res["ind"], res["score"]["pct"])
    if weekly is None:
        try:
            wdf = await asyncio.to_thread(data.fetch_weekly, t)
            weekly = analysis.weekly_trend(wdf, res["score"]["pct"])
        except Exception:
            weekly = None
    await reply_long(
        msg,
        build_verdict(res, fsc, nsent, focus, bt_sum, weekly=weekly),
        reply_markup=result_markup(f"tanya_{focus}", t),
    )


async def _do_saran(update: Update, sub: str = "", list_only: bool = False,
                    from_menu: bool = False):
    msg = update.effective_message
    chat_id = update.effective_chat.id
    if list_only or sub in ("list", "posisi"):
        lines = saranmod.paper_summary(chat_id)
        await reply_long(
            msg,
            "\n".join(lines) or "Belum ada posisi latihan.",
            disable_web_page_preview=True,
            reply_markup=home_markup() if from_menu else None,
        )
        return
    if not await cooldown_ok(update, "saran"):
        return
    if sub == "lq45":
        if len(saranmod.LQ45) < 30:
            await msg.reply_text("❌ Daftar LQ45 kosong.")
            return
        ok = await msg.reply_text(
            f"⏳ Memindai seluruh LQ45 ({len(saranmod.LQ45)} emiten) — "
            "kira-kira 1-2 menit; progres dikirim tiap 10 emiten…"
        )

        async def _prog(done, total):
            try:
                await ok.edit_text(f"⏳ LQ45: {done}/{total} emiten diproses…")
            except Exception:
                pass

        txt = await saran_scan_text(chat_id, saranmod.LQ45, _prog)
    else:
        wl = store.get_watchlist(chat_id)
        universe = (wl + [t for t in saranmod.DEFAULT_UNIVERSE if t not in wl])[:saranmod.MAX_SCAN]
        if not from_menu:
            await msg.reply_text(
                f"🔍 Memindai {len(universe)} emiten (teknikal+fundamental+berita), 1-2 menit..."
            )
        txt = await saran_scan_text(chat_id, universe)
    if txt is None:
        await msg.reply_text(
            "❌ Data sumber error, coba lagi nanti.",
            reply_markup=home_markup() if from_menu else None,
        )
        return
    await reply_long(
        msg,
        txt,
        disable_web_page_preview=True,
        reply_markup=home_markup() if from_menu else None,
    )


async def _do_watch_list(update: Update):
    msg = update.effective_message
    chat_id = update.effective_chat.id
    wl = store.get_watchlist(chat_id)
    if not wl:
        await msg.reply_text(
            "Watchlist masih kosong. Tambahkan dulu lewat tombol ➕ Tambah.",
            reply_markup=watch_menu_markup(),
        )
        return
    lines = [store_format_quick(wl)]
    for t in wl:
        try:
            res = await fetch_and_analyze(t)
            lines.append(build_quick(res))
        except ValueError as e:
            lines.append(f"❌ {esc(e)}")
        await asyncio.sleep(.3)
    await reply_long(msg, "\n".join(lines), reply_markup=watch_menu_markup())


async def _do_watch_add(update: Update, ticker: str, from_menu: bool = True):
    msg = update.effective_message
    chat_id = update.effective_chat.id
    try:
        symbol = data.normalize_ticker(ticker).replace(".JK", "")
    except ValueError as e:
        await msg.reply_text(
            f"❌ {esc(e)}",
            parse_mode=ParseMode.HTML,
            reply_markup=watch_menu_markup() if from_menu else None,
        )
        return
    wl_now = store.get_watchlist(chat_id)
    if symbol not in wl_now and len(wl_now) >= MAX_WATCH:
        await msg.reply_text(
            f"❌ Watchlist maksimal {MAX_WATCH} emiten. Hapus dulu: "
            "<code>/watch remove TICKER</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=watch_menu_markup() if from_menu else None,
        )
        return
    ok, wl = store.watchlist_add(chat_id, symbol)
    prefix = "✅ Ditambahkan" if ok else "📌 Sudah ada di"
    await msg.reply_text(
        f"{prefix} watchlist: {esc(', '.join(wl))}",
        parse_mode=ParseMode.HTML,
        reply_markup=watch_menu_markup() if from_menu else None,
    )


async def _do_watch_del(update: Update, ticker: str, from_menu: bool = True):
    msg = update.effective_message
    try:
        symbol = data.normalize_ticker(ticker).replace(".JK", "")
    except ValueError:
        symbol = ticker.strip().upper().replace(".JK", "")
    ok, wl = store.watchlist_remove(update.effective_chat.id, symbol)
    prefix = "🗑️ Dihapus" if ok else "❓ Tidak ditemukan di"
    await msg.reply_text(
        f"{prefix} watchlist. Sisanya: {esc(', '.join(wl)) or '(kosong)'}",
        parse_mode=ParseMode.HTML,
        reply_markup=watch_menu_markup() if from_menu else None,
    )


async def _do_alert_list(update: Update, from_menu: bool = True):
    msg = update.effective_message
    al = store.get_alerts(update.effective_chat.id)
    if al:
        txt = "\n".join(
            f"  • <b>{esc(s)}</b>: {esc(', '.join(sorted(k)))}" for s, k in al.items()
        )
        await msg.reply_text(
            f"Alert kamu:\n{txt}",
            parse_mode=ParseMode.HTML,
            reply_markup=alert_menu_markup() if from_menu else None,
        )
    else:
        await msg.reply_text(
            "Belum ada alert.",
            reply_markup=alert_menu_markup() if from_menu else None,
        )


async def _do_alert_add(update: Update, ticker: str, kinds: set[str] | None = None,
                        from_menu: bool = True):
    msg = update.effective_message
    chat_id = update.effective_chat.id
    try:
        symbol = data.normalize_ticker(ticker).replace(".JK", "")
    except ValueError as e:
        await msg.reply_text(
            f"❌ {esc(e)}",
            parse_mode=ParseMode.HTML,
            reply_markup=alert_menu_markup() if from_menu else None,
        )
        return
    if kinds is None:
        # Sumber kebenaran tunggal: jangan hardcode daftar yang bisa tertinggal.
        kinds = set(alerts.ALL_KINDS)
    invalid = kinds - alerts.ALL_KINDS
    if invalid:
        await msg.reply_text(
            f"❌ Jenis tidak dikenal: {esc(', '.join(sorted(invalid)))}. "
            "Coba /alert untuk lihat daftar.",
            parse_mode=ParseMode.HTML,
            reply_markup=alert_menu_markup() if from_menu else None,
        )
        return
    saved = store.set_alert(chat_id, symbol, kinds)
    wl_now = store.get_watchlist(chat_id)
    if symbol in wl_now or len(wl_now) < MAX_WATCH:
        store.watchlist_add(chat_id, symbol)
    kind_txt = ", ".join(sorted(saved))
    await msg.reply_text(
        f"🔔 Alert untuk <b>{esc(symbol)}</b> aktif: <code>{esc(kind_txt)}</code>\n"
        "Scanner mengecek tiap 30 menit saat pasar buka.",
        parse_mode=ParseMode.HTML,
        reply_markup=alert_menu_markup() if from_menu else None,
    )


async def _do_bandar(update: Update, ticker: str | None = None):
    if not await cooldown_ok(update, "bandar"):
        return
    msg = update.effective_message
    await update.effective_chat.send_action(ChatAction.TYPING)

    def _job():
        idx = bandar.get_index_summary()
        limit = 1000 if ticker else 200
        rows = bandar.get_stock_summary(
            bandar.last_trading_date(), max_stocks=limit,
            until=ticker.upper() if ticker else None)
        return idx, rows

    try:
        idx, rows = await asyncio.to_thread(_job)
    except bandar.IDXTidakTersedia as e:
        await msg.reply_text(
            "🔒 <b>Data BEI (idx.co.id) kini diblokir Cloudflare</b> "
            "(IP server diperlakukan kurang manis). Coba lagi nanti atau dari server lain.\n"
            "<i>Alternatif cepat: /analisis TICKER (pakai Yahoo Finance, delayed 15 menit).</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=home_markup(),
        )
        log.warning("BEI blocked: %s", e)
        return
    except Exception:
        log.exception("bandar gagal")
        await msg.reply_text(
            "❌ Data BEI tidak tersedia saat ini (sumber/paket bermasalah). "
            "Alternatif: <code>/analisis TICKER</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=home_markup(),
        )
        return

    comp = next((x for x in idx if x["IndexCode"] == "COMPOSITE"), None)
    lines = []
    if comp:
        def _num(k):
            v = comp.get(k)
            return f"{v:,.2f}" if isinstance(v, (int, float)) else "—"

        lines.append(f"🇮🇩 <b>IHSG</b> (BEI resmi, {esc(str(comp.get('Date', ''))[:10])})")
        for key_lbl, key in [("Penutupan", "IndexClose"), ("Buka", "IndexOpen"),
                             ("Tinggi", "IndexHigh"), ("Rendah", "IndexLow"),
                             ("Perubahan", "IndexChange")]:
            lines.append(f"  {key_lbl}: {_num(key)}")
    if ticker:
        row = bandar.find_row(rows, ticker)
        if row:
            pct = bandar.to_pct(row)
            lines.append(
                f"\n📊 <b>{esc(row['StockCode'])}</b> — {esc(row.get('StockName', ''))}"
            )
            for lbl, key in [("Buka", "OpenPrice"), ("Tertinggi", "HighestPrice"),
                             ("Terendah", "LowestPrice"), ("Tutup", "ClosingPrice"),
                             ("Volume", "Volume"), ("Nilai", "Value")]:
                v = row.get(key)
                lines.append(
                    f"  {lbl}: {v:,.0f}" if isinstance(v, (int, float)) else f"  {lbl}: —"
                )
            lines.append(f"  Perubahan: {pct:+.2f}%")
            fg = bandar.foreign_fields(row)
            if fg:
                lines.append(f"  Asing: {esc(fg)}")
        else:
            lines.append(
                f"\n❓ {esc(ticker.upper())} tidak ditemukan di ringkasan "
                "(mungkin tidak ada transaksi hari itu)"
            )
    else:
        up = bandar.top_movers(rows, n=4)
        dn = bandar.top_movers(rows, n=4, asc=True)

        def _mv(rs):
            return "\n".join(
                f"  <b>{esc(r['StockCode'])}</b> {bandar.to_pct(r):+.1f}% "
                f"vol {r.get('Volume', 0):,.0f}"
                for r in rs
            )

        lines.append("\n🔥 Top gainer:\n" + _mv(up))
        lines.append("\n💀 Top loser:\n" + _mv(dn))
    lines.append("\n⚠️ Data BEI resmi (bukan saran investasi)")
    await reply_long(
        msg,
        "\n".join(lines),
        disable_web_page_preview=True,
        reply_markup=home_markup(),
    )


async def run_menu_action(update: Update, action: str, tickers: list[str]):
    """Jalankan satu aksi menu. Dipakai tombol maupun input teks."""
    if action == "analisis":
        await _do_analisis(update, tickers)
    elif action == "skor":
        await _do_skor(update, tickers, from_menu=True)
    elif action == "berita":
        await _do_berita(update, tickers[0])
    elif action == "fundamental":
        await _do_fundamental(update, tickers[0])
    elif action == "backtest":
        await _do_backtest(update, tickers[0], from_menu=True)
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
        await _do_saran(update, from_menu=True)
    elif action == "saran_list":
        await _do_saran(update, list_only=True, from_menu=True)
    elif action == "watch_list":
        await _do_watch_list(update)
    elif action == "watch_add":
        await _do_watch_add(update, tickers[0], from_menu=True)
    elif action == "watch_del":
        await _do_watch_del(update, tickers[0], from_menu=True)
    elif action == "alert_list":
        await _do_alert_list(update, from_menu=True)
    elif action == "alert_add":
        await _do_alert_add(update, tickers[0], from_menu=True)
    elif action == "bandar":
        await _do_bandar(update)
    else:
        await update.effective_message.reply_text(
            "Aksi tidak dikenal. Buka /start untuk menu.",
            reply_markup=home_markup(),
        )


async def _safe_edit(msg, text, **kw):
    """Edit pesan menu; abaikan 'Message is not modified' (double-tap jinak)."""
    try:
        await send_with_retry(lambda: msg.edit_text(text, **kw),
                              what="edit pesan")
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


async def menu_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if ALLOWED_IDS and (not q.from_user or q.from_user.id not in ALLOWED_IDS):
        await q.answer("⛔ Bot ini privat.", show_alert=True)
        return
    await q.answer()
    cb_data = q.data or ""
    msg = q.message
    if not msg:
        return
    chat_id = update.effective_chat.id
    if cb_data == "menu":
        _pending_clear(chat_id)
        await _safe_edit(
            msg, _menu_text(), parse_mode=ParseMode.HTML, reply_markup=main_menu_markup())
        return
    if cb_data.startswith("pick:"):
        action = cb_data.split(":", 1)[1]
        label = NEED_TICKER.get(action, action)
        _pending_clear(chat_id)
        await _safe_edit(
            msg,
            f"{esc(label)} — pilih emiten:",
            parse_mode=ParseMode.HTML,
            reply_markup=ticker_pick_markup(action, store.get_watchlist(chat_id)),
        )
        return
    if cb_data.startswith("manual:"):
        action = cb_data.split(":", 1)[1]
        _pending_set(chat_id, action)
        await _safe_edit(
            msg,
            _MANUAL_PROMPT.get(action, "Ketik kode emiten:"),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Batal", callback_data="menu")]]
            ),
        )
        return
    if cb_data.startswith("tick:"):
        parts = cb_data.split(":", 2)
        if len(parts) != 3 or not parts[2]:
            await _safe_edit(
                msg, "❌ Tombol tidak valid — buka ulang menu.", reply_markup=home_markup())
            return
        _, action, ticker = parts
        _pending_clear(chat_id)
        await _safe_edit(
            msg,
            f"⏳ Memproses <b>{esc(ticker)}</b>...",
            parse_mode=ParseMode.HTML,
            reply_markup=home_markup(),
        )
        await run_menu_action(update, action, [ticker])
        return
    if cb_data.startswith("refresh:"):
        parts = cb_data.split(":", 2)
        if len(parts) != 3 or not parts[2]:
            await _safe_edit(
                msg, "❌ Tombol tidak valid — buka ulang menu.", reply_markup=home_markup())
            return
        _, action, ticker = parts
        _pending_clear(chat_id)
        await _safe_edit(
            msg,
            f"⏳ Memperbarui <b>{esc(ticker)}</b>...",
            parse_mode=ParseMode.HTML,
            reply_markup=home_markup(),
        )
        await run_menu_action(update, action, [ticker])
        return
    if cb_data.startswith("run:"):
        action = cb_data.split(":", 1)[1]
        if action == "watch":
            await _safe_edit(
                msg,
                "👀 <b>Watchlist</b> — pilih:",
                parse_mode=ParseMode.HTML,
                reply_markup=watch_menu_markup(),
            )
        elif action == "alert":
            await _safe_edit(
                msg,
                "🔔 <b>Alert</b> — pilih:",
                parse_mode=ParseMode.HTML,
                reply_markup=alert_menu_markup(),
            )
        elif action == "help":
            await _safe_edit(
                msg, _menu_text(), parse_mode=ParseMode.HTML, reply_markup=main_menu_markup())
        elif action == "saran":
            await _safe_edit(
                msg,
                "🔍 Memindai emiten (teknikal+fundamental+berita), 1-2 menit...",
                reply_markup=home_markup(),
            )
            await run_menu_action(update, "saran", [])
        elif action == "bandar":
            await _safe_edit(msg, "⏳ Mengambil data BEI...", reply_markup=home_markup())
            await run_menu_action(update, "bandar", [])
        elif action == "sinyal":
            await _do_sinyal(update, [])
        elif action == "paket":
            await _do_paket(update, [])
        elif action == "scan":
            await _do_scan(update, [], ctx.job_queue)
        else:
            await run_menu_action(update, action, [])
        return


async def menu_text_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Teks biasa: hanya diproses kalau chat sedang menunggu input emiten,
    atau kalau teksnya pertanyaan beli/jual ('BBCA beli gak?')."""
    chat_id = update.effective_chat.id
    action = _pending_get(chat_id)
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
        _pending_clear(chat_id)
        return
    text = (update.effective_message.text or "").strip().upper()
    raw_tokens = [t for t in text.replace(",", " ").split() if t]
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
            parse_mode=ParseMode.HTML)
        return
    _pending_clear(chat_id)
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
    try:
        await app.bot.set_my_commands([
            BotCommand("start", "Menu utama (tombol interaktif)"),
            BotCommand("menu", "Menu utama (tombol interaktif)"),
            BotCommand("analisis", "Analisis emiten, mis. /analisis BBCA"),
            BotCommand("skor", "Ringkasan skor cepat"),
            BotCommand("berita", "Berita + sentimen emiten"),
            BotCommand("fundamental", "PER/PBV/ROE/dividen/DER"),
            BotCommand("saran", "Kandidat BUY harian + paper trading"),
            BotCommand("sinyal", "Sinyal BELI/JUAL seluruh pasar IDX"),
            BotCommand("rencana", "Rencana beli 1 emiten + anggaran"),
            BotCommand("paket", "Paket beli multi-emiten siap Stockbit"),
            BotCommand("cek", "Ceklist sebelum beli: /cek BBCA"),
            BotCommand("portofolio", "Portofolio asli (uang sungguhan)"),
            BotCommand("sektor", "Rotasi & kekuatan sektor BEI"),
            BotCommand("scan", "Pindai seluruh pasar sekarang (±3–6 menit)"),
            BotCommand("beli", "Tanya: layak beli? mis. /beli BBCA"),
            BotCommand("jual", "Tanya: layak jual? mis. /jual BBCA"),
            BotCommand("bandar", "Data resmi BEI / IHSG"),
            BotCommand("banding", "Bandingkan 2–4 emiten"),
            BotCommand("agenda", "Ex-date dividen & aksi korporasi"),
            BotCommand("modal", "Atur modal latihan"),
            BotCommand("auto", "Ringkasan & pengingat otomatis (/auto on)"),
            BotCommand("belajar", "Kamus istilah"),
            BotCommand("validasi", "Hasil uji statistik skor"),
            BotCommand("backtest", "Backtest 5 tahun emiten"),
            BotCommand("watch", "Kelola watchlist"),
            BotCommand("alert", "Kelola alarm harga"),
            BotCommand("status", "Kesehatan bot (admin)"),
            BotCommand("id", "Lihat ID Telegram kamu"),
            BotCommand("help", "Panduan"),
        ])
    except Exception:
        log.warning("gagal mendaftarkan menu perintah", exc_info=True)


async def cmd_belajar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """[P1] Kamus istilah: /belajar RSI, /belajar PBV, /belajar MACD, ..."""
    args = tokens(update)
    if not args:
        await update.effective_message.reply_text(
            "🎓 <b>Belajar istilah IHSG</b>\n"
            "Pilih topik: <code>/belajar &lt;topik&gt;</code>\n\n"
            f"{learn.list_topics()}\n\n"
            "Contoh: <code>/belajar RSI</code>, <code>/belajar PBV</code>",
            parse_mode=ParseMode.HTML)
        return
    txt = learn.learn(args[0])
    if txt is None:
        await update.effective_message.reply_text(
            "❓ Topik tidak dikenal. Kirim <code>/belajar</code> saja "
            "buat lihat daftar topiknya.", parse_mode=ParseMode.HTML)
        return
    await update.effective_message.reply_text(
        txt, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_berita(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ringkasan berita terkini + skor sentimen lexicon Indonesia."""
    args = tokens(update)
    if not args:
        await update.effective_message.reply_text(
            "📰 Berita — pilih emiten:",
            reply_markup=ticker_pick_markup(
                "berita", store.get_watchlist(update.effective_chat.id)))
        return
    await _do_berita(update, args[0])


async def cmd_fundamental(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Rincian fundamental emiten (PER, PBV, ROE, dividen, DER)."""
    args = tokens(update)
    if not args:
        await update.effective_message.reply_text(
            "💰 Fundamental — pilih emiten:",
            reply_markup=ticker_pick_markup(
                "fundamental", store.get_watchlist(update.effective_chat.id)))
        return
    await _do_fundamental(update, args[0])


async def gather_candidate(t: str) -> dict | None:
    """Analisis gabungan satu emiten: teknikal + fundamental + berita (paralel)."""
    try:
        res = await fetch_and_analyze(t)
    except ValueError:
        return None
    # fundamental & berita jalan bersamaan: pemindaian 12 emiten jadi jauh lebih cepat
    fund_raw, items = await asyncio.gather(
        asyncio.to_thread(fundamental.fetch_fundamentals, t),
        asyncio.to_thread(news.fetch_news, t),
        return_exceptions=True)
    if isinstance(fund_raw, Exception) or not fund_raw:
        fund_raw, fsc = None, None
    else:
        try:
            fsc = await asyncio.to_thread(fundamental.score_fundamentals, fund_raw)
        except Exception:
            fsc = None
    nsent = None
    if items and not isinstance(items, Exception):
        try:
            nsent = await asyncio.to_thread(news.news_sentiment, items)
        except Exception:
            nsent = None
    return {"res": res, "fund": fsc, "nsent": nsent, "fund_raw": fund_raw}


async def cmd_saran(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Pemindaian harian: kandidat BUY berline entry/SL/TP + daftar HINDARI."""
    args = tokens(update)
    sub = args[0].lower() if args else ""
    await _do_saran(update, sub=sub)


async def saran_scan_text(chat_id: int, universe: list[str] | None = None,
                          progress=None) -> str | None:
    """[P6] Inti pemindaian /saran — reusable untuk auto-scan pagi & /saran lq45.

    universe None = default (watchlist + default, cap MAX_SCAN).
    progress: async callback(done, total) untuk pesan '10/45 ...' bertahap.
    Emiten dianalisis paralel berbatas (`SCAN_CONCURRENCY`) — hasil tetap urut
    sesuai universe, dan satu emiten gagal tidak membatalkan sisanya.
    Return teks ringkasan, atau None kalau semua sumber gagal."""
    if universe is None:
        wl = store.get_watchlist(chat_id)
        universe = (wl + [t for t in saranmod.DEFAULT_UNIVERSE if t not in wl])[:saranmod.MAX_SCAN]
    modal = store.get_modal(chat_id)
    total = len(universe)
    done = 0
    report_at = 0
    sem = asyncio.Semaphore(SCAN_CONCURRENCY)

    async def scan_one(t: str) -> dict | None:
        """Satu emiten, paralel dibatasi semaphore agar Yahoo tidak dibanjiri."""
        nonlocal done, report_at
        async with sem:
            try:
                a = await gather_candidate(t)
            except Exception:
                # satu emiten bermasalah tidak boleh membatalkan seluruh pemindaian
                log.exception("pemindaian %s gagal", t)
                a = None
            await asyncio.sleep(SCAN_PAUSE)
        done += 1
        should_report = bool(progress) and (done - report_at >= 10 or done == total)
        if should_report:
            report_at = done
            try:
                await progress(done, total)
            except Exception:
                pass
        return a

    results = await asyncio.gather(*(scan_one(t) for t in universe))
    # urutan universe dipertahankan (bukan urutan selesai) supaya hasil stabil
    analyzed = [a for a in results if a]
    latest = {a["res"]["ticker"]: a["res"]["ind"].iloc[-1] for a in analyzed}
    if not analyzed:
        return None

    # posisi yang masih hidup ikut dihitung supaya batas portofolio dihormati
    open_recs = {t: r for t, r in store.get_paper(chat_id).items()
                 if r.get("status") in ("open", "pending")}
    result = saranmod.pick(analyzed, modal, open_recs)  # sizing pakai modal latihan
    paper_msgs = saranmod.update_paper(chat_id, latest)  # notifikasi TP/SL posisi lama

    today = marketcal.now_wib().date()
    lines = [f"🧭 <b>RINGKASAN SARAN HARIAN — {pd.Timestamp.now(tz=WIB):%d %b %Y}</b>",
             "Skema: entry di open sesi berikutnya | SL 1×ATR | TP 2×ATR | "
             f"biaya beli {saranmod.BUY_FEE * 100:.2f}% & jual "
             f"{saranmod.SELL_FEE * 100:.2f}% | modal latihan {saranmod.fmt(modal)} "
             "(ubah: /modal 10000000)\n",
             f"Posisi latihan terbuka: {result['open_count']}/{result['max_open']} "
             f"(maks {saranmod.MAX_PER_SECTOR} per sektor)\n"]
    if result["picks"]:
        saranmod.queue_paper(chat_id, result["picks"], today)
        label = ("Kandidat (urut skor relatif antar-emiten — IC OOS +0,03, "
                 "tipis; setelah biaya ≈ 0)"
                 if result.get("mode") == "relatif"
                 else "Kandidat (urut PowerScore — universe terlalu kecil "
                      "untuk skor relatif)")
        lines.append(f"{label}, diantre sebagai posisi latihan:")
        for c in result["picks"]:
            fund_txt = "—" if c["fund"] is None else f"{c['fund']:+.0f}"
            news_txt = "—" if c["news"] is None else f"{c['news']:+.0f}"
            rel_txt = ""
            if c.get("rel_ok") and c.get("rel_pct") is not None:
                rel_txt = (f"\n      📊 Relatif {c['rel_pct']:.0f}/100 "
                           f"(kuintil {c['rel_quintil']}/5)")
            lines.append(
                f"  {c['emoji']} <b>{esc(c['ticker'])}</b> power {c['power']:+d}% "
                f"(tek {c['tech']:+.0f}, fund {fund_txt}, berita {news_txt})"
                f"{rel_txt}\n"
                f"      Entry {saranmod.fmt(c['entry'])} | SL {saranmod.fmt(c['sl'])} | "
                f"TP {saranmod.fmt(c['tp'])}")
            # [P5] ukuran posisi: risiko 1% modal, floor ke lot 100
            if "lots" in c:
                if c["lots"] > 0:
                    lines.append(
                        f"      💼 Ukuran (risiko 1% modal): {c['lots']} lot "
                        f"({c['shares']} lembar) ≈ {saranmod.fmt(c['value'])} | "
                        f"risiko nyata {saranmod.fmt(c['real_risk'])}")
                else:
                    lines.append(
                        "      💼 Ukuran: <i>risiko/lembar melebihi 1% modal — "
                        "butuh minimal 1 lot; naikkan modal (mis. "
                        f"{saranmod.fmt(min(int(modal) * 3, 100_000_000))}) "
                        "atau lewati ini</i>")
    else:
        lines.append("Tidak ada kandidat layak BUY hari ini "
                     "(semua skor tak mencukupi / terkena filter jebakan momentum).")
    lines.append("\n🚫 HINDARI (terlemah):")
    for c in result["avoid"]:
        rel_txt = (f"relatif {c['rel_pct']:.0f}/100"
                   if c.get("rel_ok") and c.get("rel_pct") is not None
                   else f"{c['power']:+d}%")
        reason = ("jebakan momentum" if c["ret20d"] > saranmod.TRAP_RET20D
                  else "terlemah di universe")
        lines.append(f"  {c['emoji']} <b>{esc(c['ticker'])}</b> {rel_txt} — {reason}")
    skipped = result.get("skipped") or []
    if skipped:
        lines.append("")
        lines.append(f"⚠️ {len(skipped)} emiten dilewati karena datanya tidak lengkap: "
                     + ", ".join(esc(t) for t in skipped[:10])
                     + ("…" if len(skipped) > 10 else ""))
    if paper_msgs:
        lines.append("")
        lines.extend(paper_msgs)
    lines.append("\n⚠️ Alat bantu latihan, bukan saran formal. DYOR!")
    return "\n".join(lines)


VALIDASI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "data", "validation.json")


def load_validation(path: str = VALIDASI_FILE) -> dict | None:
    """Baca hasil riset yang di-commit. None kalau belum ada / rusak."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def format_validasi(v: dict) -> str:
    """Susun laporan /validasi. Semua angka berasal dari file hasil uji."""
    m = v.get("meta", {})
    sc = v.get("score", {})
    ic = sc.get("ic", {})
    ts = sc.get("ic_timeseries", {})
    L = ["🔬 <b>VALIDASI SKOR — hasil uji, bukan klaim</b>",
         f"{m.get('start')} → {m.get('end')} · {m.get('n_tickers')} emiten · "
         f"{m.get('n_rows', 0):,} observasi · horizon {m.get('horizon')} hari bursa",
         ""]

    def verdict(val, hi=0.03):
        if val is None:
            return "tidak tersedia"
        a = abs(val)
        if a < hi:
            return "praktis nol"
        if a < 0.05:
            return "lemah"
        return "layak"

    L.append("<b>1. Skor teknikal lama</b>")
    hac = f", HAC t={ic['t_hac']}" if ic.get("t_hac") is not None else ""
    L.append(f"IC antar-emiten: {ic.get('mean_ic')} (t={ic.get('t')}{hac}) — "
             f"{verdict(ic.get('mean_ic'))}")
    ts_note = ("sedikit berlawanan arah" if (ts.get("mean_ic") or 0) < 0
               else verdict(ts.get("mean_ic")))
    L.append(f"IC dalam satu emiten: {ts.get('mean_ic')} (t={ts.get('t')}) — "
             f"{ts_note}")
    L.append("→ jadi skor dipakai sebagai <b>bacaan tren</b>, bukan sinyal beli.")
    L.append("")

    L.append("<b>2. Excess return per rating</b> (vs rata-rata pasar, 20 hari)")
    for r in v.get("ratings", []):
        L.append(f"  {r['rating']}: {r['excess_pct']:+.2f}% "
                 f"(menang {r['excess_win_rate']:.0f}%, n={r['n']:,})")
    L.append("")

    L.append("<b>3. Walk-forward</b> (latih masa lalu → uji tahun berikutnya)")
    for r in v.get("walk_forward", []):
        if r.get("ic_model") is None:
            continue
        eq = r.get("ic_eq")
        eq_txt = f" | bobot sama {eq:+.3f}" if isinstance(eq, (int, float)) else ""
        lam = r.get("lam_rel")
        lam_txt = f" [lam {lam:g}]" if isinstance(lam, (int, float)) else ""
        L.append(f"  {r['year']}: skor lama {r['ic_score']:+.3f} | "
                 f"model {r['ic_model']:+.3f}{eq_txt} | "
                 f"gabungan {r['ic_blend']:+.3f}{lam_txt}")
    wfs = v.get("walk_forward_summary") or {}
    if wfs.get("ic_model_mean") is not None and wfs.get("ic_eq_mean") is not None:
        L.append(f"  rata-rata: model {wfs['ic_model_mean']:+.3f} vs bobot sama "
                 f"{wfs['ic_eq_mean']:+.3f} · model menang "
                 f"{wfs.get('years_model_beats_eq')}/{wfs.get('n_years')} tahun")
    L.append("")

    q = v.get("quintiles", [])
    if q:
        L.append("<b>4. Kuintil prediksi OOS</b> (excess per 20 hari)")
        L.append("  " + " · ".join(f"Q{r['quintile']} {r['excess_pct']:+.2f}%"
                                    for r in q))
        L.append("")

    p = v.get("portfolio")
    if p:
        L.append(f"<b>5. Setelah biaya transaksi</b> (kuintil teratas, "
                 f"rebalance {p.get('holding_days')} hari)")
        L.append(f"  alpha bruto {p['alpha_gross_mean_pct']:+.2f}% → "
                 f"<b>neto {p['alpha_net_mean_pct']:+.2f}%</b> per periode "
                 f"(biaya {p['cost_per_round_trip_pct']:.2f}%, "
                 f"turnover {p['avg_turnover_pct']:.0f}%)")
        L.append(f"  {p['phases_net_positive']}/{p['n_phases']} fase positif · "
                 f"median neto {p['alpha_net_median_pct']:+.2f}%")
        if p.get("hysteresis"):
            L.append(f"  hysteresis: masuk top {p.get('quintile', 0) * 100:.0f}%, "
                     f"tahan sampai top {p.get('hold_quintile', 0) * 100:.0f}% "
                     f"(turnover dua arah {p.get('avg_turnover_two_way_pct')}%)")
        dsr = p.get("deflated_sharpe") or {}
        if dsr.get("dsr") is not None:
            L.append(f"  Deflated Sharpe {dsr['dsr']} "
                     f"(SR terbaik {dsr.get('best_sr')}, ambang {dsr.get('sr0')}, "
                     f"n_trials={dsr.get('n_trials')})")
        ci = p.get("ci95_alpha_net") or {}
        if ci.get("lo") is not None:
            L.append(f"  CI 95% alpha neto {ci['lo']:+.2f}% s.d. {ci['hi']:+.2f}% "
                     f"(blok {ci.get('block')} fase; fase saling tumpang tindih)")
        ihsg = p.get("alpha_vs_ihsg_mean_pct")
        if isinstance(ihsg, (int, float)):
            L.append(f"  vs IHSG {ihsg:+.2f}%/periode "
                     f"({p.get('phases_vs_ihsg_positive')}/{p.get('n_phases')} fase) — "
                     "IHSG berbobot kapitalisasi, universe di sini sama-bobot, "
                     "jadi angka ini bukan alpha murni")
        L.append("  → biaya hampir menghabiskan keunggulan; jangan baca "
                 "peringkat sebagai jaminan untung.")
        L.append("")

    k = v.get("kronos")
    if k:
        ic_k = (k.get("ic") or {}).get("kronos") or {}
        ic_s = (k.get("ic") or {}).get("score") or {}
        L.append("<b>6. Kronos vs skor</b> (uji OOS, data yang sama)")
        L.append(f"  Kronos IC {ic_k.get('mean_ic')} (t={ic_k.get('t')}) vs "
                 f"skor {ic_s.get('mean_ic')} (t={ic_s.get('t')})")
        L.append(f"  {k.get('n_obs')} titik · {k.get('n_tickers')} emiten · "
                 f"{k.get('n_dates')} tanggal")
        L.append("")

    L.append("<i>Angka di atas bisa berbeda antar periode; IC 0,03 tetap tipis "
             "dan setelah biaya transaksi keunggulannya ≈ 0. Skor relatif "
             "dipakai untuk memeringkat kandidat, bukan menjamin keuntungan.</i>")
    return "\n".join(L)


async def cmd_validasi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Tampilkan hasil uji statistik yang tersimpan (tanpa hitung ulang)."""
    v = await asyncio.to_thread(load_validation)
    if v is None:
        await update.effective_message.reply_text(
            "Belum ada data validasi. Jalankan <code>python research/evaluate.py</code> "
            "lalu <code>python research/publish.py</code>.", parse_mode=ParseMode.HTML)
        return
    await update.effective_message.reply_text(format_validasi(v),
                                              parse_mode=ParseMode.HTML)


async def cmd_jejak(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Jejak sinyal: hasil nyata BELI/JUAL setelah horizon hari bursa."""
    since = (marketcal.now_wib().date() - timedelta(days=365)).isoformat()
    rows = await asyncio.to_thread(store.closed_scan_history, since)
    counts = await asyncio.to_thread(store.scan_history_counts)
    await reply_long(update.effective_message,
                     scorecard.format_summary(scorecard.summarize(rows), counts))


async def cmd_banding(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """[P2] Bandingkan 2–4 emiten: PER/PBV/ROE/DY + return + PowerScore."""
    args = tokens(update)
    if len(args) < 2:
        await update.effective_message.reply_text(
            "Contoh: <code>/banding BBCA BBRI BMRI</code> (2–4 emiten)",
            parse_mode=ParseMode.HTML)
        return
    if not await cooldown_ok(update, "banding"):
        return
    emiten = list(dict.fromkeys(a.upper() for a in args[:4]))
    await update.effective_message.reply_text(
        f"⚖️ Membandingkan {len(emiten)} emiten… (data dari sumber yang sama "
        "dengan /fundamental & /analisis)")

    rows = []
    for t in emiten:
        try:
            res = await fetch_and_analyze(t)
        except ValueError:
            res = None
        try:
            fund = await asyncio.to_thread(fundamental.fetch_fundamentals, t)
        except Exception:
            fund = None
        rows.append(banding.compare_row(t, res, fund))
        await asyncio.sleep(.2)

    if all(row["power"] is None for row in rows):
        await update.effective_message.reply_text(
            "❌ Data pembanding tidak tersedia (semua sumber gagal) — coba lagi nanti.")
        return

    hdr = banding.format_table_header()
    body = "\n".join(banding.format_table_row(r) for r in rows)
    lines = [f"⚖️ <b>PERBANDINGAN {', '.join(esc(t) for t in emiten)}</b>",
             "<pre>" + esc(hdr) + "\n" + esc(body) + "</pre>"]
    verdict = banding.pbv_verdict(rows)
    if verdict:
        lines.append(f"\nℹ️ {esc(verdict)}")
    missing = [r['ticker'] for r in rows if r["power"] is None]
    if missing:
        lines.append(f"⚠️ Data tidak tersedia untuk: {esc(', '.join(missing))} "
                     "(dilaporkan apa adanya, bukan dikarang).")
    lines.append("\n🔒 Angka deskriptif, bukan rekomendasi. DYOR!")
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_agenda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """[P4] Agenda emiten: ex-date dividen & aksi korporasi terbaru."""
    args = tokens(update)
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


async def cmd_modal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """[P5] Set/ubah modal latihan paper trading (default Rp10 juta)."""
    args = tokens(update)
    chat_id = update.effective_chat.id
    cur = store.get_modal(chat_id)
    if not args:
        await update.effective_message.reply_text(
            f"💼 Modal latihan kamu: <b>{saranmod.fmt(cur)}</b>\n"
            "Ubah: <code>/modal 5000000</code> (atau tulis 5.000.000)\n\n"
            "Sizing /saran memakai risiko <b>1% modal per trade</b>: "
            f"berarti pas-pasan {saranmod.fmt(cur * 0.01)} per posisi.",
            parse_mode=ParseMode.HTML)
        return
    raw = args[0].replace(".", "").replace(",", "")
    try:
        val = int(raw)
    except ValueError:
        await update.effective_message.reply_text(
            "❌ Angka tidak valid. Contoh: <code>/modal 10000000</code>",
            parse_mode=ParseMode.HTML)
        return
    if not (100_000 <= val <= 10_000_000_000):
        await update.effective_message.reply_text(
            "❌ Modal wajar antara Rp100 ribu dan Rp10 miliar (latihan, kan?)")
        return
    store.set_modal(chat_id, val)
    await update.effective_message.reply_text(
        f"💼 Modal latihan diubah: <b>{saranmod.fmt(cur)}</b> → "
        f"<b>{saranmod.fmt(val)}</b>\n"
        f"Risiko per trade /saran berikutnya: {saranmod.fmt(val * 0.01)} (1%)",
        parse_mode=ParseMode.HTML)


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Kesehatan bot: uptime, error, isi store, cache, status bursa."""
    user = update.effective_user
    if ADMIN_IDS and (not user or user.id not in ADMIN_IDS):
        await update.effective_message.reply_text("❌ /status hanya untuk admin bot.")
        return
    up_min = int((time.time() - STARTED_AT) // 60)
    jam, menit = divmod(up_min, 60)
    try:
        c = store.counts()
        h = store.scan_history_counts()
    except Exception:
        log.exception("status: store gagal dibaca")
        c, h = {}, {}
    cache = data.cache_stats()
    avg = STATS["ms"] / STATS["commands"] if STATS["commands"] else 0.0
    await update.effective_message.reply_text(
        "🩺 <b>Status SahamBuddy</b>\n"
        f"Uptime: {jam}j {menit}m | perintah: {STATS['commands']} | "
        f"error: {STATS['errors']} | rata-rata {avg:.0f} ms\n"
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


async def cmd_bandar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Data resmi BEI: IHSG & sektor, plus info emiten (aliran asing bila tersedia)."""
    args = tokens(update)
    await _do_bandar(update, ticker=args[0] if args else None)


# ---------------------------------------------------------------- handlers

async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """[P11] Tampilkan ID Telegram pemakai untuk keperluan ALLOWED_USER_IDS."""
    user = update.effective_user
    uid = user.id if user else update.effective_chat.id
    await update.effective_message.reply_text(
        f"🪪 <b>ID Telegram kamu:</b> <code>{uid}</code>\n"
        "Salin angka ini ke <code>ALLOWED_USER_IDS</code> di file <code>.env</code> "
        "jika ingin membatasi akses bot hanya untuk kamu sendiri.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
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
        reply_markup=main_menu_markup())


async def cmd_analisis(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = tokens(update)
    if not args:
        await update.effective_message.reply_text(
            "📊 Analisis — pilih emiten:",
            reply_markup=ticker_pick_markup(
                "analisis", store.get_watchlist(update.effective_chat.id)))
        return
    await _do_analisis(update, args)


async def cmd_skor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = tokens(update)
    if not args:
        await update.effective_message.reply_text(
            "⚡ Skor — pilih emiten:",
            reply_markup=ticker_pick_markup(
                "skor", store.get_watchlist(update.effective_chat.id)))
        return
    await _do_skor(update, args)


async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = tokens(update)
    chat_id = update.effective_chat.id
    if not args:
        wl = store.get_watchlist(chat_id)
        if not wl:
            await update.effective_message.reply_text(
                "👀 Watchlist masih kosong — pilih:",
                parse_mode=ParseMode.HTML, reply_markup=watch_menu_markup())
            return
        lines = [store_format_quick(wl), "Ganti detail dengan /skor nama-saham"]
        await update.effective_message.reply_text(
            "\n".join(l for l in lines if l),
            parse_mode=ParseMode.HTML,
            reply_markup=watch_menu_markup(),
        )
        return

    sub, *rest = args
    sub = sub.lower()
    if sub in ("add", "tambah") and rest:
        await _do_watch_add(update, rest[0], from_menu=False)
    elif sub in ("remove", "hapus") and rest:
        await _do_watch_del(update, rest[0], from_menu=False)
    elif sub in ("list",):
        await _do_watch_list(update)
    else:
        await update.effective_message.reply_text(
            "Format: /watch add TICKER — /watch list — /watch remove TICKER")


def store_format_quick(tickers: list[str]) -> str:
    return "Daftar watchlist kamu:\n" + "\n".join(f"  • {esc(t)}" for t in tickers)


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
        kinds = {k.lower() for k in rest[1:]} or None
        await _do_alert_add(update, rest[0], kinds=kinds, from_menu=False)
    elif sub in ("remove", "hapus") and rest:
        try:
            symbol = data.normalize_ticker(rest[0]).replace(".JK", "")
        except ValueError:
            symbol = rest[0].strip().upper().replace(".JK", "")
        ok = store.remove_alert(chat_id, symbol)
        await update.effective_message.reply_text(
            "🗑️ Alert dihapus." if ok else "❓ Alert untuk emiten itu tidak ada.")
    elif sub in ("list",):
        await _do_alert_list(update, from_menu=False)
    else:
        await update.effective_message.reply_text(
            "Format salah. Coba <code>/alert</code>.", parse_mode=ParseMode.HTML)


async def cmd_backtest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = tokens(update)
    if not args:
        await update.effective_message.reply_text(
            "📈 Backtest — pilih emiten:\n"
            "Tiap hari 5 tahun terakhir bot berteori, lalu dicek hasil 20 hari kemudian.",
            reply_markup=ticker_pick_markup(
                "backtest", store.get_watchlist(update.effective_chat.id)))
        return
    await _do_backtest(update, args[0], from_menu=False)


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

async def morning_scan(app: Application):
    """[P6] Auto-scan pagi: ringkasan /saran + rekap watchlist, 08:30 WIB,
    Senin–Jumat — hanya ke chat yang mengaktifkan /auto on."""
    now = marketcal.now_wib()
    if not marketcal.is_trading_day(now.date()):
        return  # Sabtu/Minggu & libur bursa tidak jalan
    for cid in store.get_auto_chats():
        try:
            txt = await saran_scan_text(int(cid))
            if txt is None:
                continue  # semua sumber gagal — jangan spam error
            recap = []
            for t in store.get_watchlist(cid)[:10]:
                try:
                    res = await fetch_and_analyze(t)
                    recap.append(build_quick(res))
                except Exception:
                    continue
            if recap:
                txt += "\n\n📋 <b>Rekap watchlist</b>\n" + "\n".join(recap)
            body = f"🌅 <b>AUTOSCAN {now:%a, %d %b %Y}</b>\n\n" + txt
            for part in split_message(body):
                await app.bot.send_message(
                    int(cid), part, parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True)
        except Exception:
            log.exception("autoscan gagal utk %s", cid)
        await asyncio.sleep(1)
    await check_dividend_reminders(app, now.date())


async def check_band_alerts(app: Application, bars_fn) -> int:
    """[P10] Kabari chat /auto on bila band PowerScore saham watchlist berubah."""
    auto_chats = store.get_auto_chats()
    if not auto_chats:
        return 0
    sent = 0
    analysis_cache: dict[str, tuple[dict, dict, dict] | None] = {}
    for cid in auto_chats:
        for sym in store.get_watchlist(cid)[:15]:
            try:
                if sym not in analysis_cache:
                    df = await asyncio.to_thread(bars_fn, sym)
                    if df is None or len(df) < 30:
                        analysis_cache[sym] = None
                    else:
                        ind = await asyncio.to_thread(analysis.compute_indicators, df)
                        sc = analysis.score_stock(ind)
                        snap = analysis.last_snapshot(ind)
                        try:
                            fund = await asyncio.to_thread(fundamental.fetch_fundamentals, sym)
                            fsc = (await asyncio.to_thread(fundamental.score_fundamentals, fund)
                                   if fund else None)
                        except Exception:
                            fsc = None
                        pw = powerscore.combine(sc["pct"], fsc["pct"] if fsc else None, None)
                        analysis_cache[sym] = (pw, snap, sc)
                cached = analysis_cache[sym]
                if not cached:
                    continue
                pw, snap, _sc = cached
                prev_band = store.update_score_band(cid, sym, pw["rating"])
                if not prev_band:
                    continue
                msg_txt = (
                    f"{pw['emoji']} <b>Perubahan Band {esc(sym)}</b>\n"
                    f"PowerScore berpindah: <b>{esc(prev_band)}</b> → "
                    f"<b>{esc(pw['rating'])}</b> ({pw['pct']:+d}%)\n"
                    f"Harga: {fmt_rp(snap['close'])} ({snap['ret1d']:+.2f}% 1d)\n"
                    "ℹ️ <i>Cek /analisis untuk detail. Bukan saran investasi.</i>"
                )
                await app.bot.send_message(int(cid), msg_txt, parse_mode=ParseMode.HTML)
                sent += 1
                await asyncio.sleep(.2)
            except Exception:
                log.exception("band alert gagal %s (chat %s)", sym, cid)
    return sent


async def check_dividend_reminders(app: Application, today: date | None = None) -> int:
    """[P10] Pengingat H-3 & H-1 ex-date dividen watchlist untuk chat /auto on."""
    auto_chats = store.get_auto_chats()
    if not auto_chats:
        return 0
    today = today or marketcal.now_wib().date()
    sent = 0
    cal_cache: dict[str, dict | None] = {}
    for cid in auto_chats:
        for sym in store.get_watchlist(cid)[:15]:
            try:
                if sym not in cal_cache:
                    cal_cache[sym] = await asyncio.to_thread(
                        marketcal.upcoming_dividend_exdate, sym, today)
                info = cal_cache[sym]
                if not info or not info.get("ex_date"):
                    continue
                days_left = (info["ex_date"] - today).days
                if days_left not in (3, 1):
                    continue
                ex_iso = info["ex_date"].isoformat()
                if not store.mark_div_reminder(cid, sym, ex_iso, days_left):
                    continue
                amt = info.get("amount")
                amt_txt = f" (estimasi {fmt_rp(amt)}/lembar)" if amt else ""
                when = "besok" if days_left == 1 else f"{days_left} hari lagi"
                msg_txt = (
                    f"🗓️ <b>Pengingat Dividen {esc(sym)}</b>\n"
                    f"Ex-date diperkirakan <b>{esc(ex_iso)}</b> ({when}){amt_txt}.\n"
                    "ℹ️ <i>Cek pengumuman resmi emiten di BEI/KSEI. Bukan saran memburu dividen.</i>"
                )
                await app.bot.send_message(int(cid), msg_txt, parse_mode=ParseMode.HTML)
                sent += 1
                await asyncio.sleep(.2)
            except Exception:
                log.exception("pengingat dividen gagal %s (chat %s)", sym, cid)
    return sent


async def fill_open(app: Application):
    """09:20 WIB: isi posisi 'pending' di harga open sesi hari ini.

    Kalau bar hari ini belum tersedia (Yahoo delay), scanner 30 menit yang
    akan mengisinya nanti — jadi tidak ada posisi yang tertinggal.
    """
    if not marketcal.is_trading_day(marketcal.now_wib().date()):
        return
    pending: dict[str, set[str]] = {}
    for chat_id, symbol, rec in store.all_papers():
        if rec.get("status") == "pending":
            pending.setdefault(chat_id, set()).add(symbol)
    for chat_id, symbols in pending.items():
        bars_map: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            try:
                df = await asyncio.to_thread(data.fetch_history, symbol, "1mo")
            except Exception:
                log.exception("fill open gagal %s", symbol)
                continue
            if df is not None:
                bars_map[symbol] = df
        try:
            msgs = await asyncio.to_thread(
                saranmod.fill_pending, chat_id, bars_map, store.get_modal(chat_id))
            for msg in msgs:
                await app.bot.send_message(int(chat_id), msg, parse_mode=ParseMode.HTML)
        except Exception:
            log.exception("gagal isi posisi pending chat %s", chat_id)


async def heartbeat(app: Application):
    """Tulis timestamp tiap 5 menit — dipakai HEALTHCHECK Docker."""
    path = config.HEARTBEAT_FILE
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(marketcal.now_wib().isoformat(timespec="seconds"))
    except OSError:
        log.warning("gagal menulis heartbeat ke %s", path)


async def cmd_auto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """[P6] Nyalakan/matikan auto-scan pagi per chat."""
    args = tokens(update)
    sub = args[0].lower() if args else ""
    chat_id = update.effective_chat.id
    if sub in ("on", "aktif", "nyala"):
        store.set_auto(chat_id, True)
        await update.effective_message.reply_text(
            "🌅 <b>Auto-scan pagi: AKTIF</b>\n"
            " Tiap hari bursa ±08:30 WIB: ringkasan /saran + rekap watchlist "
            "dikirim otomatis di chat ini.\n"
            " Matikan: <code>/auto off</code>", parse_mode=ParseMode.HTML)
    elif sub in ("off", "mati", "nonaktif"):
        store.set_auto(chat_id, False)
        await update.effective_message.reply_text(
            "🌙 Auto-scan pagi <b>mati</b>. Kapan pun nyala lagi: <code>/auto on</code>.",
            parse_mode=ParseMode.HTML)
    else:
        status = "AKTIF ✅" if store.get_auto(chat_id) else "mati (default)"
        await update.effective_message.reply_text(
            f"🌅 Auto-scan pagi di chat ini: <b>{status}</b>\n"
            "Nyalakan: <code>/auto on</code> — Matikan: <code>/auto off</code>\n"
            "Jadwal: Senin–Jumat ±08:30 WIB (sebelum bursa buka).",
            parse_mode=ParseMode.HTML)


async def check_zone_alerts(app: Application, bars) -> None:
    """[P16] Kirim satu pesan saat emiten watchlist MASUK zona beli.

    Status zona disimpan per (chat, emiten) di `store.zone_state`. Aturan:
    pesan hanya dikirim saat status berubah dari "di luar" menjadi "di dalam";
    selama masih di zona yang sama, scan berikutnya diam. Ikut saklar
    `/auto on/off` karena sumbernya `store.get_auto_chats()`.
    """
    for chat_id in await asyncio.to_thread(store.get_auto_chats):
        wl = await asyncio.to_thread(store.get_watchlist, chat_id)
        if not wl:
            continue
        states = await asyncio.to_thread(store.get_zone_states, chat_id)
        for symbol in wl:
            try:
                df = await asyncio.to_thread(bars, symbol)
                if df is None:
                    continue
                ind = await asyncio.to_thread(analysis.compute_indicators, df)
                zones = alerts.buy_zones(ind)
                prev = states.get(symbol.upper(), "")
                cur = ",".join(zones)
                if cur == prev:
                    continue
                await asyncio.to_thread(store.set_zone_state, chat_id, symbol,
                                        cur)
                if not zones or prev:
                    continue          # keluar zona, atau tetap di dalam: diam
                snap = analysis.last_snapshot(ind)
                atr = None
                try:
                    atr_val = ind["atr14"].iloc[-1]
                    atr = float(atr_val) if pd.notna(atr_val) and atr_val > 0 \
                        else None
                except (KeyError, IndexError, TypeError, ValueError):
                    atr = None
                sl = saranmod.stop_price(snap["close"], atr) if atr else None
                tp = saranmod.target_price(snap["close"], atr) if atr else None
                await send_with_retry(
                    lambda s=symbol, z=zones, sn=snap, a=sl, b=tp, c=chat_id:
                    app.bot.send_message(
                        int(c),
                        alerts.build_zone_message(s, z, sn, a, b),
                        parse_mode=ParseMode.HTML),
                    what="kirim zona beli")
                await asyncio.sleep(.3)
            except Exception:
                log.exception("zona beli %s (chat %s) gagal", symbol, chat_id)


async def scan_alerts(app: Application):
    """Job berkala: alert + posisi latihan, hanya saat bursa buka."""
    if not marketcal.market_open():
        return  # hanya hari bursa 09:00–16:00 WIB

    cache: dict[str, pd.DataFrame] = {}

    def bars(symbol: str) -> pd.DataFrame | None:
        """Satu emiten diambil sekali per putaran, dipakai alert, paper & band."""
        if symbol not in cache:
            df = data.fetch_history(symbol, "1y")
            if df is None:
                return None
            cache[symbol] = df
        return cache[symbol]

    # 1) alert — dikirim hanya saat kondisi berubah (False → True), bukan tiap 30 menit
    for chat_id, symbol, kinds in store.alert_subscriptions():
        try:
            df = await asyncio.to_thread(bars, symbol)
            if df is None:
                continue
            ind = await asyncio.to_thread(analysis.compute_indicators, df)
            hit = alerts.match_kinds(alerts.check_triggers(ind), kinds)
            fresh = store.update_alert_state(chat_id, symbol, hit)
            if not fresh:
                continue
            snap = analysis.last_snapshot(ind)
            sc = analysis.score_stock(ind)
            await app.bot.send_message(
                int(chat_id),
                alerts.build_alert_message(symbol, sorted(fresh), snap, sc),
                parse_mode=ParseMode.HTML)
            await asyncio.sleep(.3)
        except Exception:
            log.exception("scan alert error %s (chat %s)", symbol, chat_id)

    # 2) paper trading — isi antrean 'pending' lalu cek SL/TP posisi terbuka
    papers: dict[str, dict[str, dict]] = {}
    for chat_id, symbol, rec in store.all_papers():
        papers.setdefault(chat_id, {})[symbol] = rec
    for chat_id, recs in papers.items():
        need = [s for s, r in recs.items() if r.get("status") in ("pending", "open")]
        if not need:
            continue
        bars_map: dict[str, pd.DataFrame] = {}
        for symbol in need:
            try:
                df = await asyncio.to_thread(bars, symbol)
            except Exception:
                log.exception("paper fetch gagal %s", symbol)
                continue
            if df is not None:
                bars_map[symbol] = df
        modal = store.get_modal(chat_id)
        msgs: list[str] = []
        if any(r.get("status") == "pending" for r in recs.values()):
            msgs += await asyncio.to_thread(saranmod.fill_pending, chat_id, bars_map, modal)
        latest = {s: df.iloc[-1] for s, df in bars_map.items() if len(df)}
        msgs += await asyncio.to_thread(saranmod.update_paper, chat_id, latest)
        for msg in msgs:
            try:
                await app.bot.send_message(int(chat_id), msg, parse_mode=ParseMode.HTML)
            except Exception:
                log.exception("gagal kirim update paper ke %s", chat_id)

    # 3) [P10] perubahan band PowerScore untuk watchlist chat /auto on
    await check_band_alerts(app, bars)

    # 4) [P16] zona beli watchlist — hanya saat MASUK zona, bukan tiap putaran
    await check_zone_alerts(app, bars)


# ---------------------------------------------------------------- main

async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    """[kendala 4] setiap crash handler: log penuh + balasan ramah ke user."""
    err = ctx.error
    # double-tap tombol / pesan sudah diubah: jinak, jangan ganggu user
    if isinstance(err, BadRequest) and "message is not modified" in str(err).lower():
        return
    STATS["errors"] += 1
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


# ------------------------------------------------- screener seluruh pasar
_screener_running = False

_SINYAL_DISCLAIMER = (
    "⚠️ Peringkat statistik tipis (IC OOS ±0,03), <b>bukan jaminan</b>. "
    "JUAL = hindari/kurangi bila punya — bukan instruksi short. Selalu DYOR."
)


def _sinyal_emoji(sig: str) -> str:
    return {"BELI": "🟢", "JUAL": "🔴"}.get(sig, "⚪")


def _scan_when(meta: dict) -> str:
    trade = meta.get("trade_date") or "?"
    scanned = (meta.get("scanned_at") or "").replace("T", " ")[:16]
    ok = meta.get("ok") or "?"
    txt = (f"Data per {trade} · scan {scanned} WIB · {ok} emiten "
           f"lolos data")
    if marketcal.market_open():
        txt += " · ⚠️ bursa masih buka, bar hari ini belum final"
    return txt


# ------------- P13/scan: paket, rencana & scan (kode di handlers/market.py)
PLAN_CONCURRENCY = market_handler.PLAN_CONCURRENCY
_RENCANA_USAGE = market_handler._RENCANA_USAGE
_parse_rupiah = market_handler._parse_rupiah
_snapshot_row = market_handler._snapshot_row


def _plan_card(p: dict, idx: int) -> str:
    return market_handler._plan_card(p, idx, sys.modules[__name__])


def _rencana_text(result: dict, payload: dict, meta: dict) -> str:
    return market_handler._rencana_text(result, payload, meta,
                                        sys.modules[__name__])


async def _build_plan_payload(rows: list[dict], meta: dict) -> dict:
    return await market_handler.build_plan_payload(rows, meta,
                                                   sys.modules[__name__])


def _rencana_single_text(plan: dict, c: dict, *, budget_from_modal: bool) -> str:
    return market_handler._rencana_single_text(
        plan, c, budget_from_modal=budget_from_modal,
        deps=sys.modules[__name__])


async def _do_paket(update: Update, args: list[str]):
    await market_handler.do_paket(update, args, sys.modules[__name__])


async def cmd_paket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Paket beli multi-emiten siap Stockbit: /paket [1-5|refresh]."""
    await _do_paket(update, tokens(update))


async def _do_rencana(update: Update, args: list[str]):
    await market_handler.do_rencana(update, args, sys.modules[__name__])


async def cmd_rencana(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Rencana satu emiten dengan anggaran: /rencana BBCA 5000000."""
    await _do_rencana(update, tokens(update))


async def _run_scan_job(ctx: ContextTypes.DEFAULT_TYPE):
    await market_handler.run_scan_job(ctx, sys.modules[__name__])


async def _scan_with_guard(force: bool, *, owns_flag: bool = False) -> dict | None:
    return await market_handler.scan_with_guard(force,
                                                sys.modules[__name__],
                                                owns_flag=owns_flag)


async def _do_scan(update: Update, args: list[str], job_queue):
    await market_handler.do_scan(update, args, job_queue,
                                 sys.modules[__name__])


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Pindai seluruh pasar sekarang: /scan [paksa]."""
    await _do_scan(update, tokens(update), ctx.job_queue)


# -------------------------------- P15: sinyal (kode di handlers/sinyal.py)
_sinyal_row = sinyal_handler._sinyal_row
_sinyal_csv = sinyal_handler._sinyal_csv
_sinyal_overview = sinyal_handler._sinyal_overview
_sinyal_section = sinyal_handler._sinyal_section
_sinyal_detail = sinyal_handler._sinyal_detail


async def _kualitas_map(tickers: list[str]) -> dict[str, dict]:
    return await sinyal_handler.kualitas_map(tickers, sys.modules[__name__])


async def _do_sinyal(update: Update, args: list[str]):
    await sinyal_handler.do(update, args, sys.modules[__name__])


async def cmd_sinyal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Baca snapshot sinyal seluruh pasar: /sinyal [beli|jual|KODE|csv]."""
    await _do_sinyal(update, tokens(update))


def _rp_big(v) -> str:
    """Nilai rupiah besar: Rp7,8 miliar / Rp750 juta."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    if x >= 1e12:
        return f"Rp{x / 1e12:.1f} triliun".replace(".", ",")
    if x >= 1e9:
        return f"Rp{x / 1e9:.1f} miliar".replace(".", ",")
    if x >= 1e6:
        return f"Rp{x / 1e6:.0f} juta"
    return f"Rp{x:,.0f}".replace(",", ".")


def _money(v) -> str:
    """Rupiah gaya Indonesia: Rp1.080 / Rp1.944.000 (/desimal bila perlu)."""
    return textutil.rupiah_desimal(v)


def _money_delta(v) -> str:
    """Rupiah bertanda: Rp210.000 / −Rp40.000 (tanda di depan Rp)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    return ("−" if x < 0 else "") + _money(abs(x))


def _plan_pct(v, sign: bool = True) -> str:
    try:
        txt = f"{float(v):+.1f}%" if sign else f"{float(v):.1f}%"
        return txt.replace(".", ",")
    except (TypeError, ValueError):
        return "—"


def _reject_summary(*counters, limit: int = 4) -> str:
    from collections import Counter
    total: Counter = Counter()
    for c in counters:
        if c:
            total.update(c)
    skip = {"bukan sinyal BELI", "di luar kandidat teratas"}
    items = [(k, v) for k, v in total.most_common()
             if v > 0 and k not in skip][:limit]
    return " · ".join(f"{esc(k)} ({v})" for k, v in items)


# ------------------------------------------- P13: rencana SATU emiten + anggaran
# ------------------------- P17: portofolio (kode di handlers/portfolio.py)
# Dependensi bot (reply_long, cooldown_ok, formatter, _quote_posisi) dioper ke
# handler lewat `deps` = modul ini, supaya monkeypatch.setattr(bot, ...) di tes
# tetap mengenai sasaran yang sama.
_PORTO_USAGE = portfolio_handler._PORTO_USAGE
_quote_posisi = portfolio_handler._quote_posisi


async def _porto_quotes(tickers: list[str]) -> dict[str, dict]:
    return await portfolio_handler._porto_quotes(tickers,
                                                 sys.modules[__name__])


def _porto_text(summary: dict, modal: int) -> str:
    return portfolio_handler._porto_text(summary, modal,
                                         sys.modules[__name__])


async def _do_portofolio(update: Update, args: list[str]):
    await portfolio_handler.do(update, args, sys.modules[__name__])


async def cmd_portofolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Portofolio asli: /portofolio [beli|jual|riwayat] ..."""
    await _do_portofolio(update, tokens(update))


# ------------------------------- P14: ceklist (kode di handlers/cek.py)
_CEK_ICON = cek_handler._CEK_ICON
_CEK_VERDICT = cek_handler._CEK_VERDICT
_cek_bandar_row = cek_handler._cek_bandar_row
_cek_agenda = cek_handler._cek_agenda
_cek_value_avg20 = cek_handler._cek_value_avg20


async def _cek_gather(t: str, chat_id: int) -> dict:
    return await cek_handler._cek_gather(t, chat_id, sys.modules[__name__])


def _cek_text(out: dict, res: dict) -> str:
    return cek_handler._cek_text(out, res, sys.modules[__name__])


async def _do_cek(update: Update, args: list[str]):
    await cek_handler.do(update, args, sys.modules[__name__])


async def cmd_cek(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ceklist sebelum beli: /cek BBCA."""
    await _do_cek(update, tokens(update))


# ------------------------------- P18: rotasi sektor (kode di handlers/sektor.py)
async def _do_sektor(update: Update, args: list[str]):
    await sektor_handler.do(update, args, sys.modules[__name__])


async def cmd_sektor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Kekuatan relatif sektor BEI: /sektor."""
    await _do_sektor(update, tokens(update))


def _scan_summary_text(res: dict, intro: str,
                       kval: dict | None = None) -> str:
    m = res.get("meta") or {}
    counts = res.get("counts") or {}
    if res.get("cached"):
        return (f"{intro}\n\n<i>Snapshot hari ini sudah ada — tidak scan ulang.</i>\n"
                f"{esc(_scan_when(m))}\n\n"
                f"🟢 BELI <b>{counts.get('BELI', 0)}</b> · "
                f"🔴 JUAL <b>{counts.get('JUAL', 0)}</b> · "
                f"⚪ TAHAN <b>{counts.get('TAHAN', 0)}</b>\n\n"
                "Lihat: <code>/sinyal</code>")
    buys = store.load_market_scan("BELI")[:5]
    sells = store.load_market_scan("JUAL")[:5]
    skipped = int(m.get("skipped") or 0)
    failed = int(m.get("failed") or 0)
    out = [intro, "",
           f"✅ <b>{m.get('ok', '?')}</b> emiten dianalisis dari "
           f"{m.get('universe', '?')} kode universe "
           f"({m.get('elapsed_sec', '?')} detik).",
           f"🟢 BELI <b>{counts.get('BELI', 0)}</b> · "
           f"🔴 JUAL <b>{counts.get('JUAL', 0)}</b> · "
           f"⚪ TAHAN <b>{counts.get('TAHAN', 0)}</b>"]
    if skipped or failed:
        out.append(f"ℹ️ {skipped or failed} kode dilewati — tanpa data harga "
                   "cukup (delisted/suspensi baru), bukan diisi data palsu.")
    if buys:
        out += ["", "🟢 <b>Top BELI</b>"]
        out += [_sinyal_row(i + 1, r, (kval or {}).get(r["ticker"]))
                for i, r in enumerate(buys)]
    if sells:
        out += ["", "🔴 <b>Top JUAL/hindari</b>"]
        out += [_sinyal_row(i + 1, r) for i, r in enumerate(sells)]
    out += ["", "Selengkapnya: <code>/sinyal</code> · "
            "<code>/sinyal csv</code>", "", _SINYAL_DISCLAIMER]
    return "\n".join(out)


async def backup_job(app: Application):
    """Salin SQLite harian (WAL-safe) + rotasi. Diam saja kalau sukses."""
    try:
        path = await asyncio.to_thread(
            store.backup_to, config.BACKUP_DIR, config.BACKUP_KEEP)
        log.info("backup store: %s", path)
    except Exception:
        log.exception("backup store gagal")


def admin_chat_id() -> int | None:
    """Chat tujuan notifikasi admin: ADMIN_CHAT_ID, lalu ADMIN_IDS pertama."""
    raw = str(getattr(config, "ADMIN_CHAT_ID", "") or "").strip()
    if not raw and ADMIN_IDS:
        raw = str(sorted(ADMIN_IDS)[0])
    try:
        return int(raw) if raw else None
    except ValueError:
        log.warning("ADMIN_CHAT_ID/ADMIN_IDS bukan angka: %r", raw)
        return None


async def notify_admin(app: Application, text: str) -> bool:
    """Kirim peringatan ke admin; tidak pernah melempar error ke job.

    Dipakai job latar yang gagal tanpa ada user yang menunggu jawaban — tanpa
    ini, screener harian bisa mati berhari-hari tanpa ada yang tahu.
    """
    cid = admin_chat_id()
    if cid is None:
        return False
    try:
        await send_with_retry(
            lambda: app.bot.send_message(cid, text, parse_mode=ParseMode.HTML,
                                         disable_web_page_preview=True),
            what="notifikasi admin")
        return True
    except Exception:
        log.exception("notifikasi admin gagal dikirim ke %s", cid)
        return False


async def full_market_scan_job(app: Application):
    """Job harian 16:15 WIB: snapshot sinyal untuk chat /auto on."""
    if not config.FULL_SCAN_AUTO:
        return
    if not marketcal.is_trading_day(marketcal.now_wib().date()):
        return
    try:
        res = await _scan_with_guard(False)
    except Exception as exc:
        log.exception("screener harian gagal")
        await notify_admin(
            app,
            "❌ <b>Screener harian gagal</b>\n"
            f"<code>{esc(str(exc)[:300])}</code>\n"
            "Snapshot hari ini tidak diperbarui. Cek log VPS.")
        return
    if res is None:
        log.info("screener harian dilewati — scan lain sedang berjalan")
        return
    if res.get("cached"):
        return
    if int(res.get("meta", {}).get("failed") or 0) > 100:
        await notify_admin(
            app,
            "⚠️ <b>Screener harian selesai dengan banyak kegagalan</b>\n"
            f"{res['meta'].get('failed')} kode gagal dari "
            f"{res['meta'].get('universe', '?')}. Kemungkinan rate-limit "
            "Yahoo — cek /status.")
    chats = store.get_auto_chats()
    if not chats:
        return
    txt = _scan_summary_text(res, "📡 <b>SCREENER HARIAN SELURUH PASAR</b>")
    for cid in chats:
        for part in split_message(txt):
            try:
                await app.bot.send_message(
                    int(cid), part, parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True)
            except Exception:
                log.exception("kirim screener harian ke %s gagal", cid)
                break


def build_application(token: str) -> Application:
    """Bangun `Application` dengan batas konkurensi & laju ke Bot API (D2).

    Tanpa `concurrent_updates`, update diproses berurutan: satu `/scan` 3-6
    menit membekukan chat lain. Empat cukup untuk mesin 2 CPU yang juga
    menjalankan yfinance + SQLite; `True` berarti 256 dan akan membanjiri
    sumber data.

    `AIORateLimiter` mengatur laju permintaan KE Bot API (retry saat 429/5xx).
    Itu berbeda dari `cooldown_ok()` yang membatasi per user - keduanya dipakai
    bersama, bukan saling menggantikan.
    """
    return (
        Application.builder()
        .token(token)
        .concurrent_updates(4)
        .connection_pool_size(8)
        .rate_limiter(AIORateLimiter(max_retries=2))
        .post_init(_post_init)
        .build()
    )


def main():
    """Entry point: python bot.py"""
    token = config.BOT_TOKEN  # config sudah memuat .env & menyaring placeholder
    if not token:
        raise SystemExit(
            "BOT_TOKEN belum diset.\n"
            "  1. Chat @BotFather di Telegram\n"
            "  2. Kirim /newbot, ikuti isian\n"
            "  3. Jalankan:  BOT_TOKEN=123:abc python bot.py")
    app = build_application(token)

    # [kendala 5] kalau ALLOWED_USER_IDS diisi, hanya user itu yang boleh memakai bot
    user_ids: list[int] = []
    for part in (os.getenv("ALLOWED_USER_IDS", "") or "").replace(" ", "").split(","):
        if part:
            try:
                user_ids.append(int(part))
            except ValueError:
                raise SystemExit(
                    f"ALLOWED_USER_IDS berisi '{part}' — harus berupa angka id user "
                    "Telegram dipisah koma (cek id-mu dengan @userinfobot)") from None
    user_filter = filters.User(user_id=user_ids) if user_ids else None
    if not user_ids:
        if os.getenv("REQUIRE_ALLOWLIST", "").strip().lower() in ("1", "true", "yes"):
            raise SystemExit("ALLOWED_USER_IDS kosong tetapi REQUIRE_ALLOWLIST aktif — "
                             "isi id Telegram kamu atau matikan REQUIRE_ALLOWLIST.")
        log.warning("ALLOWED_USER_IDS kosong — bot bisa dipakai siapa saja. "
                    "Isi env ALLOWED_USER_IDS (dipisah koma) untuk membatasi.")

    def reg(names, fn):
        app.add_handler(CommandHandler(names, timed(fn), filters=user_filter))
    reg(["start", "menu"], cmd_start)
    reg("help", cmd_start)
    reg(["analisa", "analysis", "analisis"], cmd_analisis)
    reg("skor", cmd_skor)
    reg(["berita", "news"], cmd_berita)
    reg(["fundamental", "fundi"], cmd_fundamental)
    reg("backtest", cmd_backtest)
    reg("watch", cmd_watch)
    reg(["saran", "recommendasi"], cmd_saran)
    reg(["sinyal", "signal"], cmd_sinyal)
    reg("rencana", cmd_rencana)
    reg(["paket", "stockbit", "plan"], cmd_paket)
    reg(["cek", "checklist", "periksa"], cmd_cek)
    reg(["portofolio", "porto"], cmd_portofolio)
    reg(["sektor", "sector"], cmd_sektor)
    reg(["scan", "pindai"], cmd_scan)
    reg(["beli", "buy"], cmd_beli)
    reg(["jual", "sell"], cmd_jual)
    reg(["bandar", "ihsg"], cmd_bandar)
    reg(["banding", "bandingkan"], cmd_banding)
    reg("alert", cmd_alert)
    reg(["agenda", "korporasi"], cmd_agenda)
    reg(["validasi", "validity"], cmd_validasi)
    reg(["jejak", "track"], cmd_jejak)
    reg("modal", cmd_modal)
    reg("auto", cmd_auto)
    reg("status", cmd_status)
    reg("id", cmd_id)
    reg(["belajar", "learn", "apa"], cmd_belajar)
    ALLOWED_IDS.update(user_ids)
    app.add_handler(CallbackQueryHandler(menu_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_text_input))

    job_queue = app.job_queue
    job_queue.run_repeating(scan_alerts, interval=config.ALERT_SCAN_MINUTES * 60, first=10)
    # [P6] auto-scan pagi tiap 08:30 WIB (libur bursa dilewati di dalam fungsi)
    import datetime as _dt
    job_queue.run_daily(morning_scan, time=_dt.time(8, 30, tzinfo=WIB),
                        name="morning-scan")
    # isi antrean 'pending' begitu harga open sesi hari ini tersedia
    job_queue.run_daily(fill_open, time=_dt.time(9, 20, tzinfo=WIB), name="fill-open")
    # screener seluruh pasar setelah bursa tutup (matikan: FULL_SCAN_AUTO=off)
    job_queue.run_daily(full_market_scan_job,
                        time=_dt.time(16, 15, tzinfo=WIB),
                        name="full-market-scan")
    # heartbeat tiap 5 menit: dipakai healthcheck Docker untuk tahu bot masih hidup
    job_queue.run_repeating(heartbeat, interval=300, first=5, name="heartbeat")
    if config.BACKUP_AUTO and config.BACKUP_DIR:
        job_queue.run_daily(backup_job, time=_dt.time(23, 30, tzinfo=WIB),
                            name="backup-store")

    log.info("Bot jalan. Tekan Ctrl+C untuk berhenti.")
    app.add_error_handler(on_error)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
