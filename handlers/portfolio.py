"""Handler `/portofolio` (P17): buku catatan uang sungguhan.

Dipisah dari `bot.py` supaya orkestrasi tetap terbaca. Dependensi milik bot
(`reply_long`, `cooldown_ok`, formatter rupiah, `_quote_posisi`) diterima lewat
parameter `deps` — modul `bot` itu sendiri. Pola ini menjaga
`monkeypatch.setattr(bot, "...")` di tes tetap bekerja meski kodenya pindah
berkas, dan menghindari impor melingkar: modul ini **tidak** mengimpor `bot`.
"""
from __future__ import annotations

import asyncio
import logging
import re

from telegram.constants import ChatAction, ParseMode

from core import data, fundamental, marketcal, portofolio as portmod, store
from core.reports import esc

log = logging.getLogger("sahambuddy")

_PORTO_USAGE = (
    "💼 <b>Portofolio asli</b> (uang sungguhan, terpisah dari paper trading)\n\n"
    "<code>/portofolio</code> — lihat posisi &amp; untung/rugi\n"
    "<code>/portofolio beli BBCA 5 9000</code> — catat beli 5 lot @ Rp9.000\n"
    "<code>/portofolio jual BBCA 2 9500</code> — catat jual 2 lot @ Rp9.500\n"
    "<code>/portofolio riwayat</code> — 10 transaksi terakhir\n\n"
    "Bot tidak pernah menaruh order ke Stockbit; ini hanya buku catatan."
)


def _quote_posisi(t: str) -> dict:
    """Harga & ATR terakhir untuk satu posisi (None kalau data gagal)."""
    try:
        df = data.fetch_history(t, "6mo")
    except Exception as e:  # noqa: BLE001 - satu emiten gagal tidak membatalkan
        log.warning("harga portofolio %s gagal: %s", t, e)
        return {"close": None, "atr": None, "stale": True}
    if df is None or len(df) < 2:
        return {"close": None, "atr": None, "stale": True}
    try:
        close = float(df["close"].iloc[-1])
    except (KeyError, TypeError, ValueError):
        return {"close": None, "atr": None, "stale": True}
    return {"close": close, "atr": portmod.atr14(df),
            "stale": bool(df.attrs.get("stale_note"))}


async def _porto_quotes(tickers: list[str], deps) -> dict[str, dict]:
    """Ambil harga semua posisi paralel berbatas (4 sekaligus)."""
    sem = asyncio.Semaphore(4)
    out: dict[str, dict] = {}

    async def one(t: str):
        async with sem:
            out[t] = await asyncio.to_thread(deps._quote_posisi, t)

    await asyncio.gather(*(one(t) for t in tickers))
    return out


def _porto_text(summary: dict, modal: int, deps) -> str:
    """Rincian posisi + PnL + konsentrasi sektor + risiko SL."""
    rows = summary.get("rows") or []
    out = ["💼 <b>PORTOFOLIO ASLI</b> (uang sungguhan)",
           f"<i>{summary['n_positions']} posisi · nilai "
           f"{deps._money(summary['value'])} · modal "
           f"{deps._money(summary['cost'])}</i>",
           f"PnL berjalan: <b>{deps._money_delta(summary['pnl'])}</b>"
           + (f" ({deps._plan_pct(summary['pnl_pct'])})"
              if summary.get("pnl_pct") is not None else ""),
           ""]
    for r in rows:
        line = (f"• <b>{esc(r['ticker'])}</b> — {r['lots']} lot @ "
                f"{deps._money(r['avg_price'])}")
        if r["priced"]:
            line += f" → {deps._money(r['close'])}"
        out.append(line)
        if r["priced"]:
            bits = [f"nilai {deps._money(r['value'])}",
                    f"PnL {deps._money_delta(r['pnl'])}"]
            if r["pnl_pct"] is not None:
                bits[-1] += f" ({deps._plan_pct(r['pnl_pct'])})"
            if r["weight_pct"] is not None:
                bits.append(f"{deps._plan_pct(r['weight_pct'], sign=False)} "
                            "portofolio")
            out.append("   " + " · ".join(bits))
            if r["sl"] and r["risk"]:
                out.append(f"   SL hari ini {deps._money(r['sl'])} (2×ATR) · "
                           f"risiko {deps._money(r['risk'])}")
        else:
            out.append("   ❔ harga tidak tersedia — tidak dihitung di total")
    if summary.get("unpriced"):
        out += ["", f"⚠️ {summary['unpriced']} posisi tanpa harga; total di "
                    "atas hanya posisi yang harganya terbaca."]
    if summary.get("sectors"):
        out += ["", "🏭 Sektor: " + " · ".join(
            f"{esc(s['sector'])} {deps._plan_pct(s['pct'], sign=False)}"
            for s in summary["sectors"])]
    if summary.get("risk") is not None:
        out.append(f"🛑 Risiko total bila semua SL tersentuh: "
                   f"<b>{deps._money(summary['risk'])}</b>"
                   + (f" ({deps._plan_pct(summary['risk_pct'], sign=False)} "
                      "nilai portofolio)" if summary.get("risk_pct") else ""))
    if modal:
        out.append(f"   Modal rencana (/modal): {deps._money(modal)} — tidak "
                   "dipakai menghitung PnL")
    for w in summary.get("warnings") or []:
        out.append("⚠️ " + esc(w.replace(".", ",")))
    out += [
        "",
        "Catat: <code>/portofolio beli BBCA 5 9000</code> · "
        "<code>/portofolio jual BBCA 2 9500</code> · "
        "<code>/portofolio riwayat</code>",
        "",
        "<i>Harga Yahoo delayed; SL dihitung di harga sekarang (2×ATR), bukan "
        "SL awal saat membeli. Fee transaksi belum masuk harga rata-rata.</i>",
        "",
        deps._SINYAL_DISCLAIMER,
    ]
    return "\n".join(out)


