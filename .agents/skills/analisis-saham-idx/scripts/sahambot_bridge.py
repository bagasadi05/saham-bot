#!/usr/bin/env python3
"""
Bridge CLI menghubungkan skill `analisis-saham-idx` dengan repositori lokal `saham-bot`.
Menyediakan akses terstruktur (JSON) ke modul domain `saham-bot/core/`:
- info      : Metadata emiten (944 emiten BEI), papan, sektor, status Syariah DES OJK, aturan ARA/ARB 2026.
- syariah   : Cek status Syariah (DES OJK) atau daftar ticker syariah lokal.
- fetch     : Ambil OHLCV + audit kualitas + teknikal + S/R + fundamental + forensik kas (via Yahoo/cache saham-bot).
- regime    : Deteksi rezim pasar IHSG (^JKSE SMA50 vs SMA200) untuk penskalaan risiko.
- arb-check : Uji risiko celah ARB asimetris 7% (Kep-00158/BEI/10-2026) antara Entry dan SL.
"""

import argparse
import json
import os
import sys
from pathlib import Path


def _find_sahambot_dir() -> Path:
    env_dir = os.environ.get("SAHAM_BOT_DIR")
    candidates = []
    if env_dir:
        candidates.append(Path(env_dir))
    here = Path(__file__).resolve()
    for parent in [Path.cwd(), here.parent, here.parent.parent, here.parent.parent.parent, here.parent.parent.parent.parent]:
        candidates.append(parent / "saham-bot")
    for c in candidates:
        if (c / "core" / "universe.py").is_file():
            return c
    raise FileNotFoundError(
        "Direktori repo `saham-bot` tidak ditemukan. Set env SAHAM_BOT_DIR atau clone repo di root proyek."
    )


BOT_DIR = _find_sahambot_dir()
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

os.environ.setdefault(
    "HTTP_USER_AGENT",
    "Mozilla/5.0 (compatible; Google-Agent; +https://developers.google.com/crawling/docs/crawlers-fetchers/google-agent)",
)

import core.syariah as syariah  # noqa: E402
import core.universe as universe  # noqa: E402
import core.pricing as pricing  # noqa: E402


def _clean_ticker(t: str) -> str:
    return t.strip().upper().replace(".JK", "")


def _board_mode(board: str | None, fca_flag: bool = False) -> str:
    if fca_flag:
        return "fca"
    if board and "pemantauan" in str(board).lower():
        return "fca"
    return "regular"


def _col_last(df, name: str):
    for col in df.columns:
        cname = col[0] if isinstance(col, tuple) else col
        if str(cname).lower() == name.lower():
            val = df[col].iloc[-1]
            return float(val) if val is not None else None
    return None


def cmd_info(args):
    code = _clean_ticker(args.ticker)
    board = universe.board_of(code)
    bmode = _board_mode(board)
    result = {
        "ticker": code,
        "name": universe.name_of(code),
        "sector": universe.sector_of(code),
        "board": board,
        "is_syariah": syariah.is_syariah(code),
        "syariah_meta": syariah.metadata(),
        "universe_count": len(universe.codes()),
        "auto_reject_status": pricing.auto_reject_status(),
    }
    if args.price is not None:
        p = float(args.price)
        result["price_rules"] = {
            "price": p,
            "board_mode": bmode,
            "tick_size": pricing.tick_size(p),
            "ara_pct": pricing.auto_reject_pct(p, None),
            "arb_pct": pricing.arb_pct(p, board=bmode),
            "arb_floor_price": pricing.arb_floor_price(p, board=bmode),
            "min_board_floor": pricing.min_board_floor(board=bmode),
        }
    print(json.dumps(result, indent=2, default=str))


def cmd_syariah(args):
    if args.list:
        tickers = sorted(syariah.syariah_tickers())
        print(json.dumps({
            "count": len(tickers),
            "metadata": syariah.metadata(),
            "tickers": tickers[:args.limit] if args.limit else tickers,
        }, indent=2, default=str))
        return
    if not args.ticker:
        print(json.dumps({"error": "Berikan ticker atau --list"}, indent=2))
        sys.exit(1)
    code = _clean_ticker(args.ticker)
    print(json.dumps({
        "ticker": code,
        "is_syariah": syariah.is_syariah(code),
        "badge": syariah.syariah_badge(code),
        "metadata": syariah.metadata(),
    }, indent=2, default=str))


