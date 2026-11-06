"""Generator saran harian + penjual paper-trading (status SL/TP ditrack).

Sumber inti: fungsi pick() menerima daftar hasil analisis yang sudah
dikumpulkan caller (bot/main). Disiplin: ini alat bantu riset & latihan,
bukan kepastian — SL/TP memakai skema ATR sesuai backtest; hasil backtest
sendiri jujur belum menguntungkan, jadi gunakan sebagai alat bantu disiplin
latihan, bukan mesin order otomatis."""
from __future__ import annotations

from core import powerscore, store

DEFAULT_UNIVERSE = [
    "BBCA", "BBRI", "BMRI", "BBNI", "TLKM", "ASII",
    "ANTM", "PGAS", "ICBP", "INDF", "UNVR", "TPIA",
]

TOP_N = 3       # jumlah kandidat BUY yang disarankan
MAX_SCAN = 12   # batasi emiten yang dianalisis per permintaan


def build_candidate(res: dict, fund: dict | None, news_sent: dict | None,
                    modal: int | None = None) -> dict:
    """Satu kandidat terkuracion (bagian dari /saran)."""
    tech = res["score"]["pct"]
    fund_pct = fund["pct"] if fund else None
    news_pct = news_sent["pct"] if news_sent else None
    pw = powerscore.combine(tech, fund_pct, news_pct)
    snap = res["snap"]
    atr = res["ind"]["atr14"].iloc[-1] if "atr14" in res["ind"] else None
    import pandas as pd
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
        "entry": entry,
        "sl": round(entry - 1.0 * atr_v, 0),
        "tp": round(entry + 2.0 * atr_v, 0),
        "date": None,  # diisi bot
    }
    # [P5] ukuran posisi: risiko 1% modal per trade
    if modal:
        c.update(position_size(modal, entry, c["sl"]))
    return c


def position_size(modal: float, entry: float, sl: float) -> dict:
    """Risiko 1% modal per trade: min. lot 100 lembar (floor).

    Return {risk, shares, lots, value, real_risk}. lots bisa 0 → terlalu kecil."""
    risk = max(float(entry) - float(sl), 0.0)
    budget = float(modal) * 0.01          # 1% modal
    shares = budget / risk if risk > 0 else 0.0
    lots = int(shares // 100)
    value = lots * 100 * float(entry)
    real_risk = lots * 100 * risk
    return {"risk": round(risk, 1), "budget": round(budget),
            "shares": int(shares), "lots": lots,
            "value": round(value), "real_risk": round(real_risk)}


def pick(analyzed: list[dict], modal: int | None = None) -> dict:
    """Ranking hasil analisis → kandidat BUY & daftar HINDARI.

    analyzed: list of {res, fund, nsent}. Filter momentum-trap memakai
    temuan backtest: ret20d > 15% dan teknikal sedang  membayar ekstrem naik = skip."""
    cands = []
    for a in analyzed:
        try:
            cands.append(build_candidate(a["res"], a.get("fund"), a.get("nsent"), modal))
        except Exception:
            continue
    buys = [c for c in cands if c["power"] >= 20 and c["ret20d"] <= 15.0]
    buys.sort(key=lambda c: -c["power"])
    avoid = sorted(cands, key=lambda c: c["power"])[:TOP_N]
    return {"picks": buys[:TOP_N], "avoid": avoid}


def open_paper(chat_id: int | str, picks: list[dict], trade_date: str) -> int:
    paper = store.get_paper(chat_id)
    for c in picks:
        t = c["ticker"]
        if t in paper and paper[t]["status"] == "open":
            continue  # sudah ada posisi latihan → tidak menggandakan
        rec = dict(c)
        rec["date"] = trade_date
        rec["status"] = "open"
        rec["hit"] = "(belum)"
        paper[t] = rec
    store.save_paper(chat_id, paper)
    return len(picks)


def update_paper(chat_id: int | str, latest: dict[str, float]) -> list[str]:
    """Cek posisi latihan terbuka terhadap harga terbaru.

    latest: {ticker: harga_close_terkini}. Return daftar pesan per perubahan status."""
    paper = store.get_paper(chat_id)
    msgs = []
    dirty = False
    for t, rec in list(paper.items()):
        if rec.get("status") != "open" or t not in latest:
            continue
        price = latest[t]
        entry = float(rec["entry"])
        if price >= float(rec["tp"]):
            rec.update(status="closed", hit="tp", exit_price=price)
            pnl = (price / entry - 1) * 100
            msgs.append(f"🎯 <b>{t}</b> TARGET TERCAPAI di {fmt(price)} "
                        f"(entry {fmt(entry)}, {pnl:+.1f}% sejak /saran)")
            dirty = True
        elif price <= float(rec["sl"]):
            rec.update(status="closed", hit="sl", exit_price=price)
            pnl = (price / entry - 1) * 100
            msgs.append(f"🛑 <b>{t}</b> STOP LOSS kena di {fmt(price)} "
                        f"(entry {fmt(entry)}, {pnl:+.1f}%) — cut loss itu disiplin, bukan kekalahan")
            dirty = True
    if dirty:
        store.save_paper(chat_id, paper)
    return msgs


def quotes_forgotten(chat_id: int | str) -> list[str]:
    """Ringkasan posisi latihan untuk /saran list."""
    paper = store.get_paper(chat_id)
    if not paper:
        return []
    lines = ["📋 <b>Posisi latihan (paper trading)</b>"]
    open_n, win_n, lose_n, pnl_sum = 0, 0, 0, 0.0
    for t, rec in sorted(paper.items()):
        status = rec.get("status")
        entry = float(rec["entry"])
        h = f" 📆 {rec.get('date','')}" if rec.get("date") else ""
        if status == "open":
            open_n += 1
            line = (f"  🟡 <b>{t}</b> entry {fmt(entry)} | SL {fmt(rec['sl'])} | "
                    f"TP {fmt(rec['tp'])}")
            if rec.get("lots"):  # [P5] nilai posisi modal 1% risiko
                line += f" | 💼 ≈ {fmt(float(rec['value']))} ({int(rec['lots'])} lot)"
            line += h
            lines.append(line)
        else:
            exit_p = float(rec.get("exit_price", entry))
            pnl = (exit_p / entry - 1) * 100
            pnl_sum += pnl
            win_n += pnl > 0
            lose_n += pnl <= 0
            badge = "🎯 TP" if rec.get("hit") == "tp" else "🛑 SL"
            lines.append(f"  {badge} <b>{t}</b> {pnl:+.1f}% {h}")
    if win_n + lose_n:
        lines.append(f"\nRekap: {win_n + lose_n} posisi selesai (masih terbuka {open_n}) | "
                     f"untung {win_n}, rugi {lose_n}, rata-rata {pnl_sum / (win_n + lose_n):+.1f}%")
    return lines


def fmt(v: float) -> str:
    return f"Rp{v:,.0f}".replace(",", ".")
