"""Handler `/jurnal` & konfirmasi eksekusi manual Stockbit (P28).

Dependensi bot (`reply_long`, `_pending_set`, `_pending_clear`, `_parse_rupiah`,
`home_markup`, `cooldown_ok`) diteruskan via parameter `deps` = modul `bot`.
Tidak pernah melakukan auto-trading, login Stockbit, atau menyimpan kredensial.
"""
from __future__ import annotations

import asyncio
import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core import (fundamental, jurnal as jurnalmod, marketcal, store)
from core.reports import esc

log = logging.getLogger("sahambuddy")

_JURNAL_USAGE = (
    "📒 <b>Jurnal Eksekusi Stockbit &amp; Realisasi (P28)</b>\n\n"
    "<code>/jurnal</code> — slippage, win rate &amp; profit factor (asli vs paper)\n"
    "<code>/jurnal beli BBCA 5 9050 [sinyal]</code> — catat beli manual\n"
    "<code>/jurnal jual BBCA 5 9500 [sinyal]</code> — catat jual &amp; realisasi\n"
    "<code>/jurnal riwayat</code> — 15 eksekusi terakhir beserta slippage\n\n"
    "<i>Atau ketuk tombol 📝 Sudah beli di Stockbit setelah /rencana, /cek, "
    "/beli, atau /sinyal KODE. Bot tidak pernah login/order di Stockbit.</i>"
)


def _norm_ticker(raw: str) -> str | None:
    t = (raw or "").strip().upper()
    t = t[:-3] if t.endswith(".JK") else t
    return t if re.fullmatch(r"[A-Z]{2,6}", t) else None


def _safe_sector(ticker: str) -> str | None:
    try:
        fr = fundamental.fetch_fundamentals(ticker)
        return (fr or {}).get("sector")
    except Exception:
        log.warning("sektor %s tidak terbaca saat catat jurnal", ticker, exc_info=True)
        return None


def exec_done_markup(*, from_menu: bool = True) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton("📒 Jurnal Eksekusi", callback_data="run:jurnal"),
        InlineKeyboardButton("💼 Portofolio", callback_data="run:portofolio"),
    ]]
    if from_menu:
        rows.append([InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def journal_markup(*, from_menu: bool = False) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton("🧾 Riwayat Jurnal", callback_data="run:jurnal_history"),
        InlineKeyboardButton("💼 Portofolio", callback_data="run:portofolio"),
    ]]
    if from_menu:
        rows.append([InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def confirm_exec_markup(action: str, ticker: str, deps,
                        *, from_menu: bool = False) -> InlineKeyboardMarkup:
    """Tombol di bawah /rencana & /cek: konfirmasi Stockbit (+ menu bila from_menu)."""
    if from_menu:
        return deps.result_markup(action, ticker)
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        "📝 Sudah beli di Stockbit", callback_data=f"exec:buy:{ticker}",
    )]])


async def _record_buy_and_reply(
    msg,
    chat_id: int,
    ticker: str,
    lots: int,
    exec_price: float,
    signal_price: float | None,
    *,
    sl: float | None = None,
    tp: float | None = None,
    source: str = "manual",
    deps,
) -> None:
    today = marketcal.now_wib().date().isoformat()
    sector = await asyncio.to_thread(_safe_sector, ticker)
    try:
        res = await asyncio.to_thread(
            store.record_execution_buy,
            chat_id, ticker, lots, exec_price, today,
            signal_price=signal_price, sl=sl, tp=tp,
            source=source, sector=sector,
        )
    except ValueError as e:
        await msg.reply_text(f"❌ {esc(e)}", parse_mode=ParseMode.HTML)
        return
    slip_txt = jurnalmod.format_slippage_note(res)
    out = [
        f"✅ <b>Eksekusi BELI {esc(ticker)} tercatat di Jurnal &amp; Portofolio Asli</b>",
        f"• Terisi: <b>{lots} lot</b> @ <b>{deps._money(exec_price)}</b> = "
        f"{deps._money(lots * 100 * exec_price)}",
        f"• Slippage: {slip_txt}",
        f"• Posisi asli sekarang: <b>{res['lots']} lot</b> @ rata-rata "
        f"{deps._money(res['avg_price'])}",
    ]
    if sl or tp:
        levels = " · ".join(
            x for x in (
                f"SL {deps._money(sl)}" if sl else None,
                f"TP {deps._money(tp)}" if tp else None,
            ) if x
        )
        if levels:
            out.append(f"• Rencana awal: {levels} (pasang alert manual di Stockbit)")
    out += [
        "",
        "<i>Bukan order otomatis — data berasal dari konfirmasi manualmu. Lihat: /jurnal</i>",
    ]
    await msg.reply_text(
        "\n".join(out), parse_mode=ParseMode.HTML,
        reply_markup=exec_done_markup(from_menu=True),
    )


