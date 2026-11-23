"""Alur panduan interaktif beli/jual & onboarding pemula Tahap 1–5."""
from __future__ import annotations

import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config
from core import kualitas as kvalmod, marketcal, saran as saranmod, store
from handlers.menu import POPULAR_TICKERS, _clean_cb_ticker, ticker_pick_markup
from core.reports import esc

_MODAL_PRESETS = (500_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000, 25_000_000)

def _guide_start_view() -> tuple[str, InlineKeyboardMarkup]:
    """Langkah awal panduan interaktif untuk pemula."""
    text = (
        "🧭 <b>Panduan Pemula — Mulai dari Mana?</b>\n\n"
        "Pilih situasi kamu sekarang, atau ikuti <b>Panduan Bertahap (Tahap 1→5)</b> "
        "dari pilih tujuan sampai hitung lot:\n\n"
        "1️⃣ <b>Cari saham cocok dibeli</b> — belum punya incaran, mau lihat kandidat dari data pasar\n"
        "2️⃣ <b>Cek nilai 1 saham</b> — sudah punya nama saham, mau cek mahal/murah &amp; layak beli\n"
        "3️⃣ <b>Cek saham untuk dijual</b> — sudah pegang saham, bingung tahan atau jual\n"
        "4️⃣ <b>Panduan 3 langkah</b> — ringkasan singkat alur bot"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Mulai Panduan Bertahap (Tahap 1→5)", callback_data="onboard:s1")],
        [InlineKeyboardButton("1️⃣ Cari Saham Cocok Dibeli", callback_data="guide:beli")],
        [InlineKeyboardButton("2️⃣ Cek Nilai 1 Saham", callback_data="guide:cek")],
        [InlineKeyboardButton("3️⃣ Cek Saham Cocok Dijual", callback_data="guide:jual")],
        [InlineKeyboardButton("4️⃣ Panduan 3 Langkah Pemula", callback_data="guide:pemula")],
        [InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
    ])
    return text, markup


def _evaluate_beli_candidates(
    rows: list[dict],
    modal: int,
    *,
    cache_dir: str | None = None,
    max_pos_pct: float = 0.25,
) -> tuple[list[dict], bool]:
    """Urutkan sinyal BELI: cache kualitas sehat dulu, lalu kecukupan 1 lot & rel_pct."""
    eff_dir = cache_dir if cache_dir is not None else config.TMP_DIR
    day = marketcal.now_wib().date().isoformat()
    seen: set[str] = set()
    cands: list[dict] = []
    has_cache = False
    for r in rows:
        if r.get("signal") != "BELI" or not r.get("ticker"):
            continue
        ticker = _clean_cb_ticker(str(r.get("ticker", "")))
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        raw_k = kvalmod.load_cached(eff_dir, ticker, day)
        judged = kvalmod.judge(raw_k) if raw_k is not None else None
        ok_val = judged.get("ok") if judged else None
        if judged is not None:
            has_cache = True
        q_tier = 0 if ok_val is True else (1 if ok_val is None else 2)
        price = float(r.get("price") or r.get("close") or 0)
        lot_cost = price * 100.0 * (1.0 + saranmod.BUY_FEE) if price > 0 else None
        afford_tier = (
            0 if (lot_cost is not None and lot_cost <= modal)
            else (1 if lot_cost is None else 2)
        )
        cands.append({
            "ticker": ticker,
            "rel_pct": float(r.get("rel_pct") or 0),
            "price": price,
            "lot_cost": lot_cost,
            "ok": ok_val,
            "q_tier": q_tier,
            "afford_tier": afford_tier,
            "judged": judged,
            "max_pos_pct": max_pos_pct,
        })
    cands.sort(key=lambda c: (c["q_tier"], c["afford_tier"], -c["rel_pct"], c["ticker"]))
    return cands, has_cache


