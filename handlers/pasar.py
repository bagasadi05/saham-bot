"""Handler `/pasar` (`/market`): laporan kondisi pasar BEI (pola `deps`).

Mengambil bahan secara paralel (IHSG `^JKSE`, snapshot `/scan` lokal, indeks
sektor BEI, dan berita pasar Google News RSS) dengan degradasi jujur bila
salah satu sumber gagal/diblokir.
"""
from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from core import bandar, data, marketcal, news, pasar as pasarmod, store

log = logging.getLogger("sahambuddy")


def pasar_markup() -> InlineKeyboardMarkup:
    """Tombol tindak lanjut kontekstual di bawah laporan `/pasar`."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏭 Sektor BEI", callback_data="run:sektor"),
            InlineKeyboardButton("📡 Sinyal Pasar", callback_data="run:sinyal"),
        ],
        [
            InlineKeyboardButton("✨ BELI Berkualitas", callback_data="run:sinyal_bagus"),
            InlineKeyboardButton("🏦 Data BEI", callback_data="run:bandar"),
        ],
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="run:pasar"),
            InlineKeyboardButton("🏠 Menu Utama", callback_data="menu"),
        ],
    ])


async def gather_market_data(
    deps,
    *,
    include_idx: bool = True,
    include_news: bool = True,
) -> dict:
    """Kumpulkan bahan kondisi pasar secara paralel & fail-soft."""
    data_mod = getattr(deps, "data", data)
    bandar_mod = getattr(deps, "bandar", bandar)
    news_mod = getattr(deps, "news", news)
    store_mod = getattr(deps, "store", store)

    async def _get_ihsg():
        return await asyncio.to_thread(
            data_mod.fetch_history, "^JKSE", "2y", raw_symbol=True
        )

    async def _get_scan():
        rows = await asyncio.to_thread(store_mod.load_market_scan)
        meta = await asyncio.to_thread(store_mod.market_scan_meta)
        return rows, meta

    async def _get_idx():
        if not include_idx:
            return None, "tidak diambil pada ringkasan pagi"
        idx_exc = getattr(bandar_mod, "IDXTidakTersedia", bandar.IDXTidakTersedia)
        try:
            rows = await asyncio.to_thread(bandar_mod.get_index_summary)
            return rows, None
        except idx_exc as exc:
            log.warning("indeks sektor BEI diblokir pada /pasar: %s", exc)
            return None, "data BEI (idx.co.id) sedang diblokir Cloudflare"
        except Exception as exc:
            log.warning("gagal ambil indeks sektor BEI pada /pasar: %s", exc)
            return None, "gagal membaca data indeks sektor BEI"

    async def _get_news():
        if not include_news:
            return None
        return await asyncio.to_thread(news_mod.fetch_market_news, 10)

    ihsg_res, scan_res, idx_res, news_res = await asyncio.gather(
        _get_ihsg(), _get_scan(), _get_idx(), _get_news(),
        return_exceptions=True,
    )

    ihsg_df = None if isinstance(ihsg_res, Exception) else ihsg_res
    if isinstance(scan_res, Exception):
        scan_rows, scan_meta = [], {}
    else:
        scan_rows, scan_meta = scan_res

    if isinstance(idx_res, Exception):
        index_rows, index_error = None, "gagal membaca data indeks sektor BEI"
    else:
        index_rows, index_error = idx_res

    news_items = None if isinstance(news_res, Exception) else news_res
    news_sent = None
    if news_items:
        try:
            news_sent = await asyncio.to_thread(news_mod.news_sentiment, news_items)
        except Exception:
            news_sent = None

    now = marketcal.now_wib()
    return pasarmod.build_market_report(
        ihsg_df=ihsg_df,
        scan_rows=scan_rows,
        scan_meta=scan_meta,
        index_rows=index_rows,
        index_error=index_error,
        news_items=news_items,
        news_sent=news_sent,
        as_of_label=f"{now:%d %b %Y %H:%M} WIB",
    )


async def gather_market_brief(deps) -> str:
    """Ringkasan kondisi pasar ringkas untuk `morning_scan` (fail-soft)."""
    try:
        report = await gather_market_data(
            deps, include_idx=False, include_news=True,
        )
        return pasarmod.format_market_brief(report)
    except Exception:
        log.warning("ringkasan kondisi pasar pagi gagal disusun", exc_info=True)
        return ""


async def do_pasar(
    update: Update,
    args: list[str],
    deps,
    *,
    from_menu: bool = False,
) -> None:
    """Tampilkan laporan kondisi pasar BEI: `/pasar` (`/market`)."""
    del args, from_menu
    if not await deps.cooldown_ok(update, "pasar"):
        return
    msg = update.effective_message
    await update.effective_chat.send_action(ChatAction.TYPING)
    report = await gather_market_data(deps, include_idx=True, include_news=True)
    text = pasarmod.format_market_report(
        report, disclaimer=getattr(deps, "_SINYAL_DISCLAIMER", None)
    )
    await deps.reply_long(
        msg,
        text,
        disable_web_page_preview=True,
        reply_markup=pasar_markup(),
    )


async def cmd_pasar(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps) -> None:
    """Laporan kondisi pasar BEI (IHSG, rezim, breadth, sektor, berita): /pasar."""
    del ctx
    await do_pasar(update, deps.tokens(update), deps)
