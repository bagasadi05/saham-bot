#!/usr/bin/env python3
"""
idx_calc.py - Kalkulator Deterministik Riset Ekuitas BEI (IDX)
Zero-dependency CLI untuk menghitung fraksi harga BEI, ARA/ARB asimetris
(Kep-00158/BEI/10-2026), Justified PBV, Reverse DCF, Position Sizing
(40/60 vs Single-Bullet 1 Lot + fee BEI), Harga Teoritis Right Issue (TERP),
serta Kalkulator Dividen & Ex-Date Trap (PMK 18/2021).
"""

import argparse
import json
import math
import sys


def get_tick_size(price: float, direction: str = "nearest") -> int:
    """Mengembalikan fraksi harga (tick size) BEI sesuai Peraturan II-A."""
    if price < 200 or (price == 200 and direction == "down"):
        return 1
    if price < 500 or (price == 500 and direction == "down"):
        return 2
    if price < 2000 or (price == 2000 and direction == "down"):
        return 5
    if price < 5000 or (price == 5000 and direction == "down"):
        return 10
    return 25


def round_tick(price: float, mode: str = "nearest") -> int:
    """Membulatkan harga ke kelipatan fraksi BEI yang valid."""
    if price <= 1:
        return 1
    direction = "down" if mode == "down" else "up"
    tick = get_tick_size(price, direction=direction)
    if mode == "down":
        return max(1, int(math.floor(price / tick) * tick))
    if mode == "up":
        return max(1, int(math.ceil(price / tick) * tick))
    return max(1, int(round(price / tick) * tick))


def calc_ara_arb(ref_price: int, is_fca: bool = False) -> dict:
    """Menghitung batas ARA dan ARB sesuai Kep-00158/BEI/10-2026 (s.d. 31 Des 2026) atau FCA."""
    tick = get_tick_size(ref_price)
    if is_fca:
        ara = round_tick(ref_price * 1.10, "down")
        arb = max(1, round_tick(ref_price * 0.90, "up"))
        rule = "Papan Pemantauan Khusus (FCA - Peraturan II-X): +/-10%"
    else:
        rule = "Kep-00158/BEI/10-2026 (Asimetris sementara s.d. 31 Des 2026)"
        if ref_price <= 10:
            ara = ref_price + 1
            arb = max(1, ref_price - 1)
        elif ref_price <= 14:
            ara = round_tick(ref_price * 1.35, "down")
            arb = max(1, ref_price - 1)
        elif ref_price <= 200:
            ara = round_tick(ref_price * 1.35, "down")
            arb = max(1, round_tick(ref_price * 0.93, "up"))
        elif ref_price <= 5000:
            ara = round_tick(ref_price * 1.25, "down")
            arb = max(1, round_tick(ref_price * 0.93, "up"))
        else:
            ara = round_tick(ref_price * 1.20, "down")
            arb = max(1, round_tick(ref_price * 0.93, "up"))
    return {
        "ref_price": ref_price,
        "valid_price": round_tick(ref_price, "nearest"),
        "tick_size": tick,
        "ara": ara,
        "arb": arb,
        "rule": rule,
    }


def calc_justified_pbv(price: float, bvps: float, roe: float, coe: float, g: float) -> dict:
    """Menghitung Justified PBV, Nilai Wajar, dan Margin of Safety untuk Perbankan."""
    if coe <= g:
        raise ValueError("CoE harus lebih besar dari pertumbuhan lestari (g).")
    justified_pbv = (roe - g) / (coe - g)
    fair_value = round_tick(justified_pbv * bvps, "nearest")
    current_pbv = price / bvps
    mos = ((fair_value - price) / fair_value) * 100.0
    upside = ((fair_value - price) / price) * 100.0
    return {
        "current_price": price,
        "bvps": bvps,
        "current_pbv": round(current_pbv, 2),
        "justified_pbv": round(justified_pbv, 2),
        "fair_value": fair_value,
        "margin_of_safety_pct": round(mos, 2),
        "upside_pct": round(upside, 2),
        "assumptions": {"roe_pct": roe * 100, "coe_pct": coe * 100, "g_pct": g * 100},
    }


