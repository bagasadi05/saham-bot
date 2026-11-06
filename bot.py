"""SahamBuddy — bot Telegram analisis saham Indonesia.

Perintah:
  /start, /help            - panduan
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
import time
from zoneinfo import ZoneInfo

import pandas as pd
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (Application, CommandHandler, ContextTypes, filters)

import config
from core import (alerts, analysis, bandar, banding, data, fundamental, learn,
                  news, powerscore, saran as saranmod, store)
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

def esc(text: str) -> str:
    """Escape teks dinamis untuk mode HTML — WAJIB untuk semua string dari
    luar (judul berita, kode emiten, sinyal, dsb). Plusy Telegram menolak
    pesan saat ada '<' mentah dari sinyal seperti 'MA20 < MA50'."""
    return html.escape(str(text), quote=False)


def fmt_rp(v: float) -> str:
    return f"Rp{v:,.0f}".replace(",", ".")


def tokens(update: Update) -> list[str]:
    txt = (update.effective_message.text or "").split()
    return txt[1:]


async def send_typing(update: Update):
    await update.effective_chat.send_action(ChatAction.TYPING)


def last_macd_cross_note(score: dict) -> str:
    return f"\n⚠️ {score['macd_note']}" if score.get("macd_note") else ""


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

def market_context_line(res: dict, mkt: dict | None) -> str:
    """[P2] Satu baris konteks IHSG: angka, bukan opini."""
    if not mkt:
        return "📊 Konteks pasar: <i>data pembanding tidak tersedia</i>"
    stock20 = res["snap"].get("ret20d")
    stock1y = res.get("ret1y")
    parts = []
    for label, stock_v, mkt_v in [("20 hari", stock20, mkt.get("ret20d")),
                                  ("1 tahun", stock1y, mkt.get("ret1y"))]:
        if stock_v is None or mkt_v is None:
            parts.append(f"{label}: —")
        else:
            diff = stock_v - mkt_v
            arah = "unggul" if diff > 0.5 else ("tertinggal" if diff < -0.5 else "sejalan")
            parts.append(f"{label}: {stock_v:+.1f}% vs IHSG {mkt_v:+.1f}% "
                         f"({arah} {diff:+.1f} poin)")
    return "📊 Konteks pasar (IHSG, data delayed): " + " | ".join(parts)


def build_report(res: dict, fund: dict | None, news_sent: dict | None,
                 mkt: dict | None = None) -> str:
    snap, sc, sr = res["snap"], res["score"], res["sr"]
    # [P1] tiap sinyal diberi penjelasan 1 baris biar pembelajar paham artinya
    def _sig_line(s: str, mark: str) -> str:
        note = learn.signal_note(s)
        return (f"  {mark} {esc(s)}" +
                (f" — <i>{note}</i>" if note else ""))

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
        conf_txt += f"  • {esc(c)}" + (f" — <i>{note}</i>" if note else "") + "\n"
    conf_txt = conf_txt.rstrip()

    # [P1] penjelasan rating & PowerScore biar tidak terasa seperti tebakan
    rating_note = learn.RATING_NOTES.get(
        sc["rating"], "(penjelasan rating tidak tersedia)")

    headline_txt = ""
    if news_sent and news_sent.get("details"):
        top = sorted(news_sent["details"], key=lambda d: -abs(d["sent"]))[:2]
        heads = "\n".join(
            f"  📰 <a href=\"{d.get('link') or 'https://finance.yahoo.com'}\">"
            f"{esc(d['title'][:80])}</a>" + (f" <i>({('%+d' % d['sent'])})</i>" if d["sent"] else "")
            for d in top)
        headline_txt = (f"\nBerita terkini (sentimen {news_sent['emoji']} "
                        f"{news_sent['pct']:+.0f}%):\n{heads}")

    return (
        f"{res.get('stale_note', '')}"
        f"📊 <b>{esc(res['ticker'])}</b>\n"
        f"Harga: <b>{fmt_rp(snap['close'])}</b> "
        f"({snap['ret1d']:+.2f}% 1d, {snap['ret20d']:+.2f}% 20d)\n\n"
        f"{power}\n\n"
        f"Skor teknikal: <b>{sc['pct']:+.0f}%</b> → {sc['emoji']} <b>{sc['rating']}</b>\n"
        f"  <i>{rating_note}</i>\n"
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
        f"{market_context_line(res, mkt)}\n"
        f"🔒 <i>Bukan saran investasi. DYOR!</i>"
    )


def build_quick(res: dict) -> str:
    snap, sc = res["snap"], res["score"]
    # [kendala 1] tanda singkat kalau data dari cache, bukan live
    note = " ⚠️(cache)" if res.get("stale_note") else ""
    return (
        f"{sc['emoji']} <b>{esc(res['ticker'])}</b> {fmt_rp(snap['close'])} "
        f"({snap['ret1d']:+.2f}%) — {sc['pct']:+.0f}% {sc['rating']}{note}"
    )


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
            "Contoh: <code>/berita BBCA</code>", parse_mode=ParseMode.HTML)
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
        src = f" — <i>{d['source']}</i>" if d.get("source") else ""
        lines.append(f"\n{d['date']:%d %b} · <a href=\"{d['link'] or 'https://news.google.com'}\">"
                     f"{d['title'][:110]}</a>{har}{age}{src}")
    lines.append("\n\n🔒 Bukan saran investasi.")
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML,
        disable_web_page_preview=True)


async def cmd_fundamental(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Rincian fundamental emiten (PER, PBV, ROE, dividen, DER)."""
    args = tokens(update)
    if not args:
        await update.effective_message.reply_text(
            "Contoh: <code>/fundamental BBRI</code>", parse_mode=ParseMode.HTML)
        return
    t = args[0].upper()
    await update.effective_chat.send_action(ChatAction.TYPING)
    try:
        fund = await asyncio.to_thread(fundamental.fetch_fundamentals, t)
    except ValueError as e:
        await update.effective_message.reply_text(f"❌ {e}")
        return
    except Exception as e:
        log.warning("fundamental gagal %s: %s", t, e)
        await update.effective_message.reply_text(
            f"❌ Data fundamental untuk {esc(t)} tidak tersedia — "
            "sumber sedang bermasalah (rate-limit/jaringan). Coba lagi nanti.")
        return
    if fund is None:
        await update.effective_message.reply_text(
            f"❌ Data fundamental untuk {esc(t)} tidak tersedia.")
        return
    sc = await asyncio.to_thread(fundamental.score_fundamentals, fund)
    rows = [
        f"🏛️ <b>Fundamental {t}</b> — {fund.get('name')}",
    ]
    if fund.get("sector"):
        rows.append(f"Sektor: {fund['sector']}")
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

    # [P3] tren 4 kuartal + tanggal data — gejala arah laba/pendapatan
    def _trend_line(trend: list[tuple[str, float]] | None) -> str | None:
        """Satu baris tren 4 kuartal + perubahan % kuartal terakhir."""
        if not trend or len(trend) < 2:
            return None
        def _rp(v: float) -> str:
            t = v / 1e12
            return f"Rp{t:.2f}T" if abs(t) >= 1 else f"Rp{v/1e9:.0f}M"
        labels = [lbl for lbl, _ in trend]
        vals = [v for _, v in trend]
        base = "→".join(_rp(v) for v in vals)
        chg = None
        if vals[-2] != 0:
            chg = (vals[-1] / abs(vals[-2]) - 1) * 100
        arrow = " 📈" if (chg or 0) > 0 else (" 📉" if (chg or 0) < 0 else "")
        out = f"{base}{arrow}"
        if chg is not None:
            out += f" ({chg:+.1f}% kuartal terakhir)"
        out += f"\n      kuartal: {esc(' → '.join(labels))}"
        return out

    rows.append(f"\nData per: {esc(str(fund.get('as_of') or 'kuartal terakhir yang tercatat'))}"
                " <i>(laporan keuangan via Yahoo, bisa delay 1-2 kuartal)</i>")
    for lblkey in ("Pendapatan", "Laba bersih"):
        trend = fund.get("rev_trend" if lblkey == "Pendapatan" else "profit_trend")
        line = _trend_line(trend)
        rows.append(f"  {lblkey} 4K: {line if line else '<i>tren tidak tersedia</i>'}")

    rows.append("\nCatatan skor:")
    rows.extend(f"  {n}" for n in sc["notes"])
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

    chat_id = update.effective_chat.id
    wl = store.get_watchlist(chat_id)
    universe = (wl + [t for t in saranmod.DEFAULT_UNIVERSE if t not in wl])[:saranmod.MAX_SCAN]
    await update.effective_message.reply_text(
        f"🔍 Memindai {len(universe)} emiten (teknikal+fundamental+berita), 3-4 menit...")
    txt = await saran_scan_text(chat_id, universe)
    if txt is None:
        await update.effective_message.reply_text("❌ Data sumber error, coba lagi nanti.")
        return
    await update.effective_message.reply_text(
        txt, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def saran_scan_text(chat_id: int, universe: list[str] | None = None) -> str | None:
    """[P6] Inti pemindaian /saran — reusable untuk auto-scan pagi.

    Return teks ringkasan, atau None kalau semua sumber gagal."""
    if universe is None:
        wl = store.get_watchlist(chat_id)
        universe = (wl + [t for t in saranmod.DEFAULT_UNIVERSE if t not in wl])[:saranmod.MAX_SCAN]
    modal = store.get_modal(chat_id)
    analyzed, latest = [], {}
    for t in universe:
        a = await gather_candidate(t)
        if a:
            analyzed.append(a)
            latest[a["res"]["ticker"]] = a["res"]["snap"]["close"]
        await asyncio.sleep(.2)
    if not analyzed:
        return None

    result = saranmod.pick(analyzed, modal)  # [P5] sizing pakai modal latihan
    saranmod.update_paper(chat_id, latest)

    lines = [f"🧭 <b>RINGKASAN SARAN HARIAN — {pd.Timestamp.now(tz=WIB):%d %b %Y}</b>",
             f"Skema cais: jual dibeli penutupan | SL 1×ATR | TP 2×ATR | "
             f"Modal latihan {saranmod.fmt(modal)} (ubah: /modal 10000000)\n"]
    if result["picks"]:
        saranmod.open_paper(
            chat_id, result["picks"],
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
        lines.append(f"  {c['emoji']} <b>{c['ticker']}</b> {c['power']:+d}% — "
                     f"{'jebakan momentum' if c['ret20d'] > 15 else 'teknikal lemah'}")
    lines.append("\n⚠️ Alat bantu latihan, bukan saran formal. DYOR!")
    return "\n".join(lines)


async def cmd_banding(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """[P2] Bandingkan 2–4 emiten: PER/PBV/ROE/DY + return + PowerScore."""
    args = tokens(update)
    if len(args) < 2:
        await update.effective_message.reply_text(
            "Contoh: <code>/banding BBCA BBRI BMRI</code> (2–4 emiten)",
            parse_mode=ParseMode.HTML)
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

    hdr = f"{'EMITEN':<7}{'PER':>6}{'PBV':>6}{'ROE%':>7}{'DY%':>6}{'1 thn':>9}{'20h':>8}{'Pow':>5}"

    def _row(r) -> str:
        pow_txt = "—" if r["power"] is None else f"{r['power']:+.0f}"
        return (
            f"{r['ticker']:<7}"
            f"{banding.fmt_cell(r['per'], '%.1f'):>6}"
            f"{banding.fmt_cell(r['pbv'], '%.2f'):>6}"
            f"{banding.fmt_cell(r['roe'], '%.1f'):>7}"
            f"{banding.fmt_cell(r['dy'], '%.1f'):>6}"
            f"{banding.fmt_cell(r['ret1y'], '%+.0f', '%'):>10}"
            f"{banding.fmt_cell(r['ret20d'], '%+.1f'):>8}"
            f"{pow_txt:>5}")

    body = "\n".join(_row(r) for r in rows)
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
            f"❌ Agenda {esc(t)} tidak tersedia (sumber bermasalah) — coba lagi nanti.")
        return

    lines = [f"📅 <b>Agenda {esc(t)}</b>"]
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
        lines.extend(f"  • {esc(k)}: {v}" for k, v in kal[:6])

    if divs is not None and len(divs):
        last = divs.iloc[-1]
        when = divs.index[-1].strftime("%d %b %Y")
        lines.append(f"\n💵 Dividen terakhir: {float(last):,.1f} /lembar (ex≈{when})")
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
            lines.append(f"\n📊 <b>{row['StockCode']}</b> — {row.get('StockName','')}")
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
        "👋 Halo! Aku <b>SahamBuddy</b> — bot analisis saham Indonesia "
        "(teknikal + fundamental + sentimen berita + data resmi BEI).\n\n"
        "Menu utama:\n"
        "• <b>/analisis BBCA</b> — PowerScore, chart, konfirmasi pro + berita\n"
        "• <b>/skor TLKM BBRI</b> — ringkasan cepat\n"
        "• <b>/berita BBCA</b> — 7 berita terbaru + sentimen (ML lokal)\n"
        "• <b>/fundamental BBRI</b> — PER/PBV/ROE/dividen/DER\n"
        "• <b>/saran</b> — pemindaian harian: kandidat BUY + entry/SL/TP + "
        "ukuran posisi (1% modal), dilacak otomatis (SL/TP kena = bot kabarin); "
        "<b>/saran list</b>, ubah modal: <b>/modal 10000000</b>\n"
        "• <b>/bandar</b> — IHSG resmi BEI + top movers; <b>/bandar BBCA</b>\n"
        "• <b>/banding BBCA BBRI</b> — tabel PER/PBV/ROE/return + konteks IHSG\n"
        "• <b>/agenda BBCA</b> — ex-date dividen & aksi korporasi\n"
        "• <b>/backtest BBCA</b> — akurasi statistik + simulasi transaksi\n"
        "• <b>/watch add ANTM</b> / <b>/alert add BBCA jump</b> — alarm otomatis\n"
        "• <b>/belajar RSI</b> — kamus istilah: cara baca + jebakan umumnya\n"
        "• <b>/auto on</b> — ringkasan harian otomatis ±08:30 WIB (Senin–Jumat)\n\n"
        "⚠️ Bukan saran investasi — selalu DYOR!",
        parse_mode=ParseMode.HTML)


async def cmd_analisis(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = tokens(update)
    if not args:
        await update.effective_message.reply_text(
            "Contoh: <code>/analisis BBCA</code> atau <code>/analisis BBCA TLKM</code>",
            parse_mode=ParseMode.HTML)
        return

    mkt = await asyncio.to_thread(banding.market_context)  # IHSG ^JKSE (cache 6 jam)
    for t in args[:5]:  # max 5 emiten per permintaan
        try:
            res = await fetch_and_analyze(t)
        except ValueError as e:
            await update.effective_message.reply_text(f"❌ {esc(e)}")
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

        # [P2] konteks pasar: bawa angka IHSG + return 1 thn emiten ke laporan
        try:
            close = res["ind"]["close"].reset_index(drop=True)
            res["ret1y"] = float(close.iloc[-1] / close.iloc[0] - 1) * 100
        except Exception:
            res["ret1y"] = None
        res["mkt"] = mkt

        await update.effective_message.reply_text(
            build_report(res, fsc, nsent), parse_mode=ParseMode.HTML)

        path = os.path.join(TMP_DIR, f"chart_{int(time.time() * 1000)}.png")
        await asyncio.to_thread(build_chart, res["ticker"].upper(), res["ind"],
                                res["score"]["pct"], path)
        try:
            with open(path, "rb") as f:
                await update.effective_message.reply_photo(
                    photo=f, caption=f"📈 Chart {res['ticker']} (6 bulan terakhir)")
        finally:
            if os.path.exists(path):
                os.remove(path)


async def cmd_skor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = tokens(update)
    if not args:
        watch = store.get_watchlist(update.effective_chat.id)
        args = watch or ["BBCA"]
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
                "Watchlist masih kosong. Tambahkan dulu: <code>/watch add BBCA</code>",
                parse_mode=ParseMode.HTML)
            return
        lines = [store_format_quick(wl, chat_id)]
        lines.append("Ganti detail dengan /skor nama-saham")
        await update.effective_message.reply_text(
            "\n".join(l for l in lines if l), parse_mode=ParseMode.HTML)
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
            "Format:\n"
            "<code>/alert add BBCA jump breakout oversold</code>\n"
            "<code>/alert list</code>\n"
            "<code>/alert remove BBCA</code>\n\n"
            "Jenis alert:\n"
            + "\n".join(f"  <code>{k}</code> — {v}" for k, v in alerts.KIND_EXAMPLES.items()),
            parse_mode=ParseMode.HTML)
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
            "Contoh: <code>/backtest BBCA</code>\n"
            "Mengukur akurasi skor bot: tiap hari 5 tahun terakhir, bot "
            "berteori, lalu dicek hasil 20 hari kemudian.",
            parse_mode=ParseMode.HTML)
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




