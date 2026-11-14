"""Jurnal eksekusi manual Stockbit & metrik realisasi vs paper (P28).

Mencatat harga eksekusi nyata pemilik di Stockbit dibandingkan harga sinyal,
menghitung slippage nyata, win rate & profit factor REALISASI setelah biaya
BEI (0,15% beli + 0,25% jual), serta merangkum perbandingan jujur antara
portofolio ASLI (uang sungguhan) dan PAPER TRADING (latihan).

Batas keras: modul ini murni pembukuan dan perhitungan offline — TIDAK ada
auto-trading, login Stockbit, order API, atau penyimpanan kredensial.
"""
from __future__ import annotations

import datetime as dt
import re
import statistics

import pandas as pd

import config
from core.reports import esc
from core.textutil import parse_rupiah, rupiah


def calc_slippage(
    side: str,
    exec_price: float,
    signal_price: float | None,
    lots: int,
) -> tuple[float | None, float | None, float | None]:
    """Hitung slippage terhadap harga sinyal acuan.

    Konvensi tanda: positif (+) berarti eksekusi lebih mahal/merugikan vs
    harga sinyal (beli di atas sinyal / jual di bawah sinyal); negatif (-)
    berarti harga lebih hemat/menguntungkan. Return (None, None, None) bila
    harga sinyal tidak tersedia.
    """
    if signal_price is None or exec_price <= 0 or lots <= 0:
        return None, None, None
    try:
        sig = float(signal_price)
        px = float(exec_price)
        n = int(lots)
    except (TypeError, ValueError):
        return None, None, None
    if sig <= 0 or px <= 0 or n <= 0:
        return None, None, None
    diff = (px - sig) if str(side).upper() == "BELI" else (sig - px)
    pct = diff / sig * 100.0
    cost_rp = diff * n * 100.0
    return round(diff, 4), round(pct, 4), round(cost_rp)


def calc_realized_pnl(
    lots: int,
    exec_price: float,
    avg_entry: float,
) -> tuple[float, float, float]:
    """Hitung realisasi kotor, realisasi neto (setelah fee 0,40%), & % neto."""
    shares = int(lots) * 100
    gross = (float(exec_price) - float(avg_entry)) * shares
    buy_cost = float(avg_entry) * shares * (1.0 + config.IDX_BUY_FEE)
    sell_net = float(exec_price) * shares * (1.0 - config.IDX_SELL_FEE)
    net_rp = sell_net - buy_cost
    net_pct = ((sell_net / buy_cost) - 1.0) * 100.0 if buy_cost > 0 else 0.0
    return round(gross), round(net_rp), round(net_pct, 2)


def parse_exec_input(
    raw_text: str,
    *,
    default_lots: int | None = None,
) -> tuple[int, float] | None:
    """Parse input manual '5 9050', '5 lot @ 9.050', atau '9050' (+default_lots).

    Return None bila format tidak valid atau lot tidak diketahui.
    """
    s = (raw_text or "").strip()
    if not s:
        return None
    cleaned = re.sub(r"\b(lot|lbr|lembar|rp|di)\b", " ", s, flags=re.IGNORECASE)
    cleaned = cleaned.replace("@", " ").replace("x", " ").replace("×", " ")
    tokens = [tok for tok in cleaned.split() if tok]
    if len(tokens) == 1:
        if not default_lots or int(default_lots) <= 0:
            return None
        px = parse_rupiah(tokens[0])
        if px is None or px <= 0:
            return None
        return int(default_lots), float(px)
    if len(tokens) == 2:
        lot_raw = tokens[0].replace(".", "").replace(",", "")
        if not lot_raw.isdigit():
            return None
        lots = int(lot_raw)
        px = parse_rupiah(tokens[1])
        if lots <= 0 or px is None or px <= 0:
            return None
        return lots, float(px)
    return None


def _profit_factor(profits: float, losses_abs: float) -> tuple[float | None, str | None]:
    if losses_abs > 0:
        return round(profits / losses_abs, 2), None
    if profits > 0:
        return None, "belum ada rugi (semua untung)"
    return None, None


