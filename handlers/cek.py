"""Handler `/cek` (P14): ceklist enam bagian sebelum membeli.

Dipisah dari `bot.py`; dependensi bot (`fetch_and_analyze`, `_cek_bandar_row`,
`_cek_agenda`, `_snapshot_row`, formatter, reply_long) diterima lewat `deps` =
modul `bot`, supaya monkeypatch di tes tetap bekerja tanpa impor melingkar.
"""
from __future__ import annotations

import asyncio
import logging
import re

from telegram import Update
from telegram.constants import ParseMode

from core import (analysis, bandar, banding, cek as cekmod, data, fundamental,
                  marketcal, news, rencana, risk as riskmod, store, universe)
from core.reports import esc
from handlers import jurnal as jurnal_handler

log = logging.getLogger("sahambuddy")


# ------------------------------------------ P14: ceklist sebelum beli /cek
_CEK_ICON = {"ok": "✅", "warn": "⚠️", "bad": "⛔", "na": "❔"}
_CEK_VERDICT = {"LAYAK": "🟢 <b>LAYAK</b>", "TUNGGU": "🟡 <b>TUNGGU</b>",
                "HINDARI": "🔴 <b>HINDARI</b>",
                "TIDAK CUKUP DATA": "⚪ <b>TIDAK CUKUP DATA</b>"}


def _cek_bandar_row(ticker: str) -> dict | None:
    """Baris ringkasan BEI untuk satu emiten; None kalau sumber diblokir."""
    try:
        rows = bandar.get_stock_summary(bandar.last_trading_date(),
                                        max_stocks=1000, until=ticker)
    except Exception as e:  # noqa: BLE001 - Cloudflare/paket rusak: bagian ini kosong
        log.warning("aliran asing %s tidak tersedia: %s", ticker, e)
        return None
    return bandar.find_row(rows, ticker)


def _cek_agenda(ticker: str) -> tuple[dict | None, int | None]:
    """Ex-date dividen terdekat + sisa hari (None bila tidak ada)."""
    ex = marketcal.upcoming_dividend_exdate(ticker)
    if not ex:
        return None, None
    days = (ex["ex_date"] - marketcal.now_wib().date()).days
    return {"ex_date": str(ex["ex_date"]), "amount": ex.get("amount")}, days


def _cek_value_avg20(res: dict, row: dict | None) -> float | None:
    """Rata-rata nilai transaksi 20 hari: snapshot /scan dulu, lalu hitung."""
    v = (row or {}).get("value_avg20")
    try:
        if v:
            return float(v)
    except (TypeError, ValueError):
        pass
    try:
        ind = res["ind"]
        return float((ind["close"] * ind["volume"]).tail(20).mean())
    except Exception:
        return None


async def _cek_gather(t: str, chat_id: int, deps) -> dict:
    """Ambil semua bahan /cek secara paralel; tiap bagian boleh gagal sendiri."""
    res = await deps.fetch_and_analyze(t)
    sym = res["ticker"]
    fund_f = asyncio.to_thread(fundamental.fetch_fundamentals, t)
    news_f = asyncio.to_thread(news.fetch_news, t)
    weekly_f = asyncio.to_thread(data.fetch_weekly, t)
    bandar_f = asyncio.to_thread(deps._cek_bandar_row, sym)
    agenda_f = asyncio.to_thread(deps._cek_agenda, t)
    fund_raw, items, weekly_df, brow, ag = await asyncio.gather(
        fund_f, news_f, weekly_f, bandar_f, agenda_f, return_exceptions=True)

    if isinstance(fund_raw, Exception):
        fund_raw = None
    if isinstance(items, Exception):
        items = None
    if isinstance(weekly_df, Exception):
        weekly_df = None
    if isinstance(brow, Exception):
        brow = None
    if isinstance(ag, Exception):
        ag = (None, None)

    fsc = None
    if fund_raw:
        try:
            fsc = await asyncio.to_thread(
                fundamental.score_fundamentals, fund_raw)
        except Exception:
            log.warning("skor fundamental %s gagal", sym, exc_info=True)
    nsent = None
    if items:
        try:
            nsent = await asyncio.to_thread(news.news_sentiment, items)
        except Exception:
            log.warning("sentimen %s gagal", sym, exc_info=True)
    res["weekly"] = (analysis.weekly_trend(weekly_df, res["score"]["pct"])
                     if weekly_df is not None else None)
    try:
        res["ret1y"] = banding.year_return(res["ind"]["close"])
    except Exception:
        res["ret1y"] = None

    if nsent is not None:
        top = [d.get("title") for d in (nsent.get("details") or [])
               if d.get("title")]
        nsent = {**nsent, "top": top[0][:90] if top else None}

    wl = await asyncio.to_thread(store.get_watchlist, chat_id)
    try:
        peers = await asyncio.to_thread(
            banding.sector_context, sym, (fund_raw or {}).get("sector"), wl)
    except Exception:
        peers = None

    row = await asyncio.to_thread(deps._snapshot_row, sym)
    agenda, days = ag
    return {"t": t, "res": res, "fund": fsc, "fund_raw": fund_raw,
            "nsent": nsent, "bandar_row": brow, "agenda": agenda,
            "agenda_days": days, "peers": peers,
            "value_avg20": _cek_value_avg20(res, row),
            "board": row.get("board") or universe.board_of(sym)}


