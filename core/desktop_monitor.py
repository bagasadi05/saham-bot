"""Mesin pemantau pasar & sinyal desktop SahamBot (tanpa ketergantungan GUI/Telegram).

Dirancang untuk aplikasi Windows lokal (`desktop.py`) dengan pola injeksi modul
(`data_mod`, `store_mod`, dll.) agar 100% bisa diuji offline dan siap disambungkan
ke provider data real-time berizin di masa depan.

Catatan kejujuran:
- Data intraday/harian Yahoo Finance untuk emiten BEI (`.JK`) umumnya memiliki
  jeda ~10-15 menit pada jam bursa; timestamp & status `stale` selalu ditampilkan.
- Bagian yang datanya kosong atau diblokir ditulis "tidak tersedia", tidak dikarang.
"""
from __future__ import annotations

import csv
import datetime as dt
import math
from typing import Any

import pandas as pd

from core import (
    alerts,
    analysis,
    bandar,
    banding,
    cek,
    data,
    data_quality,
    fundamental,
    jurnal,
    kualitas,
    marketcal,
    news,
    pasar,
    powerscore,
    pricing,
    regime,
    rencana,
    risk,
    saran,
    screener,
    store_portfolio,
    store_scan,
    syariah,
    tesis,
    universe,
)

DEFAULT_PROFILE_ID = "desktop"
DELAY_DISCLOSURE = (
    "Sumber gratis (Yahoo Finance .JK) — harga jam bursa umumnya delay ±10-15 menit; "
    "konfirmasi bid/offer langsung di Stockbit PC sebelum order."
)
VALID_CHART_PERIODS = ("3mo", "6mo", "1y", "2y")


def _num(val: Any) -> float | None:
    try:
        x = float(val)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _last_prev_close(df: pd.DataFrame | None) -> tuple[float | None, float | None, float | None]:
    if not isinstance(df, pd.DataFrame) or df.empty or "close" not in df.columns:
        return None, None, None
    close = _num(df["close"].iloc[-1])
    prev = _num(df["close"].iloc[-2]) if len(df) >= 2 else None
    ret1d = ((close - prev) / prev * 100.0) if (close and prev and prev > 0) else None
    low = _num(df["low"].iloc[-1]) if "low" in df.columns else None
    return close, ret1d, low


def _fetch_fund_raw(fundamental_mod: Any, sym: str) -> dict | None:
    """Ambil data fundamental secara kompatibel (plural utama + fallback mock singular)."""
    fn = getattr(fundamental_mod, "fetch_fundamentals", None) or getattr(
        fundamental_mod, "fetch_fundamental", None
    )
    return fn(sym) if fn is not None else None


def _score_fund_dict(fundamental_mod: Any, raw: dict | None) -> dict | None:
    """Hitung skor fundamental secara kompatibel (plural utama + fallback mock singular)."""
    if not raw:
        return None
    fn = getattr(fundamental_mod, "score_fundamentals", None) or getattr(
        fundamental_mod, "score_fundamental", None
    )
    return fn(raw) if fn is not None else None


def _csv_safe(val: Any) -> str:
    """Netralkan prefiks formula CSV (`=`, `+`, `-`, `@`, tab, CR) saat ekspor Excel."""
    s = "" if val is None else str(val)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