def _format_beli_candidate_line(c: dict, modal: int, max_pos_pct: float = 0.25) -> str:
    """Bangun satu baris ringkas kandidat BELI berisi kualitas & biaya 1 lot vs modal."""
    t = c["ticker"]
    judged = c.get("judged")
    ok_val = c.get("ok")
    if ok_val is True:
        q_lbl = "✅ Sehat"
    elif ok_val is False:
        q_lbl = f"⚠️ {judged.get('label') or 'Tidak lolos'}" if judged else "⚠️ Tidak lolos"
    else:
        q_lbl = "❔ Belum cek kualitas"
    bits = (judged or {}).get("bits") or []
    extra_bit = f" ({esc(str(bits[0]))})" if bits else ""
    lot_cost = c.get("lot_cost")
    slot_cap = float(modal) * float(max_pos_pct)
    if lot_cost is None:
        lot_lbl = "biaya 1 lot tidak tersedia"
    elif lot_cost <= slot_cap:
        lot_lbl = f"1 lot ~{saranmod.fmt(lot_cost)} ✅ pas slot {int(round(max_pos_pct * 100))}%"
    elif lot_cost <= modal:
        lot_lbl = f"1 lot ~{saranmod.fmt(lot_cost)} ⚠️ muat modal (&gt;slot {int(round(max_pos_pct * 100))}%)"
    else:
        lot_lbl = f"1 lot ~{saranmod.fmt(lot_cost)} ⛔ &gt; modal!"
    return f"• <b>{esc(t)}</b> — {q_lbl}{extra_bit} · {lot_lbl}"