async def do(update, args: list[str], deps):
    """Portofolio asli: lihat, catat beli/jual, riwayat."""
    msg = update.effective_message
    chat_id = update.effective_chat.id
    sub = (args[0].lower() if args else "")

    if sub in ("beli", "buy", "jual", "sell"):
        if len(args) < 4:
            await msg.reply_text(_PORTO_USAGE, parse_mode=ParseMode.HTML)
            return
        t = args[1].upper()
        t = t[:-3] if t.endswith(".JK") else t
        if not re.fullmatch(r"[A-Z]{2,6}", t):
            await msg.reply_text(
                f"Kode <code>{esc(args[1])}</code> tidak valid.\n\n"
                + _PORTO_USAGE, parse_mode=ParseMode.HTML)
            return
        try:
            lots = int(args[2].replace(".", ""))
        except ValueError:
            await msg.reply_text("Jumlah lot harus angka, mis. "
                                 "<code>/portofolio beli BBCA 5 9000</code>.",
                                 parse_mode=ParseMode.HTML)
            return
        price = deps._parse_rupiah(args[3])
        if price is None:
            await msg.reply_text("Harga harus angka, mis. "
                                 "<code>9000</code> atau <code>9.000</code>.",
                                 parse_mode=ParseMode.HTML)
            return
        if lots <= 0:
            await msg.reply_text("Lot harus lebih dari 0.")
            return
        today = marketcal.now_wib().date().isoformat()
        try:
            if sub in ("beli", "buy"):
                sector = None
                try:
                    fr = await asyncio.to_thread(
                        fundamental.fetch_fundamentals, t)
                    sector = (fr or {}).get("sector")
                except Exception:
                    log.warning("sektor %s tidak terbaca", t, exc_info=True)
                res = await asyncio.to_thread(
                    store.add_real_position, chat_id, t, lots, price, today,
                    sector)
                await msg.reply_text(
                    f"✅ Tercatat <b>BELI {esc(t)}</b> {lots} lot @ "
                    f"{deps._money(price)} = "
                    f"{deps._money(lots * 100 * price)}\n"
                    f"Posisi sekarang: <b>{res['lots']} lot</b> @ rata-rata "
                    f"{deps._money(res['avg_price'])}\n"
                    f"<i>Fee beli ±0,15% belum masuk harga rata-rata.</i>\n\n"
                    "Lihat: /portofolio",
                    parse_mode=ParseMode.HTML)
            else:
                res = await asyncio.to_thread(
                    store.reduce_real_position, chat_id, t, lots, price, today)
                await msg.reply_text(
                    f"✅ Tercatat <b>JUAL {esc(t)}</b> {lots} lot @ "
                    f"{deps._money(price)}\n"
                    f"Realisasi (harga − rata-rata): "
                    f"<b>{deps._money_delta(res['realized'])}</b>\n"
                    + (f"Sisa {res['lots_left']} lot @ "
                       f"{deps._money(res['avg_price'])}\n" if res["lots_left"]
                       else "Posisi tertutup.\n")
                    + "\nLihat: /portofolio",
                    parse_mode=ParseMode.HTML)
        except ValueError as e:
            await msg.reply_text(f"❌ {esc(e)}", parse_mode=ParseMode.HTML)
        return

    if sub in ("riwayat", "log", "history"):
        trades = await asyncio.to_thread(store.real_trades, chat_id, 10)
        if not trades:
            await msg.reply_text("Belum ada transaksi asli yang dicatat.\n\n"
                                 + _PORTO_USAGE, parse_mode=ParseMode.HTML)
            return
        out = ["🧾 <b>Riwayat transaksi (uang sungguhan)</b>", ""]
        for tr in trades:
            icon = "🟢" if tr["side"] == "BELI" else "🔴"
            out.append(f"{icon} {tr['trade_date']} · <b>{esc(tr['ticker'])}</b> "
                       f"{tr['side']} {tr['lots']} lot @ "
                       f"{deps._money(tr['price'])}")
        out += ["", "Catat lagi: <code>/portofolio beli BBCA 5 9000</code>"]
        await deps.reply_long(msg, "\n".join(out))
        return

    positions = await asyncio.to_thread(store.real_positions, chat_id)
    if not positions:
        await deps.reply_long(msg, "Belum ada posisi asli.\n\n" + _PORTO_USAGE)
        return
    if not await deps.cooldown_ok(update, "portofolio"):
        return
    await update.effective_chat.send_action(ChatAction.TYPING)
    quotes = await _porto_quotes([p["ticker"] for p in positions], deps)
    summary = portmod.summarize(positions, quotes)
    await deps.reply_long(msg, _porto_text(summary, store.get_modal(chat_id),
                                           deps))