def summarize_executions(execs: list[dict]) -> dict:
    """Statistik slippage + win rate & profit factor realisasi ASLI."""
    buys = [e for e in execs if e.get("side") == "BELI"]
    sells = [e for e in execs if e.get("side") == "JUAL"]
    with_sig = [e for e in execs if e.get("slippage_pct") is not None]
    buy_sig = [e for e in buys if e.get("slippage_pct") is not None]

    slips = [float(e["slippage_pct"]) for e in with_sig]
    buy_slips = [float(e["slippage_pct"]) for e in buy_sig]
    slip_costs = [float(e.get("slippage_cost") or 0.0) for e in with_sig]

    closed = [e for e in sells if e.get("net_pnl") is not None]
    wins = sum(1 for e in closed if float(e["net_pnl"]) > 0)
    losses = sum(1 for e in closed if float(e["net_pnl"]) <= 0)
    gross_profit = sum(float(e["net_pnl"]) for e in closed if float(e["net_pnl"]) > 0)
    gross_loss = abs(sum(float(e["net_pnl"]) for e in closed if float(e["net_pnl"]) < 0))
    pf, pf_note = _profit_factor(gross_profit, gross_loss) if closed else (None, None)
    net_pcts = [float(e["net_pnl_pct"]) for e in closed if e.get("net_pnl_pct") is not None]

    return {
        "n_exec": len(execs),
        "n_buys": len(buys),
        "n_sells": len(sells),
        "n_with_signal": len(with_sig),
        "n_buy_signal": len(buy_sig),
        "avg_slippage_pct": round(statistics.mean(slips), 2) if slips else None,
        "median_slippage_pct": round(statistics.median(slips), 2) if slips else None,
        "avg_buy_slippage_pct": (round(statistics.mean(buy_slips), 2)
                                 if buy_slips else None),
        "total_slippage_rp": round(sum(slip_costs)) if with_sig else None,
        "n_closed": len(closed),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(wins / len(closed) * 100.0, 1) if closed else None,
        "profit_factor": pf,
        "pf_note": pf_note,
        "total_gross_pnl_rp": round(sum(float(e.get("gross_pnl") or 0) for e in closed)) if closed else 0,
        "total_net_pnl_rp": round(sum(float(e["net_pnl"]) for e in closed)) if closed else 0,
        "avg_net_pnl_pct": round(statistics.mean(net_pcts), 2) if net_pcts else None,
    }


def summarize_paper_realized(paper: dict[str, dict]) -> dict:
    """Statistik realisasi PAPER TRADING (terpisah total dari akun asli)."""
    open_n = 0
    closed_pcts: list[float] = []
    for rec in (paper or {}).values():
        st = rec.get("status")
        if st in ("open", "pending"):
            open_n += 1
        elif st == "closed" and rec.get("entry"):
            entry = float(rec["entry"])
            exit_px = float(rec.get("exit_price", entry))
            if entry > 0:
                buy_unit = entry * (1.0 + config.IDX_BUY_FEE)
                sell_unit = exit_px * (1.0 - config.IDX_SELL_FEE)
                closed_pcts.append((sell_unit / buy_unit - 1.0) * 100.0)

    wins = sum(1 for p in closed_pcts if p > 0)
    losses = sum(1 for p in closed_pcts if p <= 0)
    pos_pct = sum(p for p in closed_pcts if p > 0)
    neg_pct = abs(sum(p for p in closed_pcts if p < 0))
    pf, pf_note = _profit_factor(pos_pct, neg_pct) if closed_pcts else (None, None)
    return {
        "n_open": open_n,
        "n_closed": len(closed_pcts),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": (round(wins / len(closed_pcts) * 100.0, 1)
                         if closed_pcts else None),
        "profit_factor": pf,
        "pf_note": pf_note,
        "avg_net_pnl_pct": (round(statistics.mean(closed_pcts), 2)
                            if closed_pcts else None),
    }


def _fmt_pct_id(val: float | None, *, sign: bool = True) -> str:
    if val is None:
        return "tidak tersedia"
    fmt = f"{val:+.2f}%" if sign else f"{val:.1f}%"
    return fmt.replace(".", ",")


def _fmt_pf(pf: float | None, note: str | None) -> str:
    if pf is not None:
        return f"{pf:.2f}".replace(".", ",")
    return note or "belum tersedia"


def _fmt_delta_rp(val: float) -> str:
    sign = "+" if val > 0 else ("-" if val < 0 else "")
    return f"{sign}{rupiah(abs(round(val)))}"