def dcf_value_per_share(fcf_per_share: float, growth: float, r: float, tg: float, years: int = 10) -> float:
    """Menghitung nilai intrinsik DCF per saham dengan horizon 10 tahun."""
    if r <= tg:
        raise ValueError("Discount rate (r) harus lebih besar dari terminal growth (tg).")
    pv = 0.0
    fcf = fcf_per_share
    for t in range(1, years + 1):
        fcf *= (1.0 + growth)
        pv += fcf / ((1.0 + r) ** t)
    terminal_val = (fcf * (1.0 + tg)) / (r - tg)
    pv += terminal_val / ((1.0 + r) ** years)
    return pv


def calc_dcf_and_reverse(price: float, fcf_ps: float, growth: float, r: float, tg: float) -> dict:
    """Menghitung kisaran DCF (+/-200 bps) dan Reverse DCF (implied FCF growth)."""
    val_low = round_tick(dcf_value_per_share(fcf_ps, growth, r + 0.02, tg), "nearest")
    val_base = round_tick(dcf_value_per_share(fcf_ps, growth, r, tg), "nearest")
    val_high = round_tick(dcf_value_per_share(fcf_ps, growth, max(tg + 0.01, r - 0.02), tg), "nearest")

    lo, hi = -0.30, 0.60
    for _ in range(60):
        mid = (lo + hi) / 2.0
        est = dcf_value_per_share(fcf_ps, mid, r, tg)
        if est < price:
            lo = mid
        else:
            hi = mid
    implied_growth = (lo + hi) / 2.0
    mos = ((val_base - price) / val_base) * 100.0
    return {
        "current_price": price,
        "fcf_per_share": fcf_ps,
        "hurdle_rate_pct": round(r * 100, 2),
        "terminal_growth_pct": round(tg * 100, 2),
        "dcf_range": {
            "conservative_plus_200bps": val_low,
            "base_fair_value": val_base,
            "optimistic_minus_200bps": val_high,
        },
        "margin_of_safety_base_pct": round(mos, 2),
        "reverse_dcf_implied_fcf_cagr_pct": round(implied_growth * 100, 2),
    }


