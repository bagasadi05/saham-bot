"""Job terjadwal: alert 30 menit, autoscan pagi, screener 16:15, backup.

Dependensi bot (`_scan_with_guard`, `_scan_summary_text`, `notify_admin`,
`saran_scan_text`, `fetch_and_analyze`, `build_quick`, `send_with_retry`, dan
`screener`/config) diakses lewat `deps`, supaya monkeypatch di tes tetap
bekerja dan tidak ada impor melingkar.
"""
from __future__ import annotations

import asyncio
import logging
import re

import pandas as pd
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application

from core import (alerts, analysis, data, fundamental, jurnal as jurnalmod,
                  marketcal, powerscore, regime, saran as saranmod, store)
from core.reports import esc, fmt_rp
from core.textutil import split_message

log = logging.getLogger("sahambuddy")

_ORIG_FETCH_REGIME = regime.fetch_ihsg_regime
_ORIG_FETCH_HISTORY = data.fetch_history
_TARGET_RE = re.compile(r"^target(>=|<=|>|<|:)(\d+(?:\.\d+)?)$", re.IGNORECASE)


async def _fetch_regime_safe(deps) -> dict | None:
    """Ambil rezim IHSG secara aman (hormati monkeypatch & hindari panggilan jaringan liar di unit test)."""
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
    data_mod = getattr(deps, "data", data)
    fh = getattr(data_mod, "fetch_history", data.fetch_history)
    if fh is not _ORIG_FETCH_HISTORY:
        try:
            return await asyncio.to_thread(fn)
        except Exception:
            return regime.evaluate_ihsg(None)
    try:
        return await asyncio.to_thread(fn, offline_only=True)
    except Exception:
        return regime.evaluate_ihsg(None)


def _call_fill_pending(chat_id: int | str, bars_map: dict[str, pd.DataFrame],
                       modal: int, market_regime: dict | None = None) -> list[str]:
    """Teruskan market_regime ke saran.fill_pending (kompatibel dgn mock 3-arg)."""
    try:
        return saranmod.fill_pending(
            chat_id, bars_map, modal, market_regime=market_regime
        )
    except TypeError:
        return saranmod.fill_pending(chat_id, bars_map, modal)


def _match_target_kinds(ind: pd.DataFrame, kinds: set[str]) -> set[str]:
    """Evaluasi pemicu target harga (`target>=9500` / `target<=9000`) dari baris terakhir."""
    if ind is None or ind.empty or "close" not in ind:
        return set()
    last = ind.iloc[-1]
    try:
        close_px = float(last.get("close"))
    except (TypeError, ValueError):
        return set()
    if pd.isna(close_px):
        return set()
    high_px = close_px
    low_px = close_px
    try:
        if "high" in ind and pd.notna(last.get("high")):
            high_px = float(last.get("high"))
        if "low" in ind and pd.notna(last.get("low")):
            low_px = float(last.get("low"))
    except (TypeError, ValueError):
        pass

    matched: set[str] = set()
    for k in kinds:
        m = _TARGET_RE.match(str(k or "").strip())
        if not m:
            continue
        op, raw_val = m.group(1), m.group(2)
        try:
            target_px = float(raw_val)
        except ValueError:
            continue
        if op in (">=", ">", ":"):
            if close_px >= target_px or high_px >= target_px:
                matched.add(k)
        elif op in ("<=", "<"):
            if close_px <= target_px or low_px <= target_px:
                matched.add(k)
    return matched


def _format_alert_trigger_label(kind: str) -> str:
    """Ubah token target harga ke label ramah pengguna untuk build_alert_message."""
    m = _TARGET_RE.match(str(kind or "").strip())
    if not m:
        return kind
    op, raw_val = m.group(1), m.group(2)
    try:
        target_px = float(raw_val)
    except ValueError:
        return kind
    sym_op = "<=" if op in ("<=", "<") else ">="
    return f"🎯 Target harga {sym_op} {fmt_rp(target_px)} tercapai"


