"""Handler `/scan`, `/paket`, `/rencana` (pola deps).

Semua dependensi milik bot — `store`, penjaga `_screener_running`,
`_scan_summary_text`, `_kualitas_map`, `_build_plan_payload`, formatter, dan
`reply_long` — diakses lewat `deps` (modul `bot`), sehingga monkeypatch di tes
tetap bekerja dan tidak ada impor melingkar.
"""
from __future__ import annotations

import asyncio
import logging
import re

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction, ParseMode

from core import (bandar, marketcal, regime, rencana, reports,
                  risk as riskmod, screener, store, universe)
from core.reports import esc
from core.textutil import parse_rupiah, split_message
from handlers import jurnal as jurnal_handler

log = logging.getLogger("sahambuddy")


PLAN_CONCURRENCY = 3


def _plan_card(p: dict, idx: int, deps) -> str:
    medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(idx, "▫️")
    name = esc(p.get("name") or "")
    head = f"{medal} <b>{esc(p['ticker'])}</b>"
    if name:
        head += f" — {name}"
    lines = [
        head,
        f"Grade <b>{esc(p.get('grade') or '—')}</b> · "
        f"{esc(p.get('sector') or 'sektor tidak tersedia')} · "
        f"{esc(p.get('board') or '—')}",
        f"Harga <b>{deps._money(p['price'])}</b>"
        + (f" · rel {p['rel_pct']:.0f}" if p.get("rel_pct") is not None else "")
        + (f" · RSI {p['rsi']:.0f}" if p.get("rsi") is not None else "")
        + f" · 20h {deps._plan_pct(p.get('ret20d'))}"
        + f" · likuiditas {deps._rp_big(p.get('value_avg20'))}/hari",
    ]
    fund_bits = []
    for key, label, fmt in (("per", "PER", "{:.1f}"), ("pbv", "PBV", "{:.2f}"),
                            ("roe", "ROE", "{:.1f}%"),
                            ("div_yield", "Div", "{:.1f}%")):
        val = p.get(key)
        if val is not None:
            fund_bits.append(f"{label} " + fmt.format(val).replace(".", ","))
    lines.append("Fundamental: " + (" · ".join(fund_bits) if fund_bits
                                    else "tidak tersedia"))
    if p.get("news_pct") is not None:
        news = (f"Berita: <b>{esc(p.get('news_label') or '—')}</b> "
                f"({p['news_pct']:+.0f}")
        if p.get("news_n"):
            news += f" · {p['news_n']} berita"
        news += ")"
        if p.get("news_top"):
            news += f" — <i>{esc(p['news_top'][0].get('title'))}</i>"
        lines.append(news)
    shares_txt = f"{p['shares']:,}".replace(",", ".")
    risk_pct_txt = f"{p['risk_pct']:.2f}".replace(".", ",")
    lines += [
        "",
        f"Beli <b>LIMIT ≤ {deps._money(p['entry'])}</b> · "
        f"<b>{p['lots']} lot</b> ({shares_txt} lembar) ≈ "
        f"<b>{deps._money(p['value'])}</b>",
        f"SL <b>{deps._money(p['sl'])}</b> · TP <b>{deps._money(p['tp'])}</b> "
        f"(net {deps._plan_pct(p.get('net_tp_pct'))}) · RR 1:{p['rr']:g}",
        f"Risiko maks <b>{deps._money(p['real_risk'])}</b> "
        f"({risk_pct_txt}% modal) · batalkan bila open di atas "
        f"{deps._money(p['chase_above'])}",
    ]
    if p.get("reason"):
        lines.append(f"Alasan: <i>{esc(p['reason'])}</i>")
    warns = list(p.get("warnings") or [])[:2]
    if warns:
        lines.append("⚠️ " + esc("; ".join(
            w.replace(".", ",") for w in warns)))
    return "\n".join(lines)