def _guide_beli_view(
    rows: list[dict],
    meta: dict,
    modal: int,
    *,
    cache_dir: str | None = None,
    max_pos_pct: float = 0.25,
) -> tuple[str, InlineKeyboardMarkup]:
    """Alur terpandu mencari kandidat beli tanpa memanggil jaringan."""
    if not rows:
        text = (
            "🟢 <b>Cari Saham Cocok Dibeli</b>\n\n"
            "Saat ini <b>belum ada hasil scan seluruh pasar</b> tersimpan di server.\n\n"
            "Kamu tetap bisa mencari kandidat tanpa menunggu lama:\n"
            "• <b>Ide Harian (Cepat)</b>: langsung cari dari watchlist/LQ45 + hitung lot\n"
            "• <b>Scan 944 Saham</b>: pindai semua saham BEI (±3–6 menit di latar belakang)\n"
            "• <b>Cek Saham Populer</b>: langsung cek BBCA, BBRI, TLKM, dll.\n\n"
            f"Modal tersimpan: <b>{saranmod.fmt(modal)}</b> "
            f"(slot 25%: {saranmod.fmt(modal * max_pos_pct)})."
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("💡 Ide Harian (Cepat)", callback_data="run:saran"),
             InlineKeyboardButton("📡 Scan 944 Saham", callback_data="sg:scan")],
            [InlineKeyboardButton("🔍 Cek Saham Populer", callback_data="guide:cek"),
             InlineKeyboardButton("💵 Atur Modal", callback_data="sub:modal")],
            [InlineKeyboardButton("⬅️ Kembali ke Panduan", callback_data="guide:start"),
             InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
        ])
        return text, markup

    cands, has_cache = _evaluate_beli_candidates(
        rows, modal, cache_dir=cache_dir, max_pos_pct=max_pos_pct
    )
    eligible = [c for c in cands if c["q_tier"] < 2]
    top_cands = eligible[:3]
    top_tickers = [c["ticker"] for c in top_cands]
    scanned = str(meta.get("scanned_at") or "").strip()[:16]
    scan_note = f" (scan: <code>{esc(scanned)}</code>)" if scanned else ""
    if top_cands:
        cand_lines = "\n".join(
            _format_beli_candidate_line(c, modal, max_pos_pct) for c in top_cands
        )
        source_note = (
            "<i>Prioritas dari cache kualitas hari ini &amp; kecukupan 1 lot:</i>"
            if has_cache
            else "<i>Cache kualitas hari ini belum ada — diurutkan berdasar skor relatif &amp; cek biaya 1 lot:</i>"
        )
        cand_block = f"\n\n{source_note}\n{cand_lines}"
    else:
        cand_block = (
            "\n\n⚠️ <i>Semua kandidat BELI pada cache kualitas hari ini belum lolos "
            "saringan fundamental. Tekan Pilihan BELI Berkualitas untuk verifikasi terbaru.</i>"
        )
    text = (
        "🟢 <b>Cari Saham Cocok Dibeli</b>\n\n"
        f"Data scan terakhir memuat <b>{len(rows)}</b> emiten dengan "
        f"<b>{len(cands)}</b> sinyal BELI{scan_note}.\n\n"
        "⚠️ <i>Sinyal BELI = peringkat skor relatif riset, bukan jaminan untung. "
        "Selalu cek 6 poin keamanannya dulu sebelum beli.</i>"
        f"{cand_block}\n\n"
        "• <b>Pilihan BELI Berkualitas</b>: saring laba positif &amp; valuasi wajar\n"
        "• <b>Cek cepat kandidat teratas</b>: ketuk tombol saham di bawah\n"
        f"• Modal: <b>{saranmod.fmt(modal)}</b> (slot 25%: {saranmod.fmt(modal * max_pos_pct)})"
    )
    kb: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("✨ Pilihan BELI Berkualitas", callback_data="run:sinyal_bagus"),
         InlineKeyboardButton("💡 Ide Harian + Hitung Lot", callback_data="run:saran")],
    ]
    if top_tickers:
        kb.append([
            InlineKeyboardButton(f"🔍 Cek {t}", callback_data=f"tick:cek:{t}")
            for t in top_tickers
        ])
    kb.extend([
        [InlineKeyboardButton("📦 Paket Multi-Saham", callback_data="run:paket"),
         InlineKeyboardButton("📡 Ringkasan Scan", callback_data="run:sinyal")],
        [InlineKeyboardButton("💵 Atur Modal", callback_data="sub:modal"),
         InlineKeyboardButton("🧭 Panduan Tahap 1→5", callback_data="onboard:s1")],
        [InlineKeyboardButton("⬅️ Kembali ke Panduan", callback_data="guide:start"),
         InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
    ])
    return text, InlineKeyboardMarkup(kb)


def _guide_jual_view(
    positions: list[dict], wl: list[str], rows: list[dict]
) -> tuple[str, InlineKeyboardMarkup]:
    """Alur terpandu meninjau saham yang layak ditahan atau dijual."""
    seen: set[str] = set()
    held: list[str] = []
    for p in positions:
        if int(p.get("lots") or 0) <= 0 or not p.get("ticker"):
            continue
        t = _clean_cb_ticker(str(p["ticker"]))
        if t and t not in seen:
            seen.add(t)
            held.append(t)
        if len(held) >= 4:
            break
    has_sell = any(r.get("signal") == "JUAL" for r in rows)
    extra = [t for t in dict.fromkeys([*wl, *POPULAR_TICKERS]) if t not in held][:4]

    if held:
        held_note = (
            "Kamu punya posisi aktif tersimpan (lot &gt; 0): <b>"
            + ", ".join(esc(t) for t in held)
            + "</b>.\nKetuk salah satu di bawah untuk cek apakah sebaiknya "
            "ditahan, ambil untung sebagian, atau cut loss:"
        )
    else:
        held_note = (
            "Belum ada posisi aktif (lot &gt; 0) di portofoliomu. Pilih saham di "
            "bawah atau ketik kode saham yang kamu pegang di Stockbit "
            "(mis. <code>TLKM jual gak?</code>):"
        )

    text = (
        "🔴 <b>Cek Saham untuk Dijual</b>\n\n"
        f"{held_note}\n\n"
        "ℹ️ <i>Catatan: sinyal JUAL hanya panduan untuk pemegang saham "
        "(ambil untung / batasi rugi), bukan instruksi short selling.</i>"
    )
    kb: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(held), 2):
        kb.append([
            InlineKeyboardButton(f"💼 Cek Jual {t}", callback_data=f"tick:jual:{t}")
            for t in held[i:i + 2]
        ])
    for i in range(0, len(extra), 2):
        kb.append([
            InlineKeyboardButton(f"🔍 Cek {t}", callback_data=f"tick:jual:{t}")
            for t in extra[i:i + 2]
        ])
    kb.append([
        InlineKeyboardButton("✏️ Ketik kode lain", callback_data="manual:jual"),
        InlineKeyboardButton("💼 Portofolio Saya", callback_data="run:portofolio"),
    ])
    if has_sell:
        kb.append([
            InlineKeyboardButton("🔴 Daftar Sinyal JUAL Pasar", callback_data="sg:sell")
        ])
    kb.append([
        InlineKeyboardButton("⬅️ Kembali ke Panduan", callback_data="guide:start"),
        InlineKeyboardButton("🏠 Menu Utama", callback_data="menu"),
    ])
    return text, InlineKeyboardMarkup(kb)