def _real_position_chats() -> list[str]:
    """Daftar chat_id yang punya posisi terbuka di real_positions."""
    with store._db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT chat_id FROM real_positions WHERE lots > 0"
        ).fetchall()
        return [str(r["chat_id"]) for r in rows]


def _exit_alert_should_send(
    chat_id: int | str, ticker: str, trigger_key: str, day_iso: str
) -> bool:
    """Return True dan catat bila (chat_id, ticker, trigger_key) belum dikirim hari ini."""
    cid = str(chat_id)
    key = f"exit_alert:{str(ticker).upper()}:{trigger_key}"
    with store._db() as conn:
        store._ensure_chat(conn, cid)
        row = conn.execute(
            "SELECT value FROM chat_settings WHERE chat_id=? AND key=?",
            (cid, key),
        ).fetchone()
        if row and str(row["value"]) == str(day_iso):
            return False
        conn.execute(
            "INSERT INTO chat_settings(chat_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id, key) DO UPDATE SET value=excluded.value",
            (cid, key, str(day_iso)),
        )
        return True


async def morning_scan(app: Application, deps):
    """[P6] Auto-scan pagi: ringkasan /saran + rekap watchlist, 08:30 WIB,
    Senin–Jumat — hanya ke chat yang mengaktifkan /auto on."""
    now = marketcal.now_wib()
    if not marketcal.is_trading_day(now.date()):
        return  # Sabtu/Minggu & libur bursa tidak jalan
    for cid in await asyncio.to_thread(store.get_auto_chats):
        try:
            txt = await deps.saran_scan_text(int(cid))
            if txt is None:
                continue  # semua sumber gagal — jangan spam error
            recap = []
            for t in (await asyncio.to_thread(store.get_watchlist,
                                              cid))[:10]:
                try:
                    res = await deps.fetch_and_analyze(t)
                    recap.append(deps.build_quick(res))
                except Exception:
                    continue
            if recap:
                txt += "\n\n📋 <b>Rekap watchlist</b>\n" + "\n".join(recap)
            body = f"🌅 <b>AUTOSCAN {now:%a, %d %b %Y}</b>\n\n" + txt
            for part in split_message(body):
                await app.bot.send_message(
                    int(cid), part, parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True)
        except Exception:
            log.exception("autoscan gagal utk %s", cid)
        await asyncio.sleep(1)
    await deps.check_dividend_reminders(app, now.date())


async def check_band_alerts(app: Application, bars_fn, deps) -> int:
    """[P10] Kabari chat /auto on bila band PowerScore saham watchlist berubah."""
    auto_chats = await asyncio.to_thread(store.get_auto_chats)
    if not auto_chats:
        return 0
    sent = 0
    analysis_cache: dict[str, tuple[dict, dict, dict] | None] = {}
    for cid in auto_chats:
        for sym in (await asyncio.to_thread(store.get_watchlist,
                                            cid))[:15]:
            try:
                if sym not in analysis_cache:
                    df = await asyncio.to_thread(bars_fn, sym)
                    if df is None or len(df) < 30:
                        analysis_cache[sym] = None
                    else:
                        ind = await asyncio.to_thread(analysis.compute_indicators, df)
                        sc = analysis.score_stock(ind)
                        snap = analysis.last_snapshot(ind)
                        try:
                            fund = await asyncio.to_thread(fundamental.fetch_fundamentals, sym)
                            fsc = (await asyncio.to_thread(fundamental.score_fundamentals, fund)
                                   if fund else None)
                        except Exception:
                            fsc = None
                        pw = powerscore.combine(sc["pct"], fsc["pct"] if fsc else None, None)
                        analysis_cache[sym] = (pw, snap, sc)
                cached = analysis_cache[sym]
                if not cached:
                    continue
                pw, snap, _sc = cached
                prev_band = await asyncio.to_thread(
                    store.update_score_band, cid, sym, pw["rating"])
                if not prev_band:
                    continue
                msg_txt = (
                    f"{pw['emoji']} <b>Perubahan Band {esc(sym)}</b>\n"
                    f"PowerScore berpindah: <b>{esc(prev_band)}</b> → "
                    f"<b>{esc(pw['rating'])}</b> ({pw['pct']:+d}%)\n"
                    f"Harga: {fmt_rp(snap['close'])} ({snap['ret1d']:+.2f}% 1d)\n"
                    "ℹ️ <i>Cek /analisis untuk detail. Bukan saran investasi.</i>"
                )
                await app.bot.send_message(int(cid), msg_txt, parse_mode=ParseMode.HTML)
                sent += 1
                await asyncio.sleep(.2)
            except Exception:
                log.exception("band alert gagal %s (chat %s)", sym, cid)
    return sent