def cmd_arb_check(args):
    bmode = "fca" if args.fca else "regular"
    entry = float(args.entry)
    sl = float(args.sl)
    gap = pricing.check_arb_gap_risk(entry, sl, board=bmode)
    print(json.dumps({
        "entry": entry,
        "sl": sl,
        "board_mode": bmode,
        "arb_pct": pricing.arb_pct(entry, board=bmode),
        "arb_floor_from_entry": pricing.arb_floor_price(entry, board=bmode),
        "arb_gap_risk": gap,
    }, indent=2, default=str))


def cmd_regime(_args):
    import core.regime as regime
    res = regime.fetch_ihsg_regime()
    print(json.dumps(res, indent=2, default=str))


def cmd_fetch(args):
    import core.data as data
    import core.data_quality as dq
    import core.analysis as analysis
    import core.fundamental as fundamental
    import core.saran as saran

    code = _clean_ticker(args.ticker)
    yf_ticker = data.normalize_ticker(code)
    board = universe.board_of(code)
    bmode = _board_mode(board)
    df = data.fetch_history(yf_ticker, period=args.period)
    out = {
        "ticker": code,
        "yf_ticker": yf_ticker,
        "name": universe.name_of(code),
        "sector": universe.sector_of(code),
        "board": board,
        "is_syariah": syariah.is_syariah(code),
    }

    if df is not None and not df.empty:
        qual = dq.check(df)
        prov = dq.format_provenance(df)
        an = analysis.analyze(code, df)
        snap = an.get("snap", {}) if isinstance(an, dict) else {}
        ind = an.get("ind")
        close = float(snap.get("close") or _col_last(df, "close") or 0.0)
        atr = 0.0
        if ind is not None and hasattr(ind, "columns"):
            for col in ind.columns:
                if str(col).lower().startswith("atr"):
                    val = ind[col].iloc[-1]
                    if val is not None and val == val:
                        atr = float(val)
                        break
        from datetime import datetime, timezone, timedelta
        _now_wib = datetime.now(timezone(timedelta(hours=7)))
        _last_dt = str(df.index[-1])[:10]
        _is_intraday = (_last_dt == _now_wib.date().isoformat()) and (_now_wib.hour < 16 or (_now_wib.hour == 16 and _now_wib.minute < 15))
        out["ohlcv"] = {
            "rows": len(df),
            "is_intraday_bar": _is_intraday,
            "last_bar_date": str(df.index[-1]),
            "close": close,
            "open": _col_last(df, "open"),
            "high": _col_last(df, "high"),
            "low": _col_last(df, "low"),
            "volume": _col_last(df, "volume"),
            "provenance": prov,
            "quality_flags": qual,
        }
        sma20 = snap.get("sma20")
        sma50 = snap.get("sma50")
        sma200 = snap.get("sma200")
        ret5d = snap.get("ret5d")
        ret20d = snap.get("ret20d")
        trend_warnings = []
        if sma50 is not None and sma200 is not None and close < sma50 and sma50 < sma200:
            trend_warnings.append("FALLING_KNIFE: harga < MA50 dan MA50 < MA200 (death cross)")
        elif sma50 is not None and close < sma50:
            trend_warnings.append("BELOW_MA50: harga di bawah MA50")
        if ret20d is not None and ret5d is not None and ret20d < -10.0 and ret5d > 8.0:
            trend_warnings.append(f"DEAD_CAT_BOUNCE: 20d {ret20d}%, 5d +{ret5d}%")
        entry_pullback = None
        if sma20 is not None and sma20 > 0 and close > sma20 * 1.02:
            pb = pricing.round_tick(sma20)
            if 0 < pb < close:
                entry_pullback = pb

        out["technical"] = {
            "as_of": snap.get("as_of"),
            "ret1d_pct": snap.get("ret1d"),
            "ret5d_pct": ret5d,
            "ret20d_pct": ret20d,
            "rsi14": snap.get("rsi"),
            "macd_hist": snap.get("macd_hist"),
            "sma20": snap.get("sma20"),
            "sma50": snap.get("sma50"),
            "sma200": snap.get("sma200"),
            "high20": snap.get("high20"),
            "low20": snap.get("low20"),
            "atr14": round(atr, 2) if atr else None,
            "support_resistance": an.get("sr"),
            "entry_pullback_ma20": entry_pullback,
            "entry_fill_odds": (
                {
                    "target_entry": entry_pullback or (an.get("sr") or {}).get("swing_support"),
                    "dist_atr": round((close - (entry_pullback or (an.get("sr") or {}).get("swing_support"))) / atr, 2),
                    "probability": (
                        "TINGGI" if (close - (entry_pullback or (an.get("sr") or {}).get("swing_support"))) <= 0.5 * atr
                        else ("SEDANG" if (close - (entry_pullback or (an.get("sr") or {}).get("swing_support"))) <= 1.2 * atr else "RENDAH")
                    ),
                    "expiry_days": 5,
                }
                if atr > 0 and (entry_pullback or (an.get("sr") or {}).get("swing_support"))
                else None
            ),
            "trend_warnings": trend_warnings,
            "volume_profile_poc_60d": analysis.volume_profile_poc(df, window=60) if hasattr(analysis, "volume_profile_poc") else None,
            "score": an.get("score"),
        }
        if atr > 0 and close > 0:
            sl_atr = saran.stop_price(close, atr)
            tp1 = pricing.round_tick(close + 1.5 * atr)
            tp2 = saran.target_price(close, atr)
            out["saran_levels"] = {
                "sl_1x_atr": sl_atr,
                "tp1_1_5x_atr": tp1,
                "tp2_2x_atr": tp2,
                "bep_after_tp1": saran.breakeven_stop(close),
                "arb_gap_risk": pricing.check_arb_gap_risk(close, sl_atr, board=bmode),
            }
    else:
        out["ohlcv"] = {"error": "Data OHLCV tidak tersedia (jaringan/Yahoo limit)"}

    if not args.no_fundamental:
        try:
            fraw = fundamental.fetch_fundamentals(yf_ticker)
            if fraw:
                sector = fraw.get("sector") or universe.sector_of(code)
                is_fin = fundamental.is_financial_sector(sector)
                cf = fundamental.analyze_cashflow_quality(fraw)
                fscore = fundamental.score_fundamentals(fraw)
                out["fundamental"] = {
                    "is_financial_sector": is_fin,
                    "currency": fraw.get("financialCurrency") or fraw.get("currency"),
                    "per": fraw.get("trailingPE") or fraw.get("per"),
                    "pbv": fraw.get("priceToBook") or fraw.get("pbv"),
                    "roe_pct": fundamental.normalize_roe(fraw.get("roe"), unit=fraw.get("roe_unit")),
                    "div_yield_pct": fundamental.normalize_div_yield(fraw.get("dividendYield")),
                    "payout_pct": fundamental.normalize_payout(fraw.get("payoutRatio")),
                    "filing_note": fundamental.build_filing_note(fraw),
                    "cashflow_forensic": cf,
                    "quarterly_trend_4q": fundamental.analyze_quarterly_trend(fraw) if hasattr(fundamental, "analyze_quarterly_trend") else None,
                    "fundamental_score": fscore,
                }
            else:
                out["fundamental"] = {"error": "Data fundamental tidak tersedia"}
        except Exception as e:
            out["fundamental"] = {"error": str(e)}

    print(json.dumps(out, indent=2, default=str))



