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
import json
import logging
import os
import re
import sys
import time
from datetime import date
from zoneinfo import ZoneInfo

import pandas as pd
from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (AIORateLimiter, Application, CallbackQueryHandler,
                          CommandHandler, ContextTypes, MessageHandler,
                          filters)
from telegram.error import BadRequest, NetworkError, TimedOut

import config
from core import (analysis, btcache, data,
                  logsetup,
                  data_quality, fundamental, marketcal, news,
                  powerscore, pricing, reports as core_reports,
                  scorecard, screener, saran as saranmod, store)  # noqa: F401 (dipatch tes)
from core import bandar  # noqa: F401 - dipatch tes
from core import banding  # noqa: F401 - dipatch tes
from core import regime  # noqa: F401 - dipatch tes
from core import textutil
from core.textutil import split_message
from core import guards as core_guards
# Alias state yang sama: tes memanipulasi bot.PENDING / bot._last_used.
cooldown_ok = core_guards.cooldown_ok
PENDING = core_guards.PENDING
PENDING_TTL = core_guards.PENDING_TTL
_pending_clear = core_guards._pending_clear
_pending_get = core_guards._pending_get
_pending_set = core_guards._pending_set
_pending_prune = core_guards._pending_prune
_pending_ts = core_guards._pending_ts
_last_used = core_guards._last_used
from core.reports import esc, fmt_rp

from core.chart import build_chart  # noqa: F401 - dipakai handler via deps
from core.reports import build_report  # noqa: F401 - dipakai handler via deps
from core.reports import _trend_line  # noqa: F401 - dipakai tes
from handlers import analisis as analisis_handler
from handlers import cek as cek_handler
from handlers import info as info_handler
from handlers import jobs as jobs_handler
from handlers import jurnal as jurnal_handler
from handlers import market as market_handler
from handlers import menu as menu_handler
from handlers import sektor as sektor_handler
from handlers import sinyal as sinyal_handler
from handlers import trading as trading_handler
from handlers import watchlist as watchlist_handler
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
_TANYA_BELI = {"beli", "dibeli", "membeli", "belinya", "borong", "buy", "akumulasi", "serok", "diserok"}
_TANYA_JUAL = {"jual", "dijual", "menjual", "jualan", "jualnya", "lepas", "dilepas", "melepas",
               "sell", "cut", "cutloss", "tp", "take", "tahan", "hold", "dihold", "holdnya"}
_TANYA_STOP = {"atau", "gak", "nggak", "ngga", "apa", "apakah", "kah",
               "dong", "ya", "lagi", "sekarang", "kapan", "bagus", "layak",
               "mau", "saya", "aku", "kamu", "untuk", "di", "ke", "dari",
               "yang", "ini", "itu", "sih", "aja", "saja", "kalau", "gimana",
               "bagaimana", "harus", "bisa", "boleh", "mending", "sebaiknya",
               "worth", "nya", "nih", "tolong", "coba", "cek", "saham",
               "emiten", "loss", "profit", "lot", "sudah", "belum", "masih",
               "punya", "pegang", "aman", "cuan", "modal", "naik", "turun",
               "koreksi", "untung", "rugi", "hold", "cut", "tp", "sl", "rp",
               "juta", "rb", "ribu", "jt", "lembar"}


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