def export_rows_to_csv(path: str, headers: list[str], rows: list[list[Any]]) -> str:
    """Simpan tabel ke CSV UTF-8-SIG dengan sanitasi formula Excel."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([_csv_safe(h) for h in headers])
        for row in rows:
            writer.writerow([_csv_safe(cell) for cell in row])
    return path


def format_order_ticket_text(plans: list[dict[str, Any]]) -> str:
    """Format rencana beli siap salin untuk eksekusi manual di Stockbit PC."""
    if not plans:
        return "Tidak ada rencana order yang dipilih."
    lines = ["=== RENCANA ORDER MANUAL (STOCKBIT PC) ==="]
    for p in plans:
        sym = str(p.get("ticker") or "-").upper()
        entry = _num(p.get("entry"))
        sl = _num(p.get("sl"))
        tp = _num(p.get("tp"))
        lots = int(p.get("lots") or 0)
        cost = _num(p.get("buy_cost") or p.get("value") or p.get("cost"))
        rr = _num(p.get("rr"))
        entry_s = f"Rp{entry:,.0f}".replace(",", ".") if entry else "-"
        sl_s = f"Rp{sl:,.0f}".replace(",", ".") if sl else "-"
        tp_s = f"Rp{tp:,.0f}".replace(",", ".") if tp else "-"
        cost_s = f"Rp{cost:,.0f}".replace(",", ".") if cost else "-"
        rr_s = f"1:{rr:.1f}" if rr is not None else "-"
        lines.append(
            f"- BELI {sym}: {lots} lot @ {entry_s} | SL {sl_s} | TP {tp_s} "
            f"(RR {rr_s}, est. {cost_s})"
        )
    lines.append("Konfirmasi bid/offer langsung di Stockbit PC — bot tidak menaruh order.")
    return "\n".join(lines)


def fetch_market_pulse(
    *,
    include_idx: bool = True,
    include_news: bool = True,
    data_mod=data,
    store_scan_mod=store_scan,
    bandar_mod=bandar,
    news_mod=news,
) -> dict[str, Any]:
    """Kumpulkan kondisi pasar BEI terkini (IHSG, rezim, breadth, sektor, asing, berita)."""
    ihsg_df = None
    try:
        ihsg_df = data_mod.fetch_history("^JKSE", "2y", raw_symbol=True)
    except Exception:
        ihsg_df = None

    try:
        scan_rows = store_scan_mod.load_market_scan()
        scan_meta = store_scan_mod.market_scan_meta()
    except Exception:
        scan_rows, scan_meta = [], {}

    index_rows, index_error, index_stale_age = None, None, None
    stock_rows = None
    if include_idx:
        idx_exc = getattr(bandar_mod, "IDXTidakTersedia", bandar.IDXTidakTersedia)
        call_stale = getattr(bandar_mod, "call_with_stale", None)
        try:
            if call_stale is not None:
                index_rows, index_stale_age = call_stale(bandar_mod.get_index_summary)
            else:
                index_rows = bandar_mod.get_index_summary()
        except idx_exc:
            index_error = "data BEI (idx.co.id) sedang diblokir Cloudflare"
        except Exception:
            index_error = "gagal membaca indeks sektor BEI"

        get_stock_fn = getattr(bandar_mod, "get_stock_summary", None)
        if get_stock_fn is not None and index_error is None:
            try:
                t_date_fn = getattr(bandar_mod, "last_trading_date", bandar.last_trading_date)
                t_date = t_date_fn()
                if call_stale is not None:
                    stock_rows, s_age = call_stale(get_stock_fn, t_date, max_stocks=250)
                    if s_age is not None and (index_stale_age is None or s_age > index_stale_age):
                        index_stale_age = s_age
                else:
                    stock_rows = get_stock_fn(t_date, max_stocks=250)
            except Exception:
                stock_rows = None
    else:
        index_error = "tidak diambil"

    news_items = None
    news_sent = None
    if include_news:
        try:
            news_items = news_mod.fetch_market_news(10)
            if news_items:
                news_sent = news_mod.news_sentiment(news_items)
        except Exception:
            news_items = None
            news_sent = None

    now = marketcal.now_wib()
    report = pasar.build_market_report(
        ihsg_df=ihsg_df,
        scan_rows=scan_rows,
        scan_meta=scan_meta,
        index_rows=index_rows,
        index_error=index_error,
        stock_rows=stock_rows,
        news_items=news_items,
        news_sent=news_sent,
        as_of_label=f"{now:%d %b %Y %H:%M:%S} WIB",
    )
    if index_stale_age is not None:
        report["index_stale_age"] = index_stale_age
    report["delay_note"] = DELAY_DISCLOSURE
    report["market_open"] = marketcal.market_open(now)
    return report


def _classify_watch_action(
    close: float,
    score_pct: float | None,
    ind: pd.DataFrame,
    entry: float,
    sl: float,
    *,
    rel_pct: float | None = None,
) -> tuple[str, str]:
    """Tentukan saran ringkas BELI / SIAP BELI / JUAL / TAHAN dari indikator, CMF & relatif."""
    last = ind.iloc[-1]
    sma20 = _num(last.get("sma20")) if "sma20" in ind.columns else None
    sma50 = _num(last.get("sma50")) if "sma50" in ind.columns else None
    sma200 = _num(last.get("sma200")) if "sma200" in ind.columns else None
    rsi = _num(last.get("rsi14")) if "rsi14" in ind.columns else None
    ret20d = _num(last.get("ret20d")) if "ret20d" in ind.columns else None
    ret1d = _num(last.get("ret1d")) if "ret1d" in ind.columns else None
    cmf20 = _num(last.get("cmf20")) if "cmf20" in ind.columns else None
    rsi_div = analysis.detect_rsi_divergence(ind)
    div_kind = rsi_div.get("kind") if isinstance(rsi_div, dict) else None

    if score_pct is not None and score_pct <= -25:
        return "JUAL", f"skor teknikal lemah ({score_pct:+.0f}%)"
    if sma20 and sma50 and close < sma20 and sma20 < sma50 and (score_pct or 0) < 0:
        return "JUAL", "harga di bawah MA20 & MA20 < MA50"
    if close <= sl:
        return "JUAL", f"harga menyentuh area stop ({sl:,.0f})"

    if sma50 and sma200 and close < sma50 and sma50 < sma200:
        return "TAHAN", "downtrend struktural (di bawah MA50 & death cross)"

    if score_pct is not None and score_pct >= 25:
        if ret20d is not None and ret1d is not None and ret20d > 15.0 and ret1d > 0.0:
            return "TAHAN", f"sudah melesat {ret20d:+.1f}%/20h — tunggu pullback"
        if div_kind == "bearish" and cmf20 is not None and cmf20 <= -0.15:
            return "TAHAN", f"divergensi RSI bearish & distribusi CMF ({cmf20:+.2f})"
        if rel_pct is not None and rel_pct < 40.0:
            return "TAHAN", f"peringkat relatif lemah ({rel_pct:.0f}%) — tunggu konfirmasi"
        if entry > 0 and close <= entry * 1.02:
            rel_txt = f", relatif {rel_pct:.0f}%" if rel_pct is not None else ""
            cmf_txt = ", akumulasi CMF" if (cmf20 is not None and cmf20 >= 0.12) else ""
            return "BELI", f"tren kuat ({score_pct:+.0f}%{rel_txt}{cmf_txt}) & dekat area entry"
        if rsi is not None and rsi >= 75:
            return "TAHAN", f"tren kuat ({score_pct:+.0f}%) namun RSI jenuh beli ({rsi:.0f})"
        return "SIAP BELI", f"tren positif ({score_pct:+.0f}%), tunggu pullback ke {entry:,.0f}"

    if entry > 0 and abs(close - entry) / entry <= 0.015 and (score_pct or 0) >= 0:
        return "SIAP BELI", "mendekati area support/entry"
    return "TAHAN", f"netral ({(score_pct or 0):+.0f}%), belum ada sinyal kuat"


def evaluate_watchlist_rows(
    tickers: list[str],
    modal: float,
    *,
    market_regime: dict | None = None,
    syariah_only: bool = False,
    alerts_map: dict[str, list | set] | None = None,
    data_mod=data,
    store_scan_mod=store_scan,
) -> list[dict[str, Any]]:
    """Evaluasi daftar pantauan menjadi baris sinyal BUY/SELL/TAHAN."""
    scan_map: dict[str, dict] = {}
    try:
        for r in store_scan_mod.load_market_scan():
            sym = str(r.get("ticker") or "").strip().upper().removesuffix(".JK")
            if sym:
                scan_map[sym] = r
    except Exception:
        scan_map = {}

    scale = regime.effective_scale(market_regime) if market_regime else 1.0
    rows: list[dict[str, Any]] = []
    for raw_t in tickers:
        sym = str(raw_t or "").strip().upper().removesuffix(".JK")
        if not sym:
            continue
        is_sy = syariah.is_syariah(sym)
        if syariah_only and not is_sy:
            continue
        sec_name = universe.sector_of(sym) or ""
        alert_kinds = sorted(str(k) for k in (alerts_map or {}).get(sym, ()))
        try:
            df = data_mod.fetch_history(sym, "1y")
        except Exception:
            df = None

        if not isinstance(df, pd.DataFrame) or len(df) < 25:
            rows.append({
                "ticker": sym,
                "sector": sec_name,
                "syariah": is_sy,
                "price": None,
                "ret1d": None,
                "action": "TIDAK TERSEDIA",
                "signal": "TIDAK TERSEDIA",
                "score_pct": None,
                "entry": None,
                "sl": None,
                "tp": None,
                "lots": 0,
                "alerts": alert_kinds,
                "triggers": [],
                "zones": [],
                "reason": "data harga tidak tersedia",
                "reasons": ["data harga tidak tersedia"],
                "as_of": "-",
                "stale": True,
            })
            continue

        close, ret1d, _ = _last_prev_close(df)
        ind = analysis.compute_indicators(df)
        sc = analysis.score_stock(ind)
        sr = analysis.support_resistance(df)
        last = ind.iloc[-1]
        raw_atr = _num(last.get("atr14"))
        atr = raw_atr if (raw_atr and raw_atr > 0) else None

        scan_row = scan_map.get(sym)
        if scan_row and _num(scan_row.get("entry")):
            entry = float(scan_row["entry"])
            sl = float(scan_row.get("sl") or saran.stop_price(entry, atr))
            tp = float(scan_row.get("tp") or saran.target_price(entry, atr))
        else:
            support = _num(sr.get("support")) or close
            entry = float(pricing.round_tick(min(close, support * 1.015))) if close else 0.0
            if entry <= 0 and close:
                entry = float(pricing.round_tick(close))
            sl = float(saran.stop_price(entry, atr)) if entry > 0 else 0.0
            tp = float(saran.target_price(entry, atr)) if entry > 0 else 0.0

        score_pct = _num(sc.get("pct"))
        rel_pct = _num((scan_row or {}).get("rel_pct"))
        action, reason = _classify_watch_action(
            close or 0.0, score_pct, ind, entry, sl, rel_pct=rel_pct,
        )

        lots = 0
        if modal > 0 and entry > 0 and sl > 0 and entry > sl:
            risk_rp = modal * saran.BASE_RISK_PCT * scale
            risk_per_share = max(entry - sl, entry * 0.01)
            lots_risk = int(risk_rp // (risk_per_share * 100))
            lots_cap = int((modal * saran.MAX_POS_PCT * scale) // (entry * 100))
            lots = max(0, min(lots_risk, lots_cap))

        trig_all = alerts.check_triggers(ind)
        matched_trig = sorted(alerts.match_kinds(trig_all, alert_kinds)) if alert_kinds else []
        zones = alerts.buy_zones(ind)

        as_of = str(df.index[-1])[:10] if len(df.index) else "-"
        rows.append({
            "ticker": sym,
            "sector": sec_name,
            "syariah": is_sy,
            "price": close,
            "ret1d": ret1d,
            "action": action,
            "signal": action,
            "score_pct": score_pct,
            "rel_pct": rel_pct,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "lots": lots,
            "alerts": alert_kinds,
            "triggers": matched_trig,
            "zones": zones,
            "reason": reason,
            "reasons": [reason] if reason else [],
            "as_of": as_of,
            "stale": bool(df.attrs.get("stale", False)),
        })
    return rows


def evaluate_portfolio_rows(
    chat_id: int | str = DEFAULT_PROFILE_ID,
    *,
    market_regime: dict | None = None,
    data_mod=data,
    store_port_mod=store_portfolio,
    store_scan_mod=store_scan,
    tesis_mod=tesis,
) -> list[dict[str, Any]]:
    """Evaluasi posisi portofolio asli + status tesis (`GUGUR`/`MELEMAH`/`MENGUAT`/`TETAP`)."""
    positions = store_port_mod.real_positions(chat_id)
    if not positions:
        return []

    open_execs = store_port_mod.open_executions_by_ticker(chat_id)
    rel_map: dict[str, float] = {}
    try:
        for r in store_scan_mod.load_market_scan():
            sym = str(r.get("ticker") or "").strip().upper().removesuffix(".JK")
            rp = _num(r.get("rel_pct"))
            if sym and rp is not None:
                rel_map[sym] = rp
    except Exception:
        rel_map = {}

    now_d = marketcal.now_wib().date()
    out: list[dict[str, Any]] = []
    for p in positions:
        sym = str(p.get("ticker") or "").strip().upper().removesuffix(".JK")
        lots = int(p.get("lots") or 0)
        avg_px = float(p.get("avg_price") or 0.0)
        if not sym or lots <= 0 or avg_px <= 0:
            continue

        sec_name = str(p.get("sector") or "").strip() or universe.sector_of(sym) or "tidak diketahui"
        try:
            df = data_mod.fetch_history(sym, "1y")
        except Exception:
            df = None

        close, ret1d, low = _last_prev_close(df)
        ind = None
        atr = None
        if isinstance(df, pd.DataFrame) and len(df) >= 20:
            ind = analysis.compute_indicators(df)
            raw_atr = _num(ind.iloc[-1].get("atr14"))
            atr = raw_atr if (raw_atr and raw_atr > 0) else None

        exec_info = open_execs.get(sym)
        sl_stored = _num((exec_info or {}).get("sl"))
        tp_stored = _num((exec_info or {}).get("tp"))
        entry_date = str((exec_info or {}).get("trade_date") or p.get("updated_at") or "")[:10] or None

        eg = None
        if close and close > 0:
            eg = saran.build_exit_guidance(
                sym,
                avg_px,
                lots=lots,
                atr=atr,
                sl=sl_stored,
                tp=tp_stored,
                close=close,
                prev_close=(_num(df["close"].iloc[-2]) if isinstance(df, pd.DataFrame) and len(df) >= 2 else None),
                df=df,
                entry_date=entry_date,
                today=now_d,
            )

        ch = (eg or {}).get("chandelier") or {}
        ts = (eg or {}).get("time_stop") or {}
        arb = (eg or {}).get("arb_risk") or {}
        sc_out = (eg or {}).get("scale_out") or {}
        eff_sl = (eg or {}).get("effective_sl") or sl_stored or (saran.stop_price(avg_px, atr) if atr else None)
        sl_source = "eksekusi" if sl_stored is not None else ("atr" if eff_sl is not None else "-")
        cached_fund_sc = None
        try:
            cache_dir = getattr(fundamental, "CACHE_DIR", "temp/cache")
            raw_cached = kualitas.load_cached(cache_dir, sym, start_of_day_tag())
            if raw_cached:
                cached_fund_sc = _score_fund_dict(fundamental, raw_cached)
        except Exception:
            cached_fund_sc = None
        t_res = tesis_mod.evaluate_position(
            sym,
            avg_px,
            close,
            low=low,
            ind=ind,
            sl=eff_sl,
            tp=(eg or {}).get("tp_target") or tp_stored,
            trailing_sl=ch.get("stop"),
            trailing_hit=bool(ch.get("hit")),
            days_held=ts.get("days_held"),
            time_stop_flag=bool(ts.get("triggered")),
            pnl_net_pct=(eg or {}).get("pnl_net_pct"),
            rel_pct=rel_map.get(sym),
            fund=cached_fund_sc,
            market_regime=market_regime,
        )
        shares = lots * 100
        cost_rp = round(avg_px * shares)
        value_rp = round(close * shares) if (close and close > 0) else None
        pnl_rp = round((close - avg_px) * shares) if (close and close > 0) else None
        out.append({
            "ticker": sym,
            "sector": sec_name,
            "syariah": syariah.is_syariah(sym),
            "lots": lots,
            "avg_price": avg_px,
            "price": close,
            "ret1d": ret1d,
            "cost_rp": cost_rp,
            "value_rp": value_rp,
            "pnl_rp": pnl_rp,
            "pnl_net_pct": (eg or {}).get("pnl_net_pct"),
            "effective_sl": eff_sl,
            "sl_source": sl_source,
            "trailing_sl": ch.get("stop"),
            "tp": (eg or {}).get("tp_target") or tp_stored,
            "scale_out_tp1": sc_out.get("tp1"),
            "days_held": ts.get("days_held"),
            "arb_warning": bool(arb.get("risk")),
            "thesis": t_res["status"],
            "thesis_emoji": t_res["emoji"],
            "reasons": t_res["reasons"],
            "data_ok": t_res["data_ok"],
        })
    return out


def compute_portfolio_risk_summary(
    chat_id: int | str,
    port_rows: list[dict[str, Any]],
    *,
    data_mod=data,
    store_port_mod=store_portfolio,
    risk_mod=risk,
) -> dict[str, Any]:
    """Ringkas nilai, PnL Rp, risiko SL, VaR/ES historis, & batas risiko portofolio."""
    max_pos, max_sec = store_port_mod.get_risk_limits(chat_id)
    priced = [r for r in port_rows if r.get("value_rp") is not None and (r.get("price") or 0) > 0]
    unpriced_count = len(port_rows) - len(priced)
    total_value = sum(int(r.get("value_rp") or 0) for r in priced)
    priced_cost = sum(int(r.get("cost_rp") or 0) for r in priced)
    total_cost = sum(int(r.get("cost_rp") or 0) for r in port_rows)
    unrealized_pnl_rp = total_value - priced_cost if priced else None
    unrealized_pnl_pct = (
        round((unrealized_pnl_rp / priced_cost) * 100.0, 2)
        if (unrealized_pnl_rp is not None and priced_cost > 0)
        else None
    )

    sl_risk_rp = 0
    sl_known = False
    quotes: dict[str, dict[str, Any]] = {}
    positions: list[dict[str, Any]] = []
    cached_fn = getattr(data_mod, "fetch_cached_history", None)
    for r in port_rows:
        sym = r["ticker"]
        lots = int(r.get("lots") or 0)
        px = _num(r.get("price"))
        eff_sl = _num(r.get("effective_sl"))
        if px and px > 0:
            if eff_sl and eff_sl > 0:
                sl_known = True
                sl_risk_rp += round(max(px - eff_sl, 0.0) * lots * 100)
            rets = None
            try:
                df = cached_fn(sym, "1y") if cached_fn is not None else data_mod.fetch_history(sym, "1y")
                if isinstance(df, pd.DataFrame) and len(df) >= 25 and "close" in df.columns:
                    rets = df["close"].astype(float).pct_change().dropna()
            except Exception:
                rets = None
            quotes[sym] = {"close": px, "rets": rets}
        positions.append({"ticker": sym, "lots": lots, "sector": r.get("sector") or "tidak diketahui"})

    risk_res: dict[str, Any] = {}
    if positions and quotes:
        try:
            risk_res = risk_mod.portfolio_risk(
                positions, quotes, max_pos_pct=max_pos, max_sector_pct=max_sec
            )
        except Exception:
            risk_res = {}

    return {
        "n_positions": len(port_rows),
        "priced_count": len(priced),
        "unpriced_count": unpriced_count,
        "total_value": total_value,
        "total_cost": total_cost,
        "total_value_rp": total_value,
        "total_cost_rp": total_cost,
        "priced_cost": priced_cost,
        "unrealized_pnl_rp": unrealized_pnl_rp,
        "unrealized_pnl_pct": unrealized_pnl_pct,
        "total_pnl_rp": unrealized_pnl_rp,
        "total_pnl_pct": unrealized_pnl_pct,
        "sl_risk_rp": sl_risk_rp if sl_known else None,
        "sl_risk_pct": round(sl_risk_rp / total_value * 100.0, 2) if (sl_known and total_value > 0) else None,
        "risk_to_sl_rp": sl_risk_rp if sl_known else 0,
        "risk_to_sl_pct": round(sl_risk_rp / total_value * 100.0, 2) if (sl_known and total_value > 0) else None,
        "max_pos_pct": max_pos,
        "max_sector_pct": max_sec,
        "max_pos_limit_pct": round(max_pos * 100.0, 1),
        "max_sec_limit_pct": round(max_sec * 100.0, 1),
        "sectors": risk_res.get("sectors") or [],
        "hist_var95_pct": risk_res.get("hist_var95_pct"),
        "hist_var95_rp": risk_res.get("hist_var95_rp"),
        "hist_es95_rp": risk_res.get("hist_es95_rp"),
        "var95_rp": risk_res.get("hist_var95_rp"),
        "es95_rp": risk_res.get("hist_es95_rp"),
        "vol_ann": risk_res.get("vol_ann"),
        "warnings": risk_res.get("warnings") or [],
        "var_note": risk_res.get("var_note"),
    }


def detect_action_alerts(
    prev_state: dict[str, str],
    watch_rows: list[dict[str, Any]],
    port_rows: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Deteksi alert baru (transisi BUY/JUAL di watchlist atau GUGUR/MELEMAH di portofolio).

    Memperbarui `prev_state` in-place agar notifikasi tidak diulang selama status sama.
    """
    now_str = f"{marketcal.now_wib():%H:%M:%S}"
    alerts: list[dict[str, str]] = []

    for r in watch_rows:
        sym = r["ticker"]
        act = r.get("action") or "TIDAK TERSEDIA"
        if act == "TIDAK TERSEDIA":
            continue
        key = f"watch:{sym}"
        old = prev_state.get(key)
        prev_state[key] = act
        if old != act and act in ("BELI", "JUAL"):
            px = r.get("price")
            px_s = f"Rp{px:,.0f}" if px else "-"
            alerts.append({
                "time": now_str,
                "kind": act,
                "ticker": sym,
                "title": f"{act} {sym} ({px_s})",
                "message": f"{r.get('reason', '')} · Entry {r.get('entry') or '-'} / SL {r.get('sl') or '-'}",
            })

    for p in port_rows:
        sym = p["ticker"]
        st = p.get("thesis") or "TIDAK_TERSEDIA"
        if not p.get("data_ok"):
            continue
        key = f"port:{sym}"
        old = prev_state.get(key)
        prev_state[key] = st
        if old != st and st in (tesis.GUGUR, tesis.MELEMAH):
            px = p.get("price")
            px_s = f"Rp{px:,.0f}" if px else "-"
            why = "; ".join((p.get("reasons") or [])[:2])
            alerts.append({
                "time": now_str,
                "kind": f"TESIS {st}",
                "ticker": sym,
                "title": f"JUAL/TINJAU {sym} — Tesis {st} ({px_s})",
                "message": why or "Periksa posisi segera di Stockbit PC",
            })

    return alerts