def _guide_pemula_view() -> tuple[str, InlineKeyboardMarkup]:
    """Ringkasan 3 langkah praktis menggunakan bot untuk pemula."""
    text = (
        "🎓 <b>Panduan 3 Langkah untuk Pemula</b>\n\n"
        "<b>Langkah 1 — Cari kandidat dulu</b>\n"
        "Ketuk <b>🟢 Saham Cocok Dibeli</b> untuk melihat saham berdasar skor riset "
        "(bukan tebakan, dan tidak ada jaminan untung).\n\n"
        "<b>Langkah 2 — Cek nilai &amp; keamanannya</b>\n"
        "Buka <b>🔍 Cek Nilai Saham</b> lalu pilih/ketik kode saham (mis. BBCA). "
        "Bot cek valuasi mahal/murah, kas, tren &amp; memberi status "
        "<b>LAYAK / TUNGGU / HINDARI</b>.\n\n"
        "<b>Langkah 3 — Hitung lot &amp; pantau jual</b>\n"
        "Jika layak, tekan <b>🎯 Hitung Lot</b> sesuai modalmu. Setelah transaksi manual "
        "di Stockbit, catat di <b>💼 Portofolio Saya</b> lalu gunakan "
        "<b>🔴 Cek Saham Jual</b> saat ragu.\n\n"
        "💡 <i>Bisa juga chat langsung, mis. <code>BBCA beli gak?</code> atau "
        "<code>BBRI fundamentalnya bagus?</code></i>"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Ikuti Panduan Bertahap (Tahap 1→5)", callback_data="onboard:s1")],
        [InlineKeyboardButton("🟢 Cari Saham Cocok Dibeli", callback_data="guide:beli"),
         InlineKeyboardButton("🛡️ Coba Cek BBCA", callback_data="tick:cek:BBCA")],
        [InlineKeyboardButton("🎓 Kamus Istilah Pemula", callback_data="run:belajar"),
         InlineKeyboardButton("⬅️ Panduan", callback_data="guide:start")],
        [InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
    ])
    return text, markup


def _load_guide_beli_data(chat_id: int) -> tuple[list[dict], dict, int, float]:
    """Muat snapshot scan, modal & batas posisi dalam satu panggilan store sekuensial."""
    max_pos, _ = store.get_risk_limits(chat_id)
    return (
        store.load_market_scan(),
        store.market_scan_meta(),
        store.get_modal(chat_id),
        max_pos,
    )


def _load_guide_jual_data(chat_id: int) -> tuple[list[dict], list[str], list[dict]]:
    """Muat posisi, watchlist & snapshot scan dalam satu panggilan store."""
    return (
        store.real_positions(chat_id),
        store.get_watchlist(chat_id),
        store.load_market_scan(),
    )


_ONBOARD_GOALS = {
    "beli": "🟢 Cari & Beli Saham",
    "cek": "🔍 Cek Nilai 1 Saham",
    "jual": "🔴 Tinjau Saham untuk Dijual",
}


def _resolve_onboard_goal(raw: str | None) -> str:
    return raw if raw in _ONBOARD_GOALS else "beli"


def _onboard_load_state_sync(chat_id: int) -> dict:
    """Muat state onboarding, modal & batas slot dalam satu panggilan sinkron."""
    st = store.get_onboard_state(chat_id)
    modal = store.get_modal(chat_id)
    max_pos, _ = store.get_risk_limits(chat_id)
    return {"state": st, "modal": modal, "max_pos": max_pos}


def _onboard_set_stage_sync(
    chat_id: int,
    stage: str,
    *,
    goal: str | None = None,
    ticker: str | None = None,
) -> dict:
    """Simpan progres onboarding lalu muat ulang modal & batas slot sekuensial."""
    updates: dict[str, str] = {"stage": stage}
    if goal is not None:
        updates["goal"] = goal
    if ticker is not None:
        updates["ticker"] = ticker
    st = store.set_onboard_state(chat_id, **updates)
    modal = store.get_modal(chat_id)
    max_pos, _ = store.get_risk_limits(chat_id)
    return {"state": st, "modal": modal, "max_pos": max_pos}