def _rencana_text(result: dict, payload: dict, meta: dict, deps) -> str:
    picks = result.get("picks") or []
    modal = result.get("modal") or 0
    reg = result.get("regime")
    scale = regime.effective_scale(reg)
    risk_lim = result.get("risk_pct_limit") or round(1.0 * scale, 2)
    pos_lim = result.get("max_pos_pct") or round(rencana.MAX_POS_PCT * scale * 100, 1)
    sec_lim = result.get("max_sector_pct")
    tot_lim = result.get("max_total_pct") or round(rencana.MAX_TOTAL_PCT * scale * 100, 1)
    sec_txt = f" · sektor maks <b>{sec_lim:g}%</b>" if sec_lim else ""
    out = [
        "🎯 <b>RENCANA BELI — SIAP STOCKBIT</b>",
        f"<i>{esc(deps._scan_when(meta))}</i>",
        f"Modal rencana <b>{deps._money(modal)}</b> · risiko maks <b>{risk_lim:g}%</b>/posisi · "
        f"nilai posisi maks <b>{pos_lim:g}%</b>/emiten{sec_txt} · "
        f"total maks <b>{tot_lim:g}%</b> modal",
    ]
    if reg is not None:
        out.append(reports.regime_banner(reg))
    out.append("")
    rejected = deps._reject_summary(payload.get("rejected"), result.get("rejected"))
    if not picks:
        out += [
            "❌ <b>Tidak ada kandidat yang lolos semua saringan hari ini.</b>",
            "",
            "Itu bukan error — lebih baik tidak memaksa beli daripada "
            "melanggar aturan likuiditas/risiko.",
        ]
        if rejected:
            out += ["", f"Alasan terbanyak: {rejected}"]
        else:
            out += ["", "Alasan: tidak ada sinyal BELI yang lolos saringan."]
        out += ["", "Cek kandidat yang lebih longgar: <code>/sinyal beli</code>",
                "Perbarui data: <code>/scan</code>",
                "Ubah modal: <code>/modal 10000000</code>",
                "", deps._SINYAL_DISCLAIMER]
        return "\n".join(out)

    for i, p in enumerate(picks, 1):
        out.append(_plan_card(p, i, deps))
        out.append("")
    if modal:
        total_cost = sum(p.get("buy_cost") or 0 for p in picks)
        total_risk = sum(p.get("real_risk") or 0 for p in picks)
        out += [
            f"Total biaya beli (termasuk fee): <b>{deps._money(total_cost)}</b> "
            f"({deps._plan_pct(total_cost / modal * 100, sign=False)} modal) · "
            f"total risiko di SL <b>{deps._money(total_risk)}</b> "
            f"({deps._plan_pct(total_risk / modal * 100, sign=False)} modal) · "
            f"sisa cash <b>{deps._money(max(modal - total_cost, 0))}</b>",
            "",
        ]
    if rejected:
        out += [f"Yang gugur: {rejected}", ""]
    out += [
        "📱 <b>Cara pasang di Stockbit</b>",
        "1. Cari kode → pilih <b>Limit Order</b>, bukan Market.",
        "2. Isi lot sesuai rencana; harga beli ≤ entry. Jangan kejar harga.",
        "3. Pasang alert harga di SL &amp; TP. Kalau Stockbit-mu tidak punya "
        "stop order otomatis, eksekusi manual saat alert berbunyi.",
        "4. Fee beli/jual (±0,15%/±0,25%) sudah diperhitungkan; "
        "cek fee akunmu di Stockbit.",
        "5. Tahan posisi selama skor relatif masih top 60% (±20–40 hari bursa) "
        "sebelum rotasi agar tidak tergerus biaya 0,40%.",
        "",
        "Grade A/B/C = kelengkapan syarat, <b>bukan probabilitas</b>. Yang "
        "tervalidasi hanya peringkat relatif (IC ±0,03); saringan likuiditas/"
        "kualitas belum di-backtest sebagai satu paket. SL wajib dipakai.",
        "", "Ubah modal rencana: <code>/modal 10000000</code>",
        "", deps._SINYAL_DISCLAIMER,
    ]
    return "\n".join(out)


_ORIG_FETCH_REGIME = regime.fetch_ihsg_regime


async def _fetch_regime_safe(deps) -> dict | None:
    """Ambil rezim IHSG secara aman (hormati monkeypatch & jangan panggil jaringan saat unit test)."""
    reg_mod = getattr(deps, "regime", regime)
    fn = getattr(reg_mod, "fetch_ihsg_regime", None)
    if not callable(fn):
        return None
    if fn is not _ORIG_FETCH_REGIME:
        try:
            return await asyncio.to_thread(fn)
        except Exception:
            return None
    with regime._regime_lock:
        cached = regime._regime_cache.get("val")
    if cached is not None:
        return dict(cached)
    gather_fn = getattr(deps, "gather_candidate", None)
    inputs_fn = getattr(deps, "_rencana_inputs", None)
    if (
        (gather_fn is not None and getattr(gather_fn, "__module__", "") != "bot")
        or (inputs_fn is not None and getattr(inputs_fn, "__module__", "") != "bot")
    ):
        return regime.evaluate_ihsg(None)
    try:
        return await asyncio.to_thread(fn)
    except Exception:
        return regime.evaluate_ihsg(None)


