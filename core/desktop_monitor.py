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

import datetime as dt
import math
from typing import Any

import pandas as pd

from core import (
    analysis,
    bandar,
    cek,
    data,
    fundamental,
    jurnal,
    kualitas,
    marketcal,
    news,
    pasar,
    pricing,
    regime,
    rencana,
    saran,
    screener,
    store_portfolio,
    store_scan,
    tesis,
)

DEFAULT_PROFILE_ID = "desktop"
DELAY_DISCLOSURE = (
    "Sumber gratis (Yahoo Finance .JK) — harga jam bursa umumnya delay ±10-15 menit; "
    "konfirmasi bid/offer langsung di Stockbit PC sebelum order."
)


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


def fetch_market_pulse(
    *,
    include_idx: bool = True,
    include_news: bool = True,
    data_mod=data,
    store_scan_mod=store_scan,
    bandar_mod=bandar,
    news_mod=news,
) -> dict[str, Any]:
    """Kumpulkan kondisi pasar BEI terkini (IHSG, rezim, breadth, sektor, berita)."""
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

    index_rows, index_error = None, None
    if include_idx:
        idx_exc = getattr(bandar_mod, "IDXTidakTersedia", bandar.IDXTidakTersedia)
        try:
            index_rows = bandar_mod.get_index_summary()
        except idx_exc:
            index_error = "data BEI (idx.co.id) sedang diblokir Cloudflare"
        except Exception:
            index_error = "gagal membaca indeks sektor BEI"
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
        news_items=news_items,
        news_sent=news_sent,
        as_of_label=f"{now:%d %b %Y %H:%M:%S} WIB",
    )
    report["delay_note"] = DELAY_DISCLOSURE
    report["market_open"] = marketcal.market_open(now)
    return report