# ---------------------------------------------------------------- scanner

async def morning_scan(app: Application):
    """[P6] Auto-scan pagi: ringkasan /saran + rekap watchlist, 08:30 WIB,
    Senin–Jumat — hanya ke chat yang mengaktifkan /auto on."""
    now = pd.Timestamp.now(tz=WIB)
    if now.weekday() >= 5:
        return  # Sabtu/Minggu tidak jalan
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
                except (ValueError, Exception):
                    continue
            if recap:
                txt += "\n\n📋 <b>Rekap watchlist</b>\n" + "\n".join(recap)
            await app.bot.send_message(
                int(cid),
                f"🌅 <b>AUTOSCAN {now:%a, %d %b %Y}</b>\n\n" + txt,
                parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except Exception:
            log.exception("autoscan gagal utk %s", cid)
        await asyncio.sleep(1)


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
    log.exception("handler kena error")
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
    app = Application.builder().token(token).build()

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
    reg("start", cmd_start)
    reg("help", cmd_start)
    reg(["analisa", "analysis", "analisis"], cmd_analisis)
    reg("skor", cmd_skor)
    reg(["berita", "news"], cmd_berita)
    reg(["fundamental", "fundi"], cmd_fundamental)
    reg("backtest", cmd_backtest)
    reg("watch", cmd_watch)
    reg(["saran", "recommendasi"], cmd_saran)
    reg(["bandar", "ihsg"], cmd_bandar)
    reg(["banding", "bandingkan"], cmd_banding)
    reg("alert", cmd_alert)
    reg(["agenda", "korporasi"], cmd_agenda)
    reg("modal", cmd_modal)
    reg("auto", cmd_auto)
    reg(["belajar", "learn", "apa"], cmd_belajar)

    job_queue = app.job_queue
    job_queue.run_repeating(scan_alerts, interval=config.ALERT_SCAN_MINUTES * 60, first=10)
    # [P6] auto-scan pagi tiap 08:30 WIB (reguler job dibatasi weekday di dalam fungsi)
    import datetime as _dt
    job_queue.run_daily(morning_scan, time=_dt.time(8, 30, tzinfo=WIB),
                        name="morning-scan")

    log.info("Bot jalan. Tekan Ctrl+C untuk berhenti.")
    app.add_error_handler(on_error)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
