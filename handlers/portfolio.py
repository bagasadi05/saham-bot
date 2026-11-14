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

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction, ParseMode

from core import (data, fundamental, marketcal, portofolio as portmod,
                  risk as riskmod, store)
from core.reports import esc

log = logging.getLogger("sahambuddy")

_PORTO_USAGE = (
    "💼 <b>Portofolio asli</b> (uang sungguhan, terpisah dari paper trading)\n\n"
    "<code>/portofolio</code> — lihat posisi &amp; untung/rugi\n"
    "<code>/portofolio beli BBCA 5 9000</code> — catat beli 5 lot @ Rp9.000\n"
    "<code>/portofolio jual BBCA 2 9500</code> — catat jual 2 lot @ Rp9.500\n"
    "<code>/portofolio riwayat</code> — 10 transaksi terakhir\n"
    "<code>/portofolio batas [20 35|reset]</code> — batas maks posisi &amp; sektor (%)\n\n"
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
    prev_close = float(df["close"].iloc[-2]) if len(df) >= 2 else None
    as_of = str(df.attrs.get("last_bar_date") or "")
    if not as_of and hasattr(df.index[-1], "strftime"):
        try:
            as_of = df.index[-1].strftime("%Y-%m-%d")
        except Exception:
            as_of = str(df.index[-1])[:10]
    return {
        "close": close,
        "prev_close": prev_close,
        "atr": portmod.atr14(df),
        "stale": bool(df.attrs.get("stale") or df.attrs.get("stale_note")),
        "as_of": as_of,
        "df": df,
        "rets": df["close"].pct_change().dropna(),
    }


async def _porto_quotes(tickers: list[str], deps) -> dict[str, dict]:
    """Ambil harga semua posisi paralel berbatas (4 sekaligus)."""
    sem = asyncio.Semaphore(4)
    out: dict[str, dict] = {}

    async def one(t: str):
        async with sem:
            out[t] = await asyncio.to_thread(deps._quote_posisi, t)

    await asyncio.gather(*(one(t) for t in tickers))
    return out


def _bench_rets():
    """Return harian IHSG 6 bulan untuk beta; None bila tidak tersedia."""
    try:
        df = data.fetch_history("^JKSE", "6mo", raw_symbol=True)
    except Exception:
        log.warning("IHSG untuk risiko portofolio gagal", exc_info=True)
        return None
    if df is None or len(df) < 25:
        return None
    return df["close"].pct_change().dropna()


def _porto_text(summary: dict, modal: int, deps, risk: dict | None = None) -> str:
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
        stale_badge = " ⚠️(cache)" if r.get("stale") else ""
        days_badge = f" · {r['days_held']}h" if r.get("days_held") is not None else ""
        line = (f"• <b>{esc(r['ticker'])}</b>{stale_badge} — {r['lots']} lot @ "
                f"{deps._money(r['avg_price'])}")
        if r["priced"]:
            line += f" → {deps._money(r['close'])}{days_badge}"
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
            if r.get("sl") and r.get("risk") is not None:
                sl_src = esc(str(r.get("sl_source") or "1×ATR"))
                trail_txt = ""
                if r.get("trailing_sl"):
                    hit_tag = " ⚠️JEBOL" if r.get("trailing_hit") else ""
                    trail_txt = f" · Chandelier {deps._money(r['trailing_sl'])}{hit_tag}"
                out.append(f"   SL hari ini {deps._money(r['sl'])} ({sl_src}){trail_txt} · "
                           f"risiko {deps._money(r['risk'])}")
            sc_out = r.get("scale_out")
            sc_note = r.get("scale_out_note") or (
                sc_out.get("action")
                if isinstance(sc_out, dict) and sc_out.get("tp1_hit")
                else None
            )
            if sc_note:
                out.append(f"   🎯 <i>{esc(str(sc_note))}</i>")
            if (r.get("time_stop_flag") or r.get("time_stop_hit")) and r.get("days_held") is not None:
                out.append(
                    f"   ⏱️ <i>Time-stop: pegang {r['days_held']} hari bursa "
                    "(>= 20 hari horizon model — evaluasi keluar/rotasi)</i>"
                )
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
    if risk and risk.get("ok"):
        out += ["", "📉 <b>Risiko portofolio</b>"]
        bits = [f"eksposur {deps._money(risk['value'])}"]
        if modal:
            bits.append(f"{risk['value'] / modal * 100:.0f}% modal")
        if risk.get("vol_ann_pct") is not None:
            bits.append(f"vol {risk['vol_ann_pct']:.1f}%/thn".replace(".", ","))
        if risk.get("max_drawdown_pct") is not None:
            bits.append(f"drawdown {risk['max_drawdown_pct']:.1f}%"
                        .replace(".", ","))
        if risk.get("beta") is not None:
            bits.append(f"beta {risk['beta']:.2f}".replace(".", ","))
        out.append("   " + " · ".join(bits))
        if risk.get("var95_pct") is not None:
            out.append(f"   VaR 95% 1 hari: <b>{deps._money(risk['var95_rp'])}</b> "
                       f"({risk['var95_pct']:.2f}% nilai)".replace(".", ","))
        if risk.get("corr_avg") is not None:
            out.append(f"   Korelasi rata-rata {risk['corr_avg']:.2f} · "
                       f"tertinggi {esc(risk.get('corr_pair') or '—')} "
                       f"{risk['corr_max']:.2f}".replace(".", ","))
        inv_vol = risk.get("inv_vol") or {}
        if inv_vol.get("ok") and inv_vol.get("weights"):
            parts = []
            cur_w = risk.get("weights") or {}
            for tk, w_tgt in inv_vol["weights"].items():
                w_now = cur_w.get(tk)
                now_txt = f" (skrg {w_now:.0f}%)" if w_now is not None else ""
                parts.append(f"{esc(tk)} {w_tgt:.0f}%{now_txt}")
            out.append("   Bobot seimbang (inv-vol): " + " · ".join(parts))
            if inv_vol.get("cap_note"):
                out.append(f"   <i>{esc(inv_vol['cap_note'])}</i>")
        for w in (risk.get("warnings") or [])[:3]:
            out.append("⚠️ " + esc(w.replace(".", ",")))
    out += [
        "",
        "Catat: <code>/portofolio beli BBCA 5 9000</code> · "
        "<code>/portofolio jual BBCA 2 9500</code> · "
        "<code>/portofolio riwayat</code>",
        "",
        "<i>Harga Yahoo delayed; SL memakai catatan eksekusi (/jurnal) atau "
        "1×ATR harga berjalan + Chandelier trailing stop (20h high − 2×ATR).</i>",
        "",
        deps._SINYAL_DISCLAIMER,
    ]
    return "\n".join(out)


async def do(update, args: list[str], deps, *, from_menu: bool = False):
    """Portofolio asli: lihat, catat beli/jual, riwayat."""
    msg = update.effective_message
    chat_id = update.effective_chat.id
    rm = deps.home_markup() if from_menu else None
    sub = (args[0].lower() if args else "")

    if sub in ("batas", "limit", "limits"):
        cur_pos, cur_sec = await asyncio.to_thread(store.get_risk_limits, chat_id)
        tail = args[1:]
        if not tail:
            await msg.reply_text(
                "🛡️ <b>Batas Risiko Portofolio</b>\n"
                f"Maks 1 posisi: <b>{cur_pos * 100:.0f}%</b> modal "
                f"(bawaan {riskmod.MAX_POS_PCT * 100:.0f}%)\n"
                f"Maks 1 sektor: <b>{cur_sec * 100:.0f}%</b> modal "
                f"(bawaan {riskmod.MAX_SECTOR_PCT * 100:.0f}%)\n\n"
                "Ubah: <code>/portofolio batas 20 35</code>\n"
                "Reset ke bawaan: <code>/portofolio batas reset</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=rm,
            )
            return
        if tail[0].lower() in ("reset", "default", "bawaan"):
            p, s = await asyncio.to_thread(store.set_risk_limits, chat_id, None, None)
            await msg.reply_text(
                f"♻️ Batas risiko dikembalikan ke bawaan: maks posisi "
                f"<b>{p * 100:.0f}%</b>, maks sektor <b>{s * 100:.0f}%</b>.",
                parse_mode=ParseMode.HTML,
                reply_markup=rm,
            )
            return

        def _parse_pct(tok: str) -> float | None:
            try:
                return float(tok.strip().rstrip("%").replace(",", ".")) / 100.0
            except ValueError:
                return None

        new_pos = _parse_pct(tail[0])
        new_sec = _parse_pct(tail[1]) if len(tail) > 1 else max(cur_sec, new_pos or 0.0)
        if new_pos is None or new_sec is None:
            await msg.reply_text(
                "Format tidak valid. Contoh: <code>/portofolio batas 20 35</code> "
                "atau <code>/portofolio batas reset</code>.",
                parse_mode=ParseMode.HTML,
                reply_markup=rm,
            )
            return
        try:
            p, s = await asyncio.to_thread(store.set_risk_limits, chat_id, new_pos, new_sec)
        except ValueError as e:
            await msg.reply_text(f"❌ {esc(e)}", parse_mode=ParseMode.HTML, reply_markup=rm)
            return
        await msg.reply_text(
            f"✅ Batas risiko diperbarui: maks 1 posisi <b>{p * 100:.0f}%</b> modal · "
            f"maks 1 sektor <b>{s * 100:.0f}%</b> modal.\n"
            "<i>Berlaku di /rencana, /cek, dan laporan /portofolio.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=rm,
        )
        return

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
                ctx_b = await asyncio.to_thread(
                    store.get_signal_context, chat_id, t, intent="beli"
                )
                res = await asyncio.to_thread(
                    store.add_real_position, chat_id, t, lots, price, today,
                    sector,
                    sl=ctx_b.get("sl") if ctx_b else None,
                    tp=ctx_b.get("tp") if ctx_b else None,
                    signal_price=ctx_b.get("signal_price") if ctx_b else None,
                    source="portofolio",
                )
                await msg.reply_text(
                    f"✅ Tercatat <b>BELI {esc(t)}</b> {lots} lot @ "
                    f"{deps._money(price)} = "
                    f"{deps._money(lots * 100 * price)}\n"
                    f"Posisi sekarang: <b>{res['lots']} lot</b> @ rata-rata "
                    f"{deps._money(res['avg_price'])}\n"
                    f"<i>Tercatat di /portofolio &amp; /jurnal (fee beli ±0,15% belum masuk harga rata-rata).</i>\n\n"
                    "Lihat: /portofolio · /jurnal",
                    parse_mode=ParseMode.HTML)
            else:
                tracked = await asyncio.to_thread(
                    store.open_execution_lots, chat_id, t)
                if tracked > 0:
                    res = await asyncio.to_thread(
                        store.record_execution_sell, chat_id, t, lots, price,
                        today, source="portofolio")
                else:
                    res = await asyncio.to_thread(
                        store.reduce_real_position, chat_id, t, lots, price, today)
                gross_val = res.get("gross_pnl", res.get("realized", 0.0))
                net_val = res.get("net_pnl", res.get("net_realized", gross_val))
                fee_val = res.get("fee_rp")
                if fee_val is None and gross_val is not None and net_val is not None:
                    fee_val = float(gross_val) - float(net_val)
                fee_txt = (
                    f" (fee BEI 0,40% ≈ {deps._money(fee_val)})"
                    if fee_val
                    else " (fee BEI 0,40%)"
                )
                extra = (
                    f"Realisasi bruto (harga − rata-rata): {deps._money_delta(gross_val)}\n"
                    f"Realisasi neto{fee_txt}: "
                    f"<b>{deps._money_delta(net_val)}</b> "
                    f"<i>(tercatat di /jurnal)</i>\n"
                )
                await msg.reply_text(
                    f"✅ Tercatat <b>JUAL {esc(t)}</b> {lots} lot @ "
                    f"{deps._money(price)}\n"
                    + extra
                    + (f"Sisa {res['lots_left']} lot @ "
                       f"{deps._money(res['avg_price'])}\n" if res["lots_left"]
                       else "Posisi tertutup.\n")
                    + "\nLihat: /portofolio · /jurnal",
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
    j_rows: list[list[InlineKeyboardButton]] = []
    for p in positions[:4]:
        tk = str(p.get("ticker") or "").upper()
        if tk and int(p.get("lots") or 0) > 0:
            j_rows.append([
                InlineKeyboardButton(f"🤔 Cek layak jual {tk}?", callback_data=f"tick:jual:{tk}"),
                InlineKeyboardButton(f"📝 Sudah jual {tk} di Stockbit", callback_data=f"exec:sell:{tk}"),
            ])
    j_rows.append([InlineKeyboardButton("📒 Jurnal Eksekusi", callback_data="run:jurnal")])
    if from_menu:
        j_rows.append([InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")])
    porto_kb = InlineKeyboardMarkup(j_rows)
    if not positions:
        await deps.reply_long(msg, "Belum ada posisi asli.\n\n" + _PORTO_USAGE,
                              reply_markup=porto_kb if from_menu else rm)
        return
    if not await deps.cooldown_ok(update, "portofolio"):
        return
    await update.effective_chat.send_action(ChatAction.TYPING)
    quotes = await _porto_quotes([p["ticker"] for p in positions], deps)
    open_execs = await asyncio.to_thread(store.open_executions_by_ticker, chat_id)
    summary = portmod.summarize(positions, quotes, open_execs=open_execs)
    modal = await asyncio.to_thread(store.get_modal, chat_id)
    max_pos, max_sec = await asyncio.to_thread(store.get_risk_limits, chat_id)
    bench = await asyncio.to_thread(_bench_rets)
    risk = riskmod.portfolio_risk(
        positions, quotes, bench,
        max_pos_pct=max_pos, max_sector_pct=max_sec,
    )
    await deps.reply_long(msg, _porto_text(summary, modal, deps, risk),
                          reply_markup=porto_kb)
