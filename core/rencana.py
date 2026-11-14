"""Rencana beli siap eksekusi (Stockbit) — penyaring & sizing yang jujur.

Alur: snapshot screener seluruh pasar → saring likuiditas/board/tren → perkaya
dengan fundamental + berita (di bot, karena butuh async) → saring veto kualitas
→ hitung lot, SL/TP, dan risiko sesuai modal.

Batas kejujuran:
  - Urutan tetap memakai peringkat relatif 12 faktor (IC OOS ±0,03), bukan
    "probabilitas naik". Grade A/B/C = kelengkapan syarat, bukan jaminan.
  - Veto fundamental/berita hanya membuang kandidat yang jelas bermasalah;
    tidak menambah klaim prediksi.
  - Jika tidak ada yang lolos, katakan tidak ada. Jangan memaksa beli.
"""
from __future__ import annotations

import math
import os
from collections import Counter

from core import fundamental, pricing, regime, saran, universe

def _env_num(name: str, default: float, cast=float):
    try:
        return cast(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


MIN_VALUE = float(_env_num("PLAN_MIN_VALUE", 2_000_000_000))
MIN_PRICE = float(_env_num("PLAN_MIN_PRICE", 50))
MAX_POS_PCT = min(0.5, max(0.05, float(_env_num("PLAN_MAX_POS_PCT", 0.20))))
MAX_PLANS = max(1, min(5, int(_env_num("PLAN_MAX_PLANS", 3))))
POOL_LIMIT = max(4, min(40, int(_env_num("PLAN_POOL", 16))))
MAX_PER_SECTOR = 2
MAX_TOTAL_PCT = 0.90
MIN_REL = 60.0
TRAP_RET20D = 15.0
NEWS_VETO = -30.0
FUND_VETO = -35.0
VALUE_GOOD = 5_000_000_000
# Judul halaman otomatis Yahoo bukan berita; jangan ditampilkan sebagai konteks.
_AUTO_NEWS = ("estimates – idx:", "stock price", "stock quote", "chart",
              "profile", "history")


# Fraksi harga & pembulatan dipakai bersama dengan /saran (satu aturan).
tick_size = pricing.tick_size
_round_tick = pricing.round_tick
_ceil_tick = pricing.ceil_tick


def _f(v, default=None):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def prefilter(rows: list[dict], pool_limit: int = POOL_LIMIT):
    """Saring snapshot jadi kandidat yang layak diperkaya (belum final).

    Likuiditas yang belum terukur (snapshot lama) tetap diloloskan; nilainya
    dihitung ulang saat pengayaan, lalu bisa menjadi veto.
    """
    rejected: Counter = Counter()
    cands: list[dict] = []
    for r in rows:
        if r.get("signal") != "BELI":
            rejected["bukan sinyal BELI"] += 1
            continue
        price = _f(r.get("price"))
        if price is None or price < MIN_PRICE:
            rejected["harga < Rp50 / tidak tersedia"] += 1
            continue
        if (r.get("board") or "") == "Pemantauan Khusus":
            rejected["papan Pemantauan Khusus"] += 1
            continue
        ret20 = _f(r.get("ret20d"))
        if ret20 is not None and ret20 > TRAP_RET20D:
            rejected["sudah naik >15% dalam 20 hari"] += 1
            continue
        rel = _f(r.get("rel_pct"))
        if rel is not None and rel < MIN_REL:
            rejected["peringkat relatif < 60"] += 1
            continue
        value = _f(r.get("value_avg20"))
        if value is not None and value < MIN_VALUE:
            rejected["likuiditas < Rp2 miliar/hari"] += 1
            continue
        c = dict(r)
        c["liquidity_unverified"] = value is None
        cands.append(c)

    cands.sort(key=lambda c: -(_f(c.get("rel_pct")) or 0))
    if len(cands) > pool_limit:
        rejected[f"di luar {pool_limit} kandidat teratas"] += len(cands) - pool_limit
        cands = cands[:pool_limit]
    return cands, rejected


def _fund_flags(per, pbv, roe, der, sector):
    """Veto & catatan dari angka fundamental yang tersedia."""
    vetoes: list[str] = []
    warnings: list[str] = []
    if per is not None and per < 0:
        warnings.append(f"PER negatif ({per:.1f}) — laba sedang rugi")
        if roe is not None and roe < 0:
            vetoes.append("PER & ROE negatif (merugi)")
    if pbv is not None and pbv <= 0:
        vetoes.append("PBV ≤ 0 (ekuitas negatif)")
    elif pbv is not None and pbv > 5:
        warnings.append(f"PBV premium ({pbv:.1f}×)")
    if roe is not None and 0 <= roe < 5:
        warnings.append(f"ROE tipis ({roe:.1f}%)")
    if der is not None and der > 300 and not fundamental.is_financial_sector(sector):
        vetoes.append("DER > 300% (utang tinggi)")
    return vetoes, warnings


def enrich(cand: dict, analyzed: dict | None,
           require_rel: bool = True) -> dict | None:
    """Ringkas satu hasil `gather_candidate` jadi kandidat rencana.

    `analyzed` = {"res":..., "fund":..., "nsent":..., "fund_raw":...} dari bot.
    `require_rel=False` dipakai `/rencana <KODE>` yang diminta eksplisit:
    peringkat relatif boleh belum ada (belum `/scan`) dan hanya jadi catatan,
    bukan veto. Untuk paket, vetonya tetap berlaku.
    Return None kalau data teknikal tidak cukup.
    """
    if not analyzed:
        return None
    res = analyzed.get("res") or {}
    snap = res.get("snap") or {}
    ind = res.get("ind")
    fund_raw = analyzed.get("fund_raw") or {}
    fund = analyzed.get("fund") or {}
    nsent = analyzed.get("nsent") or {}

    ticker = (cand.get("ticker") or res.get("ticker") or "").upper()
    price = _f(snap.get("close"), _f(cand.get("price")))
    if price is None or price <= 0:
        return None
    atr = None
    try:
        if ind is not None and "atr14" in ind:
            atr = _f(ind["atr14"].iloc[-1])
    except Exception:
        atr = None
    atr_missing = atr is None or atr <= 0
    if atr_missing:
        atr = price * 0.03

    value = _f(cand.get("value_avg20"))
    vol = _f(cand.get("vol_avg20"))
    if ind is not None:
        try:
            if value is None:
                value = _f((ind["close"] * ind["volume"]).tail(20).mean())
            if vol is None:
                vol = _f(ind["volume"].tail(20).mean())
        except Exception:
            pass

    per = _f(fund_raw.get("per"))
    pbv = _f(fund_raw.get("pbv"))
    roe = fundamental.normalize_roe(fund_raw.get("roe"))
    der = fundamental.normalize_der(fund_raw.get("debt_eq"))
    div_yield = fundamental.normalize_div_yield(fund_raw.get("div_yield"))
    sector = fund_raw.get("sector") or cand.get("sector")
    fund_pct = _f(fund.get("pct"))
    news_pct = _f(nsent.get("pct"))
    rel_pct = _f(cand.get("rel_pct"))
    ret20d = _f(snap.get("ret20d"), _f(cand.get("ret20d")))
    rsi = _f(snap.get("rsi"), _f(cand.get("rsi")))
    board = cand.get("board") or universe.board_of(ticker)

    vetoes, warnings = _fund_flags(per, pbv, roe, der, sector)
    warnings.extend(fundamental.analyze_cashflow_quality(fund_raw)["flags"])
    if price < MIN_PRICE:
        vetoes.append("harga < Rp50")
    if board == "Pemantauan Khusus":
        vetoes.append("papan Pemantauan Khusus")
    if ret20d is not None and ret20d > TRAP_RET20D:
        vetoes.append("sudah naik >15%/20 hari")
    if value is not None and value < MIN_VALUE:
        vetoes.append("likuiditas < Rp2 miliar/hari")
    if rel_pct is None or rel_pct < MIN_REL:
        if require_rel:
            vetoes.append("peringkat relatif lemah")
        elif rel_pct is None:
            warnings.append("peringkat relatif belum ada — jalankan /scan")
        else:
            warnings.append(f"peringkat relatif rendah ({rel_pct:.0f})")
    if fund_pct is not None and fund_pct < FUND_VETO:
        vetoes.append("skor fundamental sangat lemah")
    if news_pct is not None and news_pct < NEWS_VETO:
        vetoes.append("sentimen berita negatif")
    if atr_missing:
        warnings.append("ATR tidak tersedia — SL memakai 3% harga")
    if value is None:
        warnings.append("likuiditas belum terukur")
    elif value < VALUE_GOOD:
        warnings.append("likuiditas sedang (< Rp5 miliar/hari)")
    if fund_pct is None:
        warnings.append("fundamental tidak tersedia")
    if news_pct is None:
        warnings.append("berita tidak tersedia")
    if ret20d is not None and 8 < ret20d <= TRAP_RET20D:
        warnings.append(f"sudah naik {ret20d:.1f}% dalam 20 hari")
    if rsi is not None and rsi > 70:
        warnings.append(f"RSI jenuh beli ({rsi:.0f})")
    if price < 100:
        warnings.append("harga < Rp100 — spread/tick relatif besar")

    news_top = []
    for d in (nsent.get("details") or [])[:2]:
        title = d.get("title")
        if title and not any(b in str(title).lower() for b in _AUTO_NEWS):
            news_top.append({"title": str(title)[:160],
                             "sent": _f(d.get("sent")),
                             "source": str(d.get("source") or "")})

    return {
        "ticker": ticker,
        "name": universe.name_of(ticker) or (cand.get("name") or ""),
        "board": board,
        "sector": sector,
        "price": round(price, 2),
        "rsi": round(rsi, 1) if rsi is not None else None,
        "ret20d": round(ret20d, 2) if ret20d is not None else None,
        "atr": round(atr, 2),
        "rel_pct": round(rel_pct, 1) if rel_pct is not None else None,
        "tech_score": _f(cand.get("tech_score")),
        "value_avg20": round(value) if value is not None else None,
        "vol_avg20": round(vol) if vol is not None else None,
        "per": per, "pbv": pbv, "roe": roe, "der": der,
        "div_yield": div_yield,
        "fund_pct": fund_pct,
        "fund_label": fund.get("label"),
        "fund_notes": list(fund.get("notes") or [])[:3],
        "news_pct": news_pct,
        "news_label": nsent.get("label"),
        "news_n": nsent.get("n"),
        "news_top": news_top,
        "reason": cand.get("reason") or "",
        "vetoes": vetoes,
        "warnings": warnings,
        "grade": grade({"vetoes": vetoes, "rel_pct": rel_pct,
                        "fund_pct": fund_pct, "news_pct": news_pct,
                        "value_avg20": value, "ret20d": ret20d,
                        "warnings": warnings}),
    }


def grade(c: dict) -> str:
    """A/B/C = kelengkapan syarat kualitas, BUKAN probabilitas naik."""
    if c.get("vetoes"):
        return "—"
    pts = 0
    rel = _f(c.get("rel_pct")) or 0
    if rel >= 90:
        pts += 2
    elif rel >= 80:
        pts += 1
    fund_pct = _f(c.get("fund_pct"))
    if fund_pct is not None and fund_pct >= 0:
        pts += 1
    news_pct = _f(c.get("news_pct"))
    if news_pct is not None and news_pct >= 0:
        pts += 1
    if (_f(c.get("value_avg20")) or 0) >= VALUE_GOOD:
        pts += 1
    ret20 = _f(c.get("ret20d"))
    if ret20 is None or ret20 <= 8:
        pts += 1
    warns = list(c.get("warnings") or [])
    if any(w.startswith("ROE tipis") for w in warns):
        pts -= 1
    if any(w.startswith("PBV premium") for w in warns):
        pts -= 1
    if any(w.startswith("RSI jenuh") for w in warns):
        pts -= 1
    quality_gap = any(w.startswith(("ROE tipis", "PBV premium")) for w in warns)
    if pts >= 5 and not quality_gap:
        return "A"
    return "B" if pts >= 3 else "C"


def price_levels(c: dict) -> dict:
    """Entry/SL/TP/chase satu kandidat — satu aturan untuk semua perintah.

    Dipakai `make_plan` (paket multi-emiten) dan `budget_plan` (satu emiten
    dengan anggaran) supaya emiten yang sama tidak pernah menampilkan level
    berbeda hanya karena perintahnya lain.
    """
    raw_entry = _f(c.get("price")) or 0.0
    tick = tick_size(raw_entry)
    entry = _round_tick(raw_entry, tick)
    atr = _f(c.get("atr")) or entry * 0.03
    if atr <= 0:
        atr = entry * 0.03
    sl = _round_tick(max(entry - atr, tick), tick)
    if sl >= entry:
        sl = max(entry - tick, tick)
    tp = _round_tick(entry + 2.0 * atr, tick)
    if tp <= entry:
        tp = entry + 2.0 * tick
    chase_above = _ceil_tick(entry + 0.5 * atr, tick)
    if chase_above <= entry:
        chase_above = entry + tick
    risk_per_share = max(entry - sl, 0.0)
    return {
        "raw_entry": raw_entry, "tick": tick, "entry": entry, "atr": atr,
        "sl": sl, "tp": tp, "chase_above": chase_above,
        "risk_per_share": risk_per_share,
        "rr": ((tp - entry) / risk_per_share) if risk_per_share > 0 else 0.0,
    }


def make_plan(c: dict, modal: float, *, risk_scale: float = 1.0) -> dict:
    """Hitung entry/SL/TP, lot, biaya, dan risiko untuk satu kandidat."""
    lv = price_levels(c)
    entry, sl, tp = lv["entry"], lv["sl"], lv["tp"]
    atr = lv["atr"]
    chase_above = lv["chase_above"]
    risk_per_share = lv["risk_per_share"]

    scale = max(0.1, min(1.0, float(risk_scale)))
    eff_risk_pct = 0.01 * scale
    eff_max_pos_pct = MAX_POS_PCT * scale
    ps = saran.position_size(
        modal, entry, sl, risk_pct=eff_risk_pct, max_pos_pct=eff_max_pos_pct
    )
    lots = max(0, int(ps["lots"]))
    shares = lots * 100
    value = shares * entry
    real_risk = shares * risk_per_share
    min_modal = max(
        risk_per_share * (100.0 / eff_risk_pct),
        entry * (100.0 / eff_max_pos_pct),
    )
    min_modal = int(math.ceil(min_modal / 100_000.0) * 100_000)
    rr = lv["rr"]

    return {
        "ticker": c.get("ticker"),
        "name": c.get("name"),
        "board": c.get("board"),
        "sector": c.get("sector"),
        "grade": c.get("grade") or grade(c),
        "price": entry,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "atr": round(atr, 2),
        "chase_above": chase_above,
        "rr": round(rr, 2),
        "lots": lots,
        "shares": shares,
        "value": round(value),
        "buy_cost": round(value * (1 + saran.BUY_FEE)),
        "real_risk": round(real_risk),
        "risk_budget": round(modal * eff_risk_pct),
        "risk_pct": round(real_risk / modal * 100, 2) if modal else 0.0,
        "net_tp_pct": round(saran.pnl_pct(entry, tp), 2),
        "min_modal": min_modal,
        "modal": round(modal),
        "rel_pct": c.get("rel_pct"),
        "rsi": c.get("rsi"),
        "ret20d": c.get("ret20d"),
        "value_avg20": c.get("value_avg20"),
        "per": c.get("per"),
        "pbv": c.get("pbv"),
        "roe": c.get("roe"),
        "der": c.get("der"),
        "div_yield": c.get("div_yield"),
        "fund_pct": c.get("fund_pct"),
        "news_pct": c.get("news_pct"),
        "news_label": c.get("news_label"),
        "news_n": c.get("news_n"),
        "warnings": list(c.get("warnings") or []),
        "fund_notes": list(c.get("fund_notes") or []),
        "news_top": list(c.get("news_top") or []),
        "reason": c.get("reason") or "",
        "skip_reason": (f"modal Rp{modal:,.0f} < minimum Rp{min_modal:,.0f}"
                        if lots == 0 else ""),
    }


def budget_plan(c: dict, budget: float, modal: float | None = None) -> dict:
    """Rencana SATU emiten dengan anggaran eksplisit (/rencana <KODE> <Rp>).

    Bedanya dengan `make_plan`: jumlah lot di sini adalah yang benar-benar
    TERJANGKAU anggaran (dibulatkan ke bawah per 100 lembar, fee beli ikut
    dihitung), bukan yang dibatasi aturan risiko 1%. Aturan 1% tetap dihitung
    (`lots_risk`) dan ditampilkan sebagai pembanding supaya kelihatan bila
    ukuran ini lebih besar dari kebiasaan.

    Kalau anggaran tidak cukup untuk 1 lot, kembalikan `ok=False` + harga 1 lot
    — jangan mengarang jumlah lot.
    """
    lv = price_levels(c)
    entry, sl, tp = lv["entry"], lv["sl"], lv["tp"]
    budget = max(0.0, float(budget or 0.0))
    modal = float(modal) if modal else 0.0
    lot_value = entry * 100.0
    min_budget = int(math.ceil(lot_value * (1 + saran.BUY_FEE)))

    base = {
        "ticker": (c.get("ticker") or "").upper(),
        "name": c.get("name") or "",
        "grade": c.get("grade") or grade(c),
        "sector": c.get("sector"),
        "board": c.get("board"),
        "price": entry,
        "entry": entry, "sl": sl, "tp": tp, "atr": round(lv["atr"], 2),
        "chase_above": lv["chase_above"],
        "rr": round(lv["rr"], 2),
        "net_tp_pct": round(saran.pnl_pct(entry, tp), 2),
        "budget": round(budget),
        "modal": round(modal),
        "lot_value": round(lot_value),
        "min_budget": min_budget,
        "risk_budget": round(modal * 0.01),
        "warnings": list(c.get("warnings") or []),
        "vetoes": list(c.get("vetoes") or []),
        "reason": c.get("reason") or "",
    }
    if entry <= 0 or budget < min_budget:
        return {**base, "ok": False, "lots": 0, "shares": 0, "value": 0,
                "buy_cost": 0, "cash_left": round(budget),
                "max_loss": 0, "max_loss_pct": None, "pct_modal": None,
                "risk_pct": None, "lots_risk": 0, "profit_tp": None,
                "skip_reason": ("anggaran kurang dari harga 1 lot"
                                if entry > 0 else "harga tidak tersedia")}

    lots = int(budget // lot_value)
    while lots > 0 and lots * lot_value * (1 + saran.BUY_FEE) > budget:
        lots -= 1
    shares = lots * 100
    value = shares * entry
    buy_fee = value * saran.BUY_FEE
    buy_cost = value + buy_fee
    sell_fee_sl = shares * sl * saran.SELL_FEE
    max_loss = value - shares * sl + buy_fee + sell_fee_sl
    profit_tp = shares * tp * (1 - saran.SELL_FEE) - buy_cost
    max_loss_pct = (max_loss / modal * 100) if modal else None
    lots_risk = (saran.position_size(modal, entry, sl)["lots"]
                 if modal else 0)

    warnings = list(base["warnings"])
    if modal and lots_risk and lots > lots_risk:
        warnings.append(
            f"{lots} lot = risiko {max_loss_pct:.1f}% modal, di atas aturan "
            f"1%; ukuran sesuai aturan hanya {lots_risk} lot")
    elif modal and not lots_risk:
        warnings.append("risiko per lembar > 1% modal — aturan 1% tidak "
                        "mengizinkan posisi ini sekalipun 1 lot")

    return {**base, "ok": True, "lots": lots, "shares": shares,
            "value": round(value), "buy_cost": round(buy_cost),
            "cash_left": round(budget - buy_cost),
            "pct_modal": (round(buy_cost / modal * 100, 2) if modal else None),
            "max_loss": round(max_loss),
            "max_loss_pct": (round(max_loss_pct, 2)
                             if max_loss_pct is not None else None),
            "risk_pct": (round(max_loss_pct, 2)
                         if max_loss_pct is not None else None),
            "lots_risk": lots_risk,
            "profit_tp": round(profit_tp),
            "warnings": warnings, "skip_reason": ""}


def finalize(enriched: list[dict], modal: float,
             max_plans: int | None = None,
             *, market_regime: dict | None = None) -> dict:
    """Veto kualitas + batas sektor/modal/rezim IHSG → daftar rencana final."""
    limit = max(1, min(5, int(max_plans or MAX_PLANS)))
    scale = regime.effective_scale(market_regime)
    total_pct = MAX_TOTAL_PCT * scale
    pos_pct = MAX_POS_PCT * scale
    picks: list[dict] = []
    rejected: Counter = Counter()
    sectors: Counter = Counter()
    budget = modal * total_pct
    spent = 0.0
    ranked = sorted(enriched, key=lambda c: (-(_f(c.get("rel_pct")) or 0),
                                             c.get("ticker") or ""))
    over_label = f"total alokasi >{int(round(total_pct * 100))}% modal"
    for c in ranked:
        if c.get("vetoes"):
            for v in c["vetoes"]:
                rejected[v] += 1
            continue
        sector = c.get("sector")
        if sector and sectors[sector] >= MAX_PER_SECTOR:
            rejected["konsentrasi sektor (>2)"] += 1
            continue
        plan = make_plan(c, modal, risk_scale=scale)
        if plan["lots"] <= 0:
            rejected["modal kurang untuk 1 lot"] += 1
            continue
        if spent + plan["buy_cost"] > budget:
            rejected[over_label] += 1
            continue
        picks.append(plan)
        spent += plan["buy_cost"]
        if sector:
            sectors[sector] += 1
        if len(picks) >= limit:
            break
    return {
        "picks": picks,
        "rejected": rejected,
        "pool": len(enriched),
        "modal": round(modal),
        "limit": limit,
        "regime": market_regime,
        "exposure_scale": scale,
        "max_pos_pct": round(pos_pct * 100, 1),
        "max_total_pct": round(total_pct * 100, 1),
        "risk_pct_limit": round(1.0 * scale, 2),
    }