def calc_position_size(
    capital: float,
    risk_pct: float,
    entry1: float,
    entry2: float,
    tp1: float,
    tp2: float,
    sl: float,
    fee_buy: float = 0.0015,
    fee_sell: float = 0.0025,
    min_fee: float = 0.0,
) -> dict:
    """Menghitung position sizing adaptif (40/60 vs Single-Bullet 1 Lot) + untung/rugi bersih."""
    e1 = round_tick(entry1)
    e2 = round_tick(entry2)
    t1 = round_tick(tp1)
    t2 = round_tick(tp2)
    stop = round_tick(sl, "down")

    if e1 <= stop or e2 <= stop:
        raise ValueError("Harga Entry harus lebih tinggi dari Stop Loss.")

    max_risk_rp = capital * (risk_pct / 100.0)
    loss_per_lot_e1 = (e1 * (1.0 + fee_buy) - stop * (1.0 - fee_sell)) * 100.0
    risk_lots = int(math.floor(max_risk_rp / loss_per_lot_e1))
    afford_lots = int(math.floor(capital / (e1 * 100.0 * (1.0 + fee_buy))))

    if afford_lots == 0:
        return {
            "status": "INSUFFICIENT_CAPITAL",
            "message": f"Modal Rp{int(capital):,} belum cukup untuk membeli 1 lot (butuh minimal Rp{int(e1 * 100 * (1 + fee_buy)):,}).",
        }

    warnings = []
    if risk_lots == 0:
        lots = 1
        actual_risk_rp = (e2 * (1.0 + fee_buy) - stop * (1.0 - fee_sell)) * 100.0
        actual_risk_pct = (actual_risk_rp / capital) * 100.0
        if actual_risk_pct > risk_pct:
            warnings.append(
                f"PERINGATAN MODAL KECIL: 1 lot di Support 2 saja risikonya Rp{int(round(actual_risk_rp)):,} "
                f"({actual_risk_pct:.2f}% modal), melampaui batas aman {risk_pct:.2f}%."
            )
    else:
        lots = min(risk_lots, afford_lots)

    if lots == 1:
        mode = "SINGLE_BULLET_1_LOT"
        plan = [{"tranche": "100% (1 Lot Penuh di Support 2)", "lots": 1, "price": e2}]
        avg_entry = float(e2)
    else:
        mode = "SCALING_IN_40_60"
        lot1 = max(1, int(round(lots * 0.40)))
        lot2 = lots - lot1
        if lot2 == 0:
            lot1 -= 1
            lot2 = 1
        plan = [
            {"tranche": "Cicilan 1 (~40% di Support 1)", "lots": lot1, "price": e1},
            {"tranche": "Cicilan 2 (~60% di Support 2)", "lots": lot2, "price": e2},
        ]
        avg_entry = (lot1 * e1 + lot2 * e2) / lots

    gross_buy = lots * 100 * avg_entry
    buy_fee_rp = max(min_fee, gross_buy * fee_buy)
    total_buy = gross_buy + buy_fee_rp

    if min_fee > 0 and buy_fee_rp > gross_buy * fee_buy:
        eff_fee_pct = (buy_fee_rp / gross_buy) * 100.0
        warnings.append(
            f"PERINGATAN MINIMUM FEE BROKER: Fee beli efektif {eff_fee_pct:.2f}% (Rp{int(buy_fee_rp):,}) "
            f"karena terkena batas minimum broker."
        )

    net_sell_tp1 = lots * 100 * t1 - max(min_fee, lots * 100 * t1 * fee_sell)
    net_sell_tp2 = lots * 100 * t2 - max(min_fee, lots * 100 * t2 * fee_sell)
    net_sell_sl = lots * 100 * stop - max(min_fee, lots * 100 * stop * fee_sell)

    net_profit_tp1 = round(net_sell_tp1 - total_buy)
    net_profit_tp2 = round(net_sell_tp2 - total_buy)
    net_loss_sl = round(total_buy - net_sell_sl)
    rrr = round((t2 - avg_entry) / (avg_entry - stop), 2)
    trailing_be = round_tick(avg_entry * (1.0 + fee_buy + fee_sell), "up")

    return {
        "mode": mode,
        "total_lots": lots,
        "shares": lots * 100,
        "execution_plan": plan,
        "average_entry": round(avg_entry, 2),
        "total_buy_with_fee": round(total_buy),
        "allocation_pct": round((total_buy / capital) * 100.0, 2),
        "levels": {"tp1": t1, "tp2": t2, "stop_loss": stop, "trailing_stop_after_tp1": trailing_be},
        "risk_reward_ratio_tp2": f"1 : {rrr}",
        "net_profit_tp1_rp": net_profit_tp1,
        "net_profit_tp2_rp": net_profit_tp2,
        "net_loss_sl_rp": net_loss_sl,
        "actual_risk_of_capital_pct": round((net_loss_sl / capital) * 100.0, 2),
        "warnings": warnings,
    }


def calc_terp(cum_price: float, exercise_price: float, old_ratio: float, new_ratio: float) -> dict:
    """Menghitung Harga Teoritis Right Issue (TERP) & efek dilusi kepemilikan."""
    terp_raw = (old_ratio * cum_price + new_ratio * exercise_price) / (old_ratio + new_ratio)
    terp = round_tick(terp_raw, "nearest")
    dilution_pct = (new_ratio / (old_ratio + new_ratio)) * 100.0
    right_value = max(0.0, terp_raw - exercise_price)
    return {
        "cum_price": cum_price,
        "exercise_price": exercise_price,
        "ratio_old_to_new": f"{old_ratio} : {new_ratio}",
        "terp_exact": round(terp_raw, 2),
        "terp_valid_tick": terp,
        "theoretical_right_value": round(right_value, 2),
        "max_dilution_if_not_exercised_pct": round(dilution_pct, 2),
    }


def calc_dividend(price: float, dps: float, lots: int) -> dict:
    """Menghitung Dividend Yield, estimasi dividen tunai (0% PMK 18/2021 vs 10% PPh), dan breakeven Ex-Date."""
    gross_yield_pct = (dps / price) * 100.0
    net_dps_10 = dps * 0.90
    net_yield_10_pct = (net_dps_10 / price) * 100.0
    shares = lots * 100
    cash_reinvest_0pct = round(shares * dps)
    cash_non_reinvest_10pct = round(shares * net_dps_10)
    ex_date_theoretical = round_tick(price - dps, "nearest")
    return {
        "buy_price": price,
        "dps_rp": dps,
        "lots": lots,
        "shares": shares,
        "gross_yield_pct": round(gross_yield_pct, 2),
        "cash_if_reinvested_pmk18_0pct_rp": cash_reinvest_0pct,
        "cash_if_not_reinvested_pph10pct_rp": cash_non_reinvest_10pct,
        "net_yield_after_10pct_tax_pct": round(net_yield_10_pct, 2),
        "ex_date_theoretical_price": ex_date_theoretical,
        "dividend_trap_risk": gross_yield_pct >= 7.0,
    }


