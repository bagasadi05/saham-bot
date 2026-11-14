"""Handler analisis: /analisis, /skor, /banding.

Dependensi bot (fetch_and_analyze, build_report, chart, formatter, reply_long,
tokens) diterima lewat `deps` agar monkeypatch di tes tetap bekerja.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core import (
    analysis,
    banding,
    data,
    fundamental,
    marketcal,
    news,
    powerscore,
    rencana,
    store,
)
from core.reports import esc

log = logging.getLogger("sahambuddy")

_CB_TICKER_RE = re.compile(r"[A-Z0-9]{2,10}")


def _clean_cb_ticker(raw: str | None) -> str:
    s = str(raw or "").strip().upper()
    if s.endswith(".JK"):
        s = s[:-3]
    return s if _CB_TICKER_RE.fullmatch(s) else ""


def _home_kb(deps) -> InlineKeyboardMarkup:
    if hasattr(deps, "home_markup"):
        try:
            kb = deps.home_markup()
            if kb is not None:
                return kb
        except Exception:
            pass
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="menu")]])


def _strip_buy_buttons(kb: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    rows = []
    for row in getattr(kb, "inline_keyboard", ()) or ():
        kept = [
            btn for btn in row
            if not str(getattr(btn, "callback_data", "") or "").startswith("exec:buy:")
        ]
        if kept:
            rows.append(kept)
    if not rows:
        rows = [[InlineKeyboardButton("🏠 Menu", callback_data="menu")]]
    return InlineKeyboardMarkup(rows)


def _safe_result_markup(
    deps,
    action: str,
    ticker: str,
    *,
    allow_buy: bool = True,
) -> InlineKeyboardMarkup:
    tk = _clean_cb_ticker(ticker)
    if not tk:
        return _home_kb(deps)
    if hasattr(deps, "result_markup"):
        try:
            return deps.result_markup(action, tk, allow_buy=allow_buy)
        except TypeError:
            kb = deps.result_markup(action, tk)
            if allow_buy:
                return kb
            if isinstance(kb, InlineKeyboardMarkup):
                return _strip_buy_buttons(kb)
            return InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(f"🔍 Cek {tk}", callback_data=f"tick:cek:{tk}"),
                    InlineKeyboardButton(f"🔴 Cek Jual {tk}", callback_data=f"tick:jual:{tk}"),
                ],
                [
                    InlineKeyboardButton("🔄 Ulangi", callback_data=f"refresh:{action}:{tk}"),
                    InlineKeyboardButton("🏠 Menu", callback_data="menu"),
                ],
            ])
    rows = [
        [
            InlineKeyboardButton(f"🔍 Cek {tk}", callback_data=f"tick:cek:{tk}"),
            InlineKeyboardButton(f"💰 Fundamental {tk}", callback_data=f"tick:fundamental:{tk}"),
        ]
    ]
    if allow_buy:
        rows.append([InlineKeyboardButton(f"📝 Catat Beli {tk}", callback_data=f"exec:buy:{tk}")])
    else:
        rows.append([InlineKeyboardButton(f"🔴 Cek Jual {tk}", callback_data=f"tick:jual:{tk}")])
    rows.append([
        InlineKeyboardButton("🔄 Ulangi", callback_data=f"refresh:{action}:{tk}"),
        InlineKeyboardButton("🏠 Menu", callback_data="menu"),
    ])
    return InlineKeyboardMarkup(rows)


def _banding_markup(primary: str, deps) -> InlineKeyboardMarkup:
    tk = _clean_cb_ticker(primary)
    if not tk:
        return _home_kb(deps)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🔍 Cek {tk}", callback_data=f"tick:cek:{tk}"),
            InlineKeyboardButton(f"📊 Analisis {tk}", callback_data=f"tick:analisis:{tk}"),
        ],
        [
            InlineKeyboardButton(f"💰 Fundamental {tk}", callback_data=f"tick:fundamental:{tk}"),
            InlineKeyboardButton("🏠 Menu", callback_data="menu"),
        ],
    ])


def _analisis_powerscore(
    res: dict,
    fsc: dict | None,
    nsent: dict | None,
) -> dict | None:
    sc = res.get("score") or {}
    try:
        tech_pct = float(sc.get("pct"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(tech_pct):
        return None
    forensic_pen = float((fsc or {}).get("forensic_penalty") or 0.0)
    if fsc and "base_pct" in fsc and forensic_pen > 0:
        fund_input = fsc["base_pct"]
    else:
        fund_input = fsc.get("pct") if isinstance(fsc, dict) else None
    news_pct = nsent.get("pct") if isinstance(nsent, dict) else None
    return powerscore.combine(
        tech_pct, fund_input, news_pct, forensic_penalty=forensic_pen
    )


def _extract_price_atr(res: dict) -> tuple[float | None, float | None]:
    snap = res.get("snap") if isinstance(res.get("snap"), dict) else {}
    ind = res.get("ind")
    close_raw = snap.get("close")
    if close_raw is None and ind is not None:
        try:
            if hasattr(ind, "iloc") and len(ind) > 0 and "close" in ind.columns:
                close_raw = ind["close"].iloc[-1]
            elif isinstance(ind, dict) and "close" in ind and len(ind["close"]) > 0:
                close_raw = list(ind["close"])[-1]
        except Exception:
            close_raw = None
    try:
        close_px = float(close_raw) if close_raw is not None else None
    except (TypeError, ValueError):
        close_px = None
    if close_px is not None and (not math.isfinite(close_px) or close_px <= 0):
        close_px = None

    atr_raw = snap.get("atr14") if "atr14" in snap else snap.get("atr")
    if atr_raw is None and ind is not None:
        try:
            if hasattr(ind, "iloc") and len(ind) > 0:
                last = ind.iloc[-1]
                atr_raw = last.get("atr14", last.get("atr"))
        except Exception:
            atr_raw = None
    try:
        atr_px = float(atr_raw) if atr_raw is not None else None
    except (TypeError, ValueError):
        atr_px = None
    if atr_px is not None and (not math.isfinite(atr_px) or atr_px <= 0):
        atr_px = None
    return close_px, atr_px


def _analisis_allow_buy(
    res: dict,
    pw: dict | None,
    close_px: float | None,
) -> bool:
    if close_px is None or not math.isfinite(close_px) or close_px <= 0:
        return False
    if res.get("allow_buy") is False:
        return False
    verdict_raw = str(
        res.get("verdict")
        or (res.get("score") or {}).get("verdict")
        or ""
    ).upper()
    if any(tok in verdict_raw for tok in ("HINDARI", "JUAL", "SELL")):
        return False
    if pw is None:
        sc = res.get("score") or {}
        try:
            sc_pct = float(sc.get("pct"))
        except (TypeError, ValueError):
            return False
        sc_rating = str(sc.get("rating") or "").upper()
        return (
            math.isfinite(sc_pct)
            and sc_pct >= 0
            and sc_rating not in ("SELL", "STRONG SELL")
        )
    try:
        pw_pct = float(pw.get("pct"))
    except (TypeError, ValueError):
        return False
    pw_rating = str(pw.get("rating") or "").upper()
    if not math.isfinite(pw_pct) or pw_pct < 0:
        return False
    if pw_rating in ("SELL", "STRONG SELL", "HINDARI", "JUAL"):
        return False
    return True


async def do_analisis(update: Update, tickers: list[str], deps):
    if not await deps.cooldown_ok(update, "analisis"):
        return
    msg = update.effective_message
    chat_id = update.effective_chat.id if update.effective_chat else None
    wl = (await asyncio.to_thread(store.get_watchlist, chat_id)
          if chat_id is not None else [])
    mkt = await asyncio.to_thread(banding.market_context)
    for t in tickers[:5]:
        try:
            res = await deps.fetch_and_analyze(t)
        except ValueError as e:
            await msg.reply_text(
                f"❌ {esc(e)}",
                parse_mode=ParseMode.HTML,
                reply_markup=_home_kb(deps),
            )
            continue
        sym = res["ticker"]
        cb_sym = _clean_cb_ticker(sym) or _clean_cb_ticker(t)
        fund_f = asyncio.to_thread(fundamental.fetch_fundamentals, t)
        news_f = asyncio.to_thread(news.fetch_news, t)
        weekly_f = asyncio.to_thread(data.fetch_weekly, t)
        try:
            fund, items, weekly_df = await asyncio.gather(
                fund_f, news_f, weekly_f, return_exceptions=True)
        except Exception:
            fund, items, weekly_df = None, None, None
        if isinstance(fund, Exception) or fund is None:
            fund, fsc = None, None
        else:
            fsc = await asyncio.to_thread(fundamental.score_fundamentals, fund)
        if isinstance(items, Exception) or not items:
            nsent = None
        else:
            nsent = await asyncio.to_thread(news.news_sentiment, items)
        if isinstance(weekly_df, Exception) or weekly_df is None:
            res["weekly"] = None
        else:
            res["weekly"] = analysis.weekly_trend(weekly_df, res["score"]["pct"])
        try:
            res["ret1y"] = banding.year_return(res["ind"]["close"])
        except Exception:
            res["ret1y"] = None
        try:
            sector_ctx = await asyncio.to_thread(
                banding.sector_context, sym, (fund or {}).get("sector"), wl)
        except Exception:
            sector_ctx = None
        res["sector_ctx"] = sector_ctx
        mkt_ctx = {**mkt, "peers": sector_ctx} if (mkt and sector_ctx) else mkt
        res["mkt"] = mkt_ctx

        pw = _analisis_powerscore(res, fsc, nsent)
        close_px, atr_px = _extract_price_atr(res)
        allow_buy = _analisis_allow_buy(res, pw, close_px)
        if allow_buy and chat_id is not None and cb_sym:
            try:
                lv = rencana.price_levels({"price": close_px, "atr": atr_px})
                trade_date = marketcal.now_wib().date().isoformat()
                await asyncio.to_thread(
                    store.save_signal_context,
                    chat_id,
                    cb_sym,
                    "analisis",
                    float(lv["entry"]),
                    sl=float(lv["sl"]) if lv.get("sl") is not None else None,
                    tp=float(lv["tp"]) if lv.get("tp") is not None else None,
                    trade_date=trade_date,
                    intent="beli",
                )
            except Exception:
                log.exception("gagal simpan signal_context analisis utk %s", cb_sym)

        kb = (
            _safe_result_markup(deps, "analisis", cb_sym, allow_buy=allow_buy)
            if cb_sym
            else _home_kb(deps)
        )
        await deps.reply_long(
            msg,
            deps.build_report(res, fsc, nsent, mkt_ctx),
            reply_markup=kb,
        )
        os.makedirs(deps.TMP_DIR, exist_ok=True)
        path = os.path.join(deps.TMP_DIR, f"chart_{sym}_{time.time_ns()}.png")
        try:
            await asyncio.to_thread(
                deps.build_chart, sym.upper(), res["ind"], res["score"]["pct"], path)

            def _send_chart(_path=path, _sym=sym, _kb=kb):
                # buka ulang tiap percobaan: handle yang sudah terpakai tidak
                # bisa dikirim ulang oleh retry
                with open(_path, "rb") as f:
                    return msg.reply_photo(
                        photo=f,
                        caption=f"📈 Chart {_sym} (6 bulan terakhir)",
                        reply_markup=_kb,
                    )

            await deps.send_with_retry(_send_chart, what="kirim chart")
        except Exception:
            log.exception("chart gagal utk %s", sym)
        finally:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass


async def do_skor(update: Update, tickers: list[str], deps,
                        from_menu: bool = False):
    msg = update.effective_message
    chat_id = update.effective_chat.id if update.effective_chat else None
    wl = (await asyncio.to_thread(store.get_watchlist, chat_id)
          if chat_id is not None else [])
    args = tickers or wl or ["BBCA"]
    lines = []
    primary_sym = ""
    primary_allow_buy = True
    for t in args[:8]:
        try:
            res = await deps.fetch_and_analyze(t)
            lines.append(deps.build_quick(res))
            if not primary_sym:
                cand = _clean_cb_ticker(res.get("ticker") or t)
                if cand:
                    primary_sym = cand
                    sc = res.get("score") or {}
                    try:
                        pct = float(sc.get("pct"))
                    except (TypeError, ValueError):
                        pct = None
                    rating_u = str(sc.get("rating") or sc.get("verdict") or "").upper()
                    verdict_u = str(res.get("verdict") or "").upper()
                    primary_allow_buy = (
                        res.get("allow_buy") is not False
                        and pct is not None
                        and math.isfinite(pct)
                        and pct >= 0
                        and rating_u not in ("SELL", "STRONG SELL", "HINDARI", "JUAL")
                        and not any(tok in verdict_u for tok in ("HINDARI", "JUAL", "SELL"))
                    )
        except ValueError as e:
            lines.append(f"❌ {esc(e)}")
    kb = (
        _safe_result_markup(deps, "skor", primary_sym, allow_buy=primary_allow_buy)
        if primary_sym
        else _home_kb(deps)
    )
    await deps.reply_long(
        msg,
        "\n".join(lines) or "Watchlist kosong.",
        reply_markup=kb,
    )


async def cmd_banding(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    """[P2] Bandingkan 2–4 emiten: PER/PBV/ROE/DY + return + PowerScore."""
    args = deps.tokens(update)
    if len(args) < 2:
        single = _clean_cb_ticker(args[0]) if args else ""
        if single:
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(f"🔍 Cek {single}", callback_data=f"tick:cek:{single}"),
                    InlineKeyboardButton(f"📊 Analisis {single}", callback_data=f"tick:analisis:{single}"),
                ],
                [
                    InlineKeyboardButton("🏠 Menu", callback_data="menu"),
                ],
            ])
        else:
            kb = _home_kb(deps)
        await update.effective_message.reply_text(
            "Contoh: <code>/banding BBCA BBRI BMRI</code> (2–4 emiten)",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        return
    if not await deps.cooldown_ok(update, "banding"):
        return
    emiten = list(dict.fromkeys((_clean_cb_ticker(a) or a.strip().upper()) for a in args[:4]))
    await update.effective_message.reply_text(
        f"⚖️ Membandingkan {len(emiten)} emiten… (data dari sumber yang sama "
        "dengan /fundamental & /analisis)")

    rows = []
    as_ofs: list[str] = []
    stale_syms: list[str] = []
    fx_notes: list[str] = []
    for t in emiten:
        try:
            res = await deps.fetch_and_analyze(t)
        except ValueError:
            res = None
        if isinstance(res, dict):
            if res.get("as_of"):
                as_ofs.append(str(res["as_of"]))
            if res.get("stale") or res.get("stale_note"):
                stale_syms.append(t)
        try:
            fund = await asyncio.to_thread(fundamental.fetch_fundamentals, t)
        except Exception:
            fund = None
        if isinstance(fund, dict):
            ccy = str(fund.get("currency") or "").upper()
            if ccy and ccy not in ("IDR", "TIDAK TERSEDIA"):
                fx_notes.append(f"{t} ({ccy})")
        rows.append(banding.compare_row(t, res, fund))
        await asyncio.sleep(.2)

    if all(row["power"] is None for row in rows):
        await update.effective_message.reply_text(
            "❌ Data pembanding tidak tersedia (semua sumber gagal) — coba lagi nanti.",
            reply_markup=_home_kb(deps),
        )
        return

    hdr = banding.format_table_header()
    body = "\n".join(banding.format_table_row(r) for r in rows)
    lines = [f"⚖️ <b>PERBANDINGAN {', '.join(esc(t) for t in emiten)}</b>"]
    if as_ofs:
        uniq_dates = sorted(set(as_ofs))
        date_lbl = uniq_dates[-1] if len(uniq_dates) == 1 else f"{uniq_dates[0]} s.d. {uniq_dates[-1]}"
        stale_lbl = f" · ⚠️ cache lama: {', '.join(stale_syms)}" if stale_syms else ""
        lines.append(f"🔎 <i>Data per {esc(date_lbl)} WIB{esc(stale_lbl)}</i>")
    elif stale_syms:
        lines.append(f"⚠️ <i>Data dari cache lama: {esc(', '.join(stale_syms))}</i>")
    lines.append("<pre>" + esc(hdr) + "\n" + esc(body) + "</pre>")
    if fx_notes:
        lines.append(f"💱 <i>Mata uang laporan non-IDR: {esc(', '.join(fx_notes))}</i>")
    verdict = banding.pbv_verdict(rows)
    if verdict:
        lines.append(f"\nℹ️ {esc(verdict)}")
    missing = [r['ticker'] for r in rows if r["power"] is None]
    if missing:
        lines.append(f"⚠️ Data tidak tersedia untuk: {esc(', '.join(missing))} "
                     "(dilaporkan apa adanya, bukan dikarang).")
    lines.append("\n🔒 Angka deskriptif, bukan rekomendasi. DYOR!")
    primary = ""
    for row in rows:
        if row.get("power") is not None:
            cand = _clean_cb_ticker(row.get("ticker"))
            if cand:
                primary = cand
                break
    if not primary:
        primary = next((_clean_cb_ticker(t) for t in emiten if _clean_cb_ticker(t)), "")
    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=_banding_markup(primary, deps),
    )


async def cmd_analisis(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    args = deps.tokens(update)
    if not args:
        wl = await asyncio.to_thread(store.get_watchlist,
                                     update.effective_chat.id)
        await update.effective_message.reply_text(
            "📊 Analisis — pilih emiten:",
            reply_markup=deps.ticker_pick_markup("analisis", wl))
        return
    await do_analisis(update, args, deps)


async def cmd_skor(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    args = deps.tokens(update)
    if not args:
        wl = await asyncio.to_thread(store.get_watchlist,
                                     update.effective_chat.id)
        await update.effective_message.reply_text(
            "⚡ Skor — pilih emiten:",
            reply_markup=deps.ticker_pick_markup("skor", wl))
        return
    await do_skor(update, args, deps)