def _onboard_s3_payload_sync(chat_id: int, st: dict, modal: int) -> dict:
    """Muat semua data Stage 3 secara sekuensial agar koneksi SQLite aman."""
    max_pos, _ = store.get_risk_limits(chat_id)
    return {
        "state": st,
        "modal": modal,
        "max_pos": max_pos,
        "rows": store.load_market_scan(),
        "meta": store.market_scan_meta(),
        "positions": store.real_positions(chat_id),
        "wl": store.get_watchlist(chat_id),
    }


def _onboard_load_s3_sync(chat_id: int) -> dict:
    """Simpan stage s3 dan muat kandidat adaptif Stage 3 secara sekuensial."""
    st = store.set_onboard_state(chat_id, stage="s3")
    modal = store.get_modal(chat_id)
    return _onboard_s3_payload_sync(chat_id, st, modal)


def _onboard_set_modal_sync(chat_id: int, val: int) -> dict:
    """Simpan modal baru, set stage s3 & muat kandidat Stage 3 sekuensial."""
    store.set_modal(chat_id, val)
    st = store.set_onboard_state(chat_id, stage="s3")
    return _onboard_s3_payload_sync(chat_id, st, val)


def _onboard_done_sync(chat_id: int) -> dict:
    """Ambil ringkasan state terakhir lalu bersihkan state onboarding."""
    st = store.get_onboard_state(chat_id)
    store.clear_onboard_state(chat_id)
    return st


def _onboard_s1_view(state: dict) -> tuple[str, InlineKeyboardMarkup]:
    cur_goal = state.get("goal")
    cur_note = (
        f"\nTujuan terakhir: <b>{_ONBOARD_GOALS[cur_goal]}</b>."
        if cur_goal in _ONBOARD_GOALS else ""
    )
    text = (
        "🧭 <b>Panduan Bertahap · Tahap 1 dari 5 — Pilih Tujuan</b>\n\n"
        "Ikuti 5 langkah singkat agar keputusanmu terukur (data jujur, tanpa "
        "janji untung, dan eksekusi tetap manual di Stockbit):\n"
        "1️⃣ Pilih tujuan · 2️⃣ Cek modal · 3️⃣ Pilih saham · "
        "4️⃣ Cek kelayakan · 5️⃣ Hitung lot &amp; catat."
        f"{cur_note}\n\n"
        "Apa tujuanmu saat ini?"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Cari & Beli Saham", callback_data="onboard:goal:beli")],
        [InlineKeyboardButton("🔍 Cek Nilai 1 Saham", callback_data="onboard:goal:cek")],
        [InlineKeyboardButton("🔴 Tinjau Saham untuk Dijual", callback_data="onboard:goal:jual")],
        [InlineKeyboardButton("📖 Panduan Cepat", callback_data="guide:start"),
         InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
    ])
    return text, markup


def _onboard_s2_view(
    state: dict, modal: int, max_pos: float
) -> tuple[str, InlineKeyboardMarkup]:
    goal = _resolve_onboard_goal(state.get("goal"))
    slot_25 = modal * max_pos
    risk_1 = modal * 0.01
    text = (
        "🧭 <b>Panduan Bertahap · Tahap 2 dari 5 — Modal &amp; Ukuran 1 Lot</b>\n\n"
        f"Tujuan: <b>{_ONBOARD_GOALS[goal]}</b>\n"
        f"Modal tersimpan: <b>{saranmod.fmt(modal)}</b>\n"
        f"• Batas slot ideal ({int(round(max_pos * 100))}% modal): "
        f"<b>{saranmod.fmt(slot_25)}</b> per saham\n"
        f"• Risiko maks normal (1%): <b>{saranmod.fmt(risk_1)}</b> per posisi\n\n"
        "ℹ️ Di BEI minimum beli <b>1 lot = 100 lembar</b>. Jika modalmu Rp500rb–Rp1jt, "
        "bot akan menandai saham mana yang harga 1 lot-nya muat modal vs melebihi slot 25%.\n\n"
        "Pilih modal cepat atau pakai nominal tersimpan:"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("Rp 500 Rb", callback_data=f"onboard:modal:{_MODAL_PRESETS[0]}"),
         InlineKeyboardButton("Rp 1 Juta", callback_data=f"onboard:modal:{_MODAL_PRESETS[1]}")],
        [InlineKeyboardButton("Rp 2 Juta", callback_data=f"onboard:modal:{_MODAL_PRESETS[2]}"),
         InlineKeyboardButton("Rp 5 Juta", callback_data=f"onboard:modal:{_MODAL_PRESETS[3]}")],
        [InlineKeyboardButton("Rp 10 Juta", callback_data=f"onboard:modal:{_MODAL_PRESETS[4]}"),
         InlineKeyboardButton("Rp 25 Juta", callback_data=f"onboard:modal:{_MODAL_PRESETS[5]}")],
        [InlineKeyboardButton("✏️ Ketik Modal Sendiri", callback_data="manual:onboard_modal")],
        [InlineKeyboardButton(f"✅ Lanjut Pakai {saranmod.fmt(modal)}", callback_data="onboard:s3")],
        [InlineKeyboardButton("⬅️ Tahap 1", callback_data="onboard:s1"),
         InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
    ])
    return text, markup