def main():
    parser = argparse.ArgumentParser(description="Kalkulator Deterministik Riset Ekuitas BEI (IDX)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_tick = sub.add_parser("tick", help="Cek fraksi harga dan batas ARA/ARB BEI")
    p_tick.add_argument("--price", type=int, required=True)
    p_tick.add_argument("--fca", action="store_true")

    p_pbv = sub.add_parser("pbv", help="Hitung Justified PBV & MoS Perbankan")
    p_pbv.add_argument("--price", type=float, required=True)
    p_pbv.add_argument("--bvps", type=float, required=True)
    p_pbv.add_argument("--roe", type=float, required=True, help="ROE desimal, mis. 0.185 untuk 18.5%")
    p_pbv.add_argument("--coe", type=float, default=0.12, help="CoE desimal, default 0.12")
    p_pbv.add_argument("--g", type=float, default=0.055, help="Sustainable growth desimal, default 0.055")

    p_dcf = sub.add_parser("dcf", help="Hitung kisaran DCF (+/-200bps) & Reverse DCF")
    p_dcf.add_argument("--price", type=float, required=True)
    p_dcf.add_argument("--fcf", type=float, required=True, help="FCF per saham (dalam IDR)")
    p_dcf.add_argument("--growth", type=float, required=True, help="Asumsi pertumbuhan FCF desimal")
    p_dcf.add_argument("--hurdle", type=float, default=0.12, help="Hurdle rate BEI: 0.12 atau 0.14")
    p_dcf.add_argument("--tg", type=float, default=0.045, help="Terminal growth 0.04 - 0.05")

    p_size = sub.add_parser("size", help="Hitung Position Sizing adaptif (40/60 vs 1 Lot) + fee BEI")
    p_size.add_argument("--capital", type=float, required=True)
    p_size.add_argument("--risk", type=float, default=1.0, help="Batas risiko %, default 1.0")
    p_size.add_argument("--entry1", type=float, required=True)
    p_size.add_argument("--entry2", type=float, required=True)
    p_size.add_argument("--tp1", type=float, required=True)
    p_size.add_argument("--tp2", type=float, required=True)
    p_size.add_argument("--sl", type=float, required=True)
    p_size.add_argument("--min-fee", type=float, default=0.0, help="Minimum fee harian broker (Rp)")

    p_terp = sub.add_parser("terp", help="Hitung Harga Teoritis Right Issue (TERP) & dilusi")
    p_terp.add_argument("--cum", type=float, required=True)
    p_terp.add_argument("--exercise", type=float, required=True)
    p_terp.add_argument("--old", type=float, required=True)
    p_terp.add_argument("--new", type=float, required=True)

    p_div = sub.add_parser("div", help="Hitung Dividend Yield, pajak PMK 18/2021, & Ex-Date teoretis")
    p_div.add_argument("--price", type=float, required=True)
    p_div.add_argument("--dps", type=float, required=True, help="Dividen per saham (Rp)")
    p_div.add_argument("--lots", type=int, default=1)

    args = parser.parse_args()
    try:
        if args.cmd == "tick":
            res = calc_ara_arb(args.price, args.fca)
        elif args.cmd == "pbv":
            res = calc_justified_pbv(args.price, args.bvps, args.roe, args.coe, args.g)
        elif args.cmd == "dcf":
            res = calc_dcf_and_reverse(args.price, args.fcf, args.growth, args.hurdle, args.tg)
        elif args.cmd == "size":
            res = calc_position_size(
                args.capital, args.risk, args.entry1, args.entry2,
                args.tp1, args.tp2, args.sl, min_fee=args.min_fee
            )
        elif args.cmd == "terp":
            res = calc_terp(args.cum, args.exercise, args.old, args.new)
        elif args.cmd == "div":
            res = calc_dividend(args.price, args.dps, args.lots)
        print(json.dumps(res, indent=2, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