def inspect_ticker_detail(
    ticker: str,
    modal: float,
    *,
    chat_id: int | str = DEFAULT_PROFILE_ID,
    period: str = "1y",
    market_regime: dict | None = None,
    include_bandar: bool = False,
    data_mod=data,
    fundamental_mod=fundamental,
    news_mod=news,
    cek_mod=cek,
    rencana_mod=rencana,
    store_port_mod=store_portfolio,
    store_scan_mod=store_scan,
    bandar_mod=bandar,
    banding_mod=banding,
    universe_mod=universe,
) -> dict[str, Any]:
    """Analisis mendalam satu emiten untuk tab Cek Emiten (BUY / SELL / TUNGGU / HINDARI)."""
    sym = str(ticker or "").strip().upper().removesuffix(".JK")
    if not sym:
        raise ValueError("Kode saham tidak boleh kosong.")

    per = period if period in VALID_CHART_PERIODS else "1y"
    df = data_mod.fetch_history(sym, per)
    if (not isinstance(df, pd.DataFrame) or len(df) < 25) and per != "1y":
        df = data_mod.fetch_history(sym, "1y")
    if not isinstance(df, pd.DataFrame) or len(df) < 25:
        raise ValueError(f"Data harga {sym} tidak tersedia.")

    ind = analysis.compute_indicators(df)
    sc = analysis.score_stock(ind)
    sr = analysis.support_resistance(df)
    snap = analysis.last_snapshot(ind)

    w_close = ind["close"].resample("W-FRI").last().dropna()
    weekly = analysis.weekly_trend(w_close.to_frame(name="close"), sc["pct"]) if len(w_close) >= 20 else None

    qual = df.attrs.get("quality")
    q_note = data_quality.plain_note(data_quality.summary_line(qual)) if qual else ""
    as_of = snap.get("as_of") or (str(df.index[-1])[:10] if len(df.index) else "-")
    res = {
        "ticker": sym,
        "df": df,
        "ind": ind,
        "score": sc,
        "sr": sr,
        "snap": snap,
        "weekly": weekly,
        "as_of": as_of,
        "stale": bool(df.attrs.get("stale", False)),
        "stale_note": str(df.attrs.get("stale_note") or ""),
        "quality_note": q_note,
    }

    fund, fund_raw = None, None
    try:
        fund_raw = _fetch_fund_raw(fundamental_mod, sym)
        fund = _score_fund_dict(fundamental_mod, fund_raw)
    except Exception:
        fund, fund_raw = None, None

    nsent = None
    try:
        items = news_mod.fetch_news(sym, limit=8)
        if items:
            nsent = news_mod.news_sentiment(items)
    except Exception:
        nsent = None

    close = float(snap["close"])
    val_avg20 = _num((ind["close"] * ind["volume"]).tail(20).mean()) if "volume" in ind.columns else None
    board = ""
    sec_name = ""
    try:
        board = universe_mod.board_of(sym) or ""
        sec_name = str((fund_raw or {}).get("sector") or "") or (universe_mod.sector_of(sym) or "")
    except Exception:
        board, sec_name = "", ""

    peers = None
    try:
        peers = banding_mod.sector_context(sym, sec_name or None)
    except Exception:
        peers = None

    rel_pct = None
    try:
        for sr_row in store_scan_mod.load_market_scan():
            if str(sr_row.get("ticker") or "").strip().upper().removesuffix(".JK") == sym:
                rel_pct = _num(sr_row.get("rel_pct"))
                break
    except Exception:
        rel_pct = None

    bandar_row = None
    if include_bandar:
        try:
            t_date = bandar_mod.last_trading_date()
            call_stale = getattr(bandar_mod, "call_with_stale", None)
            b_rows = (
                call_stale(bandar_mod.get_stock_summary, t_date, max_stocks=1000, until=sym)[0]
                if call_stale is not None
                else bandar_mod.get_stock_summary(t_date, max_stocks=1000, until=sym)
            )
            bandar_row = bandar_mod.find_row(b_rows, sym)
        except Exception:
            bandar_row = None

    cek_res = cek_mod.build(
        res=res,
        fund=fund,
        fund_raw=fund_raw,
        nsent=nsent,
        bandar_row=bandar_row,
        agenda=None,
        agenda_days=None,
        peers=peers,
        value_avg20=val_avg20,
        board=board or None,
    )

    last = ind.iloc[-1]
    raw_atr = _num(last.get("atr14"))
    atr = raw_atr if (raw_atr and raw_atr > 0) else None
    cand = None
    try:
        cand = rencana_mod.enrich(
            {"ticker": sym, "price": close, "sector": sec_name, "board": board, "rel_pct": rel_pct},
            {"res": res, "fund": fund, "fund_raw": fund_raw, "nsent": nsent},
            require_rel=False,
        )
    except Exception:
        cand = None
    if cand is None:
        cand = {
            "ticker": sym,
            "price": close,
            "atr": atr,
            "sma20": _num(snap.get("sma20")),
            "sector": sec_name,
            "board": board,
            "rel_pct": rel_pct,
            "fund": fund,
            "news": nsent,
        }
    plan = None
    if modal and modal > 0:
        try:
            plan = rencana_mod.budget_plan(
                cand, modal, modal=modal, chat_id=chat_id, market_regime=market_regime,
            )
        except Exception:
            plan = None

    scale_out = None
    arb_risk = None
    if plan and _num(plan.get("entry")):
        try:
            lots_est = max(2, int(plan.get("lots") or 2))
            scale_out = saran.scale_out_plan(
                float(plan["entry"]), atr or float(plan["entry"]) * 0.03, lots_est, tp_full=plan.get("tp"),
            )
            prev_c = _num(df["close"].iloc[-2]) if len(df) >= 2 else None
            arb_risk = saran.arb_gap_risk(
                float(plan["entry"]), float(plan.get("sl") or plan["entry"]), prev_close=prev_c,
            )
        except Exception:
            scale_out, arb_risk = None, None

    advanced = {
        "rel_pct": rel_pct,
        "cmf20": _num(last.get("cmf20")) if "cmf20" in ind.columns else None,
        "rsi_divergence": analysis.detect_rsi_divergence(ind),
        "poc": analysis.volume_profile_poc(ind),
        "candle_patterns": analysis.detect_candle_pattern(ind),
        "expected_move": analysis.expected_move_range(ind),
        "tp_sl_odds": analysis.historical_tp_sl_odds(ind),
    }

    positions = store_port_mod.real_positions(chat_id)
    held = next((p for p in positions if str(p.get("ticker")).upper() == sym and int(p.get("lots") or 0) > 0), None)
    forensic_pen = float((fund or {}).get("forensic_penalty") or 0.0)
    base_fund_pct = (
        fund.get("base_pct")
        if (fund and "base_pct" in fund and forensic_pen > 0)
        else (fund.get("pct") if fund else None)
    )
    power_combo = powerscore.combine(
        sc["pct"],
        base_fund_pct,
        nsent.get("pct") if nsent else None,
        forensic_penalty=forensic_pen,
    )
    power_text = powerscore.judge(sc["pct"], fund, nsent, snap)

    return {
        "ok": True,
        "ticker": sym,
        "name": universe_mod.name_of(sym) or "",
        "sector": sec_name,
        "board": board,
        "syariah": syariah.is_syariah(sym),
        "period": per,
        "quality_note": res.get("quality_note") or "",
        "res": res,
        "fund": fund,
        "fund_raw": fund_raw,
        "news": nsent,
        "cek": cek_res,
        "plan": plan,
        "scale_out": scale_out,
        "arb_risk": arb_risk,
        "advanced": advanced,
        "powerscore": power_combo,
        "power_text": power_text,
        "held": held,
        "generated_at": f"{marketcal.now_wib():%d %b %Y %H:%M:%S} WIB",
        "delay_note": DELAY_DISCLOSURE,
    }