def format_slippage_note(row: dict) -> str:
    """Ringkas slippage satu transaksi secara jujur & mudah dibaca."""
    sig = row.get("signal_price")
    spct = row.get("slippage_pct")
    scost = row.get("slippage_cost")
    if sig is None or spct is None:
        return "tanpa acuan sinyal"
    qual = "lebih mahal" if float(spct) > 0 else ("lebih hemat" if float(spct) < 0 else "pas")
    cost_str = _fmt_delta_rp(float(scost or 0.0))
    return (
        f"sinyal {rupiah(sig)} → slippage <b>{_fmt_pct_id(float(spct))}</b> "
        f"({cost_str}, {qual})"
    )


def format_journal_report(
    real_sum: dict,
    paper_sum: dict,
    *,
    open_real_count: int = 0,
    periodic_title: str | None = None,
    diagnostics: dict | None = None,
) -> str:
    """Susun laporan realisasi & slippage: ASLI vs PAPER terpisah tegas."""
    header = periodic_title or "📒 <b>JURNAL EKSEKUSI STOCKBIT &amp; REALISASI</b>"
    out = [
        header,
        "<i>Pembukuan konfirmasi manual — bukan auto-trading, tanpa login Stockbit.</i>",
        "",
        "💼 <b>1. EKSEKUSI NYATA — ASLI (uang sungguhan)</b>",
    ]
    if real_sum["n_exec"] == 0:
        out.append("• Belum ada eksekusi Stockbit yang dicatat.")
        if open_real_count:
            out.append(
                f"• Kamu punya {open_real_count} posisi terbuka di <code>/portofolio</code> "
                "(belum lewat konfirmasi jurnal sinyal)."
            )
    else:
        out.append(
            f"• Total catatan: <b>{real_sum['n_exec']}</b> eksekusi "
            f"({real_sum['n_buys']} beli · {real_sum['n_sells']} jual · "
            f"{open_real_count} posisi terbuka)"
        )
        if real_sum["n_with_signal"] > 0:
            out.append(
                f"• Slippage vs sinyal ({real_sum['n_with_signal']} eksekusi): "
                f"rata-rata <b>{_fmt_pct_id(real_sum['avg_slippage_pct'])}</b> · "
                f"median {_fmt_pct_id(real_sum['median_slippage_pct'])} · "
                f"dampak <b>{_fmt_delta_rp(real_sum['total_slippage_rp'])}</b>"
            )
            out.append("  <i>(+ = eksekusi lebih mahal dari sinyal; − = lebih hemat)</i>")
        else:
            out.append("• Slippage vs sinyal: belum ada catatan yang memuat harga sinyal.")

        if real_sum["n_closed"] > 0:
            out.append(
                f"• Realisasi tutup ({real_sum['n_closed']} kali): "
                f"Win Rate <b>{_fmt_pct_id(real_sum['win_rate_pct'], sign=False)}</b> "
                f"({real_sum['wins']}W / {real_sum['losses']}L) · "
                f"Profit Factor <b>{_fmt_pf(real_sum['profit_factor'], real_sum['pf_note'])}</b>"
            )
            out.append(
                f"• Total PnL realisasi neto: <b>{_fmt_delta_rp(real_sum['total_net_pnl_rp'])}</b> "
                f"(bruto {_fmt_delta_rp(real_sum['total_gross_pnl_rp'])} · "
                f"rata-rata {_fmt_pct_id(real_sum['avg_net_pnl_pct'])}/trade · fee 0,40%)"
            )
        else:
            out.append("• Realisasi tutup: belum ada transaksi JUAL di jurnal (belum bisa hitung Win Rate/PF).")

    if diagnostics and diagnostics.get("n_trades"):
        diag_text = format_jurnal_diagnostics(diagnostics)
        if diag_text:
            out += ["", diag_text]

    out += ["", "📋 <b>2. PAPER TRADING — LATIHAN (terpisah dari akun asli)</b>"]
    if paper_sum["n_closed"] == 0:
        out.append(
            f"• Belum ada posisi paper yang selesai "
            f"({paper_sum['n_open']} masih aktif/antre di <code>/saran list</code>)."
        )
    else:
        out.append(
            f"• Realisasi paper ({paper_sum['n_closed']} selesai · {paper_sum['n_open']} aktif): "
            f"Win Rate <b>{_fmt_pct_id(paper_sum['win_rate_pct'], sign=False)}</b> "
            f"({paper_sum['wins']}W / {paper_sum['losses']}L) · "
            f"Profit Factor <b>{_fmt_pf(paper_sum['profit_factor'], paper_sum['pf_note'])}</b> "
            "(berbasis % neto)"
        )
        out.append(
            f"• Rata-rata return neto paper: "
            f"<b>{_fmt_pct_id(paper_sum['avg_net_pnl_pct'])}</b>/trade (asumsi fee 0,40%)."
        )

    out += [
        "",
        "<b>Cara catat cepat:</b>",
        "• Tekan <b>📝 Sudah beli di Stockbit</b> setelah <code>/rencana</code>, <code>/cek</code>, <code>/beli</code>, atau <code>/sinyal KODE</code>.",
        "• Manual: <code>/jurnal beli BBCA 5 9050 [harga_sinyal]</code>",
        "• Tutup: <code>/jurnal jual BBCA 5 9500</code> · detail: <code>/jurnal riwayat</code>",
        "",
        "<i>Catatan jujur: metrik realisasi butuh jumlah transaksi cukup untuk bermakna statistik. DYOR!</i>",
    ]
    return "\n".join(out)