async def build_plan_payload(rows: list[dict], meta: dict, deps) -> dict:
    """Perkaya kandidat teratas dengan fundamental + berita (paralel dibatasi)."""
    cands, rejected = rencana.prefilter(rows)
    sem = asyncio.Semaphore(PLAN_CONCURRENCY)

    async def one(c: dict):
        async with sem:
            try:
                analyzed = await deps.gather_candidate(c["ticker"])
            except Exception:
                log.exception("pengayaan %s gagal", c.get("ticker"))
                analyzed = None
            try:
                return rencana.enrich(c, analyzed)
            except Exception:
                log.exception("enrich %s gagal", c.get("ticker"))
                return None

    enriched = [x for x in await asyncio.gather(*(one(c) for c in cands))
                if x]
    return {"enriched": enriched, "rejected": dict(rejected),
            "meta": meta, "pool": len(cands),
            "built_at": marketcal.now_wib().isoformat(timespec="seconds")}


async def do_paket(update: Update, args: list[str], deps,
                   *, from_menu: bool = False):
    """Paket beli MULTI-emiten dari snapshot /scan: /paket [1-5|refresh]."""
    rm = deps.home_markup() if from_menu else None
    rows = await asyncio.to_thread(store.load_market_scan)
    meta = await asyncio.to_thread(store.market_scan_meta)
    if not rows:
        await update.effective_message.reply_text(
            "🎯 Belum ada snapshot pasar.\n\n"
            "Jalankan <code>/scan</code> dulu (±2–3 menit). Setelah itu "
            "<code>/paket</code> menyusun paket beli siap Stockbit.\n\n"
            "Untuk satu emiten tanpa menunggu scan: "
            "<code>/rencana BBCA 5000000</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=rm)
        return

    if not await deps.cooldown_ok(update, "rencana"):
        return

    reg = await asyncio.to_thread(getattr(deps, "regime", regime).fetch_ihsg_regime)

    top = rencana.MAX_PLANS
    force = False
    for a in args:
        al = a.lower()
        if al in ("refresh", "ulang", "paksa"):
            force = True
        elif al.isdigit():
            top = max(1, min(5, int(al)))

    key = "plan:v1:" + str(meta.get("scanned_at") or meta.get("trade_date") or "")
    payload = None
    if not force:
        payload = await asyncio.to_thread(store.load_plan_cache, key)
    if payload is None:
        cands, pre_rejected = rencana.prefilter(rows)
        if not cands:
            await deps.reply_long(update.effective_message, _rencana_text(
                {"picks": [], "rejected": {},
                 "regime": reg,
                 "modal": await asyncio.to_thread(
                     store.get_modal, update.effective_chat.id)},
                {"rejected": dict(pre_rejected)}, meta, deps),
                reply_markup=rm)
            return
        await update.effective_message.reply_text(
            f"⏳ Menyusun rencana dari <b>{len(cands)} kandidat</b> teratas "
            "(fundamental + berita). Sekali per scan, ±20–60 detik…",
            parse_mode=ParseMode.HTML)
        payload = await deps._build_plan_payload(rows, meta)
        if not payload.get("enriched"):
            await update.effective_message.reply_text(
                "❌ Gagal mengambil data fundamental/berita untuk kandidat. "
                "Coba lagi nanti atau <code>/paket refresh</code>.",
                parse_mode=ParseMode.HTML,
                reply_markup=rm)
            return
        await asyncio.to_thread(store.save_plan_cache, key, payload,
                                meta.get("trade_date", ""))

    chat_id = update.effective_chat.id
    modal = await asyncio.to_thread(store.get_modal, chat_id)
    positions = await asyncio.to_thread(store.real_positions, chat_id)
    max_pos, max_sec = await asyncio.to_thread(riskmod.get_risk_limits, chat_id)
    # Pertahankan default paket 20% bila batas risiko belum diubah user,
    # namun hormati batas kustom (/portofolio batas) bila sudah diatur.
    eff_pos = max_pos if (max_pos, max_sec) != (riskmod.MAX_POS_PCT, riskmod.MAX_SECTOR_PCT) else None
    result = await asyncio.to_thread(
        rencana.finalize,
        payload.get("enriched") or [],
        modal,
        top,
        market_regime=reg,
        chat_id=chat_id,
        positions=positions,
        max_pos_pct=eff_pos,
        max_sector_pct=max_sec,
    )
    await deps.reply_long(update.effective_message,
                     _rencana_text(result, payload, meta, deps),
                     reply_markup=rm)


_RENCANA_USAGE = (
    "🎯 <b>Rencana beli satu emiten</b>\n\n"
    "<code>/rencana BBCA 5000000</code> — kode + anggaran (rupiah)\n"
    "<code>/rencana BBCA</code> — pakai modal tersimpan "
    "(<code>/modal</code>)\n\n"
    "Anggaran boleh ditulis <code>5000000</code>, <code>5.000.000</code>, "
    "atau <code>5jt</code>.\n"
    "Paket beberapa emiten sekaligus: <code>/paket</code> (perlu /scan dulu)."
)


_parse_rupiah = parse_rupiah


def _snapshot_row(ticker: str) -> dict:
    """Baris snapshot /scan terakhir untuk satu ticker (kosong bila belum ada).

    Fungsi ini SINKRON dan harus dipanggil lewat `asyncio.to_thread` —
    pemanggilnya di `do_rencana` sudah begitu.
    """
    try:
        for r in store.load_market_scan():
            if (r.get("ticker") or "").upper() == ticker:
                return r
    except Exception:
        log.exception("baca snapshot untuk %s gagal", ticker)
    return {}


def _rencana_single_text(plan: dict, c: dict, *, budget_from_modal: bool,
                         deps) -> str:
    """Kartu rencana satu emiten: lot, biaya, % modal, rugi maks, RR."""
    t = esc(plan["ticker"])
    bits = [esc(x) for x in (c.get("name"), c.get("sector"), c.get("board"))
            if x]
    head = f"🎯 <b>RENCANA BELI — {t}</b>"
    sub = " · ".join(bits + [f"grade {esc(plan.get('grade') or '—')}"])
    out = [head, f"<i>{sub}</i>"]
    as_of = c.get("as_of") or plan.get("as_of")
    stale_flag = bool(c.get("stale") or plan.get("stale"))
    if as_of or stale_flag:
        date_bit = f"Data per {esc(str(as_of))} WIB" if as_of else "Data harga"
        stale_bit = " · ⚠️ cache lama" if stale_flag else ""
        out.append(f"🔎 <i>{date_bit}{stale_bit}</i>")
    if c.get("quality_note"):
        out.append(f"⚠️ <i>{esc(str(c['quality_note']))}</i>")
    reg = plan.get("regime")
    if isinstance(reg, dict) and reg.get("status") == "DEFENSIVE":
        out.append(reports.regime_banner(reg))
    out.append("")
    out += [
        f"Entry ≤ <b>{deps._money(plan['entry'])}</b> · SL "
        f"<b>{deps._money(plan['sl'])}</b> · TP <b>{deps._money(plan['tp'])}</b> "
        f"(net {deps._plan_pct(plan.get('net_tp_pct'))}) · RR 1:{plan['rr']:g}",
        f"Jangan kejar di atas <b>{deps._money(plan['chase_above'])}</b>",
        "",
    ]
    if plan.get("vetoes"):
        out += [
            "⛔ <b>Tidak lolos saringan rencana:</b> "
            + esc(", ".join(plan["vetoes"])),
            "<i>Angka di bawah hanya hitungan uang, bukan rekomendasi. "
            "Pakai dengan sadar risikonya.</i>",
            "",
        ]
    if not plan.get("ok"):
        out += [
            f"💵 Anggaran <b>{deps._money(plan['budget'])}</b> belum cukup untuk "
            f"1 lot: harga 1 lot {deps._money(plan['lot_value'])} + fee ≈ "
            f"<b>{deps._money(plan['min_budget'])}</b>.",
            "Tambah anggaran atau pilih emiten lain — tidak ada jumlah lot "
            "yang saya karang.",
        ]
    else:
        raw_pos_cap = float(plan.get("max_pos_pct") or riskmod.MAX_POS_PCT)
        pos_cap_pct = raw_pos_cap * 100.0 if raw_pos_cap <= 1.0 else raw_pos_cap
        pos_cap_txt = f"{pos_cap_pct:g}".replace(".", ",")
        src = (f"Modal tersimpan (dibatasi {pos_cap_txt}%/posisi)"
               if budget_from_modal else "Anggaran")
        shares = f"{plan['shares']:,}".replace(",", ".")
        out += [
            f"💵 {src} <b>{deps._money(plan['budget'])}</b> → "
            f"<b>{plan['lots']} lot</b> ({shares} lembar)",
            f"Nilai saham {deps._money(plan['value'])} + fee beli 0,15% = "
            f"<b>{deps._money(plan['buy_cost'])}</b>"
            + (f" · <b>{deps._plan_pct(plan['pct_modal'], sign=False)}</b> dari modal "
               f"{deps._money(plan['modal'])}" if plan.get("pct_modal") else ""),
            f"Sisa anggaran <b>{deps._money(plan['cash_left'])}</b>",
            f"Kalau kena SL: rugi <b>{deps._money(plan['max_loss'])}</b>"
            + (f" ({deps._plan_pct(plan['max_loss_pct'], sign=False)} modal, sudah "
               "termasuk fee jual)"
               if plan.get("max_loss_pct") is not None
               else " (sudah termasuk fee jual)"),
            f"Kalau kena TP: untung bersih <b>{deps._money(plan['profit_tp'])}</b>",
        ]
        if plan.get("modal") and plan.get("lots_risk") is not None:
            raw_eff_r = plan.get("risk_pct_limit") or plan.get("effective_risk_pct") or 1.0
            eff_r = float(raw_eff_r) * 100.0 if float(raw_eff_r) <= 0.1 else float(raw_eff_r)
            eff_r_txt = f"{eff_r:g}".replace(".", ",")
            out.append(
                f"Aturan risiko {eff_r_txt}% modal: maks <b>{plan['lots_risk']} lot</b> "
                f"(risiko {deps._money(plan['risk_budget'])}).")
    warns = list(plan.get("warnings") or [])[:4]
    if warns:
        out.append("")
        out += ["⚠️ " + esc(w.replace(".", ",")) for w in warns]
    out += [
        "",
        "📱 <b>Pasang di Stockbit</b>: cari kode → <b>Limit Order</b> (bukan "
        "Market) → lot di atas, harga ≤ entry → pasang alert di SL & TP.",
        f"Ubah anggaran: <code>/rencana {t} 10000000</code> · paket "
        "multi-emiten: <code>/paket</code>",
        "",
        "<i>Entry = harga beli maksimum · SL = batas rugi (jual di sini) · "
        "TP = target untung · RR = untung:rugi · lot = 100 lembar.</i>",
        "",
        deps._SINYAL_DISCLAIMER,
    ]
    return "\n".join(out)


async def do_rencana(update: Update, args: list[str], deps,
                     *, from_menu: bool = False):
    """Rencana beli SATU emiten: /rencana <KODE> [anggaran]."""
    msg = update.effective_message
    chat_id = update.effective_chat.id
    rm = deps.home_markup() if from_menu else None
    if not args:
        await deps.reply_long(msg, _RENCANA_USAGE, reply_markup=rm)
        return
    head = args[0].upper()
    head = head[:-3] if head.endswith(".JK") else head
    if head in ("PAKET", "REFRESH", "ULANG") or head.isdigit():
        await msg.reply_text(
            "ℹ️ <code>/rencana</code> sekarang untuk <b>satu emiten</b>: "
            "<code>/rencana BBCA 5000000</code>.\n"
            "Paket beberapa emiten sekaligus ada di <code>/paket</code> "
            "(butuh <code>/scan</code> dulu).",
            parse_mode=ParseMode.HTML,
            reply_markup=rm)
        return
    if not re.fullmatch(r"[A-Z]{2,6}", head):
        await deps.reply_long(msg, "Kode emiten tidak valid.\n\n" + _RENCANA_USAGE,
                              reply_markup=rm)
        return

    budget: float | None = None
    if len(args) > 1:
        budget = _parse_rupiah(args[1])
        if budget is None:
            await deps.reply_long(
                msg,
                f"Anggaran <code>{esc(args[1])}</code> tidak saya mengerti.\n\n"
                + _RENCANA_USAGE,
                reply_markup=rm)
            return

    modal = await asyncio.to_thread(store.get_modal, chat_id)
    max_pos, max_sec = await asyncio.to_thread(riskmod.get_risk_limits, chat_id)
    from_modal = budget is None
    if budget is None:
        # Tanpa anggaran eksplisit, jangan otomatis memakai seluruh modal:
        # batas satu posisi (default 25% atau batas kustom) tetap berlaku (P23).
        budget = float(modal or 0) * max_pos
    if budget <= 0:
        await deps.reply_long(
            msg,
            "Anggaran belum ada. Tulis nominalnya, mis. "
            "<code>/rencana BBCA 5000000</code>, atau set modal: "
            "<code>/modal 10000000</code>.\n\n" + _RENCANA_USAGE,
            reply_markup=rm)
        return

    # Cooldown setelah validasi: salah ketik tidak boleh memakan jatah.
    if not await deps.cooldown_ok(update, "rencana"):
        return

    if not from_menu:
        await msg.reply_text(f"⏳ Menghitung rencana {head} (±5–15 detik)…")
    try:
        analyzed = await deps.gather_candidate(head)
    except Exception:
        log.exception("gagal ambil data %s untuk /rencana", head)
        analyzed = None
    if not analyzed:
        await msg.reply_text(
            f"❌ Data harga {esc(head)} tidak tersedia (delisted, suspensi, "
            "atau sumber sedang down). Coba lagi nanti — tidak ada angka "
            "yang saya karang.",
            parse_mode=ParseMode.HTML,
            reply_markup=rm)
        return

    row = await asyncio.to_thread(_snapshot_row, head)
    res_a = analyzed.get("res") or {}
    ind_a = res_a.get("ind")
    prev_cl = None
    try:
        if ind_a is not None and len(ind_a) >= 2 and "close" in ind_a:
            prev_cl = float(ind_a["close"].iloc[-2])
    except Exception:
        prev_cl = None
    cand = {
        "ticker": head,
        "rel_pct": row.get("rel_pct"),
        "tech_score": row.get("tech_score"),
        "sector": row.get("sector"),
        "board": row.get("board"),
        "price": res_a.get("snap", {}).get("close"),
        "prev_close": prev_cl,
    }
    enriched = rencana.enrich(cand, analyzed,
                              require_rel=row.get("rel_pct") is not None)
    if not enriched:
        await msg.reply_text(
            f"❌ Data teknikal {esc(head)} tidak cukup untuk menyusun level "
            "(riwayat harga terlalu pendek).",
            parse_mode=ParseMode.HTML,
            reply_markup=rm)
        return
    if prev_cl is not None:
        enriched["prev_close"] = prev_cl
    enriched["as_of"] = res_a.get("as_of") or row.get("trade_date")
    enriched["stale"] = bool(res_a.get("stale") or res_a.get("stale_note"))
    enriched["quality_note"] = res_a.get("quality_note") or ""

    reg = await _fetch_regime_safe(deps)
    positions = await asyncio.to_thread(store.real_positions, chat_id)
    plan = await asyncio.to_thread(
        rencana.budget_plan,
        enriched,
        budget,
        modal,
        market_regime=reg,
    )
    plan["max_pos_pct"] = max_pos
    plan["max_sector_pct"] = max_sec
    if plan.get("ok") and modal:
        limits = await asyncio.to_thread(
            riskmod.check_limits, plan, positions, modal,
            max_pos_pct=max_pos, max_sector_pct=max_sec)
        if not limits["ok"]:
            lines = [f"⛔ <b>Rencana {esc(head)} ditolak — batas portofolio</b>",
                     ""]
            lines += ["• " + esc(r) for r in limits["reasons"]]
            if limits["max_lots"] > 0:
                lines.append(f"Lot maksimum yang masih lolos: "
                             f"<b>{limits['max_lots']} lot</b>.")
            else:
                lines.append("Tidak ada ruang untuk menambah posisi ini — "
                             "kurangi posisi lain atau tambah modal.")
            lines += [
                "",
                f"Batas: <b>{max_pos * 100:.0f}%</b> modal per "
                f"posisi, <b>{max_sec * 100:.0f}%</b> per sektor. "
                "Posisi lama dinilai di harga rata-rata.",
                "Ubah anggaran: <code>/rencana %s 3000000</code> · "
                "batas: <code>/portofolio batas</code>" % esc(head),
                "",
                deps._SINYAL_DISCLAIMER,
            ]
            await deps.reply_long(msg, "\n".join(lines))
            return
    today_iso = marketcal.now_wib().date().isoformat()
    if plan.get("entry") and float(plan["entry"]) > 0:
        await asyncio.to_thread(
            store.save_signal_context,
            chat_id, head, "rencana", float(plan["entry"]),
            plan.get("sl"), plan.get("tp"),
            plan.get("lots") or None, today_iso,
        )
    kb = jurnal_handler.confirm_exec_markup(
        "rencana", head, deps, from_menu=from_menu,
    )
    await deps.reply_long(
        msg,
        _rencana_single_text(plan, enriched, budget_from_modal=from_modal,
                             deps=deps),
        reply_markup=kb,
    )


async def run_scan_job(ctx: ContextTypes.DEFAULT_TYPE, deps):
    chat_id = int(ctx.job.data["chat_id"])
    try:
        res = await scan_with_guard(True, deps, owns_flag=True)
        kval = {}
        if res is not None:
            top = (await asyncio.to_thread(store.load_market_scan,
                                           "BELI"))[:5]
            kval = await deps._kualitas_map([r["ticker"] for r in top])
        txt = (await deps._scan_summary_text(res, "✅ <b>Scan pasar selesai</b>", kval)
               if res is not None
               else "⏳ Scan lain sedang berjalan — permintaan ini dilewati. "
                    "Coba /scan lagi setelah yang sekarang selesai.")
    except Exception as exc:
        log.exception("scan pasar gagal")
        txt = (f"❌ Scan gagal: <code>{esc(str(exc)[:300])}</code>\n"
               "Coba lagi nanti. Kalau berulang, cek log VPS.")
    for part in split_message(txt):
        try:
            await deps.send_with_retry(
                lambda p=part: ctx.bot.send_message(
                    chat_id, p, parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True),
                what="kirim hasil scan")
        except Exception:
            log.exception("gagal mengirim hasil scan ke %s", chat_id)


async def scan_with_guard(force: bool, deps, *,
                          owns_flag: bool = False) -> dict | None:
    """Jalankan screener seluruh pasar dengan penjaga tunggal.

    Penjaga `deps._screener_running` dipakai bersama oleh `/scan` manual dan job
    harian 16:15 supaya keduanya tidak pernah mengunduh ±944 emiten sekaligus.
    `owns_flag=True` dipakai pemanggil yang sudah menyetel penjaga lebih dulu
    (mis. `_do_scan` yang menyetelnya sebelum menjadwalkan job).

    Return None bila scan lain sedang berjalan — pemanggil melaporkannya,
    bukan menumpuk pekerjaan.
    """
    if not owns_flag:
        if deps._screener_running:
            return None
        deps._screener_running = True
    elif not deps._screener_running:
        # pemanggil mengaku sudah memegang penjaga, tetapi penjaganya lepas:
        # lebih aman melewati scan daripada berjalan bersamaan tanpa sadar.
        return None
    try:
        return await asyncio.to_thread(screener.scan, force)
    finally:
        deps._screener_running = False


async def do_scan(update: Update, args: list[str], job_queue, deps,
                  *, from_menu: bool = False):
    rm = deps.home_markup() if from_menu else None
    force = any(a.lower() in ("paksa", "force", "ulang", "refresh")
                for a in args)
    meta = await asyncio.to_thread(store.market_scan_meta)
    today = marketcal.now_wib().date().isoformat()
    if not force and (meta.get("scanned_at") or "")[:10] == today:
        counts = await asyncio.to_thread(store.market_scan_counts)
        await deps.reply_long(update.effective_message,
                         await deps._scan_summary_text({"cached": True,
                                             "meta": meta,
                                             "counts": counts},
                                            "📡 <b>Screener pasar</b>"),
                         reply_markup=rm)
        return
    if deps._screener_running:
        await update.effective_message.reply_text(
            "⏳ Scan pasar masih berjalan. Tunggu sampai selesai, ya.",
            reply_markup=rm)
        return
    # Cooldown baru setelah snapshot/status dicek: meminta data yang sudah ada
    # atau kena "sedang berjalan" tidak boleh memakan jatah cooldown.
    if not await deps.cooldown_ok(update, "scan"):
        return
    deps._screener_running = True
    try:
        n = len(universe.codes())
        await update.effective_message.reply_text(
            f"⏳ Memindai <b>{n} emiten</b> BEI secara borongan (3–6 menit).\n"
            "Hasil dikirim otomatis ke chat ini. Kamu bisa lanjut pakai perintah lain.",
            parse_mode=ParseMode.HTML,
            reply_markup=rm)
        job_queue.run_once(deps._run_scan_job, when=0.5,
                           data={"chat_id": update.effective_chat.id},
                           name=f"scan-{update.effective_chat.id}")
    except Exception:
        # penjaga jangan tertinggal "true" kalau penjadwalan gagal
        deps._screener_running = False
        raise


async def do_bandar(update: Update, deps,
                    ticker: str | None = None,
                    *, from_menu: bool = False):
    if not await deps.cooldown_ok(update, "bandar"):
        return
    msg = update.effective_message
    await update.effective_chat.send_action(ChatAction.TYPING)

    def _job():
        idx = bandar.get_index_summary()
        limit = 1000 if ticker else 200
        rows = bandar.get_stock_summary(
            bandar.last_trading_date(), max_stocks=limit,
            until=ticker.upper() if ticker else None)
        return idx, rows

    try:
        idx, rows = await asyncio.to_thread(_job)
    except bandar.IDXTidakTersedia as e:
        await msg.reply_text(
            "🔒 <b>Data BEI (idx.co.id) kini diblokir Cloudflare</b> "
            "(IP server diperlakukan kurang manis). Coba lagi nanti atau dari server lain.\n"
            "<i>Alternatif cepat: /analisis TICKER (pakai Yahoo Finance, delayed 15 menit).</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=deps.home_markup(),
        )
        log.warning("BEI blocked: %s", e)
        return
    except Exception:
        log.exception("bandar gagal")
        await msg.reply_text(
            "❌ Data BEI tidak tersedia saat ini (sumber/paket bermasalah). "
            "Alternatif: <code>/analisis TICKER</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=deps.home_markup(),
        )
        return

    comp = next((x for x in idx if x["IndexCode"] == "COMPOSITE"), None)
    lines = []
    if comp:
        def _num(k):
            v = comp.get(k)
            return f"{v:,.2f}" if isinstance(v, (int, float)) else "—"

        lines.append(f"🇮🇩 <b>IHSG</b> (BEI resmi, {esc(str(comp.get('Date', ''))[:10])})")
        for key_lbl, key in [("Penutupan", "IndexClose"), ("Buka", "IndexOpen"),
                             ("Tinggi", "IndexHigh"), ("Rendah", "IndexLow"),
                             ("Perubahan", "IndexChange")]:
            lines.append(f"  {key_lbl}: {_num(key)}")
    if ticker:
        row = bandar.find_row(rows, ticker)
        if row:
            pct = bandar.to_pct(row)
            lines.append(
                f"\n📊 <b>{esc(row['StockCode'])}</b> — {esc(row.get('StockName', ''))}"
            )
            for lbl, key in [("Buka", "OpenPrice"), ("Tertinggi", "HighestPrice"),
                             ("Terendah", "LowestPrice"), ("Tutup", "ClosingPrice"),
                             ("Volume", "Volume"), ("Nilai", "Value")]:
                v = row.get(key)
                lines.append(
                    f"  {lbl}: {v:,.0f}" if isinstance(v, (int, float)) else f"  {lbl}: —"
                )
            lines.append(f"  Perubahan: {pct:+.2f}%")
            fg = bandar.foreign_fields(row)
            if fg:
                lines.append(f"  Asing: {esc(fg)}")
        else:
            lines.append(
                f"\n❓ {esc(ticker.upper())} tidak ditemukan di ringkasan "
                "(mungkin tidak ada transaksi hari itu)"
            )
    else:
        up = bandar.top_movers(rows, n=4)
        dn = bandar.top_movers(rows, n=4, asc=True)

        def _mv(rs):
            return "\n".join(
                f"  <b>{esc(r['StockCode'])}</b> {bandar.to_pct(r):+.1f}% "
                f"vol {r.get('Volume', 0):,.0f}"
                for r in rs
            )

        lines.append("\n🔥 Top gainer:\n" + _mv(up))
        lines.append("\n💀 Top loser:\n" + _mv(dn))
    lines.append("\n⚠️ Data BEI resmi (bukan saran investasi)")
    await deps.reply_long(
        msg,
        "\n".join(lines),
        disable_web_page_preview=True,
        reply_markup=deps.home_markup(),
    )


async def cmd_bandar(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    """Data resmi BEI: IHSG & sektor, plus info emiten (aliran asing bila tersedia)."""
    args = deps.tokens(update)
    await do_bandar(update, deps, ticker=args[0] if args else None)