def load_sinyal_rows(
    signal_filter: str = "ALL",
    *,
    quality_only: bool = False,
    syariah_only: bool = False,
    search: str = "",
    sector: str = "",
    limit: int = 200,
    store_scan_mod=store_scan,
    fundamental_mod=fundamental,
    kualitas_mod=kualitas,
) -> dict[str, Any]:
    """Muat snapshot `/scan` & saring sinyal (`ALL`/`BELI`/`JUAL`/`TAHAN`, syariah, sektor, cari)."""
    sig = str(signal_filter or "ALL").strip().upper()
    arg = None if sig == "ALL" else sig
    rows = store_scan_mod.load_market_scan(arg)
    meta = store_scan_mod.market_scan_meta()
    counts = store_scan_mod.market_scan_counts()

    q_txt = str(search or "").strip().upper()
    sec_filter = str(sector or "").strip()
    if sec_filter.upper() in ("ALL", "SEMUA"):
        sec_filter = ""

    sectors_seen: set[str] = set()
    out_rows: list[dict[str, Any]] = []
    matched = 0
    today_tag = start_of_day_tag()
    cache_dir = getattr(fundamental_mod, "CACHE_DIR", "temp/cache")

    for r in rows:
        sym = str(r.get("ticker") or "").strip().upper().removesuffix(".JK")
        if not sym:
            continue
        sec_name = str(r.get("sector") or "").strip() or universe.sector_of(sym) or ""
        if sec_name:
            sectors_seen.add(sec_name)
        is_sy = syariah.is_syariah(sym)
        if syariah_only and not is_sy:
            continue
        if sec_filter and sec_name != sec_filter:
            continue
        nm = str(r.get("name") or "") or (universe.name_of(sym) or "")
        if q_txt and q_txt not in sym and q_txt not in nm.upper():
            continue

        kval = None
        if quality_only or sig == "BELI":
            try:
                cached = kualitas_mod.load_cached(cache_dir, sym, today_tag)
            except Exception:
                cached = None
            if cached is not None:
                kval = kualitas_mod.judge(cached)
            elif quality_only:
                try:
                    raw = _fetch_fund_raw(fundamental_mod, sym)
                    kval = kualitas_mod.judge(raw)
                    if raw:
                        kualitas_mod.save_cached(cache_dir, sym, today_tag, raw)
                except Exception:
                    kval = None
        if quality_only and not kualitas_mod.passes(kval):
            continue
        matched += 1
        if len(out_rows) < limit:
            out_rows.append({
                **r,
                "ticker": sym,
                "name": nm,
                "sector": sec_name,
                "syariah": is_sy,
                "quality_mark": kualitas_mod.mark(kval) if kval is not None else "❔",
                "quality_ok": kualitas_mod.passes(kval) if kval is not None else None,
            })

    return {
        "rows": out_rows,
        "total_matched": matched,
        "sectors": sorted(sectors_seen),
        "meta": meta,
        "counts": counts,
        "signal_filter": sig,
        "quality_only": quality_only,
        "syariah_only": syariah_only,
    }


