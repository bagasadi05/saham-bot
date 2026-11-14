"""Generator saran harian + paper trading yang realistis.

Kandidat TIDAK langsung dianggap terbeli di harga close saat pemindaian.
Posisi diantre ("pending") lalu diisi di harga OPEN sesi berikutnya, dan PnL
dihitung setelah biaya beli/jual — supaya hasil latihan sebanding dengan
backtest yang memakai fill bar berikutnya + komisi.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import date

import pandas as pd

from config import IDX_BUY_FEE, IDX_SELL_FEE
from core import marketcal, powerscore, pricing, regime, relatif, store

log = logging.getLogger("sahambuddy.saran")

DEFAULT_UNIVERSE = [
    "BBCA", "BBRI", "BMRI", "BBNI", "TLKM", "ASII",
    "ANTM", "PGAS", "ICBP", "INDF", "UNVR", "TPIA",
]

# [P7] Universe LQ45 (komposisi ±2026; BEI menggantikan anggota ±2x/tahun —
# cek idx.co.id untuk daftar resmi terkini. Ini universe pemindaian, bukan rekomendasi)
LQ45 = [
    "AADI", "ADRO", "AKRA", "AMMN", "AMRT", "ASII", "BBCA", "BBNI", "BBRI",
    "BMRI", "BRIS", "BRPT", "CPIN", "CTRA", "EMTK", "EXCL", "GGRM", "HMSP",
    "ICBP", "INDF", "INKP", "ISAT", "ITMG", "JPFA", "JSMR", "KLBF", "MAPI",
    "MBMA", "MDKA", "MEDC", "MNCN", "PGAS", "PGEO", "PTBA", "SRTG", "SMGR",
    "TBIG", "TLKM", "TPIA", "UNTR", "UNVR", "ANTM", "BREN", "BUMI", "DSSA",
]

TOP_N = 3               # jumlah kandidat BUY yang disarankan
MAX_SCAN = 12           # batasi emiten yang dianalisis per permintaan
MAX_OPEN = 5            # maksimal posisi latihan terbuka (risiko total)
MAX_PER_SECTOR = 2      # konsentrasi per sektor
MAX_HOLD_DAYS = 20      # hari bursa (≈ 28 hari kalender)
MAX_QUEUE_AGE_DAYS = 10  # sinyal basi: batalkan kalau open berikutnya tak kunjung ada
HOLD_BAND_PCT = 40.0    # top 60% (rel_pct >= 40): band tahan hysteresis kurangi turnover
MIN_HOLD_CYCLES = 2     # ~40 hari bursa: masa tahan referensi riset
TRAP_RET20D = 15.0      # skip jebakan momentum (temuan backtest)
CLOSED_SHOWN = 10       # posisi selesai yang ditampilkan di /saran list
BASE_RISK_PCT = 0.01    # risiko awal 1% modal per posisi (0,5x saat IHSG defensif)
CHANDELIER_LENGTH = 22  # jendela puncak high-22 untuk Chandelier Trailing Exit
CHANDELIER_MULT = 3.0   # kelipatan ATR untuk Chandelier Trailing Exit
ARB_LIMIT_PCT = 7.0     # batas ARB 7% papan reguler (BEI 2026)
REGULAR_MIN_PRICE = 1.0   # batas bawah reguler BEI Rp1 (Kep-00136/BEI/09-2026)
LIQUID_FLOOR_PRICE = 50.0  # ambang saham tidur / rawan Papan Pemantauan (FCA)

# Biaya transaksi: satu sumber di config.py (dipakai /rencana & riset juga).
BUY_FEE = IDX_BUY_FEE
SELL_FEE = IDX_SELL_FEE


def stop_price(entry: float, atr: float) -> float:
    """SL 1xATR, dibulatkan ke fraksi harga BEI dan dijamin di bawah entry."""
    tick = pricing.tick_size(entry)
    sl = pricing.round_tick(entry - atr, tick)
    if sl >= entry:
        sl = entry - tick
    return max(sl, tick)


def target_price(entry: float, atr: float) -> float:
    """TP 2xATR, dibulatkan ke fraksi harga BEI dan dijamin di atas entry."""
    tick = pricing.tick_size(entry)
    tp = pricing.round_tick(entry + 2.0 * atr, tick)
    return tp if tp > entry else entry + 2 * tick


def build_candidate(res: dict, fund: dict | None, news_sent: dict | None,
                    modal: int | None = None, fund_raw: dict | None = None,
                    *, risk_scale: float = 1.0) -> dict:
    """Satu kandidat terkurasi (bagian dari /saran)."""
    tech = res["score"]["pct"]
    fund_pct = fund["pct"] if fund else None
    news_pct = news_sent["pct"] if news_sent else None
    pw = powerscore.combine(tech, fund_pct, news_pct)
    snap = res["snap"]
    atr = res["ind"]["atr14"].iloc[-1] if "atr14" in res["ind"] else None
    entry = pricing.round_tick(snap["close"])
    atr_v = (
        float(atr)
        if atr is not None and not pd.isna(atr) and float(atr) > 0
        else entry * 0.03
    )
    c = {
        "ticker": res["ticker"],
        "price": entry,
        "tech": tech,
        "fund": fund_pct,
        "news": news_pct,
        "power": pw["pct"],
        "rating": pw["rating"],
        "emoji": pw["emoji"],
        "ret20d": snap["ret20d"],
        "sector": (fund_raw or {}).get("sector"),
        "atr": round(atr_v, 1),
        "entry": entry,
        "sl": stop_price(entry, atr_v),
        "tp": target_price(entry, atr_v),
        "risk_scale": max(0.1, min(1.0, float(risk_scale))),
        "date": None,  # diisi saat entry terisi
    }
    # [P5] ukuran posisi: risiko 1% modal per trade (0,5x saat IHSG defensif)
    if modal:
        c.update(position_size(
            modal, entry, c["sl"], risk_pct=BASE_RISK_PCT * c["risk_scale"]
        ))
    return c


def position_size(modal: float, entry: float, sl: float,
                  risk_pct: float = 0.01, max_pos_pct: float = 1.0) -> dict:
    """Risiko 1% modal per trade: min. lot 100 lembar (floor).

    Nilai posisi dibatasi modal — risiko per lembar yang sangat kecil tidak
    boleh menghasilkan saran beli melebihi uang yang ada. lots bisa 0
    (risiko/lembar melebihi batas risiko modal) → sinyal dilewati.
    """
    entry, sl, modal = float(entry), float(sl), float(modal)
    if modal <= 0 or entry <= 0:
        return {"risk": 0.0, "budget": 0, "shares": 0, "lots": 0,
                "value": 0, "real_risk": 0}
    risk = max(entry - sl, 0.0)
    budget = modal * max(0.0, float(risk_pct))
    shares = budget / risk if risk > 0 else 0.0
    lots = int(shares // 100)
    cap_pct = max(0.0, min(1.0, float(max_pos_pct)))
    max_lots = int((modal * cap_pct) // (entry * 100)) if entry > 0 else 0
    lots = max(0, min(lots, max_lots))
    shares = lots * 100                   # konsisten: lembar = lot × 100
    value = shares * entry
    real_risk = shares * risk
    return {"risk": round(risk, 1), "budget": round(budget),
            "shares": shares, "lots": lots,
            "value": round(value), "real_risk": round(real_risk)}


def pnl_pct(entry: float, exit_price: float) -> float:
    """PnL bersih setelah biaya beli & jual (persen)."""
    if entry <= 0:
        return 0.0
    return ((exit_price * (1 - SELL_FEE)) / (entry * (1 + BUY_FEE)) - 1) * 100


def hold_review(open_recs: dict[str, dict], by_ticker: dict[str, dict],
                *, today: date | None = None) -> list[dict]:
    """Tinjau posisi terbuka terhadap band tahan top 60% & masa tahan minimum."""
    ref_day = today or marketcal.now_wib().date()
    min_hold_days = MAX_HOLD_DAYS * MIN_HOLD_CYCLES
    notes: list[dict] = []
    for ticker, rec in sorted((open_recs or {}).items()):
        if rec.get("status") not in ("open", "pending"):
            continue
        it = by_ticker.get(ticker)
        if not it or not it.get("rel_ok") or it.get("rel_pct") is None:
            continue
        rel_pct = float(it["rel_pct"])
        days_held = _trading_days_held(rec, ref_day)
        in_min_hold = (
            rec.get("status") == "open"
            and days_held is not None
            and days_held < min_hold_days
        )
        if rel_pct >= HOLD_BAND_PCT:
            action = "TAHAN"
            reason = "masih di top 60% (hemat biaya rotasi 0,40%)"
        elif in_min_hold:
            action = "TAHAN"
            reason = (
                f"masa tahan minimum {MIN_HOLD_CYCLES} siklus "
                f"({days_held}/{min_hold_days} hari bursa; kurangi turnover)"
            )
        else:
            action = "ROTASI"
            reason = "keluar dari top 60% — kandidat rotasi"
        notes.append({
            "ticker": ticker,
            "rel_pct": rel_pct,
            "rel_quintil": it.get("rel_quintil"),
            "days_held": days_held,
            "action": action,
            "reason": reason,
        })
    return notes


def pick(analyzed: list[dict], modal: int | None = None,
         open_recs: dict[str, dict] | None = None,
         *, market_regime: dict | None = None) -> dict:
    """Ranking hasil analisis → kandidat BUY & daftar HINDARI.

    Sejak evaluasi OOS (temp/research/evaluation.md & data/validation.json),
    urutan kandidat memakai skor RELATIF antar-emiten (core/relatif.py,
    IC OOS +0,0304) — bukan PowerScore, yang komponennya tidak punya daya
    prediksi terukur. Kalau universe terlalu kecil (<8 emiten) untuk
    statistik cross-sectional, kembali ke PowerScore. Batas portofolio
    (open_recs, MAX_OPEN, MAX_PER_SECTOR) tetap dihormati.
    """
    open_recs = open_recs or {}
    open_count = len(open_recs)
    sector_count: Counter = Counter(
        r.get("sector") for r in open_recs.values() if r.get("sector"))
    scale = regime.effective_scale(market_regime)

    cands = []
    skipped: list[str] = []
    for a in analyzed:
        try:
            c = build_candidate(
                a["res"], a.get("fund"), a.get("nsent"),
                modal, a.get("fund_raw"),
            )
            c["risk_scale"] = scale
            if modal and scale != 1.0 and "entry" in c and "sl" in c:
                c.update(position_size(
                    modal, c["entry"], c["sl"], risk_pct=BASE_RISK_PCT * scale
                ))
            cands.append(c)
        except Exception:
            ticker = str((a.get("res") or {}).get("ticker") or "?")
            skipped.append(ticker)
            log.warning("kandidat %s dilewati: data tidak lengkap", ticker,
                        exc_info=True)

    # peringkat relatif di dalam universe pemindaian
    items = [a["res"] for a in analyzed if hasattr(a.get("res", {}).get("ind"), "iloc")]
    relatif.score_universe(items)
    by_ticker = {it["ticker"]: it for it in items}
    for c in cands:
        it = by_ticker.get(c["ticker"], {})
        c["rel_pct"] = it.get("rel_pct")
        c["rel_quintil"] = it.get("rel_quintil")
        c["rel_ok"] = bool(it.get("rel_ok"))

    hold_notes = hold_review(open_recs, by_ticker)
    use_rel = sum(1 for c in cands if c["rel_ok"]) >= relatif.MIN_UNIVERSE
    if use_rel:
        pool = [c for c in cands if c["rel_ok"] and c["rel_pct"] is not None]
        ranked = sorted(pool, key=lambda c: -c["rel_pct"])
        avoid = sorted(pool, key=lambda c: c["rel_pct"])[:TOP_N]
        mode = "relatif"
    else:
        ranked = sorted(cands, key=lambda c: -c["power"])
        avoid = sorted(cands, key=lambda c: c["power"])[:TOP_N]
        mode = "powerscore"

    buys: list[dict] = []
    for c in ranked:
        if len(buys) >= TOP_N:
            break
        if c["ret20d"] > TRAP_RET20D:
            continue
        if use_rel:
            if c["rel_pct"] < 60:
                continue
        elif c["power"] < 20:
            continue
        if c["ticker"] in open_recs:
            continue  # sudah punya posisi (open/pending) di emiten ini
        if open_count + len(buys) >= MAX_OPEN:
            break
        sector = c.get("sector")
        if sector:
            used = sector_count[sector] + sum(
                1 for b in buys if b.get("sector") == sector)
            if used >= MAX_PER_SECTOR:
                continue
        buys.append(c)

    return {"picks": buys, "avoid": avoid, "mode": mode,
            "open_count": open_count, "max_open": MAX_OPEN,
            "skipped": skipped, "hold_notes": hold_notes,
            "exposure_scale": scale, "regime": market_regime}


def queue_paper(chat_id: int | str, picks: list[dict], trade_date,
                *, risk_scale: float | None = None) -> int:
    """Antre kandidat sebagai posisi 'pending' (entry di open sesi berikutnya)."""
    paper = store.get_paper(chat_id)
    queued = 0
    for c in picks:
        symbol = c["ticker"]
        existing = paper.get(symbol)
        if existing and existing.get("status") in ("open", "pending"):
            continue
        rec = dict(c)
        if risk_scale is not None:
            rec["risk_scale"] = max(0.1, min(1.0, float(risk_scale)))
        else:
            rec.setdefault("risk_scale", 1.0)
        rec.update(status="pending", hit="(belum)", queued=str(trade_date), date=None)
        store.set_paper_record(chat_id, symbol, rec)
        queued += 1
    return queued


def _resolve_risk_scale(rec: dict, market_regime: dict | None = None) -> float:
    """Ambil faktor skala risiko (1.0 normal, 0.5 saat IHSG defensif)."""
    if market_regime is not None:
        return regime.effective_scale(market_regime)
    raw = rec.get("risk_scale")
    if raw is not None:
        try:
            return max(0.1, min(1.0, float(raw)))
        except (TypeError, ValueError):
            return 1.0
    if isinstance(rec.get("regime"), dict):
        return regime.effective_scale(rec["regime"])
    return 1.0


def fill_pending(chat_id: int | str, bars: dict[str, pd.DataFrame],
                 modal: int, *, market_regime: dict | None = None) -> list[str]:
    """Isi posisi pending di harga OPEN sesi pertama setelah tanggal antre."""
    msgs: list[str] = []
    for symbol, rec in store.get_paper(chat_id).items():
        if rec.get("status") != "pending":
            continue
        df = bars.get(symbol)
        if df is None or df.empty or "open" not in df.columns:
            continue
        try:
            queued = date.fromisoformat(str(rec.get("queued")))
        except ValueError:
            continue
        idx = pd.to_datetime(df.index)
        after = idx[idx.date > queued]
        if len(after) == 0:
            continue  # sesi berikutnya belum ada
        bar = after[0]
        if (bar.date() - queued).days > MAX_QUEUE_AGE_DAYS:
            rec.update(status="cancelled", hit="kedaluwarsa")
            store.set_paper_record(chat_id, symbol, rec)
            msgs.append(f"⏭️ <b>{symbol}</b> dibatalkan: sinyal {queued:%d %b} "
                        "tidak terisi di sesi berikutnya.")
            continue
        try:
            entry = float(df["open"].loc[bar])
        except Exception:
            entry = float(df["open"].iloc[-1])
        if pd.isna(entry) or entry <= 0:
            continue
        raw_atr = rec.get("atr")
        atr = (
            float(raw_atr)
            if raw_atr is not None and not pd.isna(raw_atr) and float(raw_atr) > 0
            else entry * 0.03
        )
        sl = stop_price(entry, atr)
        tp = target_price(entry, atr)
        scale = _resolve_risk_scale(rec, market_regime)
        rec.update(status="open", entry=entry, sl=sl, tp=tp, risk_scale=scale,
                   filled_at=bar.date().isoformat(), date=f"{bar.date():%d %b}",
                   hit="(belum)")
        rec.update(position_size(modal, entry, sl, risk_pct=BASE_RISK_PCT * scale))
        store.set_paper_record(chat_id, symbol, rec)
        scale_note = f" | risiko defensif {scale:.1f}x" if scale < 1.0 else ""
        msgs.append(f"🟢 <b>{symbol}</b> entry terisi di open {fmt(entry)} "
                    f"({bar.date():%d %b}) | SL {fmt(sl)} | TP {fmt(tp)}{scale_note}")
    return msgs


def _trading_days_held(rec: dict, today: date) -> int | None:
    """Jumlah hari bursa posisi terbuka sejak filled_at, atau None jika tidak ada."""
    raw = rec.get("filled_at")
    if not raw:
        return None
    try:
        filled = date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None
    return marketcal.trading_days_between(filled, today)


def _expired(rec: dict, today: date) -> bool:
    """True bila posisi melewati masa tahan minimum (MIN_HOLD_CYCLES x MAX_HOLD_DAYS)."""
    days = _trading_days_held(rec, today)
    return days is not None and days >= MAX_HOLD_DAYS * MIN_HOLD_CYCLES


def _close(rec: dict, exit_price: float, hit: str):
    rec.update(status="closed", hit=hit, exit_price=float(exit_price))


def _bar_date(q) -> date | None:
    """Ambil tanggal bar jika tersedia (Series/DataFrame); None untuk angka polos."""
    if q is None:
        return None
    raw = None
    if isinstance(q, pd.DataFrame):
        if not q.empty:
            raw = q.index[-1]
    elif isinstance(q, pd.Series):
        raw = q.name
    elif isinstance(q, dict):
        raw = q.get("date")
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    try:
        ts = pd.Timestamp(raw)
        if pd.isna(ts):
            return None
        return ts.date()
    except Exception:
        return None


def _ohlc(q) -> tuple[float, float, float] | None:
    """(close, high, low) dari angka, Series bar, atau DataFrame bar."""
    if q is None:
        return None
    if isinstance(q, pd.DataFrame):
        if q.empty:
            return None
        q = q.iloc[-1]
    if not hasattr(q, "get"):  # angka biasa (float/int/numpy scalar)
        try:
            p = float(q)
        except (TypeError, ValueError):
            return None
        return None if pd.isna(p) else (p, p, p)
    close = q.get("close", q.get("Close"))
    high = q.get("high", q.get("High", close))
    low = q.get("low", q.get("Low", close))
    if close is None or high is None or low is None:
        return None
    try:
        close, high, low = float(close), float(high), float(low)
    except (TypeError, ValueError):
        return None
    if any(pd.isna(v) for v in (close, high, low)):
        return None
    return close, high, low


def update_paper(chat_id: int | str, quotes: dict,
                 today: date | None = None) -> list[str]:
    """Cek posisi terbuka: TP/SL/batas waktu. Return pesan per perubahan.

    quotes[symbol] boleh harga penutup (float) atau bar (Series/DataFrame/dict).
    Kalau high/low tersedia, TP/SL yang kena di intraday ikut terdeteksi —
    dulu hanya close yang dicek, jadi level yang tersentuh lalu berbalik lolos.
    """
    today = today or marketcal.now_wib().date()
    msgs: list[str] = []
    for symbol, rec in store.get_paper(chat_id).items():
        if rec.get("status") != "open" or symbol not in quotes:
            continue
        q = quotes[symbol]
        bar_dt = _bar_date(q)
        if bar_dt is not None and rec.get("filled_at"):
            try:
                if bar_dt < date.fromisoformat(str(rec["filled_at"])):
                    continue  # bar cache lebih lama daripada tanggal entry terisi
            except ValueError:
                pass
        bar = _ohlc(q)
        if bar is None:
            continue
        close, high, low = bar
        entry = float(rec["entry"])
        tp, sl = float(rec["tp"]), float(rec["sl"])
        days_held = _trading_days_held(rec, today)
        min_hold_days = MAX_HOLD_DAYS * MIN_HOLD_CYCLES
        # satu bar menyentuh SL & TP sekaligus → urutan intraday tak diketahui,
        # jadi ambil yang konservatif (SL) daripada mencatat untung semu.
        if low <= sl:
            _close(rec, sl, "sl")
            early_note = ""
            if days_held is not None and days_held < min_hold_days:
                rec["exit_reason"] = (
                    f"stop_loss_sebelum_min_hold ({days_held}/{min_hold_days} hari bursa)"
                )
                early_note = (
                    f" | keluar sebelum masa tahan {MIN_HOLD_CYCLES} siklus "
                    f"(hari ke-{days_held}, disiplin SL tetap didahulukan)"
                )
            else:
                rec["exit_reason"] = "stop_loss"
            store.set_paper_record(chat_id, symbol, rec)
            msgs.append(f"🛑 <b>{symbol}</b> STOP LOSS kena di {fmt(sl)} "
                        f"(entry {fmt(entry)}, net {pnl_pct(entry, sl):+.1f}%) "
                        f"— cut loss itu disiplin, bukan kekalahan{early_note}")
        elif high >= tp:
            _close(rec, tp, "tp")
            rec["exit_reason"] = "take_profit"
            store.set_paper_record(chat_id, symbol, rec)
            msgs.append(f"🎯 <b>{symbol}</b> TARGET TERCAPAI di {fmt(tp)} "
                        f"(entry {fmt(entry)}, net {pnl_pct(entry, tp):+.1f}% setelah biaya)")
        elif _expired(rec, today):
            _close(rec, close, "timeout")
            rec["exit_reason"] = f"timeout_{MIN_HOLD_CYCLES}_siklus"
            store.set_paper_record(chat_id, symbol, rec)
            msgs.append(f"⏱️ <b>{symbol}</b> ditutup karena batas waktu "
                        f"({MIN_HOLD_CYCLES} siklus / {min_hold_days} hari bursa) di {fmt(close)} — "
                        f"net {pnl_pct(entry, close):+.1f}%")
    if msgs:  # ada posisi selesai → jaga riwayat tetap ringkas
        store.prune_paper(chat_id)
    return msgs


def paper_summary(chat_id: int | str) -> list[str]:
    """Ringkasan posisi latihan untuk /saran list."""
    paper = store.get_paper(chat_id)
    if not paper:
        return []
    lines = ["📋 <b>Posisi latihan (paper trading)</b>"]
    closed_lines: list[str] = []
    open_n = win_n = lose_n = 0
    pnl_sum = 0.0
    for t, rec in sorted(paper.items()):
        status = rec.get("status")
        when = f" 📆 {rec.get('date')}" if rec.get("date") else ""
        if status == "pending":
            lines.append(f"  ⏳ <b>{t}</b> antre entry — rencana SL "
                         f"{fmt(float(rec['sl']))} | TP {fmt(float(rec['tp']))}"
                         f" (menunggu open sesi berikutnya)")
            open_n += 1
        elif status == "open":
            entry = float(rec["entry"])
            line = (f"  🟡 <b>{t}</b> entry {fmt(entry)} | SL {fmt(float(rec['sl']))} | "
                    f"TP {fmt(float(rec['tp']))}")
            if rec.get("lots"):
                line += f" | 💼 ≈ {fmt(float(rec['value']))} ({int(rec['lots'])} lot)"
            lines.append(line + when)
            open_n += 1
        elif status == "cancelled":
            lines.append(f"  ⏭️ <b>{t}</b> dibatalkan (tidak terisi)")
        else:
            entry = float(rec["entry"])
            exit_p = float(rec.get("exit_price", entry))
            pnl = pnl_pct(entry, exit_p)
            pnl_sum += pnl
            win_n += pnl > 0
            lose_n += pnl <= 0
            badge = {"tp": "🎯 TP", "sl": "🛑 SL",
                     "timeout": "⏱️ batas waktu"}.get(rec.get("hit"), "✅")
            closed_lines.append(f"  {badge} <b>{t}</b> {pnl:+.1f}% net" + when)
    if len(closed_lines) > CLOSED_SHOWN:
        lines.append(f"  … {len(closed_lines) - CLOSED_SHOWN} posisi selesai "
                     "lebih lama tidak ditampilkan (riwayat dipangkas otomatis)")
    lines.extend(closed_lines[-CLOSED_SHOWN:])
    if win_n + lose_n:
        lines.append(f"\nRekap: {win_n + lose_n} posisi selesai (masih terbuka "
                     f"{open_n}) | untung {win_n}, rugi {lose_n}, rata-rata "
                     f"{pnl_sum / (win_n + lose_n):+.1f}% net")
    lines.append(f"Biaya dihitung: beli {BUY_FEE * 100:.2f}%, "
                 f"jual {SELL_FEE * 100:.2f}%.")
    return lines


# Backward-compat alias (dipakai kode & test lama)
quotes_forgotten = paper_summary


def fmt(v: float) -> str:
    """Rupiah tanpa desimal — satu implementasi di core/textutil.py."""
    from core.textutil import rupiah

    return rupiah(v)


def _parse_iso_date(raw: str | date | None) -> date | None:
    """Parse tanggal ISO/date secara defensif; None bila kosong/invalid."""
    if raw is None:
        return None
    if isinstance(raw, date):
        return raw
    txt = str(raw).strip()
    if not txt:
        return None
    try:
        return date.fromisoformat(txt[:10])
    except ValueError:
        return None


def breakeven_stop(entry: float) -> float:
    """Level SL impas bersih setelah biaya beli (0,15%) + jual (0,25%) ≈ 0,40%."""
    px = float(entry or 0.0)
    if px <= 0:
        return 0.0
    raw = px * (1.0 + BUY_FEE) / (1.0 - SELL_FEE)
    bep = pricing.ceil_tick(raw)
    if pnl_pct(px, bep) < 0:
        bep = pricing.ceil_tick(bep + pricing.tick_size(bep))
    return float(max(REGULAR_MIN_PRICE, bep))


def _chandelier_pandas_ta(high: pd.Series, low: pd.Series, close: pd.Series,
                          length: int, mult: float) -> pd.Series | None:
    """Panggil pandas-ta-classic `ce` bila panjang bar memadai; None jika gagal."""
    if len(close) < length:
        return None
    try:
        import pandas_ta_classic as ta
    except ImportError:
        return None
    try:
        ce_df = ta.ce(high, low, close, length=length, multiplier=mult)
    except Exception:
        return None
    if ce_df is None or not hasattr(ce_df, "columns"):
        return None
    for col in ce_df.columns:
        if str(col).upper().startswith("CE_L"):
            s = pd.to_numeric(ce_df[col], errors="coerce")
            if s.notna().any():
                return s
    return None


def _chandelier_pure_python(high: pd.Series, low: pd.Series, close: pd.Series,
                            length: int, mult: float) -> tuple[pd.Series, pd.Series]:
    """Fallback pure-Python Chandelier Long: High_n - mult * Wilder ATR."""
    eff_len = max(1, min(int(length), len(close)))
    high_roll = high.rolling(eff_len, min_periods=1).max()
    prev_close = close.shift(1).fillna(close)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr_s = tr.ewm(alpha=1.0 / eff_len, adjust=False, min_periods=1).mean()
    return high_roll - float(mult) * atr_s, atr_s


def chandelier_trailing_stop(
    df: pd.DataFrame | None,
    *,
    entry: float | None = None,
    atr: float | None = None,
    prev_stop: float | None = None,
    length: int = CHANDELIER_LENGTH,
    mult: float = CHANDELIER_MULT,
    since_date: str | date | None = None,
    ratchet: bool = True,
) -> dict:
    """Chandelier Trailing Exit (high-22 - 3x ATR) ratcheting naik saja.

    Memakai ``pandas-ta-classic`` ``ce`` sebagai jalur utama dengan fallback
    pure-Python ketika panjang histori < ``length`` atau library absen.
    Ratcheting menjaga level trailing tidak pernah turun saat harga naik.
    """
    prev_val = float(prev_stop) if prev_stop is not None and float(prev_stop) > 0 else None
    atr_arg = float(atr) if atr is not None and float(atr) > 0 else None
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        stop_px = pricing.round_tick(prev_val) if prev_val else None
        return {
            "stop": stop_px,
            "raw_stop": prev_val,
            "high_22": None,
            "atr": atr_arg,
            "source": "prev_stop" if stop_px else "tidak_tersedia",
            "hit": False,
            "touched_intraday": False,
            "ratcheted": bool(stop_px and prev_val),
        }

    work = df.rename(columns={c: str(c).lower() for c in df.columns})
    if not {"high", "low", "close"}.issubset(work.columns):
        stop_px = pricing.round_tick(prev_val) if prev_val else None
        return {
            "stop": stop_px,
            "raw_stop": prev_val,
            "high_22": None,
            "atr": atr_arg,
            "source": "prev_stop" if stop_px else "tidak_tersedia",
            "hit": False,
            "touched_intraday": False,
            "ratcheted": False,
        }

    work = work[["high", "low", "close"]].apply(pd.to_numeric, errors="coerce").dropna()
    if work.empty:
        stop_px = pricing.round_tick(prev_val) if prev_val else None
        return {
            "stop": stop_px,
            "raw_stop": prev_val,
            "high_22": None,
            "atr": atr_arg,
            "source": "prev_stop" if stop_px else "tidak_tersedia",
            "hit": False,
            "touched_intraday": False,
            "ratcheted": False,
        }

    high, low, close = work["high"], work["low"], work["close"]
    ta_series = _chandelier_pandas_ta(high, low, close, length, mult)
    fb_raw, atr_series = _chandelier_pure_python(high, low, close, length, mult)
    if ta_series is not None:
        raw_series = ta_series.combine_first(fb_raw)
        src = "pandas_ta_ce"
    else:
        raw_series = fb_raw
        src = "chandelier_fallback"

    raw_series = raw_series.dropna()
    if raw_series.empty:
        stop_px = pricing.round_tick(prev_val) if prev_val else None
        return {
            "stop": stop_px,
            "raw_stop": prev_val,
            "high_22": None,
            "atr": atr_arg,
            "source": "tidak_tersedia" if stop_px is None else "prev_stop",
            "hit": False,
            "touched_intraday": False,
            "ratcheted": False,
        }

    since_dt = _parse_iso_date(since_date)
    active = raw_series
    used_since = False
    if since_dt is not None:
        try:
            idx_dates = pd.to_datetime(raw_series.index).date
            masked = raw_series[idx_dates >= since_dt]
            if not masked.empty:
                active = masked
                used_since = True
        except Exception:
            pass

    if ratchet:
        if used_since:
            ratcheted_series = active.cummax()
        elif prev_val is not None:
            ratcheted_series = active.clip(lower=prev_val)
        else:
            ratcheted_series = active.tail(max(1, min(len(active), length))).cummax()
        raw_stop = float(ratcheted_series.iloc[-1])
        ratcheted_flag = (
            (prev_val is not None and prev_val >= float(active.iloc[-1]))
            or (raw_stop > float(active.iloc[-1]) + 1e-9)
        )
    else:
        raw_stop = float(active.iloc[-1])
        ratcheted_flag = False

    if prev_val is not None and ratchet:
        raw_stop = max(raw_stop, prev_val)

    raw_stop = max(REGULAR_MIN_PRICE, raw_stop)
    stop_px = float(max(REGULAR_MIN_PRICE, pricing.round_tick(raw_stop)))
    if prev_val is not None and ratchet:
        stop_px = max(stop_px, float(pricing.round_tick(prev_val)))

    high_22 = float(high.tail(length).max())
    last_close = float(close.iloc[-1])
    last_low = float(low.iloc[-1])
    atr_latest = float(atr_series.iloc[-1]) if not atr_series.empty else atr_arg
    hit_close = bool(last_close <= stop_px)
    touched_intraday = bool(last_low <= stop_px)
    return {
        "stop": stop_px,
        "raw_stop": round(raw_stop, 2),
        "high_22": round(high_22, 2),
        "atr": round(atr_latest, 2) if atr_latest is not None else None,
        "source": src,
        "hit": hit_close,
        "touched_intraday": touched_intraday,
        "ratcheted": bool(ratcheted_flag),
    }


def scale_out_plan(
    entry: float,
    atr: float,
    lots: int,
    *,
    tp_full: float | None = None,
    tp1_atr_mult: float = 1.5,
    close: float | None = None,
) -> dict:
    """Rencana scale-out TP1 (50% lot) + geser SL sisa lot ke BEP + 0,40%."""
    px = float(entry or 0.0)
    atr_v = float(atr) if atr and float(atr) > 0 else px * 0.03
    n_lots = max(0, int(lots or 0))
    if px <= 0:
        return {
            "tp1": 0.0, "tp_full": 0.0, "bep_sl": 0.0,
            "lots_total": n_lots, "lots_tp1": 0, "lots_runner": n_lots,
            "can_scale_out": False, "tp1_reached": False,
            "note": "entry tidak tersedia",
        }
    tick = pricing.tick_size(px)
    tp_target = float(tp_full) if tp_full and float(tp_full) > px else target_price(px, atr_v)
    tp1 = pricing.round_tick(px + float(tp1_atr_mult) * atr_v, tick)
    if tp1 <= px:
        tp1 = px + tick
    if tp1 > tp_target and tp_target > px + tick:
        tp1 = pricing.round_tick((px + tp_target) / 2.0, tick)
    bep_sl = breakeven_stop(px)

    if n_lots >= 2:
        lots_tp1 = max(1, n_lots // 2)
        lots_runner = n_lots - lots_tp1
        can_scale = True
    else:
        lots_tp1 = 0
        lots_runner = n_lots
        can_scale = False

    tp1_reached = bool(close is not None and float(close) >= tp1)
    if not can_scale:
        note = (
            f"hanya {n_lots} lot (minimal 2 lot untuk bagi 50%); "
            f"saat harga sentuh {fmt(tp1)}, naikkan SL ke BEP+0,40% ({fmt(bep_sl)})"
        )
    elif tp1_reached:
        note = (
            f"SUDAH CAPAI TP1 ({fmt(tp1)}): jual {lots_tp1} lot (50%), "
            f"naikkan SL sisa {lots_runner} lot ke BEP+0,40% ({fmt(bep_sl)})"
        )
    else:
        note = (
            f"di TP1 {fmt(tp1)} (+{pnl_pct(px, tp1):.1f}% net): jual {lots_tp1} lot (50%), "
            f"geser SL sisa {lots_runner} lot ke BEP+0,40% ({fmt(bep_sl)})"
        )
    return {
        "tp1": float(tp1),
        "tp_full": float(tp_target),
        "bep_sl": float(bep_sl),
        "lots_total": n_lots,
        "lots_tp1": lots_tp1,
        "lots_runner": lots_runner,
        "can_scale_out": can_scale,
        "tp1_reached": tp1_reached,
        "note": note,
    }


def time_stop_status(
    entry_date: str | date | None,
    *,
    today: date | None = None,
    pnl_net_pct: float | None = None,
    max_hold_days: int = MAX_HOLD_DAYS,
) -> dict:
    """Horizontal Time-Stop López de Prado: flag modal mandek >= 20 hari bursa."""
    start = _parse_iso_date(entry_date)
    if start is None:
        return {
            "days_held": None,
            "max_hold_days": int(max_hold_days),
            "remaining_days": None,
            "triggered": False,
            "action": "TIDAK_TERSEDIA",
            "reason": "tanggal masuk tidak tercatat",
        }
    ref_day = today or marketcal.now_wib().date()
    days_held = marketcal.trading_days_between(start, ref_day)
    limit = max(1, int(max_hold_days))
    triggered = days_held >= limit
    if triggered:
        pnl_txt = f" (PnL net {pnl_net_pct:+.1f}%)" if pnl_net_pct is not None else ""
        action = "PERTIMBANGKAN ROTASI/KELUAR"
        reason = (
            f"ditahan {days_held} hari bursa (>= batas horizontal {limit} hari)"
            f"{pnl_txt} — tinjau modal mandek / rotasi ke kandidat terkuat"
        )
    else:
        action = "TAHAN"
        reason = f"hari bursa ke-{days_held} dari batas horizontal {limit} hari"
    return {
        "days_held": days_held,
        "max_hold_days": limit,
        "remaining_days": max(0, limit - days_held),
        "triggered": triggered,
        "action": action,
        "reason": reason,
    }


def arb_gap_risk(
    entry_or_ref: float,
    sl: float,
    *,
    price: float | None = None,
    prev_close: float | None = None,
    arb_pct: float = ARB_LIMIT_PCT,
) -> dict:
    """Deteksi risiko gap ARB 7% (BEI 2026) & kedekatan dengan ambang Rp50/Rp1."""
    entry_v = float(entry_or_ref or 0.0)
    sl_v = float(sl or 0.0)
    ref_price = float(price if price is not None and float(price) > 0 else entry_v)
    pct_limit = float(arb_pct)

    dist_entry = ((entry_v - sl_v) / entry_v * 100.0) if entry_v > 0 and sl_v > 0 else 0.0
    dist_price = ((ref_price - sl_v) / ref_price * 100.0) if ref_price > 0 and sl_v > 0 else 0.0
    worst_dist = max(dist_entry, dist_price)
    sl_exceeds_arb = bool(sl_v > 0 and worst_dist > pct_limit)

    arb_base = float(prev_close if prev_close is not None and float(prev_close) > 0 else ref_price)
    if arb_base <= 0:
        arb_floor = REGULAR_MIN_PRICE
    elif arb_base <= 10.0:
        arb_floor = max(REGULAR_MIN_PRICE, arb_base - 1.0)
    else:
        arb_floor = max(
            REGULAR_MIN_PRICE,
            float(pricing.ceil_tick(arb_base * (1.0 - pct_limit / 100.0))),
        )

    arb_locked = bool(
        prev_close is not None
        and float(prev_close) > 0
        and ref_price > 0
        and ref_price <= arb_floor * 1.001
    )
    near_pemantauan = bool((0 < ref_price <= 55.0) or (0 < sl_v <= 55.0))
    below_rp50 = bool((0 < ref_price < LIQUID_FLOOR_PRICE) or (0 < sl_v < LIQUID_FLOOR_PRICE))

    warnings: list[str] = []
    if sl_exceeds_arb:
        warnings.append(
            f"Jarak SL {worst_dist:.1f}% melebihi batas ARB harian {pct_limit:.0f}% "
            f"(lantai ±{fmt(arb_floor)}): jika gap turun/ARB, cut loss bisa tertunda "
            "ke sesi berikutnya dengan slippage lebih dalam."
        )
    if arb_locked:
        warnings.append(
            f"Harga {fmt(ref_price)} mendekati/mentok ARB hari ini ({fmt(arb_floor)}): "
            "antrean jual berisiko tidak langsung laku."
        )
    if near_pemantauan:
        warnings.append(
            "Harga/SL mendekati atau di bawah Rp50: rawan tidak likuid / Papan "
            "Pemantauan Khusus (FCA), meski lantai papan reguler BEI sekarang Rp1."
        )

    return {
        "has_risk": bool(warnings),
        "sl_exceeds_arb": sl_exceeds_arb,
        "sl_dist_entry_pct": round(dist_entry, 2),
        "sl_dist_price_pct": round(dist_price, 2),
        "arb_limit_pct": pct_limit,
        "arb_floor": float(arb_floor),
        "arb_locked": arb_locked,
        "near_pemantauan": near_pemantauan,
        "below_rp50": below_rp50,
        "warnings": warnings,
    }


def build_exit_guidance(
    ticker: str,
    entry: float,
    *,
    lots: int = 0,
    atr: float | None = None,
    sl: float | None = None,
    tp: float | None = None,
    close: float | None = None,
    prev_close: float | None = None,
    df: pd.DataFrame | None = None,
    entry_date: str | date | None = None,
    prev_trailing_sl: float | None = None,
    today: date | None = None,
) -> dict:
    """Bangun panduan EXIT/JUAL terpadu untuk posisi paper maupun portofolio asli."""
    entry_v = float(entry or 0.0)
    atr_estimated = not (atr is not None and float(atr) > 0)
    atr_v = float(atr) if not atr_estimated else max(pricing.tick_size(max(entry_v, 1.0)), entry_v * 0.03)
    ref_close = float(close if close is not None and float(close) > 0 else entry_v)
    sl_initial = float(sl) if sl is not None and float(sl) > 0 else stop_price(entry_v, atr_v)
    tp_target = float(tp) if tp is not None and float(tp) > entry_v else target_price(entry_v, atr_v)

    bep_sl = breakeven_stop(entry_v)
    scale = scale_out_plan(entry_v, atr_v, lots, tp_full=tp_target, close=ref_close)
    trail = chandelier_trailing_stop(
        df, entry=entry_v, atr=atr_v, prev_stop=prev_trailing_sl, since_date=entry_date,
    )
    pnl_net = round(pnl_pct(entry_v, ref_close), 2) if entry_v > 0 else None
    tstop = time_stop_status(entry_date, today=today, pnl_net_pct=pnl_net)
    gap = arb_gap_risk(entry_v, sl_initial, price=ref_close, prev_close=prev_close)

    effective_sl = sl_initial
    if scale["tp1_reached"] and bep_sl > effective_sl:
        effective_sl = bep_sl
    if trail["stop"] is not None and ref_close > trail["stop"] and trail["stop"] > effective_sl:
        effective_sl = float(trail["stop"])

    if sl_initial > 0 and ref_close <= sl_initial:
        action = "CUT_LOSS"
    elif trail["hit"]:
        action = "TRAILING_EXIT"
    elif ref_close >= tp_target:
        action = "AMBIL_PROFIT"
    elif scale["tp1_reached"]:
        action = "SCALE_OUT_TP1"
    elif tstop["triggered"]:
        action = "PERTIMBANGKAN ROTASI/KELUAR"
    else:
        action = "TAHAN"

    return {
        "ticker": str(ticker).upper(),
        "entry": entry_v,
        "close": ref_close,
        "lots": max(0, int(lots or 0)),
        "atr": round(atr_v, 2),
        "atr_estimated": atr_estimated,
        "sl_initial": sl_initial,
        "tp_target": tp_target,
        "bep_sl": bep_sl,
        "trailing_sl": trail["stop"],
        "effective_sl": effective_sl,
        "pnl_net_pct": pnl_net,
        "scale_out": scale,
        "chandelier": trail,
        "time_stop": tstop,
        "arb_risk": gap,
        "action": action,
    }
