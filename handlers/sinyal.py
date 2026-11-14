"""Handler `/sinyal` (P15): snapshot pasar, penanda kualitas, `beli bagus`.

Dependensi bot (`_scan_when`, `_kualitas_map`, `reply_long`, `cooldown_ok`,
disclaimer) diterima lewat `deps` agar monkeypatch di tes tetap bekerja dan
tidak ada impor melingkar.
"""
from __future__ import annotations

import asyncio
import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from core import fundamental, kualitas as kvalmod, marketcal, store
from core.reports import _rp, esc
from core.textutil import rupiah

log = logging.getLogger("sahambuddy")


def _sinyal_emoji(sig: str) -> str:
    return {"BELI": "🟢", "JUAL": "🔴"}.get(sig, "⚪")


def _sinyal_row(i: int, r: dict, kval: dict | None = None) -> str:
    parts = [f"{i}. <b>{esc(r['ticker'])}</b> {_rp(r.get('price'))}"]
    if r.get("rel_pct") is not None:
        parts.append(f"rel {r['rel_pct']:.0f}")
    if r.get("rsi") is not None:
        parts.append(f"RSI {r['rsi']:.0f}")
    if r.get("ret20d") is not None:
        parts.append(f"20h {r['ret20d']:+.1f}%")
    line = " · ".join(parts)
    if kval:
        line += f" · {kvalmod.mark(kval)} {esc(kval.get('label') or '')}"
    if r.get("entry") and r.get("sl") and r.get("tp"):
        line += (f"\n   <i>entry {_rp(r['entry'])} · SL {_rp(r['sl'])} · "
                 f"TP {_rp(r['tp'])}</i>")
    if kval and kval.get("bits"):
        line += "\n   <i>fundamental: " + esc(" · ".join(kval["bits"])) + "</i>"
    return line


# Peringatan kualitas dalam kata biasa untuk ringkasan: label teknis
# ("ROE tipis") diterjemahkan supaya ringkasan bisa dibaca tanpa kamus.
# Label lengkap + angka tetap ada di tampilan rincian.
_PERINGATAN_POLOS = {
    "merugi": "perusahaan sedang merugi",
    "ROE tipis": "laba tipis",
    "mahal": "harga sudah mahal",
}


def _peringatan(kval: dict | None) -> str:
    """Peringatan satu frasa untuk ringkasan; kosong bila sehat/belum dinilai."""
    if not kval or kval.get("ok") is not False:
        return ""
    label = kval.get("label") or ""
    return _PERINGATAN_POLOS.get(label, label)


def _sinyal_row_ringkas(i: int, r: dict, kval: dict | None = None) -> str:
    """Satu baris ringkas: nama, harga, arah sebulan terakhir, satu peringatan.

    Tanpa rel/RSI/entry/fundamental — angka detail pindah ke tampilan
    rincian yang dibuka lewat tombol.
    """
    parts = [f"{i}. <b>{esc(r['ticker'])}</b> {rupiah(r.get('price'))}"]
    ret = r.get("ret20d")
    if ret is not None:
        arah = "naik" if ret >= 0 else "turun"
        parts.append(f"{arah} {abs(ret):.1f}% sebulan terakhir"
                     .replace(".", ","))
    line = " · ".join(parts)
    warn = _peringatan(kval)
    if warn:
        line += f" ⚠️ {esc(warn)}"
    return line


def _sinyal_overview(rows: list[dict], meta: dict, deps,
                     kval: dict | None = None) -> str:
    """Ringkasan satu layar: hanya intinya, kata biasa, tanpa jargon.

    Angka detail (rel, RSI, entry/SL/TP, fundamental) pindah ke tampilan
    rincian yang dibuka lewat tombol di overview_markup().
    """
    buys = [r for r in rows if r["signal"] == "BELI"]
    sells = [r for r in rows if r["signal"] == "JUAL"]
    # JUAL: yang terlemah dulu (rel terendah = paling layak dihindari),
    # supaya 3 besar yang tampil memang peringatan terkuat.
    sells.sort(key=lambda r: (r.get("rel_pct") if r.get("rel_pct") is not None
                              else 101, r["ticker"]))
    out = ["📡 <b>SINYAL PASAR IDX</b>",
           f"<i>{esc(deps._scan_when(meta))}</i>", "",
           f"Dari <b>{len(rows)}</b> saham: <b>{len(buys)}</b> layak dilirik · "
           f"<b>{len(sells)}</b> sebaiknya dihindari"]
    if buys:
        out += ["", "🟢 <b>BELI — layak dilirik</b>"]
        out += [_sinyal_row_ringkas(i + 1, r, (kval or {}).get(r["ticker"]))
                for i, r in enumerate(buys[:3])]
    else:
        out += ["", "🟢 Tidak ada kandidat BELI yang lolos filter hari ini — "
                "itu informasi, bukan kegagalan."]
    if sells:
        out += ["", "🔴 <b>JUAL — sebaiknya dihindari</b>"]
        out += [_sinyal_row_ringkas(i + 1, r) for i, r in enumerate(sells[:3])]
    out += ["", deps._SINYAL_DISCLAIMER]
    return "\n".join(out)