def run_full_market_scan(
    *,
    force: bool = True,
    screener_mod=screener,
) -> dict[str, Any]:
    """Jalankan scan penuh seluruh pasar BEI (~944 emiten) ke snapshot SQLite."""
    return screener_mod.scan(force=force)


def build_paket_picks(
    modal: float,
    *,
    chat_id: int | str = DEFAULT_PROFILE_ID,
    max_plans: int = 5,
    market_regime: dict | None = None,
    syariah_only: bool = False,
    store_scan_mod=store_scan,
    rencana_mod=rencana,
    fundamental_mod=fundamental,
    news_mod=news,
) -> dict[str, Any]:
    """Susun paket beli multi-emiten siap eksekusi Stockbit PC dari snapshot `/scan`."""
    rows = store_scan_mod.load_market_scan("BELI")
    meta = store_scan_mod.market_scan_meta()
    if syariah_only:
        rows = syariah.filter_rows(rows, syariah_only=True)
    if not rows:
        label = "BELI syariah" if syariah_only else "BELI"
        return {
            "ok": False,
            "reason": f"Snapshot /scan {label} kosong. Jalankan Scan BEI dulu.",
            "plans": [],
            "meta": meta,
        }

    pool, stats = rencana_mod.prefilter(rows)
    enriched: list[dict] = []
    for c in pool[:10]:
        sym = c["ticker"]
        fund, fund_raw, nsent = None, None, None
        try:
            fund_raw = _fetch_fund_raw(fundamental_mod, sym)
            fund = _score_fund_dict(fundamental_mod, fund_raw)
        except Exception:
            fund, fund_raw = None, None
        try:
            items = news_mod.fetch_news(sym, limit=5)
            if items:
                nsent = news_mod.news_sentiment(items)
        except Exception:
            nsent = None
        item = rencana_mod.enrich(
            c,
            {
                "res": {"ticker": sym, "snap": {"close": c.get("price")}},
                "fund": fund,
                "fund_raw": fund_raw,
                "nsent": nsent,
            },
        )
        if item is not None:
            enriched.append(item)

    final = rencana_mod.finalize(
        enriched,
        modal,
        max_plans=max_plans,
        market_regime=market_regime,
        chat_id=chat_id,
    )
    picks = list(final.get("picks") or [])
    for p in picks:
        p.setdefault("syariah", syariah.is_syariah(p.get("ticker", "")))
    total_alloc = sum(int(p.get("buy_cost") or p.get("value") or 0) for p in picks)
    total_risk = sum(int(p.get("real_risk") or 0) for p in picks)
    return {
        "ok": bool(picks),
        "plans": picks,
        "skipped": [],
        "rejected": final.get("rejected"),
        "stats": stats,
        "meta": meta,
        "summary": final,
        "modal": float(modal or 0.0),
        "total_alloc_rp": total_alloc,
        "cash_left_rp": max(0, round(float(modal or 0.0) - total_alloc)),
        "total_risk_rp": total_risk,
        "scale": final.get("scale", 1.0),
        "syariah_only": syariah_only,
    }