def _onboard_s3_view(
    payload: dict, *, cache_dir: str | None = None
) -> tuple[str, InlineKeyboardMarkup]:
    state = payload["state"]
    modal = int(payload["modal"])
    max_pos = float(payload["max_pos"])
    goal = _resolve_onboard_goal(state.get("goal"))
    rows = payload["rows"]
    positions = payload["positions"]
    wl = payload["wl"]
    kb: list[list[InlineKeyboardButton]] = []
    picks: list[str] = []

    if goal == "jual":
        active = [
            p for p in positions
            if int(p.get("lots") or 0) > 0 and p.get("ticker")
        ]
        if active:
            lines = []
            seen: set[str] = set()
            for p in active:
                t = _clean_cb_ticker(str(p["ticker"]))
                if not t or t in seen:
                    continue
                seen.add(t)
                picks.append(t)
                lots = int(p.get("lots") or 0)
                avg = float(p.get("avg_price") or 0)
                avg_str = f" @ {saranmod.fmt(avg)}" if avg > 0 else ""
                lines.append(f"• <b>{esc(t)}</b> — {lots} lot aktif{avg_str}")
                if len(picks) >= 4:
                    break
            body = (
                "Posisi aktif di portofoliomu (lot &gt; 0):\n"
                + "\n".join(lines)
                + "\n\nPilih saham yang ingin dicek sinyal tahan/jual:"
            )
        else:
            picks = list(dict.fromkeys([*wl, *POPULAR_TICKERS]))[:4]
            body = (
                "Belum ada posisi aktif (lot &gt; 0) di portofolio tersimpan.\n"
                "Pilih dari watchlist/saham populer di bawah atau ketik kode saham "
                "yang kamu pegang di Stockbit:"
            )
    elif goal == "beli":
        cands, has_cache = _evaluate_beli_candidates(
            rows, modal, cache_dir=cache_dir, max_pos_pct=max_pos
        )
        eligible = [c for c in cands if c["q_tier"] < 2][:4]
        if eligible:
            picks = [c["ticker"] for c in eligible]
            lines = "\n".join(
                _format_beli_candidate_line(c, modal, max_pos) for c in eligible
            )
            cache_note = (
                "Diprioritaskan dari cache kualitas sehat &amp; biaya 1 lot vs modal:"
                if has_cache
                else "Dari scan terakhir (cache kualitas belum tersedia; urut skor &amp; 1 lot):"
            )
            body = f"{cache_note}\n{lines}\n\nPilih 1 saham untuk dicek kelayakannya di Tahap 4:"
        else:
            picks = list(dict.fromkeys([*wl, *POPULAR_TICKERS]))[:4]
            note = (
                "Belum ada kandidat BELI lolos saringan cache kualitas hari ini."
                if cands
                else "Belum ada snapshot scan pasar tersimpan."
            )
            body = (
                f"{note} Kamu bisa pilih dari watchlist/populer di bawah, ketik kode lain, "
                "atau jalankan <b>Pilihan BELI Berkualitas</b> dulu:"
            )
            kb.append([
                InlineKeyboardButton("✨ Pilihan BELI Berkualitas", callback_data="run:sinyal_bagus"),
                InlineKeyboardButton("💡 Ide Harian", callback_data="run:saran"),
            ])
    else:
        picks = list(dict.fromkeys([*wl, *POPULAR_TICKERS]))[:4]
        src = "Watchlist &amp; saham populer" if wl else "Saham populer BEI"
        body = (
            f"{src}: <b>{', '.join(esc(t) for t in picks)}</b>.\n\n"
            "Pilih saham di bawah atau ketik kode 4 huruf yang ingin dicek:"
        )

    for i in range(0, len(picks), 2):
        kb.append([
            InlineKeyboardButton(f"➡️ Cek {t} (Tahap 4)", callback_data=f"onboard:s4:{t}")
            for t in picks[i:i + 2]
        ])
    kb.append([InlineKeyboardButton("✏️ Ketik Kode Saham Lain", callback_data="manual:onboard_ticker")])
    kb.append([
        InlineKeyboardButton("⬅️ Tahap 2 (Modal)", callback_data="onboard:s2"),
        InlineKeyboardButton("🏠 Menu Utama", callback_data="menu"),
    ])
    text = (
        "🧭 <b>Panduan Bertahap · Tahap 3 dari 5 — Pilih Saham</b>\n\n"
        f"Tujuan: <b>{_ONBOARD_GOALS[goal]}</b> · Modal: <b>{saranmod.fmt(modal)}</b>\n\n"
        f"{body}"
    )
    return text, InlineKeyboardMarkup(kb)


