"""Tes jejak sinyal: perhitungan hasil, split, dan agregasi."""
from __future__ import annotations

import asyncio

import pandas as pd

import bot
from core import scorecard, store
from tests.conftest import make_prices


def _row(df, signal="BELI", entry=100.0, sl=90.0, tp=120.0, price=100.0):
    return {"trade_date": df.index[0].date().isoformat(), "ticker": "TEST",
            "signal": signal, "entry": entry, "sl": sl, "tp": tp, "price": price}


def test_tp_hit_gives_plus_two_r():
    df = make_prices([100, 100, 125])
    got = scorecard.resolve_rows([_row(df)], {"TEST": df}, horizon=2)
    assert len(got) == 1
    assert got[0]["outcome"] == "TP"
    assert got[0]["r_multiple"] == 2.0
    assert got[0]["fwd_ret"] == 20.0
    assert got[0]["bars"] == 2


def test_sl_hit_gives_minus_one_r():
    df = make_prices([100, 85, 85])
    got = scorecard.resolve_rows([_row(df)], {"TEST": df}, horizon=2)
    assert got[0]["outcome"] == "SL"
    assert got[0]["r_multiple"] == -1.0
    assert got[0]["fwd_ret"] == -10.0


def test_sl_wins_when_both_hit_in_same_bar():
    """Tanpa data intraday, urutan SL/TP tidak diketahui — pilih konservatif."""
    idx = pd.bdate_range("2026-01-01", periods=3)
    df = pd.DataFrame({
        "open": [100, 100, 105], "high": [101, 130, 106],
        "low": [99, 80, 104], "close": [100, 110, 105], "volume": [1e6] * 3,
    }, index=idx)
    got = scorecard.resolve_rows([_row(df)], {"TEST": df}, horizon=2)
    assert got[0]["outcome"] == "SL"


def test_time_exit_uses_close_and_r_multiple():
    df = make_prices([100, 101, 102])
    got = scorecard.resolve_rows([_row(df, tp=200)], {"TEST": df}, horizon=2)
    assert got[0]["outcome"] == "TIME"
    assert got[0]["fwd_ret"] == 2.0
    assert got[0]["r_multiple"] == 0.2


def test_not_enough_bars_is_left_pending():
    df = make_prices([100, 101, 102])
    assert scorecard.resolve_rows([_row(df)], {"TEST": df}, horizon=5) == []


def test_jual_has_no_r_multiple_but_counts_as_correct_when_price_falls():
    df = make_prices([100, 96, 95])
    row = _row(df, signal="JUAL", entry=None, sl=None, tp=None)
    got = scorecard.resolve_rows([row], {"TEST": df}, horizon=2)
    assert got[0]["outcome"] == "TURUN"
    assert got[0]["r_multiple"] is None
    stats = scorecard.summarize(got, horizon=2)
    assert stats["signals"]["JUAL"]["win"] == 100.0
    assert stats["signals"]["JUAL"]["avg_fwd"] < 0
    assert stats["signals"]["JUAL"]["outcomes"].get("TURUN") == 1


def test_jual_naik_datar_and_strict_separation_from_beli():
    df_up = make_prices([100, 102, 105])
    df_flat = make_prices([100, 101, 100])
    df_down = make_prices([100, 97, 94])
    rows = [
        {**_row(df_up, signal="JUAL", entry=None, sl=None, tp=None), "ticker": "UP"},
        {**_row(df_flat, signal="JUAL", entry=None, sl=None, tp=None), "ticker": "FLT"},
        {**_row(df_down, signal="JUAL", entry=None, sl=None, tp=None), "ticker": "DWN"},
        {**_row(df_up, signal="BELI", entry=100, sl=95, tp=110), "ticker": "UP"},
        {**_row(df_flat, signal="TAHAN", entry=None, sl=None, tp=None), "ticker": "FLT"},
    ]
    got = scorecard.resolve_rows(
        rows, {"UP": df_up, "FLT": df_flat, "DWN": df_down}, horizon=2,
    )
    outcomes_jual = {r["ticker"]: r["outcome"] for r in got if r["signal"] == "JUAL"}
    assert outcomes_jual == {"UP": "NAIK", "FLT": "DATAR", "DWN": "TURUN"}
    tahan = [r for r in got if r["signal"] == "TAHAN"][0]
    assert tahan["outcome"] == "NA"

    stats = scorecard.summarize(got, horizon=2)
    assert stats["signals"]["JUAL"]["n"] == 3
    assert stats["signals"]["BELI"]["n"] == 1
    assert stats["signals"]["JUAL"]["outcomes"]["NAIK"] == 1
    assert stats["signals"]["JUAL"]["outcomes"]["DATAR"] == 1
    assert stats["signals"]["JUAL"]["outcomes"]["TURUN"] == 1
    assert stats["jual_edge"] is not None

    rendered = scorecard.format_summary(stats)
    assert "evaluasi peringatan (bukan short)" in rendered
    assert "turun 1" in rendered and "naik 1" in rendered and "datar 1" in rendered
    assert "Selisih BELI vs TAHAN" in rendered
    assert "Selisih JUAL vs TAHAN" in rendered