async def fill_open(app: Application, deps):
    """09:20 WIB: isi posisi 'pending' di harga open sesi hari ini.

    Kalau bar hari ini belum tersedia (Yahoo delay), scanner 30 menit yang
    akan mengisinya nanti — jadi tidak ada posisi yang tertinggal.
    """
    if not marketcal.is_trading_day(marketcal.now_wib().date()):
        return
    pending: dict[str, set[str]] = {}
    for chat_id, symbol, rec in await asyncio.to_thread(
            store.all_papers):
        if rec.get("status") == "pending":
            pending.setdefault(chat_id, set()).add(symbol)
    if not pending:
        return
    reg = await _fetch_regime_safe(deps)
    for chat_id, symbols in pending.items():
        bars_map: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            try:
                df = await asyncio.to_thread(data.fetch_history, symbol, "1mo")
            except Exception:
                log.exception("fill open gagal %s", symbol)
                continue
            if df is not None:
                bars_map[symbol] = df
        try:
            modal = await asyncio.to_thread(store.get_modal, chat_id)
            msgs = await asyncio.to_thread(
                _call_fill_pending, chat_id, bars_map, modal, reg)
            for msg in msgs:
                await app.bot.send_message(int(chat_id), msg, parse_mode=ParseMode.HTML)
        except Exception:
            log.exception("gagal isi posisi pending chat %s", chat_id)


async def heartbeat(app: Application, deps):
    """Tulis timestamp tiap 5 menit — dipakai HEALTHCHECK Docker."""
    path = deps.config.HEARTBEAT_FILE
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(marketcal.now_wib().isoformat(timespec="seconds"))
    except OSError:
        log.warning("gagal menulis heartbeat ke %s", path)


async def check_zone_alerts(app: Application, bars, deps) -> None:
    """[P16] Kirim satu pesan saat emiten watchlist MASUK zona beli.

    Status zona disimpan per (chat, emiten) di `store.zone_state`. Aturan:
    pesan hanya dikirim saat status berubah dari "di luar" menjadi "di dalam";
    selama masih di zona yang sama, scan berikutnya diam. Ikut saklar
    `/auto on/off` karena sumbernya `store.get_auto_chats()`.
    """
    for chat_id in await asyncio.to_thread(store.get_auto_chats):
        wl = await asyncio.to_thread(store.get_watchlist, chat_id)
        if not wl:
            continue
        states = await asyncio.to_thread(store.get_zone_states, chat_id)
        for symbol in wl:
            try:
                df = await asyncio.to_thread(bars, symbol)
                if df is None:
                    continue
                ind = await asyncio.to_thread(analysis.compute_indicators, df)
                zones = alerts.buy_zones(ind)
                prev = states.get(symbol.upper(), "")
                cur = ",".join(zones)
                if cur == prev:
                    continue
                await asyncio.to_thread(store.set_zone_state, chat_id, symbol,
                                        cur)
                if not zones or prev:
                    continue          # keluar zona, atau tetap di dalam: diam
                snap = analysis.last_snapshot(ind)
                atr = None
                try:
                    atr_val = ind["atr14"].iloc[-1]
                    atr = float(atr_val) if pd.notna(atr_val) and atr_val > 0 \
                        else None
                except (KeyError, IndexError, TypeError, ValueError):
                    atr = None
                sl = saranmod.stop_price(snap["close"], atr) if atr else None
                tp = saranmod.target_price(snap["close"], atr) if atr else None
                await deps.send_with_retry(
                    lambda s=symbol, z=zones, sn=snap, a=sl, b=tp, c=chat_id:
                    app.bot.send_message(
                        int(c),
                        alerts.build_zone_message(s, z, sn, a, b),
                        parse_mode=ParseMode.HTML),
                    what="kirim zona beli")
                await asyncio.sleep(.3)
            except Exception:
                log.exception("zona beli %s (chat %s) gagal", symbol, chat_id)