def cmd_cek(args):
    import core.data as data
    import core.analysis as analysis
    import core.fundamental as fundamental
    import core.powerscore as powerscore
    import core.cek as cek

    code = _clean_ticker(args.ticker)
    yf_ticker = data.normalize_ticker(code)
    board = universe.board_of(code)
    df = data.fetch_history(yf_ticker, period=args.period)
    if df is None or df.empty:
        print(json.dumps({"ticker": code, "error": "Data OHLCV tidak tersedia"}, indent=2))
        sys.exit(1)

    res = analysis.analyze(code, df)
    fraw = fundamental.fetch_fundamentals(yf_ticker)
    fund = fundamental.score_fundamentals(fraw) if fraw else None
    tech_pct = (res.get("score") or {}).get("pct")
    fund_pct = (fund or {}).get("pct")
    pen = float((fund or {}).get("forensic_penalty") or 0.0)
    vetoes = []
    if fund and fund.get("label") == "KRITIS":
        vetoes.append("Fundamental KRITIS")
    snap = res.get("snap") or {}
    if snap.get("close") and snap.get("sma50") and snap.get("sma200"):
        if snap["close"] < snap["sma50"] < snap["sma200"]:
            vetoes.append("Falling Knife (Close < MA50 < MA200)")
    ps = powerscore.combine(tech_pct, fund_pct, None, forensic_penalty=pen, vetoes=vetoes)
    val_avg20 = None
    try:
        c_col = [c for c in df.columns if (c[0] if isinstance(c, tuple) else c).lower() == "close"][0]
        v_col = [c for c in df.columns if (c[0] if isinstance(c, tuple) else c).lower() == "volume"][0]
        val_avg20 = float((df[c_col] * df[v_col]).tail(20).mean())
    except Exception:
        pass
    chk = cek.build(
        res=res, fund=fund, fund_raw=fraw,
        nsent=None, bandar_row=None, agenda=None, agenda_days=None,
        peers=None, value_avg20=val_avg20, board=board,
    )
    print(json.dumps({
        "ticker": code,
        "board": board,
        "is_syariah": syariah.is_syariah(code),
        "close": snap.get("close"),
        "as_of": snap.get("as_of"),
        "power_score": ps,
        "verdict": chk.get("verdict"),
        "verdict_label": chk.get("verdict_label"),
        "points": chk.get("points"),
        "bull_points": chk.get("bull_points"),
        "bear_points": chk.get("bear_points"),
        "warnings": chk.get("warnings"),
        "quarterly": chk.get("quarterly"),
    }, indent=2, default=str))