def _sinyal_section(rows: list[dict], sig: str, meta: dict, deps,
                    kval: dict | None = None) -> str:
    picked = [r for r in rows if r["signal"] == sig]
    if sig == "JUAL":
        # Sama seperti overview: yang terlemah dulu.
        picked.sort(key=lambda r: (r.get("rel_pct") if r.get("rel_pct") is not None
                                   else 101, r["ticker"]))
    head = ("🟢 <b>PERINGKAT ATAS</b> (kandidat beli)" if sig == "BELI"
            else "🔴 <b>PERINGKAT BAWAH</b> (hindari / kurangi bila punya)")
    out = [head, f"<i>{esc(deps._scan_when(meta))}</i>", ""]
    if not picked:
        out += ["Tidak ada emiten yang lolos filter ini hari ini.",
                "Itu bukan error — filter memang sengaja ketat.", ""]
    else:
        shown = picked[:25]
        out += [_sinyal_row(i + 1, r, (kval or {}).get(r["ticker"]))
                for i, r in enumerate(shown)]
        out += ["", f"Menampilkan {min(25, len(picked))} dari "
                f"{len(picked)} sinyal."]
        if kval:
            out += ["", "<i>✅ sehat · ⚠️ tidak lolos saringan · ❔ belum "
                    "dinilai (data fundamental gagal diambil). Penanda hanya "
                    "untuk 6 emiten teratas — bukan seluruh daftar.</i>"]
    out += ["", "<i>Label ini peringkat relatif 12 faktor (IC OOS ±0,03), "
            "bukan probabilitas naik. BELI = peringkat atas, JUAL = peringkat "
            "bawah — bukan instruksi short.</i>", "", deps._SINYAL_DISCLAIMER]
    return "\n".join(out)


def _sinyal_detail(r: dict, meta: dict, deps,
                   kval: dict | None = None) -> str:
    def pct(v):
        return f"{v:+.1f}%" if v is not None else "—"

    sig = r.get("signal") or "TAHAN"
    lines = [
        f"📡 <b>{esc(r['ticker'])}</b> — {esc(r.get('name') or '')}".rstrip(" —"),
        f"Sinyal: {_sinyal_emoji(sig)} <b>{esc(sig)}</b>",
        f"Harga: <b>{_rp(r.get('price'))}</b>",
        f"1 hari {pct(r.get('ret1d'))} · 5 hari {pct(r.get('ret5d'))} · "
        f"20 hari {pct(r.get('ret20d'))}",
        f"RSI <b>{r['rsi']:.1f}</b>" if r.get("rsi") is not None else "RSI —",
    ]
    if r.get("rel_pct") is not None:
        lines.append(f"Persentil relatif: <b>{r['rel_pct']:.0f}</b> dari "
                     f"{esc(meta.get('ok') or '?')} emiten")
    lines.append(f"MA20 {_rp(r.get('sma20'))} · MA50 {_rp(r.get('sma50'))} · "
                 f"MA200 {_rp(r.get('sma200'))}")
    if r.get("entry") and r.get("sl") and r.get("tp"):
        lines.append(f"Entry {_rp(r['entry'])} · SL {_rp(r['sl'])} "
                     f"(1×ATR) · TP {_rp(r['tp'])} (2×ATR)")
    if kval:
        lines.append(f"Kualitas fundamental: {kvalmod.mark(kval)} "
                     f"<b>{esc(kval.get('label') or '—')}</b>"
                     + (f" — {esc(', '.join(kval['bits']))}"
                        if kval.get("bits") else ""))
        if kval.get("reason"):
            lines.append(f"<i>{esc(kval['reason'])}</i>")
    if r.get("reason"):
        lines += ["", f"<i>{esc(r['reason'])}</i>"]
    lines += ["", f"<i>{esc(deps._scan_when(meta))}</i>",
              f"Laporan lengkap: <code>/analisis {esc(r['ticker'])}</code>",
              "", deps._SINYAL_DISCLAIMER]
    return "\n".join(lines)


