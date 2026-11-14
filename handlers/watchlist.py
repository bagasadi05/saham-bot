"""Handler watchlist & preferensi: /watch, /alert, /modal, /auto.

Dependensi bot (menu markup, formatter, `fetch_and_analyze`, `MAX_WATCH`)
diterima lewat `deps`; sisanya diimpor langsung dari `core`.
"""
from __future__ import annotations

import asyncio
import logging
import re

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core import alerts, data, pricing, saran as saranmod, store

log = logging.getLogger("sahambuddy")

_TARGET_INPUT_RE = re.compile(
    r"^(?:target)?(>=|<=|>|<|:)?\s*(?:rp\.?\s*)?([\d.,]+)$",
    re.IGNORECASE,
)


def _parse_target_kind(token: str) -> str | None:
    """Normalkan input target harga (`9500`, `>=9500`, `<9000`) ke `target>=9500`/`target<=9000`."""
    raw = (token or "").strip().lower()
    if not raw:
        return None
    m = _TARGET_INPUT_RE.match(raw)
    if not m:
        return None
    op_raw, num_raw = m.group(1) or ">=", m.group(2)
    cleaned = num_raw.replace(".", "").replace(",", ".")
    try:
        val = float(cleaned)
    except ValueError:
        return None
    if val <= 0:
        return None
    tick_val = pricing.round_tick(val)
    op = "<=" if op_raw in ("<", "<=") else ">="
    px_str = str(int(round(tick_val))) if tick_val == int(tick_val) else f"{tick_val:g}"
    return f"target{op}{px_str}"


async def do_watch_list(update: Update, deps):
    msg = update.effective_message
    chat_id = update.effective_chat.id
    wl = await asyncio.to_thread(store.get_watchlist, chat_id)
    if not wl:
        await msg.reply_text(
            "Watchlist masih kosong. Tambahkan dulu lewat tombol ➕ Tambah.",
            reply_markup=deps.watch_menu_markup(),
        )
        return
    lines = [deps.store_format_quick(wl)]
    for t in wl:
        try:
            res = await deps.fetch_and_analyze(t)
            lines.append(deps.build_quick(res))
        except ValueError as e:
            lines.append(f"❌ {deps.esc(e)}")
        await asyncio.sleep(.3)
    await deps.reply_long(msg, "\n".join(lines), reply_markup=deps.watch_menu_markup())


async def do_watch_add(update: Update, deps, ticker: str,
                       from_menu: bool = True):
    msg = update.effective_message
    chat_id = update.effective_chat.id
    try:
        symbol = data.normalize_ticker(ticker).replace(".JK", "")
    except ValueError as e:
        await msg.reply_text(
            f"❌ {deps.esc(e)}",
            parse_mode=ParseMode.HTML,
            reply_markup=deps.watch_menu_markup() if from_menu else None,
        )
        return
    wl_now = await asyncio.to_thread(store.get_watchlist, chat_id)
    if symbol not in wl_now and len(wl_now) >= deps.MAX_WATCH:
        await msg.reply_text(
            f"❌ Watchlist maksimal {deps.MAX_WATCH} emiten. Hapus dulu: "
            "<code>/watch remove TICKER</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=deps.watch_menu_markup() if from_menu else None,
        )
        return
    ok, wl = await asyncio.to_thread(store.watchlist_add, chat_id, symbol)
    prefix = "✅ Ditambahkan" if ok else "📌 Sudah ada di"
    await msg.reply_text(
        f"{prefix} watchlist: {deps.esc(', '.join(wl))}",
        parse_mode=ParseMode.HTML,
        reply_markup=deps.watch_menu_markup() if from_menu else None,
    )


async def do_watch_del(update: Update, deps, ticker: str,
                       from_menu: bool = True):
    msg = update.effective_message
    try:
        symbol = data.normalize_ticker(ticker).replace(".JK", "")
    except ValueError:
        symbol = ticker.strip().upper().replace(".JK", "")
    ok, wl = await asyncio.to_thread(store.watchlist_remove, update.effective_chat.id, symbol)
    prefix = "🗑️ Dihapus" if ok else "❓ Tidak ditemukan di"
    await msg.reply_text(
        f"{prefix} watchlist. Sisanya: {deps.esc(', '.join(wl)) or '(kosong)'}",
        parse_mode=ParseMode.HTML,
        reply_markup=deps.watch_menu_markup() if from_menu else None,
    )