def test_live_ic_reports_t_hac_and_div_adjusted_flag():
    rows = []
    # Buat >8 tanggal berurutan dengan sinyal/return tumpang tindih + div_adjusted
    for d in range(12):
        date_str = f"2026-01-{d + 1:02d}"
        for k in range(8):
            # Variasikan peringkat antar tanggal agar std(IC) > 0 dan ada autokorelasi
            jitter = 3.5 if (d % 4 < 2 and k == 2) else (-2.5 if (d % 3 == 0 and k == 5) else 0.0)
            rows.append({
                "trade_date": date_str,
                "ticker": f"T{k}",
                "signal": "BELI" if k >= 4 else "JUAL",
                "rel_pct": 10.0 + k * 10.0,
                "fwd_ret": float(k - 3) + jitter,
                "outcome": "TIME" if k >= 4 else "TURUN",
                "div_adjusted": (d == 0 and k == 0),
            })
    ic = scorecard.live_ic(rows, horizon=5)
    assert ic["n_dates"] == 12
    assert ic["mean"] is not None
    assert ic["t"] is not None
    assert ic["t_hac"] is not None
    assert ic["nlags"] == 5
    assert ic["adjusted_data"] is True
    assert ic["n_div_shifted"] == 1
    assert ic["adjusted_warning"]

    df_div = make_prices([100, 102, 104])
    row_div = _row(df_div, signal="BELI", entry=105, sl=95, tp=115)
    row_div["price"] = 105.0  # 100 vs 105 -> ~4.76% selisih (antara 0.5% dan 25%)
    resolved = scorecard.resolve_rows([row_div], {"TEST": df_div}, horizon=2)
    assert resolved[0]["div_adjusted"] is True
    assert resolved[0]["adj_shift_pct"] < 0


def test_split_adjustment_closes_row_as_adj():
    df = make_prices([50, 51, 52])          # harga kini 50, entry lama 100
    got = scorecard.resolve_rows([_row(df)], {"TEST": df}, horizon=2)
    assert got[0]["outcome"] == "ADJ"
    assert got[0]["fwd_ret"] is None


def test_summarize_computes_edge_against_tahan():
    rows = [
        {"signal": "BELI", "fwd_ret": 3.0, "r_multiple": 1.0, "outcome": "TIME",
         "trade_date": "2026-01-02"},
        {"signal": "BELI", "fwd_ret": 1.0, "r_multiple": 0.5, "outcome": "TIME",
         "trade_date": "2026-01-03"},
        {"signal": "TAHAN", "fwd_ret": 1.0, "r_multiple": None, "outcome": "NA",
         "trade_date": "2026-01-02"},
    ]
    stats = scorecard.summarize(rows, horizon=20)
    assert stats["signals"]["BELI"]["n"] == 2
    assert stats["signals"]["BELI"]["avg_fwd"] == 2.0
    assert stats["signals"]["BELI"]["win"] == 100.0
    assert stats["signals"]["BELI"]["avg_r"] == 0.75
    assert stats["edge"] == 1.0
    assert stats["first"] == "2026-01-02"
    assert stats["last"] == "2026-01-03"


def test_format_summary_empty_is_honest():
    text = scorecard.format_summary(scorecard.summarize([], horizon=20),
                                    {"pending": 12})
    assert "Belum ada sinyal" in text
    assert "12" in text
    assert "20 hari bursa" in text


def test_cmd_jejak_replies_with_measurement(tmp_store):
    store.append_scan_history([
        {"ticker": "BBCA", "signal": "BELI", "price": 100.0, "entry": 100.0,
         "sl": 90.0, "tp": 120.0, "atr": 10.0, "rel_pct": 90.0, "reason": "x"},
    ], "2026-01-05")
    store.close_scan_history([{"trade_date": "2026-01-05", "ticker": "BBCA",
                               "bars": 20, "fwd_ret": 2.0, "r_multiple": 2.0,
                               "outcome": "TP"}])

    class Msg:
        def __init__(self):
            self.replies = []

        async def reply_text(self, text, **kwargs):
            self.replies.append(text)

    class Upd:
        def __init__(self):
            self.effective_message = Msg()

    upd = Upd()
    asyncio.run(bot.cmd_jejak(upd, None))
    assert upd.effective_message.replies
    assert "JEJAK SINYAL" in upd.effective_message.replies[0]
    assert "+2.00%" in upd.effective_message.replies[0]


def test_live_ic_perfect_rank():
    rows = [
        {"trade_date": "2026-01-02", "rel_pct": i * 10.0, "fwd_ret": float(i)}
        for i in range(8)
    ]
    ic = scorecard.live_ic(rows)
    assert ic["n_dates"] == 1
    assert ic["mean"] == 1.0
    assert ic["positive_pct"] == 100.0


def test_live_ic_skips_thin_dates():
    rows = [
        {"trade_date": "2026-01-02", "rel_pct": i * 10.0, "fwd_ret": float(i)}
        for i in range(7)
    ]
    assert scorecard.live_ic(rows)["n_dates"] == 0


def test_live_ic_ignores_unresolved_rows():
    rows = [
        {"trade_date": "2026-01-02", "rel_pct": i * 10.0, "fwd_ret": None}
        for i in range(8)
    ]
    assert scorecard.live_ic(rows)["mean"] is None


def test_format_summary_shows_live_ic():
    rows = [
        {"signal": "TAHAN", "fwd_ret": float(i), "rel_pct": i * 10.0,
         "trade_date": "2026-01-02", "r_multiple": None, "outcome": "NA"}
        for i in range(8)
    ]
    text = scorecard.format_summary(scorecard.summarize(rows))
    assert "IC live" in text


def test_format_summary_renders_sections():
    stats = scorecard.summarize([
        {"signal": "BELI", "fwd_ret": 2.0, "r_multiple": 2.0, "outcome": "TP",
         "trade_date": "2026-01-02"},
        {"signal": "TAHAN", "fwd_ret": 0.5, "r_multiple": None, "outcome": "NA",
         "trade_date": "2026-01-02"},
    ])
    text = scorecard.format_summary(stats)
    assert "<b>BELI</b>" in text
    assert "+2.00%" in text
    assert "Selisih BELI vs TAHAN" in text