def _classify_watch_action(
    close: float,
    score_pct: float | None,
    ind: pd.DataFrame,
    entry: float,
    sl: float,
) -> tuple[str, str]:
    """Tentukan saran ringkas BELI / SIAP BELI / JUAL / TAHAN dari indikator & level."""
    last = ind.iloc[-1]
    sma20 = _num(last.get("sma20")) if "sma20" in ind.columns else None
    sma50 = _num(last.get("sma50")) if "sma50" in ind.columns else None
    rsi = _num(last.get("rsi14")) if "rsi14" in ind.columns else None

    if score_pct is not None and score_pct <= -25:
        return "JUAL", f"skor teknikal lemah ({score_pct:+.0f}%)"
    if sma20 and sma50 and close < sma20 and sma20 < sma50 and (score_pct or 0) < 0:
        return "JUAL", "harga di bawah MA20 & MA20 < MA50"
    if close <= sl:
        return "JUAL", f"harga menyentuh area stop ({sl:,.0f})"

    if score_pct is not None and score_pct >= 25:
        if entry > 0 and close <= entry * 1.02:
            return "BELI", f"tren kuat ({score_pct:+.0f}%) & dekat area entry"
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
        try:
            df = data_mod.fetch_history(sym, "6mo")
        except Exception:
            df = None

        if not isinstance(df, pd.DataFrame) or len(df) < 25:
            rows.append({
                "ticker": sym,
                "price": None,
                "ret1d": None,
                "action": "TIDAK TERSEDIA",
                "score_pct": None,
                "entry": None,
                "sl": None,
                "tp": None,
                "lots": 0,
                "reason": "data harga tidak tersedia",
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
        action, reason = _classify_watch_action(close or 0.0, score_pct, ind, entry, sl)

        lots = 0
        if modal > 0 and entry > 0 and sl > 0 and entry > sl:
            risk_rp = modal * saran.BASE_RISK_PCT * scale
            risk_per_share = max(entry - sl, entry * 0.01)
            lots_risk = int(risk_rp // (risk_per_share * 100))
            lots_cap = int((modal * 0.25 * scale) // (entry * 100))
            lots = max(0, min(lots_risk, lots_cap))

        as_of = str(df.index[-1])[:10] if len(df.index) else "-"
        rows.append({
            "ticker": sym,
            "price": close,
            "ret1d": ret1d,
            "action": action,
            "score_pct": score_pct,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "lots": lots,
            "reason": reason,
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

        try:
            df = data_mod.fetch_history(sym, "6mo")
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
        eff_sl = (eg or {}).get("effective_sl") or sl_stored or (saran.stop_price(avg_px, atr) if atr else None)
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
            market_regime=market_regime,
        )
        out.append({
            "ticker": sym,
            "lots": lots,
            "avg_price": avg_px,
            "price": close,
            "ret1d": ret1d,
            "pnl_net_pct": (eg or {}).get("pnl_net_pct"),
            "effective_sl": eff_sl,
            "trailing_sl": ch.get("stop"),
            "tp": (eg or {}).get("tp_target") or tp_stored,
            "thesis": t_res["status"],
            "thesis_emoji": t_res["emoji"],
            "reasons": t_res["reasons"],
            "data_ok": t_res["data_ok"],
        })
    return out


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
    data_mod=data,
    fundamental_mod=fundamental,
    news_mod=news,
    cek_mod=cek,
    rencana_mod=rencana,
    store_port_mod=store_portfolio,
) -> dict[str, Any]:
    """Analisis mendalam satu emiten untuk tab Cek Emiten (BUY / SELL / TUNGGU / HINDARI)."""
    sym = str(ticker or "").strip().upper().removesuffix(".JK")
    if not sym:
        raise ValueError("Kode saham tidak boleh kosong.")

    df = data_mod.fetch_history(sym, "1y")
    if not isinstance(df, pd.DataFrame) or len(df) < 25:
        raise ValueError(f"Data harga {sym} tidak tersedia.")

    ind = analysis.compute_indicators(df)
    sc = analysis.score_stock(ind)
    sr = analysis.support_resistance(df)
    snap = analysis.last_snapshot(ind)

    w_close = ind["close"].resample("W-FRI").last().dropna()
    weekly = analysis.weekly_trend(w_close.to_frame(name="close"), sc["pct"]) if len(w_close) >= 20 else None

    res = {
        "ticker": sym,
        "df": df,
        "ind": ind,
        "score": sc,
        "sr": sr,
        "snap": snap,
        "weekly": weekly,
        "as_of": str(df.index[-1])[:10],
        "stale": bool(df.attrs.get("stale", False)),
    }

    fund, fund_raw = None, None
    try:
        fund_raw = fundamental_mod.fetch_fundamental(sym)
        if fund_raw:
            fund = fundamental_mod.score_fundamental(fund_raw)
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
    cek_res = cek_mod.build(
        res=res,
        fund=fund,
        fund_raw=fund_raw,
        nsent=nsent,
        bandar_row=None,
        agenda=None,
        agenda_days=None,
        peers=None,
        value_avg20=val_avg20,
        board=None,
    )

    last = ind.iloc[-1]
    raw_atr = _num(last.get("atr14"))
    atr = raw_atr if (raw_atr and raw_atr > 0) else None
    cand = {
        "ticker": sym,
        "close": close,
        "atr": atr,
        "support": _num(sr.get("support")),
        "resistance": _num(sr.get("resistance")),
        "tech_pct": _num(sc.get("pct")),
        "fund": fund,
        "news": nsent,
    }
    plan = None
    if modal and modal > 0:
        try:
            plan = rencana_mod.budget_plan(cand, modal, modal=modal, chat_id=chat_id)
        except Exception:
            plan = None

    positions = store_port_mod.real_positions(chat_id)
    held = next((p for p in positions if str(p.get("ticker")).upper() == sym and int(p.get("lots") or 0) > 0), None)

    return {
        "ticker": sym,
        "res": res,
        "fund": fund,
        "fund_raw": fund_raw,
        "news": nsent,
        "cek": cek_res,
        "plan": plan,
        "held": held,
        "generated_at": f"{marketcal.now_wib():%d %b %Y %H:%M:%S} WIB",
        "delay_note": DELAY_DISCLOSURE,
    }


def load_sinyal_rows(
    signal_filter: str = "ALL",
    *,
    quality_only: bool = False,
    limit: int = 80,
    store_scan_mod=store_scan,
    fundamental_mod=fundamental,
    kualitas_mod=kualitas,
) -> dict[str, Any]:
    """Muat snapshot `/scan` & saring sinyal (`ALL` / `BELI` / `JUAL` / `TAHAN`)."""
    sig = str(signal_filter or "ALL").strip().upper()
    arg = None if sig == "ALL" else sig
    rows = store_scan_mod.load_market_scan(arg)
    meta = store_scan_mod.market_scan_meta()
    counts = store_scan_mod.market_scan_counts()

    out_rows: list[dict[str, Any]] = []
    today_tag = start_of_day_tag()
    cache_dir = getattr(fundamental_mod, "CACHE_DIR", "temp/cache")
    for r in rows:
        sym = str(r.get("ticker") or "").strip().upper().removesuffix(".JK")
        if not sym:
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
                    raw = fundamental_mod.fetch_fundamental(sym)
                    kval = kualitas_mod.judge(raw)
                    if raw:
                        kualitas_mod.save_cached(cache_dir, sym, today_tag, raw)
                except Exception:
                    kval = None
        if quality_only and not kualitas_mod.passes(kval):
            continue
        out_rows.append({
            **r,
            "ticker": sym,
            "quality_mark": kualitas_mod.mark(kval) if kval is not None else "❔",
            "quality_ok": kualitas_mod.passes(kval) if kval is not None else None,
        })
        if len(out_rows) >= limit:
            break

    return {
        "rows": out_rows,
        "meta": meta,
        "counts": counts,
        "signal_filter": sig,
        "quality_only": quality_only,
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
    store_scan_mod=store_scan,
    rencana_mod=rencana,
    fundamental_mod=fundamental,
    news_mod=news,
) -> dict[str, Any]:
    """Susun paket beli multi-emiten siap eksekusi Stockbit PC dari snapshot `/scan`."""
    rows = store_scan_mod.load_market_scan("BELI")
    meta = store_scan_mod.market_scan_meta()
    if not rows:
        return {"ok": False, "reason": "Snapshot /scan BELI kosong. Jalankan Scan BEI dulu.", "plans": [], "meta": meta}

    pool, stats = rencana_mod.prefilter(rows)
    enriched: list[dict] = []
    for c in pool[:8]:
        sym = c["ticker"]
        fund, fund_raw, nsent = None, None, None
        try:
            fund_raw = fundamental_mod.fetch_fundamental(sym)
            if fund_raw:
                fund = fundamental_mod.score_fundamental(fund_raw)
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
    picks = final.get("picks") or []
    return {
        "ok": bool(picks),
        "plans": picks,
        "skipped": [],
        "rejected": final.get("rejected"),
        "stats": stats,
        "meta": meta,
        "summary": final,
    }


def build_journal_summary(
    chat_id: int | str = DEFAULT_PROFILE_ID,
    *,
    limit: int = 50,
    store_port_mod=store_portfolio,
    jurnal_mod=jurnal,
) -> dict[str, Any]:
    """Ringkasan jurnal eksekusi manual Stockbit (win rate, slippage, MAE/MFE, alpha)."""
    execs = store_port_mod.real_executions(chat_id, limit=limit)
    if not execs:
        return {
            "ok": False,
            "execs": [],
            "summary_text": "Belum ada catatan eksekusi di profil ini. Catat BELI/JUAL di tab Portofolio.",
            "history_text": "",
        }
    summ = jurnal_mod.summarize_executions(execs)
    paper_sum = jurnal_mod.summarize_paper_realized(store_port_mod.get_paper(chat_id))
    open_real_count = len(store_port_mod.real_positions(chat_id))
    diag = jurnal_mod.jurnal_diagnostics(execs)
    summary_text = jurnal_mod.format_journal_report(
        summ,
        paper_sum,
        open_real_count=open_real_count,
        diagnostics=diag,
    )
    history_text = jurnal_mod.format_journal_history(execs[:15])
    return {
        "ok": True,
        "execs": execs,
        "summary": summ,
        "diag": diag,
        "summary_text": summary_text,
        "history_text": history_text,
    }


def start_of_day_tag() -> str:
    return dt.date.today().isoformat()
