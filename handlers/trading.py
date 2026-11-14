"""Handler trading: /saran, /backtest, /beli, /jual.

Dependensi bot (`fetch_and_analyze`, `gather_candidate`, formatter, menu,
`build_verdict`, `TMP_DIR`) diterima lewat `deps`; modul inti diimpor langsung.
"""
from __future__ import annotations

import asyncio
import logging

import pandas as pd
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import backtest as btmod
from core import (analysis, btcache, data, fundamental, marketcal, news,
                  pricing, regime, reports, saran as saranmod, store)

log = logging.getLogger("sahambuddy")


async def do_saran(update: Update, deps, sub: str = "",
                   list_only: bool = False,
                   from_menu: bool = False):
    msg = update.effective_message
    chat_id = update.effective_chat.id
    if list_only or sub in ("list", "posisi"):
        lines = await asyncio.to_thread(saranmod.paper_summary, chat_id)
        await deps.reply_long(
            msg,
            "\n".join(lines) or "Belum ada posisi latihan.",
            disable_web_page_preview=True,
            reply_markup=deps.home_markup() if from_menu else None,
        )
        return
    if not await deps.cooldown_ok(update, "saran"):
        return
    if sub == "lq45":
        if len(saranmod.LQ45) < 30:
            await msg.reply_text("❌ Daftar LQ45 kosong.")
            return
        ok = await msg.reply_text(
            f"⏳ Memindai seluruh LQ45 ({len(saranmod.LQ45)} emiten) — "
            "kira-kira 1-2 menit; progres dikirim tiap 10 emiten…"
        )

        async def _prog(done, total):
            try:
                await ok.edit_text(f"⏳ LQ45: {done}/{total} emiten diproses…")
            except Exception:
                pass

        txt = await deps.saran_scan_text(chat_id, saranmod.LQ45, _prog)
    else:
        wl = await asyncio.to_thread(store.get_watchlist, chat_id)
        universe = (wl + [t for t in saranmod.DEFAULT_UNIVERSE if t not in wl])[:saranmod.MAX_SCAN]
        if not from_menu:
            await msg.reply_text(
                f"🔍 Memindai {len(universe)} emiten (teknikal+fundamental+berita), 1-2 menit..."
            )
        txt = await deps.saran_scan_text(chat_id, universe)
    if txt is None:
        await msg.reply_text(
            "❌ Data sumber error, coba lagi nanti.",
            reply_markup=deps.home_markup() if from_menu else None,
        )
        return
    await deps.reply_long(
        msg,
        txt,
        disable_web_page_preview=True,
        reply_markup=deps.home_markup() if from_menu else None,
    )