async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    args = deps.tokens(update)
    chat_id = update.effective_chat.id
    if not args:
        wl = await asyncio.to_thread(store.get_watchlist, chat_id)
        if not wl:
            await update.effective_message.reply_text(
                "👀 Watchlist masih kosong — pilih:",
                parse_mode=ParseMode.HTML, reply_markup=deps.watch_menu_markup())
            return
        lines = [deps.store_format_quick(wl), "Ganti detail dengan /skor nama-saham"]
        await update.effective_message.reply_text(
            "\n".join(l for l in lines if l),
            parse_mode=ParseMode.HTML,
            reply_markup=deps.watch_menu_markup(),
        )
        return

    sub, *rest = args
    sub = sub.lower()
    if sub in ("add", "tambah") and rest:
        await do_watch_add(update, deps, rest[0], from_menu=False)
    elif sub in ("remove", "hapus") and rest:
        await do_watch_del(update, deps, rest[0], from_menu=False)
    elif sub in ("list",):
        await do_watch_list(update, deps)
    else:
        await update.effective_message.reply_text(
            "Format: /watch add TICKER — /watch list — /watch remove TICKER")


async def do_alert_list(update: Update, deps,
                        from_menu: bool = True):
    msg = update.effective_message
    al = await asyncio.to_thread(store.get_alerts, update.effective_chat.id)
    if al:
        txt = "\n".join(
            f"  • <b>{deps.esc(s)}</b>: {deps.esc(', '.join(sorted(k)))}" for s, k in al.items()
        )
        await msg.reply_text(
            f"Alert kamu:\n{txt}",
            parse_mode=ParseMode.HTML,
            reply_markup=deps.alert_menu_markup() if from_menu else None,
        )
    else:
        await msg.reply_text(
            "Belum ada alert.",
            reply_markup=deps.alert_menu_markup() if from_menu else None,
        )


async def do_alert_add(update: Update, deps, ticker: str,
                       kinds: set[str] | None = None,
                       from_menu: bool = True):
    msg = update.effective_message
    chat_id = update.effective_chat.id
    try:
        symbol = data.normalize_ticker(ticker).replace(".JK", "")
    except ValueError as e:
        await msg.reply_text(
            f"❌ {deps.esc(e)}",
            parse_mode=ParseMode.HTML,
            reply_markup=deps.alert_menu_markup() if from_menu else None,
        )
        return
    if kinds is None:
        # Sumber kebenaran tunggal: jangan hardcode daftar yang bisa tertinggal.
        normalized_kinds = set(alerts.ALL_KINDS)
        invalid: set[str] = set()
    else:
        normalized_kinds = set()
        invalid = set()
        for k in kinds:
            kl = str(k).strip().lower()
            if kl in alerts.ALL_KINDS:
                normalized_kinds.add(kl)
            else:
                target_k = _parse_target_kind(kl)
                if target_k is not None:
                    normalized_kinds.add(target_k)
                else:
                    invalid.add(kl)
    if invalid:
        await msg.reply_text(
            f"❌ Jenis tidak dikenal: {deps.esc(', '.join(sorted(invalid)))}. "
            "Coba /alert untuk lihat daftar atau tulis target harga "
            "(mis. <code>/alert BBCA 9500</code> atau <code>/alert BBCA &lt;9000</code>).",
            parse_mode=ParseMode.HTML,
            reply_markup=deps.alert_menu_markup() if from_menu else None,
        )
        return
    saved = await asyncio.to_thread(store.set_alert, chat_id, symbol, normalized_kinds)
    wl_now = await asyncio.to_thread(store.get_watchlist, chat_id)
    if symbol in wl_now or len(wl_now) < deps.MAX_WATCH:
        await asyncio.to_thread(store.watchlist_add, chat_id, symbol)
    kind_txt = ", ".join(sorted(saved))
    await msg.reply_text(
        f"🔔 Alert untuk <b>{deps.esc(symbol)}</b> aktif: <code>{deps.esc(kind_txt)}</code>\n"
        "Scanner mengecek tiap 30 menit saat pasar buka.",
        parse_mode=ParseMode.HTML,
        reply_markup=deps.alert_menu_markup() if from_menu else None,
    )


