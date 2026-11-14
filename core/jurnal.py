"""Jurnal eksekusi manual Stockbit & metrik realisasi vs paper (P28).

Mencatat harga eksekusi nyata pemilik di Stockbit dibandingkan harga sinyal,
menghitung slippage nyata, win rate & profit factor REALISASI setelah biaya
BEI (0,15% beli + 0,25% jual), serta merangkum perbandingan jujur antara
portofolio ASLI (uang sungguhan) dan PAPER TRADING (latihan).

Batas keras: modul ini murni pembukuan dan perhitungan offline — TIDAK ada
auto-trading, login Stockbit, order API, atau penyimpanan kredensial.
"""
from __future__ import annotations

import re
import statistics

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