async def _record_sell_and_reply(
    msg,
    chat_id: int,
    ticker: str,
    lots: int,
    exec_price: float,
    signal_price: float | None,
    *,
    source: str = "manual",
    deps,
) -> None:
    today = marketcal.now_wib().date().isoformat()
    try:
        res = await asyncio.to_thread(
            store.record_execution_sell,
            chat_id, ticker, lots, exec_price, today,
            signal_price=signal_price, source=source,
        )
    except ValueError as e:
        await msg.reply_text(f"❌ {esc(e)}", parse_mode=ParseMode.HTML)
        return
    net_pct = f"{res['net_pnl_pct']:+.2f}%".replace(".", ",")
    out = [
        f"✅ <b>Eksekusi JUAL {esc(ticker)} tercatat di Jurnal &amp; Portofolio Asli</b>",
        f"• Terjual: <b>{lots} lot</b> @ <b>{deps._money(exec_price)}</b> "
        f"(avg beli {deps._money(res['avg_price'])})",
        f"• Realisasi bruto: {deps._money_delta(res['gross_pnl'])}",
        f"• Realisasi neto (fee BEI 0,40%): <b>{deps._money_delta(res['net_pnl'])}</b> ({net_pct})",
        f"• Slippage keluar: {jurnalmod.format_slippage_note(res)}",
        (f"• Sisa posisi: {res['lots_left']} lot @ {deps._money(res['avg_price'])}"
         if res["lots_left"] else "• Posisi tertutup penuh."),
        "",
        "<i>Lihat Win Rate &amp; Profit Factor realisasi: /jurnal</i>",
    ]
    await msg.reply_text(
        "\n".join(out), parse_mode=ParseMode.HTML,
        reply_markup=exec_done_markup(from_menu=True),
    )


