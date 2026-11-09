"""Generator saran harian + paper trading yang realistis.

Kandidat TIDAK langsung dianggap terbeli di harga close saat pemindaian.
Posisi diantre ("pending") lalu diisi di harga OPEN sesi berikutnya, dan PnL
dihitung setelah biaya beli/jual — supaya hasil latihan sebanding dengan
backtest yang memakai fill bar berikutnya + komisi.
"""
from __future__ import annotations

from collections import Counter
from datetime import date

import pandas as pd

from core import powerscore, relatif, store

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
TRAP_RET20D = 15.0      # skip jebakan momentum (temuan backtest)
CLOSED_SHOWN = 10       # posisi selesai yang ditampilkan di /saran list

BUY_FEE = 0.0015        # broker beli
SELL_FEE = 0.0025       # broker + pajak jual


def build_candidate(res: dict, fund: dict | None, news_sent: dict | None,
                    modal: int | None = None, fund_raw: dict | None = None) -> dict:
    """Satu kandidat terkurasi (bagian dari /saran)."""
    tech = res["score"]["pct"]
    fund_pct = fund["pct"] if fund else None
    news_pct = news_sent["pct"] if news_sent else None
    pw = powerscore.combine(tech, fund_pct, news_pct)
    snap = res["snap"]
    atr = res["ind"]["atr14"].iloc[-1] if "atr14" in res["ind"] else None
    entry = snap["close"]
    atr_v = float(atr) if atr is not None and not pd.isna(atr) else entry * 0.03
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
        "sl": round(entry - 1.0 * atr_v, 0),
        "tp": round(entry + 2.0 * atr_v, 0),
        "date": None,  # diisi saat entry terisi
    }
    # [P5] ukuran posisi: risiko 1% modal per trade
    if modal:
        c.update(position_size(modal, entry, c["sl"]))
    return c


