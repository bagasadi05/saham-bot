"""Handler `/sinyal` (P15): snapshot pasar, penanda kualitas, `beli bagus`.

Dependensi bot (`_scan_when`, `_kualitas_map`, `reply_long`, `cooldown_ok`,
disclaimer) diterima lewat `deps` agar monkeypatch di tes tetap bekerja dan
tidak ada impor melingkar.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

import config
from core import fundamental, kualitas as kvalmod, marketcal, pricing, store
from core.reports import _rp, esc
from core.textutil import rupiah

log = logging.getLogger("sahambuddy")


def _sinyal_emoji(sig: str) -> str:
    return {"BELI": "🟢", "JUAL": "🔴"}.get(sig, "⚪")


def _sort_buys(rows: list[dict]) -> list[dict]:
    """Urutkan kandidat BELI secara konsisten (rel_pct tertinggi, lalu kode)."""
    buys = [r for r in rows if r.get("signal") == "BELI"]
    buys.sort(
        key=lambda r: (
            -(r.get("rel_pct") if r.get("rel_pct") is not None else -1),
            r.get("ticker") or "",
        )
    )
    return buys


def _sort_sells(rows: list[dict]) -> list[dict]:
    """Urutkan kandidat JUAL secara konsisten (rel_pct terendah dulu)."""
    sells = [r for r in rows if r.get("signal") == "JUAL"]
    sells.sort(
        key=lambda r: (
            r.get("rel_pct") if r.get("rel_pct") is not None else 101,
            r.get("ticker") or "",
        )
    )
    return sells


def _sinyal_row(i: int, r: dict, kval: dict | None = None) -> str:
    parts = [f"{i}. <b>{esc(r['ticker'])}</b> {_rp(r.get('price'))}"]
    if r.get("rel_pct") is not None:
        parts.append(f"rel {r['rel_pct']:.0f}")
    if r.get("rsi") is not None:
        parts.append(f"RSI {r['rsi']:.0f}")
    if r.get("ret20d") is not None:
        parts.append(f"20h {r['ret20d']:+.1f}%")
    if r.get("stale"):
        parts.append("⚠️stale")
    line = " · ".join(parts)
    if kval:
        line += f" · {kvalmod.mark(kval)} {esc(kval.get('label') or '')}"
    if r.get("quality_note"):
        line += f" · ⚠️ <i>{esc(r['quality_note'])}</i>"
    if r.get("entry") and r.get("sl") and r.get("tp"):
        arb = pricing.check_arb_gap_risk(float(r["entry"]), float(r["sl"]))
        arb_tag = " · ⚠️ risiko gap ARB" if arb.get("risk") else ""
        line += (f"\n   <i>entry {_rp(r['entry'])} · SL {_rp(r['sl'])} · "
                 f"TP {_rp(r['tp'])}{arb_tag}</i>")
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
    buys = _sort_buys(rows)
    sells = _sort_sells(rows)
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
    if sig == "BELI":
        picked = _sort_buys(rows)
    elif sig == "JUAL":
        picked = _sort_sells(rows)
    else:
        picked = [r for r in rows if r.get("signal") == sig]
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
        if sig == "BELI":
            out.append("<i>Ketuk tombol saham di bawah untuk cek 6 poin "
                       "atau hitung lot sesuai modal.</i>")
        elif sig == "JUAL":
            out.append("<i>Ketuk tombol saham di bawah untuk cek layak "
                       "jual atau buka analisis lengkap.</i>")
    out += ["", "<i>Label ini peringkat relatif 12 faktor (IC OOS ±0,03), "
            "bukan probabilitas naik. BELI = peringkat atas, JUAL = peringkat "
            "bawah — bukan instruksi short.</i>", "", deps._SINYAL_DISCLAIMER]
    return "\n".join(out)


def _sinyal_detail(r: dict, meta: dict, deps,
                   kval: dict | None = None) -> str:
    def pct(v):
        return f"{v:+.1f}%" if v is not None else "—"

    sig = r.get("signal") or "TAHAN"
    td_badge = f" · data per <code>{esc(r['trade_date'])}</code>" if r.get("trade_date") else ""
    stale_badge = " ⚠️(stale)" if r.get("stale") else ""
    lines = [
        f"📡 <b>{esc(r['ticker'])}</b> — {esc(r.get('name') or '')}".rstrip(" —"),
        f"Sinyal: {_sinyal_emoji(sig)} <b>{esc(sig)}</b>",
        f"Harga: <b>{_rp(r.get('price'))}</b>{td_badge}{stale_badge}",
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
        arb = pricing.check_arb_gap_risk(float(r["entry"]), float(r["sl"]))
        if arb.get("warning"):
            lines.append(f"⚠️ <i>{esc(arb['warning'])}</i>")
    if r.get("quality_note"):
        lines.append(f"⚠️ <i>Catatan data: {esc(r['quality_note'])}</i>")
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


def _clean_ticker(raw: str | None) -> str:
    """Normalkan kode emiten menjadi `[A-Z0-9]{2,10}` tanpa `.JK`/spasi."""
    s = str(raw or "").strip().upper()
    if s.endswith(".JK"):
        s = s[:-3]
    return s if re.fullmatch(r"[A-Z0-9]{2,10}", s) else ""


def _clean_tickers(tickers: list[str] | None, limit: int) -> list[str]:
    """Bersihkan, deduplikasi, dan batasi daftar ticker untuk tombol."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in tickers or []:
        t = _clean_ticker(raw)
        if t and t not in seen:
            seen.add(t)
            out.append(t)
            if len(out) >= limit:
                break
    return out


