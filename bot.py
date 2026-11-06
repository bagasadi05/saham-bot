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
import logging
import os
import time
from zoneinfo import ZoneInfo

import pandas as pd
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (Application, CommandHandler, ContextTypes)

import config
import config
from core import alerts, analysis, bandar, data, fundamental, news, powerscore, saran as saranmod, store
from core.chart import build_chart

import backtest as btmod

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)",
                    level=logging.INFO)
logging.getLogger("telegram").setLevel(logging.WARNING)
log = logging.getLogger("sahambuddy")

WIB = ZoneInfo("Asia/Jakarta")
TMP_DIR = os.getenv("TMP_DIR", "temp")
os.makedirs(TMP_DIR, exist_ok=True)


# ---------------------------------------------------------------- helpers

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
    return res


# ---------------------------------------------------------------- report

def build_report(res: dict, fund: dict | None, news_sent: dict | None) -> str:
    snap, sc, sr = res["snap"], res["score"], res["sr"]
    bull_txt = "\n".join(f"  ✅ {s}" for s in sc["bull"]) or "  —"
    bear_txt = "\n".join(f"  ❌ {s}" for s in sc["bear"]) or "  —"
    ma_row = " | ".join(
        f"MA{n}:{snap['sma' + n]}" if snap.get('sma' + n) else f"MA{n}:—"
        for n in ("20", "50", "200"))
    power = powerscore.judge(sc["pct"], fund, news_sent, snap)
    conf = analysis.extra_confirmations(res["ind"])
    conf_txt = "\n".join(f"  • {c}" for c in conf) if conf else ""

    headline_txt = ""
    if news_sent and news_sent.get("details"):
        top = sorted(news_sent["details"], key=lambda d: -abs(d["sent"]))[:2]
        heads = "\n".join(
            f"  📰 <a href=\"{d.get('link') or 'https://finance.yahoo.com'}\">"
            f"{d['title'][:80]}</a>" + (f" <i>({('%+d' % d['sent'])})</i>" if d["sent"] else "")
            for d in top)
        headline_txt = (f"\nBerita terkini (sentimen {news_sent['emoji']} "
                        f"{news_sent['pct']:+.0f}%):\n{heads}")

    return (
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
    return (
        f"{sc['emoji']} <b>{res['ticker']}</b> {fmt_rp(snap['close'])} "
        f"({snap['ret1d']:+.2f}%) — {sc['pct']:+.0f}% {sc['rating']}"
    )


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
    fund = await asyncio.to_thread(fundamental.fetch_fundamentals, t)
    if fund is None:
        await update.effective_message.reply_text(
            f"❌ Data fundamental untuk {t} tidak tersedia.")
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
        "• <b>/saran</b> — pemindaian harian: kandidat BUY + entry/SL/TP, "
        "dilacak otomatis (SL/TP kena = bot kabarin); <b>/saran list</b>\n"
        "• <b>/bandar</b> — IHSG resmi BEI + top movers; <b>/bandar BBCA</b>\n"
        "• <b>/backtest BBCA</b> — akurasi statistik + simulasi transaksi\n"
        "• <b>/watch add ANTM</b> / <b>/alert add BBCA jump</b> — alarm otomatis\n\n"
        "⚠️ Bukan saran investasi — selalu DYOR!",
        parse_mode=ParseMode.HTML)


async def cmd_analisis(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = tokens(update)
    if not args:
        await update.effective_message.reply_text(
            "Contoh: <code>/analisis BBCA</code> atau <code>/analisis BBCA TLKM</code>",
            parse_mode=ParseMode.HTML)
        return

    for t in args[:5]:  # max 5 emiten per permintaan
        try:
            res = await fetch_and_analyze(t)
        except ValueError as e:
            await update.effective_message.reply_text(f"❌ {e}")
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

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler(["analisa", "analysis", "analisis"], cmd_analisis))
    app.add_handler(CommandHandler("skor", cmd_skor))
    app.add_handler(CommandHandler(["berita", "news"], cmd_berita))
    app.add_handler(CommandHandler(["fundamental", "fundi"], cmd_fundamental))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler(["saran", "recommendasi"], cmd_saran))
    app.add_handler(CommandHandler(["bandar", "ihsg"], cmd_bandar))
    app.add_handler(CommandHandler("alert", cmd_alert))
    app.add_handler(CommandHandler("backtest", cmd_backtest))

    job_queue = app.job_queue
    job_queue.run_repeating(scan_alerts, interval=config.ALERT_SCAN_MINUTES * 60, first=10)

    log.info("Bot jalan. Tekan Ctrl+C untuk berhenti.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