def format_journal_history(execs: list[dict]) -> str:
    """Daftar riwayat transaksi jurnal eksekusi asli terbaru."""
    if not execs:
        return (
            "📒 Belum ada catatan eksekusi Stockbit.\n\n"
            "Catat dengan menekan tombol <b>📝 Sudah beli di Stockbit</b> setelah "
            "cek sinyal, atau ketik <code>/jurnal beli BBCA 5 9050 9000</code>."
        )
    out = ["🧾 <b>RIWAYAT JURNAL EKSEKUSI ASLI (uang sungguhan)</b>", ""]
    for row in execs:
        side = str(row.get("side") or "?")
        icon = "🟢" if side == "BELI" else "🔴"
        ticker = esc(row.get("ticker") or "?")
        line = (
            f"{icon} {esc(row.get('trade_date') or '')} · <b>{ticker}</b> "
            f"{side} {int(row.get('lots') or 0)} lot @ {rupiah(row.get('exec_price'))}"
        )
        if row.get("source") and row.get("source") != "manual":
            line += f" <i>[{esc(row['source'])}]</i>"
        out.append(line)
        if row.get("signal_price") is not None:
            out.append("   " + format_slippage_note(row))
        if side == "JUAL" and row.get("net_pnl") is not None:
            pct = float(row.get("net_pnl_pct") or 0.0)
            out.append(
                f"   Realisasi neto: <b>{_fmt_delta_rp(float(row['net_pnl']))}</b> "
                f"({_fmt_pct_id(pct)} setelah fee 0,40%)"
            )
    out += ["", "Ringkasan Win Rate &amp; PF: <code>/jurnal</code>"]
    return "\n".join(out)


def _parse_trade_date(raw: str | dt.date | None) -> dt.date | None:
    if raw is None:
        return None
    if isinstance(raw, dt.datetime):
        return raw.date()
    if isinstance(raw, dt.date):
        return raw
    txt = str(raw).strip()
    if not txt:
        return None
    try:
        return dt.date.fromisoformat(txt[:10])
    except ValueError:
        return None