def _collect_journal_context(
    execs: list[dict[str, Any]],
    *,
    data_mod=data,
    fetch_bench: bool = True,
    include_mae_mfe: bool = True,
    max_tickers: int = 15,
) -> tuple[dict[str, pd.DataFrame], pd.Series | None]:
    has_sell = any(str(e.get("side") or "").upper() == "JUAL" for e in execs)
    if not has_sell:
        return {}, None

    bench_close = None
    if fetch_bench:
        try:
            cached_fn = getattr(data_mod, "fetch_cached_history", None)
            bdf = cached_fn("^JKSE", "1y", raw_symbol=True) if cached_fn is not None else None
            if bdf is None or getattr(bdf, "empty", True):
                bdf = data_mod.fetch_history("^JKSE", "1y", raw_symbol=True)
            if isinstance(bdf, pd.DataFrame) and not bdf.empty:
                col = next((c for c in bdf.columns if str(c).lower() == "close"), None)
                if col is not None:
                    bench_close = bdf[col].dropna()
        except Exception:
            bench_close = None

    bars_by_ticker: dict[str, pd.DataFrame] = {}
    if include_mae_mfe:
        cached_fn = getattr(data_mod, "fetch_cached_history", None)
        seen: set[str] = set()
        for e in execs:
            sym = str(e.get("ticker") or "").strip().upper().removesuffix(".JK")
            if not sym or sym in seen:
                continue
            seen.add(sym)
            if len(seen) > max_tickers:
                break
            try:
                df = cached_fn(sym, "1y") if cached_fn is not None else data_mod.fetch_history(sym, "1y")
                if isinstance(df, pd.DataFrame) and not df.empty:
                    bars_by_ticker[sym] = df
            except Exception:
                continue
    return bars_by_ticker, bench_close