async def do_jurnal(update: Update, args: list[str], deps,
                    *, from_menu: bool = False) -> None:
    """Handler /jurnal [riwayat|beli|jual ...]."""
    msg = update.effective_message
    chat_id = update.effective_chat.id
    sub = (args[0].lower() if args else "")

    if sub in ("riwayat", "history", "log"):
        limit = 15
        if len(args) > 1 and args[1].isdigit():
            limit = max(1, min(30, int(args[1])))
        execs = await asyncio.to_thread(store.real_executions, chat_id, limit)
        await deps.reply_long(
            msg,
            jurnalmod.format_journal_history(execs),
            reply_markup=deps.home_markup() if from_menu else None,
        )
        return

    if sub in ("beli", "buy", "jual", "sell"):
        if len(args) < 4:
            await msg.reply_text(_JURNAL_USAGE, parse_mode=ParseMode.HTML)
            return
        t = _norm_ticker(args[1])
        if not t:
            await msg.reply_text(
                f"Kode <code>{esc(args[1])}</code> tidak valid.\n\n{_JURNAL_USAGE}",
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            lots = int(args[2].replace(".", "").replace(",", ""))
        except ValueError:
            await msg.reply_text(
                "Jumlah lot harus angka, mis. <code>/jurnal beli BBCA 5 9050 9000</code>.",
                parse_mode=ParseMode.HTML,
            )
            return
        exec_px = deps._parse_rupiah(args[3])
        if exec_px is None:
            await msg.reply_text(
                "Harga eksekusi harus angka, mis. <code>9050</code>.",
                parse_mode=ParseMode.HTML,
            )
            return
        sig_px = deps._parse_rupiah(args[4]) if len(args) > 4 else None
        if len(args) > 4 and sig_px is None:
            await msg.reply_text(
                "Harga sinyal opsional tidak valid, mis. <code>9000</code>.",
                parse_mode=ParseMode.HTML,
            )
            return
        source = "manual"
        sl = tp = None
        if sig_px is None:
            ctx = await asyncio.to_thread(store.get_signal_context, chat_id, t)
            today = marketcal.now_wib().date().isoformat()
            if ctx and ctx.get("trade_date") == today:
                sig_px = ctx.get("signal_price")
                sl = ctx.get("sl")
                tp = ctx.get("tp")
                source = ctx.get("source") or "manual"
        if sub in ("beli", "buy"):
            await _record_buy_and_reply(
                msg, chat_id, t, lots, exec_px, sig_px,
                sl=sl, tp=tp, source=source, deps=deps,
            )
        else:
            await _record_sell_and_reply(
                msg, chat_id, t, lots, exec_px, sig_px,
                source=source, deps=deps,
            )
        return

    if sub in ("help", "bantuan", "?"):
        await deps.reply_long(
            msg, _JURNAL_USAGE,
            reply_markup=deps.home_markup() if from_menu else None,
        )
        return

    if sub:
        await deps.reply_long(
            msg, f"Sub-perintah <code>{esc(sub)}</code> tidak dikenal.\n\n{_JURNAL_USAGE}",
            reply_markup=deps.home_markup() if from_menu else None,
        )
        return

    execs = await asyncio.to_thread(store.real_executions, chat_id, 500)
    paper = await asyncio.to_thread(store.get_paper, chat_id)
    positions = await asyncio.to_thread(store.real_positions, chat_id)
    real_sum = jurnalmod.summarize_executions(execs)
    paper_sum = jurnalmod.summarize_paper_realized(paper)
    txt = jurnalmod.format_journal_report(
        real_sum, paper_sum, open_real_count=len(positions),
    )
    await deps.reply_long(msg, txt, reply_markup=journal_markup(from_menu=from_menu))


async def cmd_jurnal(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps) -> None:
    """Jurnal eksekusi Stockbit & realisasi (asli vs paper)."""
    await do_jurnal(update, deps.tokens(update), deps)


async def handle_exec_callback(update: Update, cb_data: str, deps) -> bool:
    """Tangani callback tombol `exec:*`. Return True bila ditangani."""
    if not cb_data.startswith("exec:"):
        return False
    q = update.callback_query
    msg = q.message if q else update.effective_message
    if not msg:
        return True
    chat_id = update.effective_chat.id

    if cb_data == "exec:cancel":
        deps._pending_clear(chat_id)
        await deps._safe_edit(
            msg, "❌ Pencatatan eksekusi Stockbit dibatalkan.",
            reply_markup=deps.home_markup(),
        )
        return True

    parts = cb_data.split(":", 2)
    if len(parts) != 3:
        await msg.reply_text("❌ Tombol eksekusi tidak valid.")
        return True
    _, kind, raw_ticker = parts
    ticker = _norm_ticker(raw_ticker)
    if not ticker or kind not in ("buy", "sell"):
        await msg.reply_text("❌ Kode saham eksekusi tidak valid.")
        return True

    cancel_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Batal", callback_data="exec:cancel"),
        InlineKeyboardButton("🏠 Menu Utama", callback_data="menu"),
    ]])
    ctx = await asyncio.to_thread(store.get_signal_context, chat_id, ticker)

    if kind == "buy":
        deps._pending_set(chat_id, f"exec_buy:{ticker}")
        lines = [f"📝 <b>Catat BELI {esc(ticker)} (Stockbit)</b>", ""]
        if ctx:
            bits = [f"Harga sinyal acuan: <b>{deps._money(ctx['signal_price'])}</b>"]
            if ctx.get("source"):
                bits[0] += f" <i>(dari /{esc(ctx['source'])})</i>"
            lines.extend(bits)
            if ctx.get("suggested_lots"):
                lines.append(
                    f"Ukuran rencana: <b>{int(ctx['suggested_lots'])} lot</b> · "
                    f"Ketik harga eksekusimu saja (mis. <code>9050</code>) atau "
                    "<code>lot harga</code> (mis. <code>5 9050</code>)."
                )
            else:
                lines.append(
                    "Ketik <b>jumlah lot</b> dan <b>harga beli Stockbit</b> "
                    "(contoh: <code>5 9050</code>):"
                )
        else:
            lines.append("Harga sinyal terakhir belum tersimpan (akan dicatat tanpa acuan sinyal).")
            lines.append(
                "Ketik <b>jumlah lot</b> dan <b>harga beli Stockbit</b> "
                "(contoh: <code>5 9050</code>):"
            )
        await msg.reply_text(
            "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=cancel_kb,
        )
        return True

    positions = await asyncio.to_thread(store.real_positions, chat_id)
    held = next((p for p in positions if p["ticker"] == ticker), None)
    if not held:
        await msg.reply_text(
            f"ℹ️ Kamu belum punya posisi terbuka <b>{esc(ticker)}</b> di portofolio asli.\n"
            f"Catat beli dulu lewat tombol <b>📝 Sudah beli di Stockbit</b> atau "
            f"<code>/jurnal beli {esc(ticker)} 5 9000</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=deps.home_markup(),
        )
        return True

    deps._pending_set(chat_id, f"exec_sell:{ticker}")
    held_lots = int(held["lots"])
    lines = [
        f"📝 <b>Catat JUAL {esc(ticker)} (Stockbit)</b>",
        "",
        f"Posisi terbuka: <b>{held_lots} lot</b> @ avg {deps._money(held['avg_price'])}",
    ]
    if ctx:
        lines.append(f"Harga acuan terakhir: <b>{deps._money(ctx['signal_price'])}</b>")
    lines.append(
        f"Ketik <code>lot harga</code> (mis. <code>2 9500</code>), atau ketik "
        f"harganya saja (mis. <code>9500</code>) untuk jual semua {held_lots} lot:"
    )
    await msg.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=cancel_kb,
    )
    return True


