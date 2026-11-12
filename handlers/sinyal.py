"""Handler `/sinyal` (P15): snapshot pasar, penanda kualitas, `beli bagus`.

Dependensi bot (`_scan_when`, `_kualitas_map`, `reply_long`, `cooldown_ok`,
disclaimer) diterima lewat `deps` agar monkeypatch di tes tetap bekerja dan
tidak ada impor melingkar.
"""
from __future__ import annotations

import asyncio
import logging
import re

from telegram import Update
from telegram.constants import ParseMode

from core import fundamental, kualitas as kvalmod, marketcal, store
from core.reports import _rp, esc

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


def _sinyal_overview(rows: list[dict], meta: dict, deps,
                     kval: dict | None = None) -> str:
    buys = [r for r in rows if r["signal"] == "BELI"]
    sells = [r for r in rows if r["signal"] == "JUAL"]
    holds = len(rows) - len(buys) - len(sells)
    # JUAL: yang terlemah dulu (rel terendah = paling layak dihindari),
    # supaya 3 besar yang tampil memang peringatan terkuat.
    sells.sort(key=lambda r: (r.get("rel_pct") if r.get("rel_pct") is not None
                              else 101, r["ticker"]))
    out = ["📡 <b>SINYAL PASAR IDX</b>", f"<i>{esc(deps._scan_when(meta))}</i>", "",
           f"🟢 BELI: <b>{len(buys)}</b> · 🔴 JUAL/HINDARI: <b>{len(sells)}</b>"
           f" · ⚪ TAHAN: <b>{holds}</b>",
           "<i>rel = peringkat 0–100, makin tinggi makin kuat · RSI di atas "
           "70 = jenuh beli · 20h = perubahan 20 hari terakhir</i>"]
    if buys:
        out += ["", "🟢 <b>Kandidat BELI</b> (peringkat relatif tertinggi)"]
        out += [_sinyal_row(i + 1, r, (kval or {}).get(r["ticker"]))
                for i, r in enumerate(buys[:3])]
    else:
        out += ["", "🟢 Tidak ada kandidat BELI yang lolos filter hari ini — "
                "itu informasi, bukan kegagalan."]
    if sells:
        out += ["", "🔴 <b>Hindari / JUAL bila punya</b> (tren &amp; peringkat lemah)"]
        out += [_sinyal_row(i + 1, r) for i, r in enumerate(sells[:3])]
    out += ["", "Rincian: <code>/sinyal beli</code> · <code>/sinyal jual</code> · "
            "<code>/sinyal BBCA</code> · <code>/sinyal csv</code>",
            "Perbarui data: <code>/scan</code>", "", deps._SINYAL_DISCLAIMER]
    return "\n".join(out)


def _sinyal_section(rows: list[dict], sig: str, meta: dict, deps,
                    kval: dict | None = None) -> str:
    picked = [r for r in rows if r["signal"] == sig]
    if sig == "JUAL":
        # Sama seperti overview: yang terlemah dulu.
        picked.sort(key=lambda r: (r.get("rel_pct") if r.get("rel_pct") is not None
                                   else 101, r["ticker"]))
    head = ("🟢 <b>KANDIDAT BELI</b>" if sig == "BELI"
            else "🔴 <b>HINDARI / JUAL BILA PUNYA</b>")
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
    out += ["", deps._SINYAL_DISCLAIMER]
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
    out += ["", "Semua sinyal: <code>/sinyal beli</code> · perbarui data: "
            "<code>/scan</code>", "", deps._SINYAL_DISCLAIMER]
    return "\n".join(out)


async def do(update: Update, args: list[str], deps):
    rows = store.load_market_scan()
    meta = store.market_scan_meta()
    if not rows:
        await update.effective_message.reply_text(
            "📡 Belum ada hasil pemindaian pasar.\n\n"
            "Jalankan <code>/scan</code> dulu (±3–6 menit, sekali sehari). "
            "Untuk cepat di watchlist, pakai <code>/saran</code>.",
            parse_mode=ParseMode.HTML)
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
        await deps.reply_long(msg, _sinyal_section(rows, "BELI", meta, deps, kval))
        return
    if arg in ("beli bagus", "bagus", "bagus beli", "beli berkualitas"):
        buys = [r for r in rows if r["signal"] == "BELI"]
        buys.sort(key=lambda r: -(r.get("rel_pct") or -1))
        if not buys:
            await msg.reply_text(
                "🟢 Tidak ada kandidat BELI di snapshot hari ini — jadi tidak "
                "ada yang bisa disaring.", parse_mode=ParseMode.HTML)
            return
        pool = buys[:BELI_BAGUS_POOL]
        kval = await deps._kualitas_map([r["ticker"] for r in pool])
        await deps.reply_long(msg, _beli_bagus_text(pool, meta, deps, kval))
        return
    if arg in ("jual", "sell", "hindari"):
        await deps.reply_long(msg, _sinyal_section(rows, "JUAL", meta, deps))
        return
    if re.fullmatch(r"[A-Za-z]{4}", arg):
        found = next((r for r in rows if r["ticker"] == arg.upper()), None)
        if not found:
            await msg.reply_text(
                f"Kode <b>{esc(arg.upper())}</b> tidak ada di snapshot "
                f"(mungkin delisted/suspensi, atau tidak ada data harga cukup).\n"
                f"Coba <code>/analisis {esc(arg.upper())}</code> untuk cek "
                "langsung.", parse_mode=ParseMode.HTML)
            return
        kval = await deps._kualitas_map([found["ticker"]])
        await deps.reply_long(msg, _sinyal_detail(found, meta, deps,
                                             kval.get(found["ticker"])))
        return
    buys = [r for r in rows if r["signal"] == "BELI"]
    buys.sort(key=lambda r: -(r.get("rel_pct") or -1))
    kval = await deps._kualitas_map([r["ticker"] for r in buys[:3]])
    await deps.reply_long(msg, _sinyal_overview(rows, meta, deps, kval))


