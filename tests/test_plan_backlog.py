"""Unit test untuk backlog PLAN.md: survivorship bias PIT, validasi sektor OOS, & tools/build_sectors."""
from __future__ import annotations

import numpy as np
import pandas as pd

from research import bias, evaluate_sector
from tools import build_sectors


def test_bias_filter_by_membership():
    df = pd.DataFrame({
        "date": ["2020-01-02", "2022-06-01", "2024-01-05", "2020-01-02", "2023-01-02"],
        "ticker": ["BBCA", "BBCA", "BBCA", "ACES", "ACES"],
        "score": [20.0, 30.0, 40.0, 10.0, 50.0],
        "fwd": [0.01, 0.02, 0.03, -0.01, 0.04],
    })
    membership = [
        {"ticker": "BBCA", "start": "2021-01-01", "end": None},
        {"ticker": "ACES", "start": "2020-01-01", "end": "2022-02-01"},
    ]
    pit = bias.filter_by_membership(df, membership)
    assert len(pit) == 3
    assert set(zip(pit["ticker"], pit["date"], strict=True)) == {
        ("BBCA", "2022-06-01"),
        ("BBCA", "2024-01-05"),
        ("ACES", "2020-01-02"),
    }


def test_evaluate_sector_compare_global_vs_sector():
    rng = np.random.default_rng(123)
    dates = pd.bdate_range("2024-01-01", periods=30)
    tickers = [f"T{i:03d}" for i in range(12)]
    sec_map = {t: ("Energy" if i < 6 else "Financial Services") for i, t in enumerate(tickers)}
    rows = []
    for d in dates:
        for t in tickers:
            rows.append({
                "date": d,
                "ticker": t,
                "fwd": float(rng.normal(0.0, 0.03)),
                "f_mom20": float(rng.normal()),
                "f_mom60": float(rng.normal()),
                "f_mom120": float(rng.normal()),
                "f_dist_ma20": float(rng.normal()),
                "f_dist_ma50": float(rng.normal()),
                "f_dist_ma200": float(rng.normal()),
                "f_rsi": float(rng.normal()),
                "f_macd_hist": float(rng.normal()),
                "f_vol_ratio": float(rng.normal()),
                "f_volat20": float(rng.normal()),
                "f_bb_pos": float(rng.normal()),
                "f_pos_52w": float(rng.normal()),
            })
    df = pd.DataFrame(rows)
    res = evaluate_sector.compare_global_vs_sector(df, sec_map)
    assert res["n_tickers"] == 12
    assert "global" in res and "sector" in res and "delta_sector_minus_global" in res


def test_build_sectors_parse_dan_validasi(tmp_path):
    html = """
    <table>
      <tr><th>Ticker</th><th>Name</th><th>Sector</th></tr>
      <tr><td>BBCA</td><td>Bank Central Asia</td><td>Financial Services</td></tr>
      <tr><td>ADRO</td><td>Alamtri Resources</td><td>Energy</td></tr>
      <tr><td>BAD1</td><td>Invalid</td><td>Energy</td></tr>
    </table>
    """
    parsed = build_sectors.parse_tweenvest_html(html)
    assert parsed == {"ADRO": "Energy", "BBCA": "Financial Services"}
    ok, _ = build_sectors.validate_sectors(parsed, min_count=10)
    assert ok is False

    many = {f"A{chr(65 + i // 26 // 26)}{chr(65 + (i // 26) % 26)}{chr(65 + i % 26)}": "Energy" for i in range(520)}
    ok2, msg2 = build_sectors.validate_sectors(many)
    assert ok2 is True, msg2
    out = tmp_path / "idx_sectors.json"
    build_sectors.write_sectors_atomic(many, "test", out_path=str(out))
    assert out.exists()