def _cek_text(out: dict, res: dict, deps) -> str:
    """Satu layar: enam bagian + verdict + alasan."""
    t = esc(out.get("ticker") or res.get("ticker") or "?")
    price = out.get("price")
    meta = " · ".join(esc(x) for x in (out.get("sector"), out.get("board")) if x)
    head = f"🧾 <b>CEK SEBELUM BELI — {t}"
    if price:
        head += f" {deps._money(price)}"
    head += "</b>"
    out_lines = [head]
    if meta:
        out_lines.append(f"<i>{meta}</i>")
    stale = res.get("stale_note") or ""
    if stale:
        out_lines.append(stale.strip())
    out_lines.append("")

    num = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣")
    for i, it in enumerate(out.get("items") or []):
        icon = _CEK_ICON.get(it["status"], "•")
        label = esc(it["label"])
        out_lines.append(f"{num[i] if i < len(num) else '•'} <b>{label}</b> — "
                         f"{icon} {esc(it['headline'])}")
        if it.get("detail"):
            out_lines.append(f"   <i>{esc(it['detail'])}</i>")

    out_lines += ["", "━━━━━━━━"]
    out_lines.append("Kesimpulan: "
                     + _CEK_VERDICT.get(out["verdict"], esc(out["verdict"])))
    for r in out.get("reasons") or []:
        out_lines.append(f"• {esc(r)}")
    for w in out.get("warnings") or []:
        out_lines.append("⚠️ " + esc(w.replace(".", ",")))
    if out.get("missing"):
        out_lines.append("❔ Tidak tersedia: " + esc(", ".join(out["missing"]))
                         + " — bagian lain tetap ditampilkan apa adanya.")
    out_lines += [
        "",
        f"Rencana uangnya: <code>/rencana {t} 5000000</code> · analisis penuh: "
        f"<code>/analisis {t}</code>",
        "",
        "<i>Ambang bot: likuid ≥ Rp5 miliar/hari, kurang likuid &lt; Rp2 "
        "miliar/hari · murah: PER ≤ 20 atau PBV ≤ 2 · mahal: PER &gt; 40 atau "
        "PBV &gt; 6 · ROE bagus ≥ 15%.</i>",
        "",
        deps._SINYAL_DISCLAIMER,
    ]
    return "\n".join(out_lines)


async def do(update: Update, args: list[str], deps, *, from_menu: bool = False):
    """Ceklist satu layar: /cek <KODE>."""
    msg = update.effective_message
    rm = deps.home_markup() if from_menu else None
    if not args:
        wl = (await asyncio.to_thread(store.get_watchlist, update.effective_chat.id)
              if update.effective_chat else [])
        await msg.reply_text(
            "🛡️ <b>Cek Layak Beli (6 bagian)</b>\n"
            "Contoh: <code>/cek BBCA</code> — atau ketuk salah satu saham di bawah:\n\n"
            "Isinya: tren, valuasi vs sektor, aliran asing, berita, "
            "agenda 14 hari, likuiditas + kesimpulan LAYAK / TUNGGU / HINDARI.",
            parse_mode=ParseMode.HTML,
            reply_markup=deps.ticker_pick_markup("cek", wl))
        return
    t = args[0].upper()
    t = t[:-3] if t.endswith(".JK") else t
    if not re.fullmatch(r"[A-Z]{2,6}", t):
        await msg.reply_text(
            f"Kode <code>{esc(args[0])}</code> tidak valid. Contoh: "
            "<code>/cek BBCA</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=rm)
        return
    if not await deps.cooldown_ok(update, "cek"):
        return

    if not from_menu:
        await msg.reply_text(f"⏳ Memeriksa {t} (6 bagian, ±10–25 detik)…")
    try:
        bahan = await _cek_gather(t, update.effective_chat.id, deps)
    except ValueError as e:
        await msg.reply_text(f"❌ {esc(e)}", parse_mode=ParseMode.HTML, reply_markup=rm)
        return
    except Exception:
        log.exception("cek %s gagal", t)
        await msg.reply_text(
            f"❌ Data {esc(t)} tidak tersedia saat ini. Coba lagi nanti — "
            "tidak ada bagian yang saya karang.",
            parse_mode=ParseMode.HTML,
            reply_markup=rm)
        return

    out = cekmod.build(res=bahan["res"], fund=bahan["fund"],
                       fund_raw=bahan["fund_raw"], nsent=bahan["nsent"],
                       bandar_row=bahan["bandar_row"], agenda=bahan["agenda"],
                       agenda_days=bahan["agenda_days"], peers=bahan["peers"],
                       value_avg20=bahan["value_avg20"], board=bahan["board"])
    # P23: posisi yang sudah ada tidak boleh diam-diam ditambah bila
    # melewati batas portofolio.
    positions = await asyncio.to_thread(store.real_positions,
                                        update.effective_chat.id)
    modal = await asyncio.to_thread(store.get_modal,
                                    update.effective_chat.id)
    out["warnings"].extend(riskmod.position_fit(t, positions, modal))
    if out.get("price") and float(out["price"]) > 0:
        atr_val = None
        try:
            ind = bahan["res"].get("ind")
            if ind is not None and "atr14" in ind:
                atr_val = float(ind["atr14"].iloc[-1])
        except Exception:
            atr_val = None
        lv = rencana.price_levels({"price": float(out["price"]), "atr": atr_val})
        today_iso = marketcal.now_wib().date().isoformat()
        await asyncio.to_thread(
            store.save_signal_context,
            update.effective_chat.id, t, "cek", float(out["price"]),
            lv.get("sl"), lv.get("tp"), None, today_iso,
        )
    kb = jurnal_handler.confirm_exec_markup("cek", t, deps, from_menu=from_menu)
    await deps.reply_long(
        msg,
        _cek_text(out, bahan["res"], deps),
        reply_markup=kb,
    )