async def cmd_saran(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    """Pemindaian harian: kandidat BUY berline entry/SL/TP + daftar HINDARI."""
    args = deps.tokens(update)
    sub = args[0].lower() if args else ""
    await do_saran(update, deps, sub=sub)


async def saran_scan_text(chat_id: int, deps,
                          universe: list[str] | None = None,
                          progress=None) -> str | None:
    """[P6] Inti pemindaian /saran — reusable untuk auto-scan pagi & /saran lq45.

    universe None = default (watchlist + default, cap MAX_SCAN).
    progress: async callback(done, total) untuk pesan '10/45 ...' bertahap.
    Emiten dianalisis paralel berbatas (`deps.SCAN_CONCURRENCY`) — hasil tetap urut
    sesuai universe, dan satu emiten gagal tidak membatalkan sisanya.
    Return teks ringkasan, atau None kalau semua sumber gagal."""
    real_pos = await asyncio.to_thread(store.real_positions, chat_id)
    open_execs = await asyncio.to_thread(store.open_executions_by_ticker, chat_id)
    if universe is None:
        wl = await asyncio.to_thread(store.get_watchlist, chat_id)
        held_syms = [
            str(p.get("ticker") or "").upper()
            for p in real_pos
            if int(p.get("lots") or 0) > 0 and p.get("ticker")
        ]
        seed = list(dict.fromkeys(held_syms + list(wl) + list(saranmod.DEFAULT_UNIVERSE)))
        universe = seed[:saranmod.MAX_SCAN]
    modal = await asyncio.to_thread(store.get_modal, chat_id)
    total = len(universe)
    done = 0
    report_at = 0
    sem = asyncio.Semaphore(deps.SCAN_CONCURRENCY)

    async def scan_one(t: str) -> dict | None:
        """Satu emiten, paralel dibatasi semaphore agar Yahoo tidak dibanjiri."""
        nonlocal done, report_at
        async with sem:
            try:
                a = await deps.gather_candidate(t)
            except Exception:
                # satu emiten bermasalah tidak boleh membatalkan seluruh pemindaian
                log.exception("pemindaian %s gagal", t)
                a = None
            await asyncio.sleep(deps.SCAN_PAUSE)
        done += 1
        should_report = bool(progress) and (done - report_at >= 10 or done == total)
        if should_report:
            report_at = done
            try:
                await progress(done, total)
            except Exception:
                pass
        return a

    results = await asyncio.gather(*(scan_one(t) for t in universe))
    # urutan universe dipertahankan (bukan urutan selesai) supaya hasil stabil
    analyzed = [a for a in results if a]
    latest = {a["res"]["ticker"]: a["res"]["ind"].iloc[-1] for a in analyzed}
    if not analyzed:
        return None

    # posisi yang masih hidup ikut dihitung supaya batas portofolio dihormati
    paper = await asyncio.to_thread(store.get_paper, chat_id)
    open_recs = {t: r for t, r in paper.items()
                 if r.get("status") in ("open", "pending")}
    reg = await asyncio.to_thread(getattr(deps, "regime", regime).fetch_ihsg_regime)
    result = await asyncio.to_thread(
        saranmod.pick, analyzed, modal, open_recs, market_regime=reg
    )
    paper_msgs = await asyncio.to_thread(
        saranmod.update_paper, chat_id, latest
    )  # notifikasi TP/SL posisi lama

    scale = float(result.get("exposure_scale", 1.0))
    exp_pct_txt = f"{scale * 100:g}%"
    eff_risk_pct = saranmod.BASE_RISK_PCT * scale * 100.0
    risk_txt = f"{eff_risk_pct:g}"
    today = marketcal.now_wib().date()

    bar_dates: list[str] = []
    stale_count = 0
    for a in analyzed:
        r_a = a.get("res") or {}
        if r_a.get("stale") or r_a.get("stale_note"):
            stale_count += 1
        b_d = r_a.get("as_of")
        if not b_d:
            ind_a = r_a.get("ind")
            if ind_a is not None and not ind_a.empty and hasattr(ind_a.index[-1], "strftime"):
                try:
                    b_d = ind_a.index[-1].strftime("%Y-%m-%d")
                except Exception:
                    b_d = str(ind_a.index[-1])[:10]
        if b_d:
            bar_dates.append(str(b_d)[:10])
    uniq_bars = sorted(set(bar_dates))
    bar_lbl = (
        uniq_bars[-1]
        if len(uniq_bars) == 1
        else (f"{uniq_bars[0]} s.d. {uniq_bars[-1]}" if uniq_bars else today.isoformat())
    )
    stale_lbl = f" · ⚠️ {stale_count} dari cache" if stale_count else ""

    lines = [f"🧭 <b>RINGKASAN SARAN HARIAN — {pd.Timestamp.now(tz=deps.WIB):%d %b %Y}</b>",
             f"🔎 <i>Data bar terakhir: {deps.esc(bar_lbl)} WIB{deps.esc(stale_lbl)}</i>",
             "Skema: entry di open sesi berikutnya | SL 1×ATR | TP 2×ATR | "
             f"biaya beli {saranmod.BUY_FEE * 100:.2f}% & jual "
             f"{saranmod.SELL_FEE * 100:.2f}% | modal latihan {saranmod.fmt(modal)} "
             "(ubah: /modal 10000000)",
             reports.regime_banner(reg),
             f"\nPosisi latihan terbuka: {result['open_count']}/{result['max_open']} "
             f"(maks {saranmod.MAX_PER_SECTOR} per sektor · eksposur {exp_pct_txt} · "
             f"risiko {risk_txt}%/posisi)\n"]
    if result["picks"]:
        await asyncio.to_thread(
            saranmod.queue_paper, chat_id, result["picks"], today, risk_scale=scale
        )
        label = ("Kandidat (urut skor relatif antar-emiten — IC OOS +0,03, "
                 "tipis; setelah biaya ≈ 0)"
                 if result.get("mode") == "relatif"
                 else "Kandidat (urut PowerScore — universe terlalu kecil "
                      "untuk skor relatif)")
        lines.append(f"{label}, diantre sebagai posisi latihan:")
        for c in result["picks"]:
            fund_txt = "—" if c["fund"] is None else f"{c['fund']:+.0f}"
            news_txt = "—" if c["news"] is None else f"{c['news']:+.0f}"
            rel_txt = ""
            if c.get("rel_ok") and c.get("rel_pct") is not None:
                rel_txt = (f"\n      📊 Relatif {c['rel_pct']:.0f}/100 "
                           f"(kuintil {c['rel_quintil']}/5)")
            lines.append(
                f"  {c['emoji']} <b>{deps.esc(c['ticker'])}</b> power {c['power']:+d}% "
                f"(tek {c['tech']:+.0f}, fund {fund_txt}, berita {news_txt})"
                f"{rel_txt}\n"
                f"      Entry {saranmod.fmt(c['entry'])} | SL {saranmod.fmt(c['sl'])} | "
                f"TP {saranmod.fmt(c['tp'])}")
            # [P5] ukuran posisi: risiko 1% modal (0,5% saat IHSG defensif), floor lot 100
            if "lots" in c:
                if c["lots"] > 0:
                    lines.append(
                        f"      💼 Ukuran (risiko {risk_txt}% modal, eksposur {exp_pct_txt}): "
                        f"{c['lots']} lot ({c['shares']} lembar) ≈ {saranmod.fmt(c['value'])} | "
                        f"risiko nyata {saranmod.fmt(c['real_risk'])}")
                else:
                    lines.append(
                        f"      💼 Ukuran: <i>risiko/lembar melebihi {risk_txt}% modal — "
                        "butuh minimal 1 lot; naikkan modal (mis. "
                        f"{saranmod.fmt(min(int(modal) * 3, 100_000_000))}) "
                        "atau lewati ini</i>")
    else:
        lines.append("Tidak ada kandidat layak BUY hari ini "
                     "(semua skor tak mencukupi / terkena filter jebakan momentum).")

    hold_notes = list(result.get("hold_notes") or [])
    # Tinjau juga posisi asli (real_positions + open_executions_by_ticker)
    analyzed_map = {str((a.get("res") or {}).get("ticker") or "").upper(): a for a in analyzed}
    cand_rel_map: dict[str, float] = {}
    for c_item in (result.get("picks") or []) + (result.get("avoid") or []):
        if c_item.get("ticker") and c_item.get("rel_pct") is not None:
            cand_rel_map[str(c_item["ticker"]).upper()] = float(c_item["rel_pct"])
    for h_item in hold_notes:
        if h_item.get("ticker") and h_item.get("rel_pct") is not None:
            cand_rel_map[str(h_item["ticker"]).upper()] = float(h_item["rel_pct"])

    real_hold_lines: list[str] = []
    for pos in real_pos:
        t_pos = str(pos.get("ticker") or "").upper()
        lots_pos = int(pos.get("lots") or 0)
        if not t_pos or lots_pos <= 0:
            continue
        avg_px = float(pos.get("avg_price") or 0.0)
        a_pos = analyzed_map.get(t_pos)
        r_pos = (a_pos or {}).get("res") or {}
        snap_pos = r_pos.get("snap") or {}
        ind_pos = r_pos.get("ind")
        cur_px = float(snap_pos.get("close") or avg_px)
        atr_pos = None
        try:
            if ind_pos is not None and "atr14" in ind_pos and len(ind_pos) >= 1:
                v_a = float(ind_pos["atr14"].iloc[-1])
                if v_a > 0:
                    atr_pos = v_a
        except Exception:
            atr_pos = None
        ex_rec = (open_execs or {}).get(t_pos)
        sl_st = float(ex_rec["sl"]) if ex_rec and ex_rec.get("sl") else None
        tp_st = float(ex_rec["tp"]) if ex_rec and ex_rec.get("tp") else None
        d_raw = str((ex_rec or {}).get("trade_date") or pos.get("updated_at") or "")[:10] or None
        exit_g = saranmod.build_exit_guidance(
            t_pos,
            avg_px if avg_px > 0 else cur_px,
            lots=lots_pos,
            atr=atr_pos,
            sl=sl_st,
            tp=tp_st,
            close=cur_px,
            df=ind_pos,
            entry_date=d_raw,
            today=today,
        )
        rel_val = cand_rel_map.get(t_pos)
        if rel_val is None and hasattr(deps, "_snapshot_row"):
            try:
                s_row = await asyncio.to_thread(deps._snapshot_row, t_pos)
                if s_row and s_row.get("rel_pct") is not None:
                    rel_val = float(s_row["rel_pct"])
            except Exception:
                rel_val = None
        eff_sl = float(exit_g.get("effective_sl") or exit_g.get("sl_initial") or 0.0)
        trail = exit_g.get("chandelier") or {}
        so = exit_g.get("scale_out") or {}
        ts = exit_g.get("time_stop") or {}
        days_h = ts.get("days_held")
        net_pct = float(exit_g.get("pnl_net_pct") or 0.0)
        if eff_sl > 0 and cur_px <= eff_sl:
            act_icon, act_lbl = "🔴", "JUAL / CUT LOSS"
            act_note = f"harga {saranmod.fmt(cur_px)} <= SL efektif {saranmod.fmt(eff_sl)}"
        elif trail.get("hit") and trail.get("stop"):
            act_icon, act_lbl = "🟠", "PERTIMBANGKAN JUAL"
            act_note = f"Chandelier trailing SL {saranmod.fmt(trail['stop'])} ditembus"
        elif so.get("tp1_reached") and so.get("tp1"):
            act_icon, act_lbl = "🟡", "SCALE-OUT 50% (TP1)"
            act_note = (
                f"TP1 {saranmod.fmt(so['tp1'])} tercapai → geser SL sisa ke "
                f"BEP+fee {saranmod.fmt(so.get('bep_sl') or exit_g.get('bep_sl') or avg_px)}"
            )
        elif ts.get("triggered"):
            act_icon, act_lbl = "🟠", "EVALUASI TIME-STOP"
            act_note = f"pegang {days_h} hari bursa (>= {ts.get('max_hold_days', 20)}h)"
        elif rel_val is not None and rel_val < saranmod.HOLD_BAND_PCT:
            act_icon, act_lbl = "🔁", "EVALUASI ROTASI"
            act_note = f"relatif {rel_val:.0f}/100 di bawah band tahan {saranmod.HOLD_BAND_PCT:.0f}"
        else:
            act_icon, act_lbl = "🟢", "TAHAN"
            rel_bit = f"relatif {rel_val:.0f}/100 · " if rel_val is not None else ""
            act_note = f"{rel_bit}SL efektif {saranmod.fmt(eff_sl)}"
        real_hold_lines.append(
            f"  {act_icon} <b>{deps.esc(t_pos)}</b> [Portofolio {lots_pos} lot @ "
            f"{saranmod.fmt(avg_px)}, net {net_pct:+.1f}%] → <b>{act_lbl}</b> "
            f"({deps.esc(act_note)})"
        )

    if hold_notes or real_hold_lines:
        lines.append("\n📌 <b>TINJAU POSISI TERBUKA (band tahan top 60%):</b>")
        for rl in real_hold_lines:
            lines.append(rl)
        for h in hold_notes:
            icon = "🟢" if h["action"] == "TAHAN" else "🔁"
            lines.append(
                f"  {icon} <b>{deps.esc(h['ticker'])}</b> "
                f"relatif {h['rel_pct']:.0f}/100 → <b>{h['action']}</b> ({deps.esc(h['reason'])})"
            )
    lines.append("\n🚫 HINDARI (terlemah):")
    for c in result["avoid"]:
        rel_txt = (f"relatif {c['rel_pct']:.0f}/100"
                   if c.get("rel_ok") and c.get("rel_pct") is not None
                   else f"{c['power']:+d}%")
        reason = ("jebakan momentum" if c["ret20d"] > saranmod.TRAP_RET20D
                  else "terlemah di universe")
        lines.append(f"  {c['emoji']} <b>{deps.esc(c['ticker'])}</b> {rel_txt} — {reason}")
    skipped = result.get("skipped") or []
    if skipped:
        lines.append("")
        lines.append(f"⚠️ {len(skipped)} emiten dilewati karena datanya tidak lengkap: "
                     + ", ".join(deps.esc(t) for t in skipped[:10])
                     + ("…" if len(skipped) > 10 else ""))
    if paper_msgs:
        lines.append("")
        lines.extend(paper_msgs)
    lines.append("\n⏱️ Evaluasi rotasi tiap ±20–40 hari bursa; tahan posisi selama "
                 "skor relatif ≥40/100 (top 60%) agar biaya 0,40% tidak menggerus hasil.")
    lines.append("⚠️ Alat bantu latihan, bukan saran formal. DYOR!")
    return "\n".join(lines)


async def do_backtest(update: Update, deps, ticker: str,
                      from_menu: bool = False):
    if not await deps.cooldown_ok(update, "backtest"):
        return
    msg = update.effective_message
    try:
        t = data.normalize_ticker(ticker).replace(".JK", "")
    except ValueError as e:
        await msg.reply_text(f"❌ {deps.esc(e)}", parse_mode=ParseMode.HTML, reply_markup=deps.home_markup())
        return
    if not from_menu:
        await msg.reply_text(
            "⏳ Menghitung backtest 5 tahun + simulasi transaksi, tunggu 1-2 menit..."
        )
    parts = []
    try:
        bt = await asyncio.to_thread(btmod.backtest, t)
        parts.append(
            f"🧪 <b>Backtest statistik {deps.esc(t)}</b>\n"
            f"<pre>{deps.esc(btmod.summarize(bt))}</pre>"
        )
    except ValueError as e:
        await msg.reply_text(f"❌ {deps.esc(e)}", parse_mode=ParseMode.HTML, reply_markup=deps.home_markup())
        return
    try:
        import backtest_real
        stats = await asyncio.to_thread(backtest_real.run_backtest, t)
        summary = backtest_real.summarize_stats(t, stats)
        await asyncio.to_thread(btcache.save_summary, deps.TMP_DIR, summary)

        def _f(v, tpl):
            return "—" if v is None else tpl.format(v)

        bh_note = (
            f" (total {summary['bh_total']:+.1f}%)"
            if summary.get("bh_total") is not None else ""
        )
        parts.append(
            f"<b>Simulasi transaksi nyata</b> (buy sinyal BUY, SL 1×ATR, "
            f"TP 2×ATR, biaya beli {backtest_real.BUY_FEE * 100:.2f}% / "
            f"jual {backtest_real.SELL_FEE * 100:.2f}%):"
        )
        parts.append(
            "<pre>"
            f"Return/thn      : {_f(summary['ret_ann'], '{:+.1f}%')}\n"
            f"Buy &amp; hold/thn  : {_f(summary['bh_ann'], '{:+.1f}%')}{bh_note}\n"
            f"Transaksi       : {summary['trades']}\n"
            f"Win-rate        : {_f(summary['win_rate'], '{:.1f}%')}\n"
            f"Profit factor   : {_f(summary['profit_factor'], '{:.2f}')}\n"
            f"Expectancy/trade: {_f(summary.get('expectancy'), '{:+.2f}%')}\n"
            f"Max drawdown    : {_f(summary['max_dd'], '{:.1f}%')}"
            "</pre>"
        )
    except ValueError as e:
        parts.append(f"⚠️ Simulasi transaksi dilewati: {deps.esc(e)}")
    except Exception:
        log.exception("backtest_real gagal utk %s", t)
        parts.append("⚠️ Simulasi transaksi gagal dijalankan (library backtesting.py).")
    parts.append(
        "⚠️ Skor = deskripsi kondisi saat itu, bukan ramalan. "
        "Uji dengan beberapa emiten sebelum menarik kesimpulan."
    )
    await deps.reply_long(
        msg,
        "\n".join(parts),
        disable_web_page_preview=True,
        reply_markup=deps.result_markup("backtest", t),
    )


async def cmd_backtest(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    args = deps.tokens(update)
    if not args:
        wl = await asyncio.to_thread(store.get_watchlist,
                                     update.effective_chat.id)
        await update.effective_message.reply_text(
            "📈 Backtest — pilih emiten:\n"
            "Tiap hari 5 tahun terakhir bot berteori, lalu dicek hasil 20 hari kemudian.",
            reply_markup=deps.ticker_pick_markup("backtest", wl))
        return
    await do_backtest(update, deps, args[0], from_menu=False)


async def do_tanya(update: Update, deps, ticker: str, focus: str):
    """Jawab 'beli/jual gak?' untuk satu emiten (focus: beli/jual/umum)."""
    msg = update.effective_message
    try:
        res = await deps.fetch_and_analyze(ticker)
    except ValueError as e:
        await msg.reply_text(f"❌ {deps.esc(e)}", parse_mode=ParseMode.HTML, reply_markup=deps.home_markup())
        return
    t = res["ticker"]
    fund_f = asyncio.to_thread(fundamental.fetch_fundamentals, t)
    news_f = asyncio.to_thread(news.fetch_news, t)
    bt_f = asyncio.to_thread(btcache.load_summary, deps.TMP_DIR, t)
    try:
        fund, items, bt_sum = await asyncio.gather(
            fund_f, news_f, bt_f, return_exceptions=True)
    except Exception:
        fund, items, bt_sum = None, None, None
    if isinstance(fund, Exception) or fund is None:
        fund, fsc = None, None
    else:
        fsc = await asyncio.to_thread(fundamental.score_fundamentals, fund)
    if isinstance(items, Exception) or not items:
        nsent = None
    else:
        nsent = await asyncio.to_thread(news.news_sentiment, items)
    if isinstance(bt_sum, Exception):
        bt_sum = None
    weekly = deps._weekly_from_daily(res["ind"], res["score"]["pct"])
    if weekly is None:
        try:
            wdf = await asyncio.to_thread(data.fetch_weekly, t)
            weekly = analysis.weekly_trend(wdf, res["score"]["pct"])
        except Exception:
            weekly = None
    snap = res.get("snap") or {}
    ind = res.get("ind")
    sig_px = snap.get("close")
    atr = None
    try:
        if ind is not None and "atr14" in ind:
            val = float(ind["atr14"].iloc[-1])
            atr = val if val > 0 else None
    except Exception:
        atr = None
    chat_id = update.effective_chat.id if update.effective_chat else 0
    positions = (
        await asyncio.to_thread(store.real_positions, chat_id) if chat_id else []
    )
    open_execs = (
        await asyncio.to_thread(store.open_executions_by_ticker, chat_id)
        if chat_id
        else {}
    )
    snap_row = {}
    if hasattr(deps, "_snapshot_row"):
        try:
            snap_row = await asyncio.to_thread(deps._snapshot_row, t)
        except Exception:
            snap_row = {}
    rel_pct = snap_row.get("rel_pct") if isinstance(snap_row, dict) else None
    if sig_px and float(sig_px) > 0:
        px = float(pricing.round_tick(float(sig_px)))
        sl_lvl = float(saranmod.stop_price(px, atr))
        tp_lvl = float(saranmod.target_price(px, atr))
        intent = "jual" if focus == "jual" else "beli"
        await asyncio.to_thread(
            store.save_signal_context,
            chat_id, t, f"tanya_{focus}", px,
            sl_lvl, tp_lvl, None, marketcal.now_wib().date().isoformat(),
            intent=intent,
        )
    try:
        verdict_txt = deps.build_verdict(
            res,
            fsc,
            nsent,
            focus,
            bt_sum,
            weekly=weekly,
            positions=positions,
            open_execs=open_execs,
            rel_pct=rel_pct,
        )
    except TypeError:
        verdict_txt = deps.build_verdict(res, fsc, nsent, focus, bt_sum, weekly=weekly)
    await deps.reply_long(
        msg,
        verdict_txt,
        reply_markup=deps.result_markup(f"tanya_{focus}", t),
    )


async def cmd_beli(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    """Jawab 'layak beli gak?' untuk satu emiten."""
    args = deps.tokens(update)
    if not args:
        wl = await asyncio.to_thread(store.get_watchlist,
                                     update.effective_chat.id)
        await update.effective_message.reply_text(
            "🤔 Tanya: Layak Beli? — pilih saham (tidak membeli otomatis):",
            reply_markup=deps.ticker_pick_markup("beli", wl))
        return
    await do_tanya(update, deps, args[0], "beli")


async def cmd_jual(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deps):
    """Jawab 'layak jual gak?' untuk satu emiten (sudut pandang yang pegang)."""
    args = deps.tokens(update)
    if not args:
        wl = await asyncio.to_thread(store.get_watchlist,
                                     update.effective_chat.id)
        await update.effective_message.reply_text(
            "🤔 Tanya: Layak Jual? — pilih saham yang kamu pegang:",
            reply_markup=deps.ticker_pick_markup("jual", wl))
        return
    await do_tanya(update, deps, args[0], "jual")