def _onboard_s4_view(
    state: dict, modal: int, ticker: str
) -> tuple[str, InlineKeyboardMarkup]:
    goal = _resolve_onboard_goal(state.get("goal"))
    t = _clean_cb_ticker(ticker)
    text = (
        f"🧭 <b>Panduan Bertahap · Tahap 4 dari 5 — Cek Kelayakan {esc(t)}</b>\n\n"
        f"Tujuan: <b>{_ONBOARD_GOALS[goal]}</b> · Modal: <b>{saranmod.fmt(modal)}</b>\n\n"
        "Sebelum menentukan ukuran lot atau eksekusi manual di Stockbit, cek dulu:\n"
        "• <b>Cek Kelayakan</b> — status LAYAK / TUNGGU / HINDARI + Pro/Risiko\n"
        "• <b>Rincian Nilai</b> — PER, PBV, ROE, dividen &amp; kas perusahaan\n"
        "• <b>Grafik &amp; Analisis</b> — tren harga, momentum &amp; sentimen\n\n"
        "Setelah membaca hasilnya, lanjut ke <b>Tahap 5</b>:"
    )
    kb: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(f"🛡️ Cek Kelayakan {t}", callback_data=f"tick:cek:{t}"),
         InlineKeyboardButton("💰 Fundamental", callback_data=f"tick:fundamental:{t}")],
        [InlineKeyboardButton("📈 Grafik & Analisis", callback_data=f"tick:analisis:{t}")],
    ]
    if goal == "jual":
        kb[1].append(InlineKeyboardButton(f"🔴 Cek Jual {t}", callback_data=f"tick:jual:{t}"))
    kb.append([InlineKeyboardButton(f"➡️ Lanjut Tahap 5: Hitung & Catat {t}", callback_data=f"onboard:s5:{t}")])
    kb.append([
        InlineKeyboardButton("⬅️ Ganti Saham (Tahap 3)", callback_data="onboard:s3"),
        InlineKeyboardButton("🏠 Menu Utama", callback_data="menu"),
    ])
    return text, InlineKeyboardMarkup(kb)


