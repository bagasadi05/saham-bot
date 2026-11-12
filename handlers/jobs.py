"""Job terjadwal: alert 30 menit, autoscan pagi, screener 16:15, backup.

Dependensi bot (`_scan_with_guard`, `_scan_summary_text`, `notify_admin`,
`saran_scan_text`, `fetch_and_analyze`, `build_quick`, `send_with_retry`, dan
`screener`/config) diakses lewat `deps`, supaya monkeypatch di tes tetap
bekerja dan tidak ada impor melingkar.
"""
from __future__ import annotations

import asyncio
import logging

import pandas as pd
from telegram.constants import ParseMode
from telegram.ext import Application

from core import (alerts, analysis, data, fundamental, marketcal, powerscore,
                  saran as saranmod, store)
from core.reports import esc, fmt_rp
from core.textutil import split_message

log = logging.getLogger("sahambuddy")


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
                saranmod.fill_pending, chat_id, bars_map, modal)
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


async def scan_alerts(app: Application, deps):
    """Job berkala: alert + posisi latihan, hanya saat bursa buka."""
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
            fresh = await asyncio.to_thread(
                store.update_alert_state, chat_id, symbol, hit)
            if not fresh:
                continue
            snap = analysis.last_snapshot(ind)
            sc = analysis.score_stock(ind)
            await app.bot.send_message(
                int(chat_id),
                alerts.build_alert_message(symbol, sorted(fresh), snap, sc),
                parse_mode=ParseMode.HTML)
            await asyncio.sleep(.3)
        except Exception:
            log.exception("scan alert error %s (chat %s)", symbol, chat_id)

    # 2) paper trading — isi antrean 'pending' lalu cek SL/TP posisi terbuka
    papers: dict[str, dict[str, dict]] = {}
    for chat_id, symbol, rec in await asyncio.to_thread(
            store.all_papers):
        papers.setdefault(chat_id, {})[symbol] = rec
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
            msgs += await asyncio.to_thread(saranmod.fill_pending, chat_id, bars_map, modal)
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
