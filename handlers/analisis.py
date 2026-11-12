"""Handler analisis: /analisis, /skor, /banding.

Dependensi bot (fetch_and_analyze, build_report, chart, formatter, reply_long,
tokens) diterima lewat `deps` agar monkeypatch di tes tetap bekerja.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core import analysis, banding, data, fundamental, news, store
from core.reports import esc

log = logging.getLogger("sahambuddy")


async def do_analisis(update: Update, tickers: list[str], deps):
    if not await deps.cooldown_ok(update, "analisis"):
        return
    msg = update.effective_message
    chat_id = update.effective_chat.id if update.effective_chat else None
    wl = (await asyncio.to_thread(store.get_watchlist, chat_id)
          if chat_id is not None else [])
    mkt = await asyncio.to_thread(banding.market_context)
    for t in tickers[:5]:
        try:
            res = await deps.fetch_and_analyze(t)
        except ValueError as e:
            await msg.reply_text(f"❌ {esc(e)}", parse_mode=ParseMode.HTML, reply_markup=deps.home_markup())
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
        await deps.reply_long(
            msg,
            deps.build_report(res, fsc, nsent, mkt_ctx),
            reply_markup=deps.result_markup("analisis", sym),
        )
        os.makedirs(deps.TMP_DIR, exist_ok=True)
        path = os.path.join(deps.TMP_DIR, f"chart_{sym}_{time.time_ns()}.png")
        try:
            await asyncio.to_thread(
                deps.build_chart, sym.upper(), res["ind"], res["score"]["pct"], path)
            def _send_chart(_path=path, _sym=sym):
                # buka ulang tiap percobaan: handle yang sudah terpakai tidak
                # bisa dikirim ulang oleh retry
                with open(_path, "rb") as f:
                    return msg.reply_photo(
                        photo=f,
                        caption=f"📈 Chart {_sym} (6 bulan terakhir)",
                        reply_markup=deps.result_markup("analisis", _sym),
                    )

            await deps.send_with_retry(_send_chart, what="kirim chart")
        except Exception:
            log.exception("chart gagal utk %s", sym)
        finally:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass


async def do_skor(update: Update, tickers: list[str], deps,
                        from_menu: bool = False):
    msg = update.effective_message
    wl = await asyncio.to_thread(store.get_watchlist,
                                 update.effective_chat.id)
    args = tickers or wl or ["BBCA"]
    lines = []
    for t in args[:8]:
        try:
            res = await deps.fetch_and_analyze(t)
            lines.append(deps.build_quick(res))
        except ValueError as e:
            lines.append(f"❌ {esc(e)}")
    await deps.reply_long(
        msg,
        "\n".join(lines) or "Watchlist kosong.",
        reply_markup=deps.home_markup() if from_menu else None,
    )


async def cmd_banding(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    """[P2] Bandingkan 2–4 emiten: PER/PBV/ROE/DY + return + PowerScore."""
    args = deps.tokens(update)
    if len(args) < 2:
        await update.effective_message.reply_text(
            "Contoh: <code>/banding BBCA BBRI BMRI</code> (2–4 emiten)",
            parse_mode=ParseMode.HTML)
        return
    if not await deps.cooldown_ok(update, "banding"):
        return
    emiten = list(dict.fromkeys(a.upper() for a in args[:4]))
    await update.effective_message.reply_text(
        f"⚖️ Membandingkan {len(emiten)} emiten… (data dari sumber yang sama "
        "dengan /fundamental & /analisis)")

    rows = []
    for t in emiten:
        try:
            res = await deps.fetch_and_analyze(t)
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


async def cmd_analisis(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    args = deps.tokens(update)
    if not args:
        wl = await asyncio.to_thread(store.get_watchlist,
                                     update.effective_chat.id)
        await update.effective_message.reply_text(
            "📊 Analisis — pilih emiten:",
            reply_markup=deps.ticker_pick_markup("analisis", wl))
        return
    await do_analisis(update, args, deps)


async def cmd_skor(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    args = deps.tokens(update)
    if not args:
        wl = await asyncio.to_thread(store.get_watchlist,
                                     update.effective_chat.id)
        await update.effective_message.reply_text(
            "⚡ Skor — pilih emiten:",
            reply_markup=deps.ticker_pick_markup("skor", wl))
        return
    await do_skor(update, args, deps)