def _slice_ohlcv_window(
    df: pd.DataFrame | None,
    entry_date: str | dt.date | None,
    exit_date: str | dt.date | None,
) -> pd.DataFrame | None:
    """Ambil baris OHLCV dalam rentang [entry_date, exit_date] secara ketat."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    work = df.rename(columns={c: str(c).lower() for c in df.columns})
    if not {"high", "low", "close"}.issubset(work.columns):
        return None
    work = work[["high", "low", "close"]].apply(pd.to_numeric, errors="coerce").dropna()
    if work.empty:
        return None

    start = _parse_trade_date(entry_date)
    end = _parse_trade_date(exit_date)
    if start is not None or end is not None:
        try:
            idx_dates = pd.to_datetime(work.index).date
            mask = pd.Series(True, index=work.index)
            if start is not None:
                mask = mask & (idx_dates >= start)
            if end is not None:
                mask = mask & (idx_dates <= end)
            sliced = work[mask.values]
            if sliced.empty:
                return None
            return sliced
        except Exception:
            return None
    return work


def compute_trade_excursions(
    df: pd.DataFrame | None,
    *,
    entry_price: float,
    exit_price: float | None = None,
    sl: float | None = None,
    entry_date: str | dt.date | None = None,
    exit_date: str | dt.date | None = None,
    lots: int = 1,
) -> dict:
    """Hitung MAE, MFE, dan R-multiple (bruto & neto setelah 0,40% biaya BEI).

    Jujur terhadap data: tanpa OHLCV jendela masuk-keluar, MAE/MFE bernilai
    None (`ok=False`); tanpa SL awal yang valid (< entry), R-multiple bernilai
    None agar tidak mengarang angka risiko.
    """
    entry = float(entry_price or 0.0)
    exit_px = float(exit_price) if exit_price is not None and float(exit_price) > 0 else None
    sl_px = float(sl) if sl is not None and float(sl) > 0 else None
    n_lots = max(1, int(lots or 1))
    fee_hurdle = round((config.IDX_BUY_FEE + config.IDX_SELL_FEE) * 100.0, 2)

    gross_ret_pct = ((exit_px / entry - 1.0) * 100.0) if entry > 0 and exit_px is not None else None
    net_ret_pct = None
    if entry > 0 and exit_px is not None:
        buy_u = entry * (1.0 + config.IDX_BUY_FEE)
        sell_u = exit_px * (1.0 - config.IDX_SELL_FEE)
        net_ret_pct = (sell_u / buy_u - 1.0) * 100.0

    risk_per_share = (entry - sl_px) if (entry > 0 and sl_px is not None and sl_px < entry) else None
    r_gross = None
    r_net = None
    if risk_per_share and exit_px is not None:
        r_gross = round((exit_px - entry) / risk_per_share, 2)
        buy_u = entry * (1.0 + config.IDX_BUY_FEE)
        sell_u = exit_px * (1.0 - config.IDX_SELL_FEE)
        r_net = round((sell_u - buy_u) / risk_per_share, 2)

    window = _slice_ohlcv_window(df, entry_date, exit_date) if entry > 0 else None
    if window is None or window.empty:
        return {
            "ok": False,
            "reason": "data OHLCV jendela masuk-keluar tidak tersedia",
            "entry_price": entry,
            "exit_price": exit_px,
            "sl": sl_px,
            "lots": n_lots,
            "bars_held": 0,
            "mae_pct": None,
            "mfe_pct": None,
            "mae_r": None,
            "mfe_r": None,
            "r_multiple": r_net,
            "r_multiple_gross": r_gross,
            "gross_return_pct": round(gross_ret_pct, 2) if gross_ret_pct is not None else None,
            "net_return_pct": round(net_ret_pct, 2) if net_ret_pct is not None else None,
            "fee_hurdle_pct": fee_hurdle,
            "beat_bei_fee": (net_ret_pct > 0) if net_ret_pct is not None else None,
            "capture_ratio": None,
        }

    min_low = float(window["low"].min())
    max_high = float(window["high"].max())
    mae_pct = min(0.0, (min_low / entry - 1.0) * 100.0)
    mfe_pct = max(0.0, (max_high / entry - 1.0) * 100.0)

    mae_r = round(min(0.0, (min_low - entry) / risk_per_share), 2) if risk_per_share else None
    mfe_r = round(max(0.0, (max_high - entry) / risk_per_share), 2) if risk_per_share else None

    capture_ratio = None
    if mfe_pct > 0 and gross_ret_pct is not None:
        capture_ratio = round(gross_ret_pct / mfe_pct, 2)

    return {
        "ok": True,
        "reason": "",
        "entry_price": entry,
        "exit_price": exit_px,
        "sl": sl_px,
        "lots": n_lots,
        "bars_held": int(len(window)),
        "min_low": round(min_low, 2),
        "max_high": round(max_high, 2),
        "mae_pct": round(mae_pct, 2),
        "mfe_pct": round(mfe_pct, 2),
        "mae_r": mae_r,
        "mfe_r": mfe_r,
        "r_multiple": r_net,
        "r_multiple_gross": r_gross,
        "gross_return_pct": round(gross_ret_pct, 2) if gross_ret_pct is not None else None,
        "net_return_pct": round(net_ret_pct, 2) if net_ret_pct is not None else None,
        "fee_hurdle_pct": fee_hurdle,
        "beat_bei_fee": (net_ret_pct > 0) if net_ret_pct is not None else None,
        "capture_ratio": capture_ratio,
    }


def _chronological_execs(execs: list[dict]) -> list[dict]:
    """Urutkan transaksi dari terlama ke terbaru (aman untuk DB DESC maupun list uji)."""
    if not execs:
        return []
    if all(e.get("id") is not None for e in execs):
        return sorted(execs, key=lambda e: int(e["id"]))
    return list(execs)


def jurnal_diagnostics(
    execs: list[dict],
    bars_by_ticker: dict[str, pd.DataFrame] | None = None,
) -> dict:
    """Evaluasi kualitas eksekusi beli & jual nyata: MAE, MFE, R-multiple vs fee BEI 0,40%."""
    ordered = _chronological_execs(execs)
    bars_map = {str(k).upper(): v for k, v in (bars_by_ticker or {}).items()}
    fee_hurdle = round((config.IDX_BUY_FEE + config.IDX_SELL_FEE) * 100.0, 2)

    cycles: dict[str, dict] = {}
    trades: list[dict] = []
    buy_slips: list[float] = []
    sell_slips: list[float] = []

    for e in ordered:
        t = str(e.get("ticker") or "").upper()
        side = str(e.get("side") or "").upper()
        lots_e = int(e.get("lots") or 0)
        px = float(e.get("exec_price") or 0.0)
        if e.get("slippage_pct") is not None:
            if side == "BELI":
                buy_slips.append(float(e["slippage_pct"]))
            elif side == "JUAL":
                sell_slips.append(float(e["slippage_pct"]))

        st = cycles.setdefault(t, {"lots": 0, "cost": 0.0, "entry_date": None, "sl": None})
        if side == "BELI" and lots_e > 0 and px > 0:
            if st["lots"] <= 0:
                st["entry_date"] = e.get("trade_date")
            st["lots"] += lots_e
            st["cost"] += lots_e * 100.0 * px
            if e.get("sl") is not None and float(e["sl"]) > 0:
                st["sl"] = float(e["sl"])
        elif side == "JUAL" and lots_e > 0 and px > 0:
            avg_entry = (
                st["cost"] / (st["lots"] * 100.0)
                if st["lots"] > 0
                else (float(e["avg_entry"]) if e.get("avg_entry") else None)
            )
            if not avg_entry or avg_entry <= 0:
                continue
            entry_dt = st["entry_date"] or e.get("entry_date")
            sl_val = st["sl"] or (float(e["sl"]) if e.get("sl") else None)
            exit_dt = e.get("trade_date")
            exc = compute_trade_excursions(
                bars_map.get(t),
                entry_price=avg_entry,
                exit_price=px,
                sl=sl_val,
                entry_date=entry_dt,
                exit_date=exit_dt,
                lots=lots_e,
            )
            exc["ticker"] = t
            exc["entry_date"] = entry_dt
            exc["exit_date"] = exit_dt
            exc["net_pnl_pct"] = e.get("net_pnl_pct") if e.get("net_pnl_pct") is not None else exc["net_return_pct"]
            trades.append(exc)

            if st["lots"] > 0:
                sold = min(st["lots"], lots_e)
                st["lots"] -= sold
                st["cost"] = st["lots"] * 100.0 * avg_entry if st["lots"] > 0 else 0.0
                if st["lots"] <= 0:
                    st.update({"lots": 0, "cost": 0.0, "entry_date": None, "sl": None})

    with_ohlcv = [tr for tr in trades if tr["ok"]]
    with_r = [tr for tr in trades if tr["r_multiple"] is not None]
    with_cap = [tr for tr in with_ohlcv if tr["capture_ratio"] is not None]
    maes = [tr["mae_pct"] for tr in with_ohlcv if tr["mae_pct"] is not None]
    mfes = [tr["mfe_pct"] for tr in with_ohlcv if tr["mfe_pct"] is not None]
    mae_rs = [tr["mae_r"] for tr in with_ohlcv if tr["mae_r"] is not None]
    mfe_rs = [tr["mfe_r"] for tr in with_ohlcv if tr["mfe_r"] is not None]
    r_nets = [tr["r_multiple"] for tr in with_r]
    r_grosses = [tr["r_multiple_gross"] for tr in with_r if tr["r_multiple_gross"] is not None]
    caps = [tr["capture_ratio"] for tr in with_cap]

    beat_fee = sum(1 for tr in trades if tr.get("beat_bei_fee") is True)
    eaten_by_fee = sum(
        1 for tr in trades
        if tr.get("gross_return_pct") is not None
        and tr["gross_return_pct"] > 0
        and tr.get("beat_bei_fee") is False
    )

    notes: list[str] = []
    if trades and eaten_by_fee:
        notes.append(
            f"{eaten_by_fee}/{len(trades)} transaksi jual untung tipis bruto "
            f"tetapi rugi neto tergerus biaya BEI ±{fee_hurdle:.2f}%."
        )
    if trades and not with_ohlcv:
        notes.append("Histori OHLCV belum tersedia untuk menghitung MAE/MFE nyata.")
    if trades and len(with_r) < len(trades):
        notes.append(
            f"{len(trades) - len(with_r)}/{len(trades)} trade tanpa SL awal tersimpan — "
            "R-multiple tidak dihitung untuk posisi tanpa SL."
        )
    if caps and statistics.mean(caps) < 0.45 and mfes and statistics.mean(mfes) >= 3.0:
        notes.append(
            f"Rata-rata MFE +{statistics.mean(mfes):.1f}% tetapi hanya menangkap "
            f"{statistics.mean(caps) * 100:.0f}% puncaknya — pertimbangkan scale-out TP1 & Chandelier trailing."
        )
    if buy_slips and statistics.mean(buy_slips) > 0.5:
        notes.append(
            f"Slippage beli rata-rata +{statistics.mean(buy_slips):.2f}% — "
            "patuhi batas kejar agar entry tidak tergerus sebelum biaya BEI."
        )

    return {
        "n_trades": len(trades),
        "n_with_ohlcv": len(with_ohlcv),
        "n_with_r": len(with_r),
        "fee_hurdle_pct": fee_hurdle,
        "beat_fee_count": beat_fee,
        "eaten_by_fee_count": eaten_by_fee,
        "avg_mae_pct": round(statistics.mean(maes), 2) if maes else None,
        "avg_mfe_pct": round(statistics.mean(mfes), 2) if mfes else None,
        "avg_mae_r": round(statistics.mean(mae_rs), 2) if mae_rs else None,
        "avg_mfe_r": round(statistics.mean(mfe_rs), 2) if mfe_rs else None,
        "avg_r_multiple": round(statistics.mean(r_nets), 2) if r_nets else None,
        "avg_r_gross": round(statistics.mean(r_grosses), 2) if r_grosses else None,
        "expectancy_r": round(statistics.mean(r_nets), 2) if r_nets else None,
        "avg_capture_ratio": round(statistics.mean(caps), 2) if caps else None,
        "avg_buy_slippage_pct": round(statistics.mean(buy_slips), 2) if buy_slips else None,
        "avg_sell_slippage_pct": round(statistics.mean(sell_slips), 2) if sell_slips else None,
        "trades": trades,
        "notes": notes,
    }


def format_jurnal_diagnostics(diag: dict) -> str:
    """Format ringkasan diagnostik MAE/MFE/R-multiple & efisiensi fee BEI."""
    if not diag or not diag.get("n_trades"):
        return ""
    lines = [
        f"• Diagnostik kualitas eksekusi ({diag['n_trades']} trade tutup · "
        f"ambang impas fee BEI {diag['fee_hurdle_pct']:.2f}%):"
    ]
    if diag.get("avg_r_multiple") is not None:
        r_net = f"{diag['avg_r_multiple']:+.2f}R".replace(".", ",")
        r_gr = f"{diag['avg_r_gross']:+.2f}R".replace(".", ",") if diag.get("avg_r_gross") is not None else "—"
        lines.append(
            f"  R-multiple rata-rata: <b>{r_net}</b> neto (bruto {r_gr}, dari {diag['n_with_r']} trade ber-SL)"
        )
    else:
        lines.append("  R-multiple rata-rata: tidak tersedia (SL awal belum tercatat)")

    if diag.get("n_with_ohlcv"):
        mae = _fmt_pct_id(diag.get("avg_mae_pct"))
        mfe = _fmt_pct_id(diag.get("avg_mfe_pct"))
        cap = (
            f" · tangkap {diag['avg_capture_ratio'] * 100:.0f}% MFE"
            if diag.get("avg_capture_ratio") is not None else ""
        )
        lines.append(f"  MAE rata-rata {mae} · MFE rata-rata {mfe}{cap}")
    else:
        lines.append("  MAE / MFE: data OHLCV tidak tersedia")

    for note in diag.get("notes") or []:
        lines.append(f"  💡 {esc(note)}")
    return "\n".join(lines)
