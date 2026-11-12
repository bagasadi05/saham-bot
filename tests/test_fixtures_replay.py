"""Tes replay fixture kontrak data (offline).

Fixture di `tests/fixtures/` adalah **sampel sintetis** dengan bentuk kolom
yang sama seperti yang dipakai kode. Tujuannya menangkap perubahan bentuk data
yang kita konsumsi (kolom OHLCV, field ringkasan BEI) tanpa menyentuh jaringan.
Rekaman respons asli: `python tools/record_fixtures.py` (butuh internet).
"""
from __future__ import annotations

import json
import pathlib

import pandas as pd

from core import bandar, data_quality, sektor

FIX = pathlib.Path(__file__).parent / "fixtures"


def _load_csv(name: str) -> pd.DataFrame:
    df = pd.read_csv(FIX / name, index_col="date", parse_dates=["date"])
    df.attrs["provider"] = "fixture"
    return df


def test_ohlcv_fixture_punya_kolom_yang_diharapkan():
    df = _load_csv("ohlcv_bbca_normalized.csv")
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 60
    assert (df["high"] >= df["low"]).all()


def test_ohlcv_fixture_lolos_pemeriksaan_kualitas():
    df = _load_csv("ohlcv_bbca_normalized.csv")
    report = data_quality.check(df)
    # Fixture sintetis tidak boleh menghasilkan flag "mustahil".
    assert report["flags"].get("ohlc_invalid", 0) == 0
    assert report["flags"].get("nonpositive_price", 0) == 0


def test_stock_summary_fixture_terbaca_bandar():
    payload = json.loads((FIX / "idx_stock_summary.json").read_text("utf-8"))
    rows = payload["data"]
    bbca = bandar.find_row(rows, "bbca")
    assert bbca is not None
    assert bandar.to_pct(bbca) == (8850 / 8750 - 1) * 100
    fg = bandar.foreign_fields(bbca)
    assert fg["ForeignNet"] == 12_500_000_000


def test_index_fixture_terbaca_sektor():
    payload = json.loads((FIX / "idx_index_summary.json").read_text("utf-8"))
    out = sektor.summarize(payload["data"])
    assert out["ok"] is True
    codes = [s["code"] for s in out["sectors"]]
    assert codes == ["IDXENERGY", "IDXFINANCE"]      # COMPOSITE & IDXTRANS dibuang
    assert out["sectors"][0]["pct"] > 0 > out["sectors"][1]["pct"]