async def cmd_alert(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    args = deps.tokens(update)
    chat_id = update.effective_chat.id
    if not args:
        await update.effective_message.reply_text(
            "🔔 Alert — pilih di bawah.\n"
            "Jenis: jump, drop, breakout, breakdown, oversold, overbought, "
            "ma_golden, ma_death, vol_spike, atau target harga "
            "(mis. /alert BBCA 9500 atau /alert BBCA <9000).",
            reply_markup=deps.alert_menu_markup())
        return

    sub, *rest = args
    sub_l = sub.lower()
    if sub_l in ("add", "buat") and rest:
        kinds = {k.lower() for k in rest[1:]} or None
        await do_alert_add(update, deps, rest[0], kinds=kinds, from_menu=False)
    elif sub_l in ("remove", "hapus") and rest:
        try:
            symbol = data.normalize_ticker(rest[0]).replace(".JK", "")
        except ValueError:
            symbol = rest[0].strip().upper().replace(".JK", "")
        ok = await asyncio.to_thread(store.remove_alert, chat_id, symbol)
        await update.effective_message.reply_text(
            "🗑️ Alert dihapus." if ok else "❓ Alert untuk emiten itu tidak ada.")
    elif sub_l in ("list",):
        await do_alert_list(update, deps, from_menu=False)
    elif rest and re.fullmatch(r"[a-z]{4}(?:\.jk)?", sub_l):
        kinds = {k.lower() for k in rest} or None
        await do_alert_add(update, deps, sub, kinds=kinds, from_menu=False)
    else:
        await update.effective_message.reply_text(
            "Format salah. Coba <code>/alert</code>.", parse_mode=ParseMode.HTML)


async def cmd_modal(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    """[P5] Set/ubah modal rencana posisi asli & simulasi paper trading (default Rp10 juta)."""
    args = deps.tokens(update)
    chat_id = update.effective_chat.id
    cur = await asyncio.to_thread(store.get_modal, chat_id)
    if not args:
        await update.effective_message.reply_text(
            f"💼 Modal rencana &amp; latihan kamu: <b>{saranmod.fmt(cur)}</b>\n"
            "Dipakai untuk <b>sizing posisi asli</b> (<code>/rencana</code>, "
            "<code>/paket</code>, <code>/cek</code>, <code>/portofolio</code>) "
            "dan <b>simulasi paper trading</b> (<code>/saran</code>).\n"
            "Ubah: <code>/modal 5000000</code> (atau tulis 5.000.000)\n\n"
            "Aturan risiko: <b>1% modal per trade</b> saat pasar normal "
            f"({saranmod.fmt(cur * 0.01)} per posisi) atau <b>0,5%</b> saat "
            f"IHSG defensif ({saranmod.fmt(cur * 0.005)}).",
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
            "❌ Modal wajar antara Rp100 ribu dan Rp10 miliar (sizing posisi & latihan).")
        return
    await asyncio.to_thread(store.set_modal, chat_id, val)
    await update.effective_message.reply_text(
        f"💼 Modal rencana &amp; latihan diubah: <b>{saranmod.fmt(cur)}</b> → "
        f"<b>{saranmod.fmt(val)}</b>\n"
        "Dipakai untuk sizing posisi asli (<code>/rencana</code>, <code>/paket</code>, "
        "<code>/portofolio</code>) &amp; simulasi paper trading (<code>/saran</code>).\n"
        f"Risiko per trade: {saranmod.fmt(val * 0.01)} (1% normal) / "
        f"{saranmod.fmt(val * 0.005)} (0,5% defensif).",
        parse_mode=ParseMode.HTML)


async def cmd_auto(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    """[P6] Nyalakan/matikan auto-scan pagi per chat."""
    args = deps.tokens(update)
    sub = args[0].lower() if args else ""
    chat_id = update.effective_chat.id
    if sub in ("on", "aktif", "nyala"):
        await asyncio.to_thread(store.set_auto, chat_id, True)
        await update.effective_message.reply_text(
            "🌅 <b>Auto-scan pagi: AKTIF</b>\n"
            " Tiap hari bursa ±08:30 WIB: ringkasan /saran + rekap watchlist "
            "dikirim otomatis di chat ini.\n"
            " Matikan: <code>/auto off</code>", parse_mode=ParseMode.HTML)
    elif sub in ("off", "mati", "nonaktif"):
        await asyncio.to_thread(store.set_auto, chat_id, False)
        await update.effective_message.reply_text(
            "🌙 Auto-scan pagi <b>mati</b>. Kapan pun nyala lagi: <code>/auto on</code>.",
            parse_mode=ParseMode.HTML)
    else:
        auto_on = await asyncio.to_thread(store.get_auto, chat_id)
        status = "AKTIF ✅" if auto_on else "mati (default)"
        await update.effective_message.reply_text(
            f"🌅 Auto-scan pagi di chat ini: <b>{status}</b>\n"
            "Nyalakan: <code>/auto on</code> — Matikan: <code>/auto off</code>\n"
            "Jadwal: Senin–Jumat ±08:30 WIB (sebelum bursa buka).",
            parse_mode=ParseMode.HTML)