async def _scan_real_position_exits(
    app: Application, bars, deps, reg: dict | None
) -> int:
    """[Wave 2B] Peringatan keluar/manajemen risiko posisi asli (real_positions + open_executions).

    Mencakup:
    - SL tersentuh (`sl_hit`)
    - Chandelier trailing SL tembus (`chandelier_hit`)
    - TP1 scale-out 50% + geser SL ke BEP+0,40% fee BEI (`tp1_scale_out`)
    - Horizontal time-stop >= 20 hari bursa (`time_stop`)
    - Peringatan rezim IHSG defensif (`regime_defensive`)
    Dedup harian per (chat_id, ticker, trigger_key, today_iso) agar tidak spam.
    """
    pos_chats = await asyncio.to_thread(_real_position_chats)
    if not pos_chats:
        return 0
    today_d = marketcal.now_wib().date()
    today_iso = today_d.isoformat()
    sent_count = 0

    for chat_id in pos_chats:
        try:
            positions = await asyncio.to_thread(store.real_positions, chat_id)
            if not positions:
                continue
            open_execs = await asyncio.to_thread(
                store.open_executions_by_ticker, chat_id
            )
        except Exception:
            log.exception("gagal muat posisi asli chat %s", chat_id)
            continue

        for pos in positions:
            sym = str(pos.get("ticker") or "").upper()
            lots = int(pos.get("lots") or 0)
            avg_px = float(pos.get("avg_price") or 0.0)
            if not sym or lots <= 0 or avg_px <= 0:
                continue
            try:
                df = await asyncio.to_thread(bars, sym)
                if df is None or df.empty:
                    continue
                ind = await asyncio.to_thread(analysis.compute_indicators, df)
                snap = analysis.last_snapshot(ind)
                close_px = float(snap["close"])
                prev_close = (
                    float(ind["close"].iloc[-2])
                    if len(ind) >= 2 and "close" in ind
                    else None
                )
                low_px = (
                    float(ind["low"].iloc[-1])
                    if "low" in ind and len(ind) and pd.notna(ind["low"].iloc[-1])
                    else close_px
                )
                high_px = (
                    float(ind["high"].iloc[-1])
                    if "high" in ind and len(ind) and pd.notna(ind["high"].iloc[-1])
                    else close_px
                )
                atr_val = None
                try:
                    raw_atr = ind["atr14"].iloc[-1]
                    if pd.notna(raw_atr) and float(raw_atr) > 0:
                        atr_val = float(raw_atr)
                except (KeyError, IndexError, TypeError, ValueError):
                    atr_val = None

                ex_info = open_execs.get(sym) or {}
                sl_exec = ex_info.get("sl")
                tp_exec = ex_info.get("tp")
                entry_date = ex_info.get("trade_date") or (
                    str(pos.get("updated_at") or "")[:10] or None
                )

                eg = saranmod.build_exit_guidance(
                    sym,
                    avg_px,
                    lots=lots,
                    atr=atr_val,
                    sl=sl_exec,
                    tp=tp_exec,
                    close=close_px,
                    prev_close=prev_close,
                    df=ind,
                    entry_date=entry_date,
                    today=today_d,
                )

                candidates: list[tuple[str, str]] = []
                sl_lvl = float(eg.get("sl_initial") or 0.0)
                sl_src_lbl = "SL eksekusi" if sl_exec else "1×ATR"
                if eg.get("action") == "CUT_LOSS" or (
                    sl_lvl > 0 and (close_px <= sl_lvl or low_px <= sl_lvl)
                ):
                    candidates.append((
                        "sl_hit",
                        f"🛑 <b>SL Tersentuh ({sl_src_lbl})</b>: harga {fmt_rp(close_px)} "
                        f"(low {fmt_rp(low_px)}) menembus batas SL <b>{fmt_rp(sl_lvl)}</b> "
                        "— disiplin cut loss untuk lindungi modal.",
                    ))

                ch = eg.get("chandelier") or {}
                if ch.get("hit") or eg.get("action") == "TRAILING_EXIT":
                    candidates.append((
                        "chandelier_hit",
                        f"📉 <b>Chandelier Trailing SL Tembus</b>: harga {fmt_rp(close_px)} "
                        f"di bawah trailing stop 22h−3×ATR (<b>{fmt_rp(ch.get('stop'))}</b>) "
                        "— amankan profit / batasi pembalikan tren.",
                    ))

                sc_out = eg.get("scale_out") or {}
                tp1_px = float(sc_out.get("tp1") or 0.0)
                if (
                    sc_out.get("tp1_reached")
                    or (tp1_px > 0 and high_px >= tp1_px)
                    or eg.get("action") in ("SCALE_OUT_TP1", "AMBIL_PROFIT")
                ):
                    candidates.append((
                        "tp1_scale_out",
                        f"🎯 <b>Scale-Out TP1 Tercapai</b>: {esc(sc_out.get('note') or '')} "
                        f"(geser SL sisa lot ke BEP+0,40% fee BEI: <b>{fmt_rp(eg.get('bep_sl'))}</b>).",
                    ))

                ts = eg.get("time_stop") or {}
                if ts.get("triggered"):
                    candidates.append((
                        "time_stop",
                        f"⏳ <b>Time-Stop Horizontal ({ts.get('days_held')} hari bursa)</b>: "
                        f"{esc(ts.get('reason') or '')}.",
                    ))

                if reg and reg.get("status") == "DEFENSIVE":
                    candidates.append((
                        "regime_defensive",
                        f"🛡️ <b>Rezim IHSG Defensif (SMA50 &lt; SMA200)</b>: "
                        f"perketat SL efektif ke <b>{fmt_rp(eg.get('effective_sl'))}</b> "
                        "&amp; hindari tambah muatan baru.",
                    ))

                if not candidates:
                    continue

                fresh_lines: list[str] = []
                for trig_key, trig_line in candidates:
                    should_send = await asyncio.to_thread(
                        _exit_alert_should_send, chat_id, sym, trig_key, today_iso
                    )
                    if should_send:
                        fresh_lines.append("• " + trig_line)

                if not fresh_lines:
                    continue

                pnl_net = eg.get("pnl_net_pct")
                pnl_str = f" ({pnl_net:+.1f}% net)" if pnl_net is not None else ""
                trail_px = eg.get("trailing_sl")
                trail_str = (
                    f" · Trailing 22h: <b>{fmt_rp(trail_px)}</b>" if trail_px else ""
                )
                msg_lines = [
                    f"🚨 <b>ALERT POSISI PORTOFOLIO — {esc(sym)}</b>",
                    f"Posisi: <b>{lots} lot</b> @ {fmt_rp(avg_px)} · "
                    f"Harga: <b>{fmt_rp(close_px)}</b>{pnl_str}",
                    f"SL efektif: <b>{fmt_rp(eg.get('effective_sl'))}</b> ({sl_src_lbl}){trail_str}",
                    "",
                    *fresh_lines,
                ]
                arb_warns = (eg.get("arb_risk") or {}).get("warnings") or []
                if arb_warns:
                    msg_lines.append(f"⚠️ <i>{esc(arb_warns[0])}</i>")
                msg_lines += [
                    "",
                    f"<i>Panduan lengkap: <code>/jual {esc(sym)}</code> · "
                    f"catat jual: <code>/portofolio jual {esc(sym)} LOT HARGA</code>. "
                    "Bukan auto-trading.</i>",
                ]
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        f"🤔 Cek layak jual {sym}?", callback_data=f"tick:jual:{sym}"
                    ),
                    InlineKeyboardButton(
                        f"📝 Sudah jual {sym} di Stockbit",
                        callback_data=f"exec:sell:{sym}",
                    ),
                ]])
                await app.bot.send_message(
                    int(chat_id),
                    "\n".join(msg_lines),
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
                sent_count += 1
                await asyncio.sleep(0.3)
            except Exception:
                log.exception("exit alert posisi %s (chat %s) gagal", sym, chat_id)
    return sent_count


async def scan_alerts(app: Application, deps):
    """Job berkala: alert + posisi latihan + exit alert portofolio, hanya saat bursa buka."""
    if not marketcal.market_open():
        return  # hanya hari bursa 09:00–16:00 WIB

    cache: dict[str, pd.DataFrame] = {}

    def bars(symbol: str) -> pd.DataFrame | None:
        """Satu emiten diambil sekali per putaran, dipakai alert, paper & band."""
        if symbol not in cache:
            df = data.fetch_history(symbol, "1y")
            if df is None:
                return None
            cache[symbol] = df
        return cache[symbol]

    # 1) alert — dikirim hanya saat kondisi berubah (False → True), bukan tiap 30 menit
    for chat_id, symbol, kinds in await asyncio.to_thread(
            store.alert_subscriptions):
        try:
            df = await asyncio.to_thread(bars, symbol)
            if df is None:
                continue
            ind = await asyncio.to_thread(analysis.compute_indicators, df)
            hit = alerts.match_kinds(alerts.check_triggers(ind), kinds)
            hit |= _match_target_kinds(ind, kinds)
            fresh = await asyncio.to_thread(
                store.update_alert_state, chat_id, symbol, hit)
            if not fresh:
                continue
            snap = analysis.last_snapshot(ind)
            sc = analysis.score_stock(ind)
            labels = [_format_alert_trigger_label(k) for k in sorted(fresh)]
            await app.bot.send_message(
                int(chat_id),
                alerts.build_alert_message(symbol, labels, snap, sc),
                parse_mode=ParseMode.HTML)
            await asyncio.sleep(.3)
        except Exception:
            log.exception("scan alert error %s (chat %s)", symbol, chat_id)

    # 2) paper trading — isi antrean 'pending' lalu cek SL/TP posisi terbuka
    papers: dict[str, dict[str, dict]] = {}
    for chat_id, symbol, rec in await asyncio.to_thread(
            store.all_papers):
        papers.setdefault(chat_id, {})[symbol] = rec
    has_pending = any(
        any(r.get("status") == "pending" for r in recs.values())
        for recs in papers.values()
    )
    pos_chats = await asyncio.to_thread(_real_position_chats)
    reg = await _fetch_regime_safe(deps) if (has_pending or pos_chats) else None

    for chat_id, recs in papers.items():
        need = [s for s, r in recs.items() if r.get("status") in ("pending", "open")]
        if not need:
            continue
        bars_map: dict[str, pd.DataFrame] = {}
        for symbol in need:
            try:
                df = await asyncio.to_thread(bars, symbol)
            except Exception:
                log.exception("paper fetch gagal %s", symbol)
                continue
            if df is not None:
                bars_map[symbol] = df
        modal = await asyncio.to_thread(store.get_modal, chat_id)
        msgs: list[str] = []
        if any(r.get("status") == "pending" for r in recs.values()):
            msgs += await asyncio.to_thread(
                _call_fill_pending, chat_id, bars_map, modal, reg)
        latest = {s: df.iloc[-1] for s, df in bars_map.items() if len(df)}
        msgs += await asyncio.to_thread(saranmod.update_paper, chat_id, latest)
        for msg in msgs:
            try:
                await app.bot.send_message(int(chat_id), msg, parse_mode=ParseMode.HTML)
            except Exception:
                log.exception("gagal kirim update paper ke %s", chat_id)

    # 3) [P10] perubahan band PowerScore untuk watchlist chat /auto on
    await deps.check_band_alerts(app, bars)

    # 4) [P16] zona beli watchlist — hanya saat MASUK zona, bukan tiap putaran
    await deps.check_zone_alerts(app, bars)

    # 5) [Wave 2B] exit alert portofolio asli (SL, TP1 scale-out, Chandelier, time-stop, rezim)
    if pos_chats:
        await _scan_real_position_exits(app, bars, deps, reg)


async def backup_job(app: Application, deps):
    """Salin SQLite harian (WAL-safe) + rotasi. Diam saja kalau sukses."""
    try:
        path = await asyncio.to_thread(
            store.backup_to, deps.config.BACKUP_DIR, deps.config.BACKUP_KEEP)
        log.info("backup store: %s", path)
    except Exception:
        log.exception("backup store gagal")


async def full_market_scan_job(app: Application, deps):
    """Job harian 16:15 WIB: snapshot sinyal untuk chat /auto on."""
    if not deps.config.FULL_SCAN_AUTO:
        return
    if not marketcal.is_trading_day(marketcal.now_wib().date()):
        return
    try:
        res = await deps._scan_with_guard(False)
    except Exception as exc:
        log.exception("screener harian gagal")
        await deps.notify_admin(
            app,
            "❌ <b>Screener harian gagal</b>\n"
            f"<code>{esc(str(exc)[:300])}</code>\n"
            "Snapshot hari ini tidak diperbarui. Cek log VPS.")
        return
    if res is None:
        log.info("screener harian dilewati — scan lain sedang berjalan")
        return
    if res.get("cached"):
        return
    if int(res.get("meta", {}).get("failed") or 0) > 100:
        await deps.notify_admin(
            app,
            "⚠️ <b>Screener harian selesai dengan banyak kegagalan</b>\n"
            f"{res['meta'].get('failed')} kode gagal dari "
            f"{res['meta'].get('universe', '?')}. Kemungkinan rate-limit "
            "Yahoo — cek /status.")
    chats = await asyncio.to_thread(store.get_auto_chats)
    if not chats:
        return
    txt = await deps._scan_summary_text(res, "📡 <b>SCREENER HARIAN SELURUH PASAR</b>")
    for cid in chats:
        for part in split_message(txt):
            try:
                await app.bot.send_message(
                    int(cid), part, parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True)
            except Exception:
                log.exception("kirim screener harian ke %s gagal", cid)
                break


async def weekly_journal_report(app: Application, deps, *, force: bool = False):
    """[P28] Laporan berkala mingguan (Jumat): realisasi asli vs paper."""
    now = marketcal.now_wib()
    if not force:
        if now.weekday() != 4 or not marketcal.is_trading_day(now.date()):
            return
    auto_chats = set(await asyncio.to_thread(store.get_auto_chats))
    exec_chats = set(await asyncio.to_thread(store.execution_chats))
    targets = sorted(auto_chats & exec_chats)
    title = f"📒 <b>LAPORAN BERKALA JURNAL — {now:%d %b %Y}</b>"
    for cid in targets:
        try:
            execs = await asyncio.to_thread(store.real_executions, cid, 500)
            if not execs:
                continue
            paper = await asyncio.to_thread(store.get_paper, cid)
            positions = await asyncio.to_thread(store.real_positions, cid)
            diag = jurnalmod.jurnal_diagnostics(execs)
            txt = jurnalmod.format_journal_report(
                jurnalmod.summarize_executions(execs),
                jurnalmod.summarize_paper_realized(paper),
                open_real_count=len(positions),
                periodic_title=title,
                diagnostics=diag,
            )
            for part in split_message(txt):
                await deps.send_with_retry(
                    lambda c=int(cid), p=part: app.bot.send_message(
                        c, p, parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True),
                    what="kirim laporan berkala jurnal")
        except Exception:
            log.exception("laporan berkala jurnal gagal utk %s", cid)