def _onboard_s5_view(
    state: dict, modal: int, ticker: str
) -> tuple[str, InlineKeyboardMarkup]:
    goal = _resolve_onboard_goal(state.get("goal"))
    t = _clean_cb_ticker(ticker)
    if goal == "jual":
        action_hint = (
            "1️⃣ Jalankan <b>Cek Jual</b> untuk lihat apakah tren/trailing stop sudah tersentuh.\n"
            "2️⃣ Jika mengeksekusi jual manual di Stockbit, catat lewat <b>Catat Jual Stockbit</b>.\n"
            "3️⃣ Pasang/perbarui <b>Watchlist &amp; Alarm</b> atau selesaikan panduan."
        )
        row1 = [
            InlineKeyboardButton(f"🔴 Cek Jual {t}", callback_data=f"tick:jual:{t}"),
            InlineKeyboardButton(f"📝 Catat Jual {t}", callback_data=f"exec:sell:{t}"),
        ]
    else:
        action_hint = (
            "1️⃣ Ketuk <b>Hitung Lot &amp; Rencana</b> untuk lihat area beli, SL, TP &amp; lot pas modal.\n"
            "2️⃣ Jika benar beli manual di Stockbit, catat lewat <b>Catat Beli Stockbit</b>.\n"
            "3️⃣ Pantau harga lewat <b>Watchlist &amp; Alarm</b> atau akhiri panduan."
        )
        row1 = [
            InlineKeyboardButton(f"🎯 Hitung Lot {t}", callback_data=f"tick:rencana:{t}"),
            InlineKeyboardButton(f"📝 Catat Beli {t}", callback_data=f"exec:buy:{t}"),
        ]
    text = (
        f"🧭 <b>Panduan Bertahap · Tahap 5 dari 5 — Hitung Lot &amp; Catat ({esc(t)})</b>\n\n"
        f"Tujuan: <b>{_ONBOARD_GOALS[goal]}</b> · Modal: <b>{saranmod.fmt(modal)}</b>\n\n"
        f"{action_hint}\n\n"
        "<i>Bot tidak terhubung ke akun Stockbit dan tidak pernah menaruh order otomatis.</i>"
    )
    kb = [
        row1,
        [InlineKeyboardButton("👀 Watchlist & Alarm", callback_data="sub:pantau"),
         InlineKeyboardButton("✅ Selesai Panduan", callback_data="onboard:done")],
        [InlineKeyboardButton(f"⬅️ Tahap 4 ({t})", callback_data=f"onboard:s4:{t}"),
         InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
    ]
    return text, InlineKeyboardMarkup(kb)


def _onboard_done_view(prev_state: dict) -> tuple[str, InlineKeyboardMarkup]:
    t = prev_state.get("ticker")
    note = f" untuk <b>{esc(t)}</b>" if t else ""
    text = (
        f"✅ <b>Panduan Bertahap Selesai{note}!</b>\n\n"
        "Kamu siap pakai menu utama kapan saja, memasang alarm harga di "
        "<b>Watchlist &amp; Alarm</b>, atau ganti ke <b>Mode Expert</b> lewat "
        "<code>/mode expert</code> jika ingin akses cepat semua alat."
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("👀 Watchlist & Alarm", callback_data="sub:pantau"),
         InlineKeyboardButton("💼 Portofolio Saya", callback_data="run:portofolio")],
        [InlineKeyboardButton("🔁 Ulangi Panduan (Tahap 1)", callback_data="onboard:s1"),
         InlineKeyboardButton("🏠 Menu Utama", callback_data="menu")],
    ])
    return text, markup


async def _guide_content(
    step: str, chat_id: int, cache_dir: str | None = None
) -> tuple[str, InlineKeyboardMarkup] | None:
    """Bangun konten & tombol panduan interaktif langkah demi langkah."""
    if step == "start":
        return _guide_start_view()
    if step == "beli":
        rows, meta, modal, max_pos = await asyncio.to_thread(_load_guide_beli_data, chat_id)
        return _guide_beli_view(rows, meta, modal, cache_dir=cache_dir, max_pos_pct=max_pos)
    if step == "cek":
        wl = await asyncio.to_thread(store.get_watchlist, chat_id)
        text = (
            "🔍 <b>Cek Nilai &amp; Keamanan Saham</b>\n\n"
            "Bot mengecek kondisi saham secara menyeluruh:\n"
            "• <b>Nilai &amp; Fundamental</b>: mahal/murah (PER, PBV), untung (ROE), dividen &amp; kas\n"
            "• <b>Tren &amp; Asing</b>: harga menguat/melemah serta aliran dana asing\n"
            "• <b>Berita &amp; Likuiditas</b>: sentimen berita dan kemudahan transaksi\n\n"
            "Hasil akhirnya jujur: <b>LAYAK / TUNGGU / HINDARI</b>, plus rincian nilai "
            "&amp; tombol hitung lot kalau layak.\n\n"
            "<i>Pilih saham di bawah, atau ketik kode saham lain:</i>"
        )
        return text, ticker_pick_markup("cek", wl, show_guide_back=True)
    if step == "jual":
        positions, wl, rows = await asyncio.to_thread(_load_guide_jual_data, chat_id)
        return _guide_jual_view(positions, wl, rows)
    if step == "pemula":
        return _guide_pemula_view()
    return None