def position_size(modal: float, entry: float, sl: float) -> dict:
    """Risiko 1% modal per trade: min. lot 100 lembar (floor).

    Nilai posisi dibatasi modal — risiko per lembar yang sangat kecil tidak
    boleh menghasilkan saran beli melebihi uang yang ada. lots bisa 0
    (risiko/lembar melebihi 1% modal) → sinyal dilewati.
    """
    entry, sl, modal = float(entry), float(sl), float(modal)
    risk = max(entry - sl, 0.0)
    budget = modal * 0.01                 # 1% modal
    shares = budget / risk if risk > 0 else 0.0
    lots = int(shares // 100)
    max_lots = int(modal // (entry * 100)) if entry > 0 else 0
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


def pick(analyzed: list[dict], modal: int | None = None,
         open_recs: dict[str, dict] | None = None) -> dict:
    """Ranking hasil analisis → kandidat BUY & daftar HINDARI.

    Sejak evaluasi OOS (research/evaluation.md), urutan kandidat memakai skor
    RELATIF antar-emiten (core/relatif.py, IC OOS +0,031) — bukan PowerScore,
    yang komponennya tidak punya daya prediksi terukur. Kalau universe terlalu
    kecil (<8 emiten) untuk statistik cross-sectional, kembali ke PowerScore.
    Batas portofolio (open_recs, MAX_OPEN, MAX_PER_SECTOR) tetap dihormati.
    """
    open_recs = open_recs or {}
    open_count = len(open_recs)
    sector_count: Counter = Counter(
        r.get("sector") for r in open_recs.values() if r.get("sector"))

    cands = []
    for a in analyzed:
        try:
            cands.append(build_candidate(a["res"], a.get("fund"), a.get("nsent"),
                                         modal, a.get("fund_raw")))
        except Exception:
            continue

    # peringkat relatif di dalam universe pemindaian
    items = [a["res"] for a in analyzed if hasattr(a.get("res", {}).get("ind"), "iloc")]
    relatif.score_universe(items)
    by_ticker = {it["ticker"]: it for it in items}
    for c in cands:
        it = by_ticker.get(c["ticker"], {})
        c["rel_pct"] = it.get("rel_pct")
        c["rel_quintil"] = it.get("rel_quintil")
        c["rel_ok"] = bool(it.get("rel_ok"))

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
            "open_count": open_count, "max_open": MAX_OPEN}


def queue_paper(chat_id: int | str, picks: list[dict], trade_date) -> int:
    """Antre kandidat sebagai posisi 'pending' (entry di open sesi berikutnya)."""
    paper = store.get_paper(chat_id)
    queued = 0
    for c in picks:
        symbol = c["ticker"]
        existing = paper.get(symbol)
        if existing and existing.get("status") in ("open", "pending"):
            continue
        rec = dict(c)
        rec.update(status="pending", hit="(belum)", queued=str(trade_date), date=None)
        store.set_paper_record(chat_id, symbol, rec)
        queued += 1
    return queued


def fill_pending(chat_id: int | str, bars: dict[str, pd.DataFrame],
                 modal: int) -> list[str]:
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
        atr = float(rec.get("atr") or entry * 0.03)
        sl = round(entry - atr, 0)
        tp = round(entry + 2 * atr, 0)
        rec.update(status="open", entry=entry, sl=sl, tp=tp,
                   filled_at=bar.date().isoformat(), date=f"{bar.date():%d %b}",
                   hit="(belum)")
        rec.update(position_size(modal, entry, sl))
        store.set_paper_record(chat_id, symbol, rec)
        msgs.append(f"🟢 <b>{symbol}</b> entry terisi di open {fmt(entry)} "
                    f"({bar.date():%d %b}) | SL {fmt(sl)} | TP {fmt(tp)}")
    return msgs


def _expired(rec: dict, today: date) -> bool:
    raw = rec.get("filled_at")
    if not raw:
        return False
    try:
        filled = date.fromisoformat(str(raw))
    except ValueError:
        return False
    return (today - filled).days >= round(MAX_HOLD_DAYS * 7 / 5)  # 20 hari bursa ≈ 28 hari


def _close(rec: dict, exit_price: float, hit: str):
    rec.update(status="closed", hit=hit, exit_price=float(exit_price))


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
    today = today or date.today()
    msgs: list[str] = []
    for symbol, rec in store.get_paper(chat_id).items():
        if rec.get("status") != "open" or symbol not in quotes:
            continue
        bar = _ohlc(quotes[symbol])
        if bar is None:
            continue
        close, high, low = bar
        entry = float(rec["entry"])
        tp, sl = float(rec["tp"]), float(rec["sl"])
        # satu bar menyentuh SL & TP sekaligus → urutan intraday tak diketahui,
        # jadi ambil yang konservatif (SL) daripada mencatat untung semu.
        if low <= sl:
            _close(rec, sl, "sl")
            store.set_paper_record(chat_id, symbol, rec)
            msgs.append(f"🛑 <b>{symbol}</b> STOP LOSS kena di {fmt(sl)} "
                        f"(entry {fmt(entry)}, net {pnl_pct(entry, sl):+.1f}%) "
                        "— cut loss itu disiplin, bukan kekalahan")
        elif high >= tp:
            _close(rec, tp, "tp")
            store.set_paper_record(chat_id, symbol, rec)
            msgs.append(f"🎯 <b>{symbol}</b> TARGET TERCAPAI di {fmt(tp)} "
                        f"(entry {fmt(entry)}, net {pnl_pct(entry, tp):+.1f}% setelah biaya)")
        elif _expired(rec, today):
            _close(rec, close, "timeout")
            store.set_paper_record(chat_id, symbol, rec)
            msgs.append(f"⏱️ <b>{symbol}</b> ditutup karena batas waktu "
                        f"({MAX_HOLD_DAYS} hari bursa) di {fmt(close)} — "
                        f"net {pnl_pct(entry, close):+.1f}%")
    if msgs:  # ada posisi selesai → jaga riwayat tetap ringkas
        store.prune_paper(chat_id)
    return msgs


def quotes_forgotten(chat_id: int | str) -> list[str]:
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


def fmt(v: float) -> str:
    return f"Rp{v:,.0f}".replace(",", ".")