def _sinyal_csv(rows: list[dict]) -> bytes:
    import csv
    import io

    cols = ["ticker", "name", "board", "signal", "price", "ret1d", "ret5d",
            "ret20d", "rsi", "macd_hist", "sma20", "sma50", "sma200", "atr",
            "tech_score", "rel_pct", "entry", "sl", "tp", "trade_date",
            "reason"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


BELI_BAGUS_POOL = 20      # kandidat teratas yang layak dinilai (bukan 944)
BELI_BAGUS_SHOW = 8


async def kualitas_map(tickers: list[str], deps) -> dict[str, dict]:
    """Nilai kualitas fundamental kandidat BELI, cache harian di TMP_DIR.

    Hanya emiten yang dipanggil (bukan seluruh pasar) dan hasilnya dipakai
    ulang sepanjang hari — pola yang sama dengan cache backtest (P8).
    """
    if not tickers:
        return {}
    day = marketcal.now_wib().date().isoformat()
    out: dict[str, dict] = {}
    sem = asyncio.Semaphore(3)

    async def one(t: str):
        async with sem:
            raw = await asyncio.to_thread(kvalmod.load_cached, deps.TMP_DIR, t, day)
            if raw is None:
                try:
                    raw = await asyncio.to_thread(
                        fundamental.fetch_fundamentals, t)
                except Exception:
                    log.warning("fundamental %s untuk kualitas gagal", t,
                                exc_info=True)
                    raw = None
                if raw:
                    await asyncio.to_thread(kvalmod.save_cached, deps.TMP_DIR, t,
                                            day, raw)
                await asyncio.sleep(0.3)   # sopan ke Yahoo
            out[t] = kvalmod.judge(raw)

    await asyncio.gather(*(one(t) for t in tickers))
    return out


def _beli_bagus_text(rows: list[dict], meta: dict, deps,
                     kval: dict) -> str:
    """Daftar kandidat BELI yang lolos saringan kualitas."""
    pool = rows[:BELI_BAGUS_POOL]
    dinilai = [r for r in pool if (kval.get(r["ticker"]) or {}).get("ok")
               is not None]
    lolos = [r for r in dinilai if kvalmod.passes(kval.get(r["ticker"]))]
    belum = [r for r in pool if (kval.get(r["ticker"]) or {}).get("ok") is None]
    gugur = [r for r in dinilai if not kvalmod.passes(kval.get(r["ticker"]))]
    out = ["🟢 <b>BELI BERKUALITAS</b>",
           f"<i>{esc(deps._scan_when(meta))}</i>",
           "",
           f"Dinilai <b>{len(pool)}</b> kandidat BELI teratas (peringkat "
           f"relatif) · <b>{len(lolos)}</b> lolos saringan",
           "<i>Saringan: laba positif · ROE ≥ 8% · PER ≤ 25× · PBV ≤ 4×. "
           "Ini syarat kualitas, <b>bukan jaminan naik</b>.</i>", ""]
    if not lolos:
        out += ["Tidak ada kandidat yang lolos hari ini.",
                "Itu bukan error — lebih baik tidak memaksa beli daripada "
                "membeli perusahaan yang tidak lolos syarat.", ""]
        if gugur:
            out.append("Alasan terbanyak: " + esc(
                " · ".join(f"{r['ticker']} {kval[r['ticker']]['label']}"
                           for r in gugur[:4])))
    else:
        for i, r in enumerate(lolos[:BELI_BAGUS_SHOW], 1):
            out.append(_sinyal_row(i, r, kval.get(r["ticker"])))
        if len(lolos) > BELI_BAGUS_SHOW:
            out.append(f"… dan {len(lolos) - BELI_BAGUS_SHOW} lainnya.")
    if gugur:
        out += ["", "Tidak lolos: " + esc(
            " · ".join(f"{r['ticker']} ({kval[r['ticker']]['label']})"
                       for r in gugur[:6]))]
    if belum:
        out += [f"❔ {len(belum)} kandidat belum dinilai (data fundamental "
                "gagal diambil) — tidak ditampilkan, bukan berarti buruk."]
    out += ["", deps._SINYAL_DISCLAIMER]
    return "\n".join(out)


def overview_markup() -> InlineKeyboardMarkup:
    """Tombol ringkasan: semua navigasi sinyal tanpa ketik perintah."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Semua BELI", callback_data="sg:buy"),
         InlineKeyboardButton("🔴 Semua JUAL", callback_data="sg:sell")],
        [InlineKeyboardButton("✅ BELI berkualitas", callback_data="sg:good"),
         InlineKeyboardButton("🔍 Detail saham",
                              callback_data="pick:sinyal_detail")],
        [InlineKeyboardButton("⬇️ Unduh CSV", callback_data="sg:csv"),
         InlineKeyboardButton("🔄 Scan ulang", callback_data="sg:scan")],
    ])


def section_markup(sig: str) -> InlineKeyboardMarkup:
    rows = []
    if sig == "BELI":
        rows.append([InlineKeyboardButton("✅ BELI berkualitas",
                                          callback_data="sg:good")])
    rows.append([InlineKeyboardButton("⬅️ Ringkasan", callback_data="sg:ov"),
                 InlineKeyboardButton("🔄 Scan ulang",
                                      callback_data="sg:scan")])
    return InlineKeyboardMarkup(rows)


def detail_markup(ticker: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Sudah beli di Stockbit",
                              callback_data=f"exec:buy:{ticker}")],
        [InlineKeyboardButton("📊 Analisis lengkap",
                              callback_data=f"sg:an:{ticker}")],
        [InlineKeyboardButton("⬅️ Ringkasan", callback_data="sg:ov")],
    ])


def bagus_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Semua BELI", callback_data="sg:buy"),
         InlineKeyboardButton("⬅️ Ringkasan", callback_data="sg:ov")],
    ])


def empty_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Scan pasar sekarang",
                              callback_data="sg:scan")],
    ])


_EMPTY_TEXT = (
    "📡 Belum ada hasil pemindaian pasar.\n\n"
    "Jalankan <code>/scan</code> dulu (±3–6 menit, sekali sehari). "
    "Untuk cepat di watchlist, pakai <code>/saran</code>.")


async def _overview_text(rows: list[dict], meta: dict, deps) -> str:
    buys = [r for r in rows if r["signal"] == "BELI"]
    buys.sort(key=lambda r: -(r.get("rel_pct") or -1))
    kval = await deps._kualitas_map([r["ticker"] for r in buys[:3]])
    return _sinyal_overview(rows, meta, deps, kval)


async def nav(update: Update, cb_data: str, deps, job_queue=None):
    """Navigasi tombol `sg:*`: ringkasan, rincian, CSV, scan, analisis.

    Dipanggil dari menu_callback di bot.py. Ringkasan diedit di tempat
    (pesan pendek); tampilan panjang dikirim sebagai pesan baru mengikuti
    pola tombol Perbarui yang sudah ada.
    """
    msg = update.effective_message
    if not msg:
        return
    rows = await asyncio.to_thread(store.load_market_scan)
    meta = await asyncio.to_thread(store.market_scan_meta)
    if not rows:
        await deps._safe_edit(msg, _EMPTY_TEXT, parse_mode=ParseMode.HTML,
                              reply_markup=empty_markup())
        return
    if cb_data == "sg:ov":
        await deps._safe_edit(
            msg, await _overview_text(rows, meta, deps),
            parse_mode=ParseMode.HTML, reply_markup=overview_markup())
        return
    if cb_data == "sg:scan":
        await deps._safe_edit(
            msg, "⏳ Memindai seluruh pasar (±3–6 menit)...",
            reply_markup=deps.home_markup())
        await deps._do_scan(update, [], job_queue)
        return
    if cb_data == "sg:csv":
        await msg.reply_document(
            document=_sinyal_csv(rows), filename="sinyal_idx.csv",
            caption=f"📡 Sinyal seluruh pasar — {len(rows)} emiten. "
                    "Buka di Excel/Sheets. Bukan saran investasi.")
        return
    if cb_data.startswith("sg:an:"):
        ticker = cb_data.split(":", 2)[2].upper()
        if not ticker:
            return
        await deps._safe_edit(
            msg, f"⏳ Menganalisis <b>{esc(ticker)}</b>...",
            parse_mode=ParseMode.HTML, reply_markup=deps.home_markup())
        await deps.run_menu_action(update, "analisis", [ticker])
        return
    if cb_data in ("sg:buy", "sg:sell", "sg:good"):
        await deps._safe_edit(msg, "⏳ Memuat...",
                              reply_markup=deps.home_markup())
        if cb_data == "sg:buy":
            buys = [r for r in rows if r["signal"] == "BELI"]
            buys.sort(key=lambda r: -(r.get("rel_pct") or -1))
            kval = await deps._kualitas_map([r["ticker"] for r in buys[:6]])
            text = _sinyal_section(rows, "BELI", meta, deps, kval)
            kb = section_markup("BELI")
        elif cb_data == "sg:sell":
            text = _sinyal_section(rows, "JUAL", meta, deps)
            kb = section_markup("JUAL")
        else:
            buys = [r for r in rows if r["signal"] == "BELI"]
            buys.sort(key=lambda r: -(r.get("rel_pct") or -1))
            if not buys:
                await deps.reply_long(
                    msg, "🟢 Tidak ada kandidat BELI di snapshot hari ini — "
                    "jadi tidak ada yang bisa disaring.",
                    reply_markup=section_markup("BELI"))
                return
            pool = buys[:BELI_BAGUS_POOL]
            kval = await deps._kualitas_map([r["ticker"] for r in pool])
            text = _beli_bagus_text(pool, meta, deps, kval)
            kb = bagus_markup()
        await deps.reply_long(msg, text, reply_markup=kb)
        return


def _with_home(kb: InlineKeyboardMarkup, from_menu: bool) -> InlineKeyboardMarkup:
    if not from_menu:
        return kb
    return InlineKeyboardMarkup(
        list(kb.inline_keyboard)
        + [[InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")]]
    )


async def do(update: Update, args: list[str], deps, *, from_menu: bool = False):
    rows = await asyncio.to_thread(store.load_market_scan)
    meta = await asyncio.to_thread(store.market_scan_meta)
    if not rows:
        await update.effective_message.reply_text(
            _EMPTY_TEXT, parse_mode=ParseMode.HTML,
            reply_markup=_with_home(empty_markup(), from_menu))
        return

    if not await deps.cooldown_ok(update, "sinyal"):
        return

    arg = (" ".join(a.strip().lower() for a in args).strip()
           if args else "")
    msg = update.effective_message
    if arg in ("csv", "unduh", "download"):
        data = _sinyal_csv(rows)
        await msg.reply_document(
            document=data, filename="sinyal_idx.csv",
            caption=f"📡 Sinyal seluruh pasar — {len(rows)} emiten. "
                    "Buka di Excel/Sheets. Bukan saran investasi.")
        return
    if arg in ("beli", "buy"):
        buys = [r for r in rows if r["signal"] == "BELI"]
        buys.sort(key=lambda r: -(r.get("rel_pct") or -1))
        kval = await deps._kualitas_map([r["ticker"] for r in buys[:6]])
        await deps.reply_long(msg, _sinyal_section(rows, "BELI", meta, deps,
                                                   kval),
                              reply_markup=_with_home(section_markup("BELI"), from_menu))
        return
    if arg in ("beli bagus", "bagus", "bagus beli", "beli berkualitas"):
        buys = [r for r in rows if r["signal"] == "BELI"]
        buys.sort(key=lambda r: -(r.get("rel_pct") or -1))
        if not buys:
            await msg.reply_text(
                "🟢 Tidak ada kandidat BELI di snapshot hari ini — jadi tidak "
                "ada yang bisa disaring.", parse_mode=ParseMode.HTML,
                reply_markup=_with_home(section_markup("BELI"), from_menu))
            return
        pool = buys[:BELI_BAGUS_POOL]
        kval = await deps._kualitas_map([r["ticker"] for r in pool])
        await deps.reply_long(msg, _beli_bagus_text(pool, meta, deps, kval),
                              reply_markup=_with_home(bagus_markup(), from_menu))
        return
    if arg in ("jual", "sell", "hindari"):
        await deps.reply_long(msg, _sinyal_section(rows, "JUAL", meta, deps),
                              reply_markup=_with_home(section_markup("JUAL"), from_menu))
        return
    if re.fullmatch(r"[A-Za-z]{4}", arg):
        found = next((r for r in rows if r["ticker"] == arg.upper()), None)
        if not found:
            await msg.reply_text(
                f"Kode <b>{esc(arg.upper())}</b> tidak ada di snapshot "
                f"(mungkin delisted/suspensi, atau tidak ada data harga cukup).\n"
                f"Coba <code>/analisis {esc(arg.upper())}</code> untuk cek "
                "langsung.", parse_mode=ParseMode.HTML,
                reply_markup=deps.home_markup() if from_menu else None)
            return
        kval = await deps._kualitas_map([found["ticker"]])
        sig_px = found.get("entry") or found.get("price")
        if sig_px and float(sig_px) > 0:
            await asyncio.to_thread(
                store.save_signal_context,
                update.effective_chat.id, found["ticker"], "sinyal",
                float(sig_px), found.get("sl"), found.get("tp"), None,
                str(meta.get("trade_date") or ""),
            )
        await deps.reply_long(msg, _sinyal_detail(found, meta, deps,
                                                  kval.get(found["ticker"])),
                              reply_markup=_with_home(detail_markup(found["ticker"]), from_menu))
        return
    await deps.reply_long(msg, await _overview_text(rows, meta, deps),
                          reply_markup=_with_home(overview_markup(), from_menu))