async def reply_long(message, text: str, reply_markup=None, **kwargs) -> None:
    """Kirim pesan panjang: Telegram menolak pesan >4096 karakter.

    Dibagi per baris; kalau satu bagian ditolak parser HTML, bagian itu dikirim
    ulang sebagai teks polos supaya laporan panjang (/saran lq45) tetap sampai.
    Setiap bagian diulang saat NetworkError/TimedOut (P12). `reply_markup`
    (bila ada) hanya dipasang di potongan terakhir supaya tombol tidak ganda.
    """
    kwargs.setdefault("parse_mode", ParseMode.HTML)
    parts = split_message(text)
    for idx, part in enumerate(parts):
        kw = dict(kwargs)
        if reply_markup is not None and idx == len(parts) - 1:
            kw["reply_markup"] = reply_markup
        try:
            await send_with_retry(
                lambda p=part, k=kw: message.reply_text(p, **k),
                what="kirim laporan")
        except Exception:
            log.warning("bagian pesan ditolak parser HTML — dikirim sebagai teks polos")
            plain_kw = {k: v for k, v in kw.items() if k != "parse_mode"}
            await send_with_retry(
                lambda p=part, k=plain_kw: message.reply_text(
                    re.sub(r"<[^>]+>", "", p), **k),
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
    trend = sc.get("trend_label") or analysis.describe_rating(sc.get("rating"))
    as_of = res.get("as_of")
    as_of_txt = f" · data per {esc(str(as_of))}" if as_of else ""
    return (
        f"{sc['emoji']} <b>{esc(res['ticker'])}</b> {fmt_rp(snap['close'])} "
        f"({snap['ret1d']:+.2f}%) — {sc['pct']:+.0f}% {esc(str(trend))}{as_of_txt}{note}"
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


def _verdict_jual(
    ts_pct: float,
    close: float,
    support: float,
    *,
    held_pos: dict | None = None,
    exit_guidance: dict | None = None,
    rel_pct: float | None = None,
    forensic_penalty: float = 0.0,
    forensic_flags: list[str] | None = None,
    market_regime: dict | None = None,
) -> tuple[str, str]:
    """(judul, alasan singkat) untuk yang sudah pegang atau meninjau keluar posisi."""
    extra_notes: list[str] = []

    if isinstance(exit_guidance, dict):
        eff_sl = exit_guidance.get("effective_sl") or exit_guidance.get("sl_initial")
        trail = exit_guidance.get("chandelier") or {}
        scale_out = exit_guidance.get("scale_out") or {}
        time_stop = exit_guidance.get("time_stop") or {}

        if held_pos and eff_sl and close <= float(eff_sl):
            return (
                "🔴 SAATNYA JUAL / CUT LOSS (SL TERSENTUH)",
                f"Harga berjalan {fmt_rp(close)} sudah menyentuh/menembus batas SL efektif "
                f"{fmt_rp(float(eff_sl))}. Disiplin batasi kerugian.",
            )
        if held_pos and trail.get("hit") and trail.get("stop"):
            return (
                "🟠 Pertimbangkan JUAL / amankan profit (Trailing Stop jebol)",
                f"Harga {fmt_rp(close)} turun menembus Chandelier trailing stop "
                f"{fmt_rp(float(trail['stop']))} (puncak 22h "
                f"{fmt_rp(float(trail.get('high_22') or close))} − 3×ATR).",
            )
        if held_pos and scale_out.get("tp1_reached") and scale_out.get("tp1"):
            bep_sl = scale_out.get("bep_sl") or exit_guidance.get("bep_sl")
            bep_txt = (
                f" dan geser SL sisa lot ke BEP+0,40% fee ({fmt_rp(float(bep_sl))})"
                if bep_sl
                else ""
            )
            extra_notes.append(
                f"Target TP1 ({fmt_rp(float(scale_out['tp1']))}) sudah tercapai — "
                f"pertimbangkan scale-out jual 50% lot{bep_txt}."
            )
        if held_pos and time_stop.get("triggered"):
            days_h = time_stop.get("days_held")
            extra_notes.append(
                f"Time-stop aktif: posisi sudah dipegang {days_h} hari bursa "
                f"(>= {time_stop.get('max_hold_days', 20)} hari horizon model)."
            )

    if rel_pct is not None:
        if float(rel_pct) < saranmod.HOLD_BAND_PCT:
            extra_notes.append(
                f"Skor relatif lintas-pasar ({float(rel_pct):.0f}%) turun di bawah "
                f"pita tahan {saranmod.HOLD_BAND_PCT:.0f}% (keluar hold-band)."
            )
        else:
            extra_notes.append(
                f"Skor relatif ({float(rel_pct):.0f}%) masih di dalam pita tahan "
                f">= {saranmod.HOLD_BAND_PCT:.0f}%."
            )

    if forensic_penalty < 0 or forensic_flags:
        fl = "; ".join(str(x) for x in (forensic_flags or [])[:2])
        extra_notes.append(
            f"Waspada kualitas kas/forensik ({forensic_penalty:+.0f} poin"
            + (f": {fl}" if fl else "")
            + ")."
        )

    if isinstance(market_regime, dict) and market_regime.get("status") == "DEFENSIVE":
        extra_notes.append("Rezim IHSG defensif (SMA50 < SMA200) — perketat trailing stop.")

    tail = (" " + " ".join(extra_notes)) if extra_notes else ""
    out_of_band = rel_pct is not None and float(rel_pct) < saranmod.HOLD_BAND_PCT
    time_hit = bool(
        held_pos
        and isinstance(exit_guidance, dict)
        and (exit_guidance.get("time_stop") or {}).get("triggered")
    )
    tp1_hit = bool(
        held_pos
        and isinstance(exit_guidance, dict)
        and (exit_guidance.get("scale_out") or {}).get("tp1_reached")
    )

    if ts_pct <= -50:
        extra = (" Harga sudah di bawah support "
                 f"{fmt_rp(support)}." if close < support else "")
        return ("🔴 Pertimbangkan JUAL / cut loss",
                "Downtrend kuat. Jangan ditahan dengan harapan." + extra + tail)
    if ts_pct <= -20 or out_of_band or time_hit:
        return ("🟠 Pertimbangkan JUAL SEBAGIAN",
                "Tren melemah — amankan sebagian profit/modal, sisanya pasang "
                "stop loss." + tail)
    if tp1_hit:
        return ("🟡 AMBIL UNTUNG SEBAGIAN (Scale-out TP1)",
                "Harga sudah mencapai area TP1 — amankan 50% posisi dan naikkan "
                "stop loss ke titik impas bersih." + tail)
    if ts_pct >= 50 and forensic_penalty > -15:
        return ("🟢 TAHAN",
                "Tren naik — biarkan profit jalan, pasang stop loss di bawah "
                "support." + tail)
    return ("🟡 TAHAN dengan SL ketat",
            "Tren belum jelas — tahan, tapi pasang stop loss supaya profit "
            "tidak berubah jadi rugi." + tail)


def _weekly_from_daily(ind: pd.DataFrame, score_pct: float) -> dict | None:
    """Hitung tren mingguan dari data harian (tanpa panggilan Yahoo ekstra bila cukup >=20 pekan)."""
    if ind is None or "close" not in ind.columns or not isinstance(ind.index, pd.DatetimeIndex):
        return None
    w_close = ind["close"].resample("W-FRI").last().dropna()
    if len(w_close) < 20:
        return None
    return analysis.weekly_trend(w_close.to_frame(name="close"), score_pct)


def build_verdict(
    res: dict,
    fund: dict | None,
    news_sent: dict | None,
    focus: str = "umum",
    bt_summary: dict | None = None,
    weekly: dict | None = None,
    *,
    positions: list[dict] | None = None,
    open_execs: dict[str, dict] | None = None,
    rel_pct: float | None = None,
    market_regime: dict | None = None,
) -> str:
    snap, sc, sr = res["snap"], res["score"], res["sr"]
    t = res["ticker"]
    forensic_pen = float((fund or {}).get("forensic_penalty") or 0.0)
    forensic_flags = list((fund or {}).get("forensic_flags") or [])
    fund_input = fund.get("base_pct", fund.get("pct")) if fund else None
    news_pct = news_sent["pct"] if news_sent else None
    pw = powerscore.combine(sc["pct"], fund_input, news_pct, forensic_penalty=forensic_pen)
    pw_label = pw.get("trend_label") or analysis.describe_rating(pw.get("rating"))
    sc_label = sc.get("trend_label") or analysis.describe_rating(sc.get("rating"))
    close = float(snap["close"])

    ind = res.get("ind")
    last = ind.iloc[-1] if ind is not None and not ind.empty else pd.Series(dtype=float)
    raw_atr = float(last["atr14"]) if not pd.isna(last.get("atr14", float("nan"))) else None
    atr = raw_atr if (raw_atr is not None and raw_atr > 0) else None
    sl_beli = float(saranmod.stop_price(close, atr)) if atr else float(pricing.round_tick(sr["support"]))
    tp_beli = float(saranmod.target_price(close, atr)) if atr else float(pricing.round_tick(sr["resistance"]))

    t_up = str(t).upper()
    held_pos = None
    if positions:
        for p in positions:
            if str(p.get("ticker") or "").upper() == t_up and int(p.get("lots") or 0) > 0:
                held_pos = p
                break
    exec_info = (open_execs or {}).get(t_up) if isinstance(open_execs, dict) else None

    entry_ref = float(held_pos.get("avg_price") or close) if held_pos else close
    lots_ref = int(held_pos.get("lots") or 0) if held_pos else 0
    stored_sl = float(exec_info["sl"]) if exec_info and exec_info.get("sl") else None
    stored_tp = float(exec_info["tp"]) if exec_info and exec_info.get("tp") else None
    entry_date_raw = (
        str((exec_info or {}).get("trade_date") or (held_pos or {}).get("updated_at") or "")[:10]
        or None
    )
    as_of_raw = str(res.get("as_of") or "")[:10]
    try:
        ref_today = date.fromisoformat(as_of_raw) if as_of_raw else marketcal.now_wib().date()
    except ValueError:
        ref_today = marketcal.now_wib().date()

    prev_close = (
        float(ind["close"].iloc[-2])
        if ind is not None and len(ind) >= 2 and "close" in ind.columns
        else None
    )
    exit_g = saranmod.build_exit_guidance(
        t_up,
        entry_ref,
        lots=lots_ref,
        atr=atr,
        sl=stored_sl,
        tp=stored_tp,
        close=close,
        prev_close=prev_close,
        df=ind,
        entry_date=entry_date_raw,
        today=ref_today,
    )
    days_held = (exit_g.get("time_stop") or {}).get("days_held")

    beli_judul, beli_alasan = _verdict_beli(pw["pct"])
    jual_judul, jual_alasan = _verdict_jual(
        sc["pct"],
        close,
        sr["support"],
        held_pos=held_pos,
        exit_guidance=exit_g,
        rel_pct=rel_pct,
        forensic_penalty=forensic_pen,
        forensic_flags=forensic_flags,
        market_regime=market_regime,
    )

    sym_html = esc(t)
    title = {"beli": f"🛒 <b>Beli {sym_html}?</b>",
             "jual": f"💰 <b>Jual {sym_html}?</b>",
             "umum": f"⚖️ <b>Beli atau jual {sym_html}?</b>"}.get(
                 focus, f"⚖️ <b>Beli atau jual {sym_html}?</b>"
             )

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
        reasons.append(f"Teknikal {sc['pct']:+.0f}% ({esc(str(sc_label))}): {top}")
    elif sc["bull"]:
        top = "; ".join(esc(s) for s in sc["bull"][:2])
        reasons.append(f"Teknikal {sc['pct']:+.0f}% ({esc(str(sc_label))}): {top}")
    else:
        reasons.append(f"Teknikal {sc['pct']:+.0f}% ({esc(str(sc_label))})")
    if fund:
        notes = "; ".join(esc(n) for n in fund.get("notes", [])[:2])
        reasons.append(f"Fundamental {fund['pct']:+.0f}% ({esc(fund['label'])}): "
                       f"{notes}" if notes else
                       f"Fundamental {fund['pct']:+.0f}% ({esc(fund['label'])})")
        if forensic_pen < 0 or forensic_flags:
            fl_txt = "; ".join(esc(str(x)) for x in forensic_flags[:2])
            reasons.append(
                f"Forensik kas/dividen ({forensic_pen:+.0f} poin)"
                + (f": {fl_txt}" if fl_txt else "")
            )
    if news_sent:
        reasons.append(f"Berita {news_sent['pct']:+.0f}% "
                       f"({news_sent['n']} berita)")
    if rel_pct is not None:
        reasons.append(
            f"Peringkat relatif lintas-pasar: persentil {float(rel_pct):.0f}% "
            f"(pita tahan >= {saranmod.HOLD_BAND_PCT:.0f}%)"
        )
    if isinstance(market_regime, dict) and market_regime.get("status") in ("BULL", "DEFENSIVE"):
        reasons.append(
            f"Rezim IHSG: {esc(str(market_regime.get('label') or market_regime.get('status')))}"
        )
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

    arb_warns = list((exit_g.get("arb_risk") or {}).get("warnings") or [])
    level_lines = [
        f"  Support: {fmt_rp(sr['support'])} | Resistance: {fmt_rp(sr['resistance'])}"
    ]
    if focus == "jual":
        if held_pos:
            lots_h = int(held_pos.get("lots") or 0)
            net_pct = float(exit_g.get("pnl_net_pct") or 0.0)
            days_txt = f" · pegang {days_held} hari bursa" if days_held is not None else ""
            level_lines.append(
                f"  Posisi tercatat: <b>{lots_h} lot</b> @ {fmt_rp(entry_ref)} "
                f"(PnL bersih {net_pct:+.2f}% setelah fee BEI 0,40%{days_txt})"
            )
        eff_sl = float(exit_g.get("effective_sl") or exit_g.get("sl_initial") or sl_beli)
        init_sl = float(exit_g.get("sl_initial") or sl_beli)
        sl_src_lbl = "SL eksekusi" if stored_sl else "1×ATR"
        trail = exit_g.get("chandelier") or {}
        trail_sl = trail.get("stop")
        trail_txt = (
            f" | Chandelier trailing SL: {fmt_rp(float(trail_sl))} "
            f"(puncak 22h {fmt_rp(float(trail.get('high_22') or close))} − 3×ATR)"
            if trail_sl
            else ""
        )
        level_lines.append(
            f"  Batas keluar (SL efektif): <b>{fmt_rp(eff_sl)}</b> "
            f"(SL awal {fmt_rp(init_sl)} [{sl_src_lbl}]{trail_txt})"
        )
        so = exit_g.get("scale_out") or {}
        if so.get("tp1"):
            bep_sl = float(so.get("bep_sl") or exit_g.get("bep_sl") or entry_ref)
            tp2_px = float(so.get("tp_full") or exit_g.get("tp_target") or tp_beli)
            level_lines.append(
                f"  Scale-out TP1 (jual 50% lot): <b>{fmt_rp(float(so['tp1']))}</b> "
                f"→ geser SL sisa ke BEP+0,40% fee BEI <b>{fmt_rp(bep_sl)}</b> | "
                f"TP penuh: <b>{fmt_rp(tp2_px)}</b>"
            )
        ts = exit_g.get("time_stop") or {}
        level_lines.append(
            f"  Time-stop: evaluasi keluar bila pegang >= {ts.get('max_hold_days', 20)} hari bursa "
            f"({esc(str(ts.get('reason') or ''))})"
        )
        for w in arb_warns:
            level_lines.append(f"  ⚠️ {esc(str(w))}")
    elif focus == "beli":
        level_lines.append(
            f"  Kalau beli — SL: {fmt_rp(sl_beli)} (1×ATR) | "
            f"TP: {fmt_rp(tp_beli)} (2×ATR)"
        )
        for w in arb_warns:
            level_lines.append(f"  ⚠️ {esc(str(w))}")
    else:
        level_lines.append(
            f"  Kalau beli — SL: {fmt_rp(sl_beli)} (1×ATR) | "
            f"TP: {fmt_rp(tp_beli)} (2×ATR)"
        )
        eff_sl = float(exit_g.get("effective_sl") or exit_g.get("sl_initial") or sl_beli)
        so = exit_g.get("scale_out") or {}
        tp1_px = float(so.get("tp1") or tp_beli)
        bep_sl = float(so.get("bep_sl") or exit_g.get("bep_sl") or entry_ref)
        level_lines.append(
            f"  Kalau sudah pegang — SL efektif: {fmt_rp(eff_sl)} | "
            f"Scale-out TP1 (50%): {fmt_rp(tp1_px)} → SL BEP+fee {fmt_rp(bep_sl)}"
        )
        for w in arb_warns:
            level_lines.append(f"  ⚠️ {esc(str(w))}")
    levels_txt = "\n".join(level_lines)

    prov_line = core_reports.provenance_line(res)
    if not prov_line and res.get("as_of"):
        prov_line = f"🔎 <i>Data per {esc(str(res['as_of']))} WIB</i>\n"
    qual_note = (
        f"⚠️ <i>{esc(str(res['quality_note']))}</i>\n"
        if res.get("quality_note")
        else ""
    )

    bt_line = btcache.format_verdict_backtest(bt_summary, t)
    trap = powerscore.trap_warning(snap.get("ret20d"), snap.get("ret1d"))
    trap_txt = f"\n{trap}" if trap else ""

    return (
        f"{res.get('stale_note', '')}{prov_line}{qual_note}{title}\n"
        f"Harga: <b>{fmt_rp(close)}</b> "
        f"({snap['ret1d']:+.2f}% 1d, {snap['ret20d']:+.2f}% 20d) | "
        f"PowerScore <b>{pw['pct']:+d}% {pw['emoji']} {esc(str(pw_label))}</b>\n\n"
        f"{headline}{other}\n\n"
        f"<b>Kenapa:</b>\n{reasons_txt}\n\n"
        f"<b>Level penting:</b>\n"
        f"{levels_txt}\n"
        f"{bt_line}\n"
        f"{('<b>Yang bisa mengubah jawaban:</b>\n' + conds_txt + chr(10)) if conds_txt else ''}"
        f"{trap_txt}\n"
        f"🔒 <i>Bukan saran investasi. Skor bot = deskripsi kondisi saat itu, "
        f"bukan ramalan. DYOR!</i>"
    )


# ---------------------------------------------------------------- menu interaktif
# Logika tombol & alur ramah pemula ada di handlers/menu.py; alias & wrapper
# tetap diekspor di sini agar monkeypatch pada modul `bot` bekerja mulus.

POPULAR_TICKERS = menu_handler.POPULAR_TICKERS
NEED_TICKER = menu_handler.NEED_TICKER
_MANUAL_PROMPT = menu_handler._MANUAL_PROMPT

# chat_id -> aksi yang sedang menunggu input emiten / modal
# diisi dari ALLOWED_USER_IDS di main(); kosong = siapa pun boleh
ALLOWED_IDS: set[int] = set()


def main_menu_markup(mode: str = "pemula") -> InlineKeyboardMarkup:
    return menu_handler.main_menu_markup(mode)


def mode_menu_markup(mode: str = "pemula") -> InlineKeyboardMarkup:
    return menu_handler.mode_menu_markup(mode)


def home_markup() -> InlineKeyboardMarkup:
    return menu_handler.home_markup()


def result_markup(
    action: str, ticker: str, *, allow_buy: bool = True
) -> InlineKeyboardMarkup:
    return menu_handler.result_markup(action, ticker, allow_buy=allow_buy)


def ticker_pick_markup(
    action: str,
    watchlist: list[str] | None = None,
    *,
    show_guide_back: bool = False,
    extra_tickers: list[str] | None = None,
) -> InlineKeyboardMarkup:
    return menu_handler.ticker_pick_markup(
        action,
        watchlist,
        show_guide_back=show_guide_back,
        extra_tickers=extra_tickers,
    )


def watch_menu_markup() -> InlineKeyboardMarkup:
    return menu_handler.watch_menu_markup()


def alert_menu_markup() -> InlineKeyboardMarkup:
    return menu_handler.alert_menu_markup()


def belajar_markup() -> InlineKeyboardMarkup:
    return menu_handler.belajar_markup()


def belajar_detail_markup() -> InlineKeyboardMarkup:
    return menu_handler.belajar_detail_markup()


def help_markup() -> InlineKeyboardMarkup:
    return menu_handler.help_markup()


def modal_menu_markup() -> InlineKeyboardMarkup:
    return menu_handler.modal_menu_markup()


async def run_menu_action(update: Update, action: str, tickers: list[str]):
    """Jalankan satu aksi menu. Dipakai tombol maupun input teks."""
    await menu_handler.run_menu_action(update, action, tickers,
                                       sys.modules[__name__])


async def _safe_edit(msg, text, **kw):
    """Edit pesan menu; abaikan double-tap & tangani pesan foto/media."""
    await menu_handler.safe_edit(
        msg, text,
        send_fn=lambda call: send_with_retry(call, what="edit pesan"),
        **kw,
    )


async def menu_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await menu_handler.menu_callback(update, ctx, sys.modules[__name__])


async def menu_text_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await menu_handler.menu_text_input(update, ctx, sys.modules[__name__])


def _menu_text(mode: str = "pemula") -> str:
    return menu_handler.menu_text(mode)


def _help_text() -> str:
    return menu_handler.help_text()


async def _post_init(app: Application):
    """Daftarkan daftar perintah di menu '/' Telegram (pemula di atas)."""
    from telegram import BotCommand
    try:
        await app.bot.set_my_commands([
            BotCommand("start", "Buka menu utama (tombol)"),
            BotCommand("mode", "Ganti level menu: pemula / expert"),
            BotCommand("help", "Panduan pemula (3 langkah mulai)"),
            BotCommand("cek", "Cek 6 poin layak beli: /cek BBCA"),
            BotCommand("analisis", "Grafik & analisis lengkap: /analisis BBCA"),
            BotCommand("rencana", "Hitung lot & batas rugi: /rencana BBCA 5jt"),
            BotCommand("modal", "Atur modal rencana & latihan"),
            BotCommand("saran", "Ide saham harian + ukuran aman"),
            BotCommand("paket", "Paket beberapa saham siap Stockbit"),
            BotCommand("beli", "Tanya: layak beli? mis. /beli BBCA"),
            BotCommand("jual", "Tanya: layak jual? mis. /jual BBCA"),
            BotCommand("belajar", "Kamus istilah saham (RSI, PER, PBV...)"),
            BotCommand("portofolio", "Catat & pantau portofolio asli"),
            BotCommand("jurnal", "Jurnal eksekusi Stockbit & win rate realisasi"),
            BotCommand("watch", "Daftar saham pantauan (watchlist)"),
            BotCommand("alert", "Pasang alarm harga otomatis"),
            BotCommand("sinyal", "Sinyal pasar seluruh BEI (±944 emiten)"),
            BotCommand("sektor", "Sektor industri terkuat & terlemah"),
            BotCommand("skor", "Nilai cepat kondisi saham"),
            BotCommand("berita", "Berita terbaru + sentimen"),
            BotCommand("fundamental", "Valuasi & kesehatan keuangan"),
            BotCommand("banding", "Bandingkan 2–4 saham sekaligus"),
            BotCommand("agenda", "Jadwal dividen & aksi korporasi"),
            BotCommand("bandar", "Data resmi BEI / IHSG"),
            BotCommand("scan", "Pindai seluruh pasar sekarang (±3–6 menit)"),
            BotCommand("auto", "Ringkasan otomatis pagi (/auto on)"),
            BotCommand("backtest", "Uji histori aturan sinyal 5 tahun"),
            BotCommand("validasi", "Hasil uji statistik model"),
            BotCommand("jejak", "Rekam jejak hasil sinyal nyata"),
            BotCommand("menu", "Buka menu utama (tombol)"),
            BotCommand("id", "Lihat ID Telegram kamu"),
            BotCommand("status", "Status kesehatan bot (admin)"),
        ])
    except Exception:
        log.warning("gagal mendaftarkan menu perintah", exc_info=True)


# --------------------------- analisis (kode di handlers/analisis.py)
async def _do_analisis(update: Update, tickers: list[str]):
    await analisis_handler.do_analisis(update, tickers,
                                       sys.modules[__name__])


async def _do_skor(update: Update, tickers: list[str],
                   from_menu: bool = False):
    await analisis_handler.do_skor(update, tickers,
                                   sys.modules[__name__],
                                   from_menu=from_menu)


async def cmd_analisis(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await analisis_handler.cmd_analisis(update, ctx, sys.modules[__name__])


async def cmd_skor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await analisis_handler.cmd_skor(update, ctx, sys.modules[__name__])


async def cmd_banding(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await analisis_handler.cmd_banding(update, ctx, sys.modules[__name__])


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
            extra = [f"masuk top {p.get('quintile', 0) * 100:.0f}%",
                     f"tahan sampai top {p.get('hold_quintile', 0) * 100:.0f}%"]
            if (p.get("min_hold") or 1) > 1:
                extra.append(f"min_hold={p['min_hold']} siklus")
            if p.get("regime") and p["regime"] != "none":
                extra.append(f"regime={p['regime']}")
            L.append(f"  hysteresis: {', '.join(extra)} "
                     f"(turnover dua arah {p.get('avg_turnover_two_way_pct')}%)")
        elif (p.get("min_hold") or 1) > 1 or p.get("regime", "none") != "none":
            L.append(f"  aturan: min_hold={p.get('min_hold', 1)}, "
                     f"regime={p.get('regime', 'none')}")
        if p.get("max_drawdown_mean_pct") is not None:
            act_txt = (
                f" · alpha aktif {p['alpha_active_gross_mean_pct']:+.2f}%"
                if p.get("alpha_active_gross_mean_pct") is not None
                and p.get("regime", "none") != "none"
                else ""
            )
            L.append(f"  MDD rata-rata {p['max_drawdown_mean_pct']:+.1f}% · "
                     f"eksposur {p.get('avg_exposure_pct', 100.0):.1f}%{act_txt}")
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


async def _do_berita(update: Update, ticker: str):
    await info_handler.do_berita(update, ticker, sys.modules[__name__])


async def cmd_berita(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ringkasan berita terkini + skor sentimen lexicon Indonesia."""
    await info_handler.cmd_berita(update, ctx, sys.modules[__name__])


async def _do_fundamental(update: Update, ticker: str):
    await info_handler.do_fundamental(update, ticker, sys.modules[__name__])


async def cmd_fundamental(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Rincian fundamental emiten (PER, PBV, ROE, dividen, DER)."""
    await info_handler.cmd_fundamental(update, ctx, sys.modules[__name__])


# --------------------------- info (kode di handlers/info.py)
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await info_handler.do_start(update, ctx, sys.modules[__name__])


async def cmd_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await info_handler.do_mode(update, ctx, sys.modules[__name__])


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await info_handler.do_help(update, ctx, sys.modules[__name__])


# --------------------------- bandar / agenda / jejak / belajar (handlers/)
async def _do_bandar(update: Update, ticker: str | None = None,
                     *, from_menu: bool = False):
    await market_handler.do_bandar(update, sys.modules[__name__],
                                   ticker=ticker, from_menu=from_menu)


async def cmd_bandar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await market_handler.cmd_bandar(update, ctx, sys.modules[__name__])


async def cmd_agenda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await info_handler.cmd_agenda(update, ctx, sys.modules[__name__])


async def _do_jejak(update: Update, *, from_menu: bool = False):
    await info_handler.cmd_jejak(update, None, sys.modules[__name__],
                                 from_menu=from_menu)


async def cmd_jejak(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await info_handler.cmd_jejak(update, ctx, sys.modules[__name__])


async def cmd_belajar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await info_handler.cmd_belajar(update, ctx, sys.modules[__name__])


async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await info_handler.do_id(update, ctx, sys.modules[__name__])


async def _do_validasi(update: Update, *, from_menu: bool = False):
    await info_handler.do_validasi(update, None, sys.modules[__name__],
                                   from_menu=from_menu)


async def cmd_validasi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Tampilkan hasil uji statistik yang tersimpan (tanpa hitung ulang)."""
    await info_handler.do_validasi(update, ctx, sys.modules[__name__])


# --------------------------- watch/alert/modal/auto (handlers/watchlist.py)
async def _do_watch_list(update: Update):
    await watchlist_handler.do_watch_list(update, sys.modules[__name__])


async def _do_watch_add(update: Update, ticker: str, from_menu: bool = True):
    await watchlist_handler.do_watch_add(update, sys.modules[__name__], ticker,
                                         from_menu=from_menu)


async def _do_watch_del(update: Update, ticker: str, from_menu: bool = True):
    await watchlist_handler.do_watch_del(update, sys.modules[__name__], ticker,
                                         from_menu=from_menu)


async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await watchlist_handler.cmd_watch(update, ctx, sys.modules[__name__])


async def _do_alert_list(update: Update, from_menu: bool = True):
    await watchlist_handler.do_alert_list(update, sys.modules[__name__],
                                          from_menu=from_menu)


async def _do_alert_add(update: Update, ticker: str,
                        kinds: set[str] | None = None,
                        from_menu: bool = True):
    await watchlist_handler.do_alert_add(update, sys.modules[__name__], ticker,
                                         kinds=kinds, from_menu=from_menu)


async def cmd_alert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await watchlist_handler.cmd_alert(update, ctx, sys.modules[__name__])


async def cmd_modal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await watchlist_handler.cmd_modal(update, ctx, sys.modules[__name__])


async def cmd_auto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await watchlist_handler.cmd_auto(update, ctx, sys.modules[__name__])


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Kesehatan bot: uptime, error, isi store, cache, status bursa."""
    await info_handler.do_status(update, ctx, sys.modules[__name__])


# ---------------------------------------------------------------- handlers

# --------------------------- trading (kode di handlers/trading.py)
async def _do_saran(update: Update, sub: str = "", list_only: bool = False,
                    from_menu: bool = False):
    await trading_handler.do_saran(update, sys.modules[__name__], sub=sub,
                                   list_only=list_only, from_menu=from_menu)


async def cmd_saran(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await trading_handler.cmd_saran(update, ctx, sys.modules[__name__])


async def saran_scan_text(chat_id: int, universe: list[str] | None = None,
                          progress=None) -> str | None:
    return await trading_handler.saran_scan_text(
        chat_id, sys.modules[__name__], universe=universe, progress=progress)


async def _do_backtest(update: Update, ticker: str, from_menu: bool = False):
    await trading_handler.do_backtest(update, sys.modules[__name__], ticker,
                                      from_menu=from_menu)


async def cmd_backtest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await trading_handler.cmd_backtest(update, ctx, sys.modules[__name__])


async def _do_tanya(update: Update, ticker: str, focus: str):
    await trading_handler.do_tanya(update, sys.modules[__name__], ticker, focus)


async def cmd_beli(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await trading_handler.cmd_beli(update, ctx, sys.modules[__name__])


async def cmd_jual(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await trading_handler.cmd_jual(update, ctx, sys.modules[__name__])


def store_format_quick(tickers: list[str]) -> str:
    return "Daftar watchlist kamu:\n" + "\n".join(f"  • {esc(t)}" for t in tickers)


# ---------------------------------------------------------------- scanner

async def check_dividend_reminders(app: Application, today: date | None = None) -> int:
    """[P10] Pengingat H-3 & H-1 ex-date dividen watchlist untuk chat /auto on."""
    auto_chats = await asyncio.to_thread(store.get_auto_chats, )
    if not auto_chats:
        return 0
    today = today or marketcal.now_wib().date()
    sent = 0
    cal_cache: dict[str, dict | None] = {}
    for cid in auto_chats:
        for sym in (await asyncio.to_thread(store.get_watchlist,
                                            cid))[:15]:
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
                ok_rem = await asyncio.to_thread(
                    store.mark_div_reminder, cid, sym, ex_iso, days_left)
                if not ok_rem:
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
    trade_min = meta.get("trade_date_min")
    scanned = (meta.get("scanned_at") or "").replace("T", " ")[:16]
    ok = meta.get("ok") or "?"
    span = f" (bar terlama {trade_min})" if trade_min and trade_min != trade else ""
    txt = (f"Data per {trade}{span} · scan {scanned} WIB · {ok} emiten "
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


async def _do_paket(update: Update, args: list[str], *, from_menu: bool = False):
    await market_handler.do_paket(update, args, sys.modules[__name__],
                                  from_menu=from_menu)


async def cmd_paket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Paket beli multi-emiten siap Stockbit: /paket [1-5|refresh]."""
    await _do_paket(update, tokens(update))


async def _do_rencana(update: Update, args: list[str], *, from_menu: bool = False):
    await market_handler.do_rencana(update, args, sys.modules[__name__],
                                    from_menu=from_menu)


async def cmd_rencana(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Rencana satu emiten dengan anggaran: /rencana BBCA 5000000."""
    await _do_rencana(update, tokens(update))


async def _run_scan_job(ctx: ContextTypes.DEFAULT_TYPE):
    await market_handler.run_scan_job(ctx, sys.modules[__name__])


async def _scan_with_guard(force: bool, *, owns_flag: bool = False) -> dict | None:
    return await market_handler.scan_with_guard(force,
                                                sys.modules[__name__],
                                                owns_flag=owns_flag)


async def _do_scan(update: Update, args: list[str], job_queue,
                   *, from_menu: bool = False):
    await market_handler.do_scan(update, args, job_queue,
                                 sys.modules[__name__], from_menu=from_menu)


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


async def _do_sinyal(update: Update, args: list[str], *, from_menu: bool = False):
    await sinyal_handler.do(update, args, sys.modules[__name__],
                            from_menu=from_menu)


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


async def _do_portofolio(update: Update, args: list[str], *, from_menu: bool = False):
    await portfolio_handler.do(update, args, sys.modules[__name__],
                               from_menu=from_menu)


async def cmd_portofolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Portofolio asli: /portofolio [beli|jual|riwayat] ..."""
    await _do_portofolio(update, tokens(update))


async def _do_jurnal(update: Update, args: list[str], *, from_menu: bool = False):
    await jurnal_handler.do_jurnal(update, args, sys.modules[__name__],
                                   from_menu=from_menu)


async def cmd_jurnal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Jurnal eksekusi Stockbit & realisasi (asli vs paper)."""
    await jurnal_handler.cmd_jurnal(update, ctx, sys.modules[__name__])


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


async def _do_cek(update: Update, args: list[str], *, from_menu: bool = False):
    await cek_handler.do(update, args, sys.modules[__name__],
                         from_menu=from_menu)


async def cmd_cek(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ceklist sebelum beli: /cek BBCA."""
    await _do_cek(update, tokens(update))


# ------------------------------- P18: rotasi sektor (kode di handlers/sektor.py)
async def _do_sektor(update: Update, args: list[str], *, from_menu: bool = False):
    await sektor_handler.do(update, args, sys.modules[__name__],
                            from_menu=from_menu)


async def cmd_sektor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Kekuatan relatif sektor BEI: /sektor."""
    await _do_sektor(update, tokens(update))


async def _scan_summary_text(res: dict, intro: str,
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
    buys_all = await asyncio.to_thread(store.load_market_scan, "BELI")
    buys_all.sort(
        key=lambda r: (
            -(r.get("rel_pct") if r.get("rel_pct") is not None else -1),
            r.get("ticker", ""),
        )
    )
    buys = buys_all[:5]
    sells = (await asyncio.to_thread(store.load_market_scan, "JUAL"))[:5]
    skipped = int(m.get("skipped") or 0)
    failed = int(m.get("failed") or 0)
    skipped_stale = int(m.get("skipped_stale") or 0)
    skipped_quality = int(m.get("skipped_quality") or 0)
    quality_flagged = int(m.get("quality_flagged") or 0)
    out = [intro, "",
           f"✅ <b>{m.get('ok', '?')}</b> emiten dianalisis dari "
           f"{m.get('universe', '?')} kode universe "
           f"({m.get('elapsed_sec', '?')} detik).",
           f"🟢 BELI <b>{counts.get('BELI', 0)}</b> · "
           f"🔴 JUAL <b>{counts.get('JUAL', 0)}</b> · "
           f"⚪ TAHAN <b>{counts.get('TAHAN', 0)}</b>"]
    if skipped or failed:
        detail_skip = []
        if skipped_stale:
            detail_skip.append(f"{skipped_stale} data basi")
        if skipped_quality:
            detail_skip.append(f"{skipped_quality} gagal cek kualitas")
        detail_str = f" ({', '.join(detail_skip)})" if detail_skip else ""
        out.append(f"ℹ️ {skipped or failed} kode dilewati{detail_str} — tanpa data harga "
                   "cukup (delisted/suspensi baru), bukan diisi data palsu.")
    if quality_flagged:
        out.append(f"⚠️ {quality_flagged} emiten lolos dengan catatan perbaikan anomali bar.")
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


# ------------------------- job terjadwal (kode di handlers/jobs.py)
async def morning_scan(app: Application):
    await jobs_handler.morning_scan(app, sys.modules[__name__])


async def check_band_alerts(app: Application, bars_fn) -> int:
    return await jobs_handler.check_band_alerts(app, bars_fn,
                                                sys.modules[__name__])


async def fill_open(app: Application):
    await jobs_handler.fill_open(app, sys.modules[__name__])


async def heartbeat(app: Application):
    await jobs_handler.heartbeat(app, sys.modules[__name__])


async def check_zone_alerts(app: Application, bars) -> None:
    await jobs_handler.check_zone_alerts(app, bars, sys.modules[__name__])


async def scan_alerts(app: Application):
    await jobs_handler.scan_alerts(app, sys.modules[__name__])


async def backup_job(app: Application):
    await jobs_handler.backup_job(app, sys.modules[__name__])


async def full_market_scan_job(app: Application):
    await jobs_handler.full_market_scan_job(app, sys.modules[__name__])


async def weekly_journal_report(app: Application):
    await jobs_handler.weekly_journal_report(app, sys.modules[__name__])


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
    reg(["mode", "level"], cmd_mode)
    reg("help", cmd_help)
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
    reg(["jurnal", "journal"], cmd_jurnal)
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
    # [P28] laporan berkala mingguan (Jumat 16:45 WIB) realisasi asli vs paper
    job_queue.run_daily(weekly_journal_report,
                        time=_dt.time(16, 45, tzinfo=WIB),
                        name="weekly-journal")
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