def cmd_track(args):
    import core.accuracy_tracker as tracker
    rec = tracker.record_recommendation(
        args.ticker, args.date, args.verdict,
        args.entry_low, args.entry_high, args.sl, args.tp1, args.tp2,
        regime=args.regime, power_score=args.power, notes=args.notes,
    )
    print(json.dumps(rec, indent=2, default=str))


def cmd_track_eval(_args):
    import core.accuracy_tracker as tracker
    rows = tracker.evaluate_recommendations()
    print(json.dumps({
        "evaluated": len(rows),
        "summary": tracker.summarize_accuracy(rows),
    }, indent=2, default=str))


def cmd_track_report(_args):
    import core.accuracy_tracker as tracker
    print(json.dumps(tracker.summarize_accuracy(), indent=2, default=str))


def main():
    parser = argparse.ArgumentParser(description="Bridge CLI analisis-saham-idx <-> saham-bot")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_info = sub.add_parser("info", help="Info offline emiten, papan, sektor, syariah, ARA/ARB")
    p_info.add_argument("ticker")
    p_info.add_argument("--price", type=float, default=None)
    p_info.set_defaults(func=cmd_info)

    p_sy = sub.add_parser("syariah", help="Cek status Syariah DES OJK atau daftar ticker")
    p_sy.add_argument("ticker", nargs="?")
    p_sy.add_argument("--list", action="store_true")
    p_sy.add_argument("--limit", type=int, default=50)
    p_sy.set_defaults(func=cmd_syariah)

    p_arb = sub.add_parser("arb-check", help="Cek risiko ARB gap 7% antara Entry dan SL")
    p_arb.add_argument("--entry", type=float, required=True)
    p_arb.add_argument("--sl", type=float, required=True)
    p_arb.add_argument("--fca", action="store_true")
    p_arb.set_defaults(func=cmd_arb_check)

    p_reg = sub.add_parser("regime", help="Cek rezim kausal IHSG (^JKSE SMA50 vs SMA200)")
    p_reg.set_defaults(func=cmd_regime)

    p_fetch = sub.add_parser("fetch", help="Ambil OHLCV + teknikal + fundamental + forensik kas")
    p_fetch.add_argument("ticker")
    p_fetch.add_argument("--period", default="1y")
    p_fetch.add_argument("--no-fundamental", action="store_true")
    p_fetch.set_defaults(func=cmd_fetch)

    p_cek = sub.add_parser("cek", help="Jalankan cek kelayakan & veto PowerScore")
    p_cek.add_argument("ticker")
    p_cek.add_argument("--period", default="1y")
    p_cek.set_defaults(func=cmd_cek)

    p_trk = sub.add_parser("track", help="Catat rekomendasi untuk audit akurasi H+5/H+20")
    p_trk.add_argument("ticker")
    p_trk.add_argument("--date", required=True)
    p_trk.add_argument("--verdict", required=True)
    p_trk.add_argument("--entry-low", type=float, required=True)
    p_trk.add_argument("--entry-high", type=float, required=True)
    p_trk.add_argument("--sl", type=float, required=True)
    p_trk.add_argument("--tp1", type=float, required=True)
    p_trk.add_argument("--tp2", type=float, default=None)
    p_trk.add_argument("--regime", default=None)
    p_trk.add_argument("--power", type=float, default=None)
    p_trk.add_argument("--notes", default="")
    p_trk.set_defaults(func=cmd_track)

    p_teval = sub.add_parser("track-eval", help="Evaluasi rekomendasi terhadap OHLCV aktual")
    p_teval.set_defaults(func=cmd_track_eval)

    p_trep = sub.add_parser("track-report", help="Ringkasan Fill Rate & Win Rate rekomendasi")
    p_trep.set_defaults(func=cmd_track_report)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