def build_journal_summary(
    chat_id: int | str = DEFAULT_PROFILE_ID,
    *,
    limit: int = 50,
    data_mod=data,
    fetch_bench: bool = True,
    include_mae_mfe: bool = True,
    store_port_mod=store_portfolio,
    jurnal_mod=jurnal,
) -> dict[str, Any]:
    """Ringkasan jurnal eksekusi manual Stockbit (win rate, slippage, MAE/MFE, alpha)."""
    execs = store_port_mod.real_executions(chat_id, limit=limit)
    if not execs:
        return {
            "ok": False,
            "execs": [],
            "kpis": {},
            "summary_text": "Belum ada catatan eksekusi di profil ini. Catat BELI/JUAL di tab Portofolio.",
            "history_text": "",
        }
    summ = jurnal_mod.summarize_executions(execs)
    paper_sum = jurnal_mod.summarize_paper_realized(store_port_mod.get_paper(chat_id))
    open_real_count = len(store_port_mod.real_positions(chat_id))
    bars_by_ticker, bench_close = _collect_journal_context(
        execs,
        data_mod=data_mod,
        fetch_bench=fetch_bench,
        include_mae_mfe=include_mae_mfe,
    )
    diag = jurnal_mod.jurnal_diagnostics(
        execs, bars_by_ticker=bars_by_ticker, bench_close=bench_close,
    )
    summary_text = jurnal_mod.format_journal_report(
        summ,
        paper_sum,
        open_real_count=open_real_count,
        diagnostics=diag,
    )
    history_text = jurnal_mod.format_journal_history(execs[:15])
    kpis = {
        "n_exec": summ.get("n_exec", len(execs)),
        "n_buys": summ.get("n_buys", 0),
        "n_sells": summ.get("n_sells", 0),
        "n_closed": summ.get("n_closed", 0),
        "win_rate_pct": summ.get("win_rate_pct"),
        "profit_factor": summ.get("profit_factor"),
        "total_net_pnl_rp": summ.get("total_net_pnl_rp", 0),
        "avg_slippage_pct": summ.get("avg_slippage_pct"),
        "avg_r_multiple": diag.get("avg_r_multiple"),
        "avg_mae_pct": diag.get("avg_mae_pct"),
        "avg_mfe_pct": diag.get("avg_mfe_pct"),
        "mean_alpha_pct": diag.get("mean_alpha_pct"),
        "win_vs_bench_pct": diag.get("win_vs_bench_pct"),
    }
    return {
        "ok": True,
        "execs": execs,
        "summary": summ,
        "diag": diag,
        "kpis": kpis,
        "summary_text": summary_text,
        "history_text": history_text,
    }


def start_of_day_tag() -> str:
    return dt.date.today().isoformat()