async def handle_pending_exec(
    update: Update,
    chat_id: int,
    action: str,
    raw_text: str,
    deps,
) -> bool:
    """Proses balasan teks setelah menekan tombol konfirmasi Stockbit."""
    if not (action.startswith("exec_buy:") or action.startswith("exec_sell:")):
        return False
    msg = update.effective_message
    side, raw_ticker = action.split(":", 1)
    ticker = _norm_ticker(raw_ticker)
    if not ticker:
        deps._pending_clear(chat_id)
        await msg.reply_text("❌ Aksi eksekusi tidak valid; dibuka ulang dari menu.")
        return True

    ctx = await asyncio.to_thread(store.get_signal_context, chat_id, ticker)
    default_lots: int | None = None
    if side == "exec_buy":
        default_lots = (ctx or {}).get("suggested_lots")
    else:
        positions = await asyncio.to_thread(store.real_positions, chat_id)
        held = next((p for p in positions if p["ticker"] == ticker), None)
        default_lots = int(held["lots"]) if held else None

    parsed = jurnalmod.parse_exec_input(raw_text, default_lots=default_lots)
    if not parsed:
        hint = (
            f"atau cukup harganya saja (otomatis <b>{default_lots} lot</b>), mis. <code>9050</code>"
            if default_lots else "dengan format <code>lot harga</code>, mis. <code>5 9050</code>"
        )
        await msg.reply_text(
            f"❌ Input eksekusi <b>{esc(ticker)}</b> belum terbaca.\n"
            f"Ketik <code>5 9050</code> {hint}, atau ketuk Batal.",
            parse_mode=ParseMode.HTML,
        )
        return True

    lots, exec_px = parsed
    deps._pending_clear(chat_id)
    sig_px = (ctx or {}).get("signal_price")
    src = (ctx or {}).get("source") or "manual"
    if side == "exec_buy":
        await _record_buy_and_reply(
            msg, chat_id, ticker, lots, exec_px, sig_px,
            sl=(ctx or {}).get("sl"), tp=(ctx or {}).get("tp"),
            source=src, deps=deps,
        )
    else:
        await _record_sell_and_reply(
            msg, chat_id, ticker, lots, exec_px, sig_px,
            source=src, deps=deps,
        )
    return True