def _safe_cb(cb: str) -> str:
    """Pastikan callback_data tidak melebihi batas 64 byte Telegram."""
    if len(cb.encode("utf-8")) > 64:
        raise ValueError(f"callback_data melebihi 64 byte: {cb!r}")
    return cb


def _partisi_kualitas(
    rows: list[dict], kval: dict
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Pisahkan pool BELI menjadi lolos, belum dinilai, dan gugur."""
    pool = rows[:BELI_BAGUS_POOL]
    dinilai = [r for r in pool
               if (kval.get(r["ticker"]) or {}).get("ok") is not None]
    lolos = [r for r in dinilai if kvalmod.passes(kval.get(r["ticker"]))]
    belum = [r for r in pool
             if (kval.get(r["ticker"]) or {}).get("ok") is None]
    gugur = [r for r in dinilai if not kvalmod.passes(kval.get(r["ticker"]))]
    return pool, lolos, belum, gugur


def _lot_cost_1(price) -> int | None:
    """Estimasi biaya beli 1 lot (100 lembar + fee beli BEI 0,15%)."""
    try:
        px = float(price)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(px) or px <= 0:
        return None
    return int(math.ceil(round(px * 100.0 * (1.0 + config.IDX_BUY_FEE), 6)))


def _lot_vs_modal_note(price, modal: float | None) -> str:
    """Baris estimasi biaya 1 lot dan kecukupannya terhadap modal."""
    cost = _lot_cost_1(price)
    if cost is None:
        return "   <i>biaya 1 lot: harga tidak tersedia</i>"
    if modal is not None and modal > 0:
        slot25 = modal * 0.25
        if cost <= slot25:
            status = f"✅ cukup slot 25% &amp; modal {rupiah(modal)}"
        elif cost <= modal:
            status = f"⚠️ &gt; slot 25%, cukup modal penuh {rupiah(modal)}"
        else:
            kurang = cost - modal
            status = f"⛔ &gt; modal {rupiah(modal)}, kurang {rupiah(kurang)}"
        return f"   <i>1 lot ≈ {rupiah(cost)} ({status})</i>"
    return f"   <i>1 lot ≈ {rupiah(cost)} (termasuk fee beli 0,15%)</i>"


def _beli_bagus_text(rows: list[dict], meta: dict, deps,
                     kval: dict, modal: int | float | None = None) -> str:
    """Daftar kandidat BELI yang lolos saringan kualitas."""
    pool, lolos, belum, gugur = _partisi_kualitas(rows, kval)
    modal_val: float | None = None
    if modal is not None:
        try:
            m = float(modal)
            if math.isfinite(m) and m > 0:
                modal_val = m
        except (TypeError, ValueError):
            modal_val = None
    out = ["🟢 <b>BELI BERKUALITAS</b>",
           f"<i>{esc(deps._scan_when(meta))}</i>",
           "",
           f"Dinilai <b>{len(pool)}</b> kandidat BELI teratas (peringkat "
           f"relatif) · <b>{len(lolos)}</b> lolos saringan"]
    if modal_val is not None:
        out.append(
            f"Modal tersimpan: <b>{rupiah(modal_val)}</b> "
            f"(slot 25%/posisi: <b>{rupiah(modal_val * 0.25)}</b>)"
        )
    out += ["<i>Saringan: laba positif · ROE ≥ 8% · PER ≤ 25× · PBV ≤ 4×. "
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
            out.append(_lot_vs_modal_note(r.get("price"), modal_val))
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


def section_markup(
    sig: str,
    tickers: list[str] | None = None,
    *,
    from_menu: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    sig_up = (sig or "").upper()
    clean = _clean_tickers(tickers, limit=4)
    if sig_up == "BELI":
        for t in clean:
            rows.append([
                InlineKeyboardButton(f"🔍 Cek {t}",
                                     callback_data=_safe_cb(f"tick:cek:{t}")),
                InlineKeyboardButton(f"🎯 Hitung Lot {t}",
                                     callback_data=_safe_cb(f"tick:rencana:{t}")),
            ])
        rows.append([InlineKeyboardButton("✅ BELI berkualitas",
                                          callback_data="sg:good")])
    elif sig_up == "JUAL":
        for t in clean:
            rows.append([
                InlineKeyboardButton(f"🤔 Cek Jual {t}",
                                     callback_data=_safe_cb(f"tick:jual:{t}")),
                InlineKeyboardButton(f"📈 Analisis {t}",
                                     callback_data=_safe_cb(f"tick:analisis:{t}")),
            ])
    rows.append([InlineKeyboardButton("⬅️ Ringkasan", callback_data="sg:ov"),
                 InlineKeyboardButton("🔄 Scan ulang",
                                      callback_data="sg:scan")])
    return _with_home(InlineKeyboardMarkup(rows), from_menu)


def detail_markup(
    ticker: str,
    sig: str | None = None,
    *,
    from_menu: bool = False,
) -> InlineKeyboardMarkup:
    t = _clean_ticker(ticker)
    if not t:
        return _with_home(
            InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Ringkasan", callback_data="sg:ov"),
            ]]),
            from_menu,
        )
    rows: list[list[InlineKeyboardButton]] = []
    if (sig or "").upper() == "JUAL":
        rows.append([
            InlineKeyboardButton("🤔 Cek layak jual?",
                                 callback_data=_safe_cb(f"tick:jual:{t}")),
            InlineKeyboardButton("📝 Sudah jual di Stockbit",
                                 callback_data=_safe_cb(f"exec:sell:{t}")),
        ])
    else:
        rows.append([
            InlineKeyboardButton("🛡️ Cek 6 Poin",
                                 callback_data=_safe_cb(f"tick:cek:{t}")),
            InlineKeyboardButton("🎯 Hitung Lot",
                                 callback_data=_safe_cb(f"tick:rencana:{t}")),
        ])
        rows.append([
            InlineKeyboardButton("📝 Sudah beli di Stockbit",
                                 callback_data=_safe_cb(f"exec:buy:{t}")),
            InlineKeyboardButton("🤔 Cek layak jual?",
                                 callback_data=_safe_cb(f"tick:jual:{t}")),
        ])
    rows.append([InlineKeyboardButton("📊 Analisis lengkap",
                                      callback_data=_safe_cb(f"sg:an:{t}"))])
    rows.append([InlineKeyboardButton("⬅️ Ringkasan", callback_data="sg:ov")])
    return _with_home(InlineKeyboardMarkup(rows), from_menu)


def bagus_markup(
    tickers: list[str] | None = None,
    *,
    from_menu: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for t in _clean_tickers(tickers, limit=5):
        rows.append([
            InlineKeyboardButton(f"🔍 Cek {t}",
                                 callback_data=_safe_cb(f"tick:cek:{t}")),
            InlineKeyboardButton(f"🎯 Hitung Lot {t}",
                                 callback_data=_safe_cb(f"tick:rencana:{t}")),
        ])
    rows.append([InlineKeyboardButton("🟢 Semua BELI", callback_data="sg:buy"),
                 InlineKeyboardButton("⬅️ Ringkasan", callback_data="sg:ov")])
    return _with_home(InlineKeyboardMarkup(rows), from_menu)


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
    buys = _sort_buys(rows)
    kval = await deps._kualitas_map([r["ticker"] for r in buys[:3]])
    return _sinyal_overview(rows, meta, deps, kval)


async def _load_modal(update: Update) -> float | None:
    chat = getattr(update, "effective_chat", None)
    chat_id = getattr(chat, "id", None)
    if chat_id is None:
        return None
    return await asyncio.to_thread(store.get_modal, chat_id)


def _csv_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Ringkasan", callback_data="sg:ov"),
    ]])


async def _build_section_reply(
    rows: list[dict], sig: str, meta: dict, deps
) -> tuple[str, InlineKeyboardMarkup]:
    if sig == "BELI":
        picked = _sort_buys(rows)
        kval = await deps._kualitas_map([r["ticker"] for r in picked[:6]])
    else:
        picked = _sort_sells(rows)
        kval = None
    text = _sinyal_section(rows, sig, meta, deps, kval)
    kb = section_markup(sig, [r.get("ticker") for r in picked])
    return text, kb


async def _build_bagus_reply(
    update: Update, buys: list[dict], meta: dict, deps
) -> tuple[str, InlineKeyboardMarkup]:
    modal = await _load_modal(update)
    pool = buys[:BELI_BAGUS_POOL]
    kval = await deps._kualitas_map([r["ticker"] for r in pool])
    _, lolos, _, _ = _partisi_kualitas(pool, kval)
    text = _beli_bagus_text(pool, meta, deps, kval, modal=modal)
    kb = bagus_markup([r.get("ticker") for r in lolos])
    return text, kb


async def nav(update: Update, cb_data: str, deps, job_queue=None):
    """Navigasi tombol `sg:*`: ringkasan, rincian, CSV, scan, analisis.

    Dipanggil dari menu_callback di bot.py. Ringkasan diedit di tempat
    (pesan pendek); tampilan panjang dikirim sebagai pesan baru mengikuti
    pola tombol Perbarui yang sudah ada.
    """
    msg = update.effective_message
    if not msg:
        return
    if cb_data == "sg:scan":
        await deps._safe_edit(
            msg, "⏳ Memproses permintaan scan pasar...",
            reply_markup=deps.home_markup())
        await deps._do_scan(update, ["paksa"], job_queue, from_menu=True)
        return
    rows = await asyncio.to_thread(store.load_market_scan)
    meta = await asyncio.to_thread(store.market_scan_meta)
    if not rows:
        await deps._safe_edit(msg, _EMPTY_TEXT, parse_mode=ParseMode.HTML,
                              reply_markup=_with_home(empty_markup(), True))
        return
    if cb_data == "sg:ov":
        await deps._safe_edit(
            msg, await _overview_text(rows, meta, deps),
            parse_mode=ParseMode.HTML,
            reply_markup=_with_home(overview_markup(), True))
        return
    if cb_data == "sg:csv":
        await msg.reply_document(
            document=_sinyal_csv(rows), filename="sinyal_idx.csv",
            caption=f"📡 Sinyal seluruh pasar — {len(rows)} emiten. "
                    "Buka di Excel/Sheets. Bukan saran investasi.",
            reply_markup=_with_home(_csv_markup(), True),
        )
        return
    if cb_data.startswith("sg:an:"):
        ticker = _clean_ticker(cb_data.split(":", 2)[2])
        if not ticker:
            await deps._safe_edit(
                msg, "❌ Kode saham tidak valid.",
                reply_markup=deps.home_markup())
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
            text, kb = await _build_section_reply(rows, "BELI", meta, deps)
        elif cb_data == "sg:sell":
            text, kb = await _build_section_reply(rows, "JUAL", meta, deps)
        else:
            buys = _sort_buys(rows)
            if not buys:
                await deps.reply_long(
                    msg, "🟢 Tidak ada kandidat BELI di snapshot hari ini — "
                    "jadi tidak ada yang bisa disaring.",
                    reply_markup=_with_home(section_markup("BELI"), True))
                return
            text, kb = await _build_bagus_reply(update, buys, meta, deps)
        await deps.reply_long(msg, text, reply_markup=_with_home(kb, True))
        return
    await deps._safe_edit(
        msg, await _overview_text(rows, meta, deps),
        parse_mode=ParseMode.HTML,
        reply_markup=_with_home(overview_markup(), True))


def _has_home(kb: InlineKeyboardMarkup) -> bool:
    return any(b.callback_data == "menu"
               for row in kb.inline_keyboard for b in row)


def _with_home(kb: InlineKeyboardMarkup, from_menu: bool) -> InlineKeyboardMarkup:
    if not from_menu or _has_home(kb):
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
                    "Buka di Excel/Sheets. Bukan saran investasi.",
            reply_markup=_with_home(_csv_markup(), from_menu),
        )
        return
    if arg in ("beli", "buy"):
        text, kb = await _build_section_reply(rows, "BELI", meta, deps)
        await deps.reply_long(msg, text, reply_markup=_with_home(kb, from_menu))
        return
    if arg in ("beli bagus", "bagus", "bagus beli", "beli berkualitas"):
        buys = _sort_buys(rows)
        if not buys:
            await msg.reply_text(
                "🟢 Tidak ada kandidat BELI di snapshot hari ini — jadi tidak "
                "ada yang bisa disaring.", parse_mode=ParseMode.HTML,
                reply_markup=_with_home(section_markup("BELI"), from_menu))
            return
        text, kb = await _build_bagus_reply(update, buys, meta, deps)
        await deps.reply_long(msg, text, reply_markup=_with_home(kb, from_menu))
        return
    if arg in ("jual", "sell", "hindari"):
        text, kb = await _build_section_reply(rows, "JUAL", meta, deps)
        await deps.reply_long(msg, text, reply_markup=_with_home(kb, from_menu))
        return
    if re.fullmatch(r"[A-Za-z]{4}(?:\.jk)?", arg):
        clean_t = _clean_ticker(arg)
        found = next(
            (r for r in rows if _clean_ticker(r.get("ticker")) == clean_t),
            None,
        )
        if not found:
            await msg.reply_text(
                f"Kode <b>{esc(clean_t or arg.upper())}</b> tidak ada di snapshot "
                f"(mungkin delisted/suspensi, atau tidak ada data harga cukup).\n"
                f"Coba <code>/analisis {esc(clean_t or arg.upper())}</code> untuk cek "
                "langsung.", parse_mode=ParseMode.HTML,
                reply_markup=deps.home_markup() if from_menu else None)
            return
        kval = await deps._kualitas_map([found["ticker"]])
        sig_px = found.get("entry") or found.get("price")
        intent = "jual" if found.get("signal") == "JUAL" else "beli"
        ctx_ticker = _clean_ticker(found["ticker"]) or found["ticker"]
        if sig_px and float(sig_px) > 0:
            await asyncio.to_thread(
                store.save_signal_context,
                update.effective_chat.id, ctx_ticker, "sinyal",
                float(sig_px), found.get("sl"), found.get("tp"), None,
                str(found.get("trade_date") or meta.get("trade_date") or ""),
                intent=intent,
            )
        await deps.reply_long(
            msg,
            _sinyal_detail(found, meta, deps, kval.get(found["ticker"])),
            reply_markup=_with_home(
                detail_markup(found["ticker"], sig=found.get("signal")),
                from_menu,
            ),
        )
        return
    await deps.reply_long(msg, await _overview_text(rows, meta, deps),
                          reply_markup=_with_home(overview_markup(), from_menu))


