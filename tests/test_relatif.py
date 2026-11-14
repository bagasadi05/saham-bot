"""Test skor relatif antar-emiten (core/relatif.py)."""
from __future__ import annotations

import pandas as pd
import pytest

from core import relatif


def make_ind(**kw) -> pd.DataFrame:
    """Satu baris indikator sintetis dengan nilai default netral."""
    base = dict(
        close=100.0, sma20=100.0, sma50=100.0, sma200=100.0, high20=100.0,
        volume=1_000_000.0, vol_avg20=1_000_000.0, macd_hist=0.0, rsi14=50.0,
        ret20d=0.0, ret1d=0.0, volat20=0.02, atr14=2.0,
    )
    base.update(kw)
    return pd.DataFrame([base])


GOOD = dict(close=100.0, sma50=120.0, high20=140.0, sma200=100.0,
            rsi14=70.0, ret20d=5.0, macd_hist=1.0, volat20=0.01,
            ret1d=-2.0, volume=2_000_000.0, sma20=130.0, atr14=3.0)
BAD = dict(close=100.0, sma50=80.0, high20=100.0, sma200=100.0,
           rsi14=30.0, ret20d=-5.0, macd_hist=-1.0, volat20=0.05,
           ret1d=3.0, volume=500_000.0, sma20=75.0, atr14=0.5)


def test_factors_from_ind_memuat_semua_bobot():
    f = relatif.factors_from_ind(make_ind())
    assert set(f) == set(relatif.WEIGHTS)
    # close/sma50 - 1 = 0 pada data netral
    assert f["close_sma50"] == pytest.approx(0.0)


def test_faktor_nan_kalau_kolom_hilang():
    df = make_ind().drop(columns=["sma200", "volat20", "atr14"])
    f = relatif.factors_from_ind(df)
    assert pd.isna(f["close_sma200"])
    assert pd.isna(f["volat"])
    assert pd.isna(f["atr"])


def test_universe_kecil_tidak_diberi_peringkat():
    items = [{"ticker": f"T{i}", "ind": make_ind()} for i in range(5)]
    out = relatif.score_universe(items)
    assert all(it["rel_ok"] is False and it["rel_pct"] is None for it in out)


def test_universe_besar_mengurutkan_sesuai_bobot():
    items = ([{"ticker": f"G{i}", "ind": make_ind(**GOOD)} for i in range(5)]
             + [{"ticker": f"B{i}", "ind": make_ind(**BAD)} for i in range(5)])
    out = relatif.score_universe(items)
    assert all(it["rel_ok"] for it in out)
    good = [it["rel_pct"] for it in out if it["ticker"].startswith("G")]
    bad = [it["rel_pct"] for it in out if it["ticker"].startswith("B")]
    assert min(good) > max(bad)
    assert all(0 <= p <= 100 for p in good + bad)
    assert {it["rel_quintil"] for it in out} <= {1, 2, 3, 4, 5}


def test_item_tanpa_dataframe_tidak_bikin_crash():
    items = ([{"ticker": f"T{i}", "ind": make_ind()} for i in range(8)]
             + [{"ticker": "RUSAK", "ind": {"bukan": "dataframe"}}])
    out = relatif.score_universe(items)
    rusak = next(it for it in out if it["ticker"] == "RUSAK")
    assert rusak["rel_ok"] is False


def test_summary_line_jujur_saat_tidak_cukup_data():
    assert "8 emiten" in relatif.summary_line({"rel_ok": False})
    teks = relatif.summary_line({"rel_ok": True, "rel_pct": 87.5, "rel_quintil": 5})
    assert "88/100" in teks and "kuintil 5" in teks
    assert "bukan jaminan return" in teks


def test_weights_and_quintiles_synced_with_validation_json():
    import json
    import pathlib

    val = json.loads(pathlib.Path(relatif.VALIDATION_FILE).read_text(encoding="utf-8"))
    val_w = {k.removeprefix("f_"): float(v) for k, v in val["weights"].items()}
    assert set(val_w) == set(relatif.WEIGHTS)
    for key, val_weight in val_w.items():
        assert abs(relatif.WEIGHTS[key] - val_weight) <= 1e-9

    val_q = {int(row["quintile"]): row for row in val["quintiles"]}
    assert set(val_q) == set(relatif.OOS_QUINTILES) == {1, 2, 3, 4, 5}
    for q in (1, 2, 3, 4, 5):
        assert relatif.OOS_QUINTILES[q]["n"] == val_q[q]["n"]
        assert abs(relatif.OOS_QUINTILES[q]["excess_pct"] - val_q[q]["excess_pct"]) <= 1e-6
        assert abs(relatif.OOS_QUINTILES[q]["win_rate"] - val_q[q]["win_rate"]) <= 1e-6


def test_quintile_dispersion_and_summary_line_with_iqr_or_safe_fallback(tmp_path):
    # Fallback aman jika JSON belum punya P25-P75
    no_iqr = tmp_path / "val_no_iqr.json"
    no_iqr.write_text('{"quintiles": [{"quintile": 5, "n": 100, "excess_pct": 0.4, "win_rate": 47.2}]}')
    q_map = relatif.load_oos_quintiles(str(no_iqr))
    disp = relatif.quintile_dispersion(5, q_map)
    assert disp["has_iqr"] is False
    assert disp["p25_pct"] is None and disp["p75_pct"] is None
    line_no_iqr = relatif.summary_line({"rel_ok": True, "rel_pct": 90.0, "rel_quintil": 5}, q_map)
    assert "sebaran P25–P75" not in line_no_iqr
    assert "bukan jaminan return" in line_no_iqr

    # Dengan P25-P75 tersedia -> ikut ditampilkan sebagai dispersi deskriptif
    with_iqr = tmp_path / "val_iqr.json"
    with_iqr.write_text(
        '{"quintiles": [{"quintile": 5, "n": 100, "excess_pct": 0.4, '
        '"median_pct": 0.15, "p25_pct": -3.2, "p75_pct": 4.1, "iqr_pct": 7.3, "win_rate": 47.2}]}'
    )
    q_iqr = relatif.load_oos_quintiles(str(with_iqr))
    disp_iqr = relatif.quintile_dispersion(5, q_iqr)
    assert disp_iqr["has_iqr"] is True
    assert disp_iqr["iqr_pct"] == 7.3
    line_iqr = relatif.summary_line({"rel_ok": True, "rel_pct": 90.0, "rel_quintil": 5}, q_iqr)
    assert "P25–P75: -3.20% s.d. +4.10%" in line_iqr
    assert "bukan jaminan return" in line_iqr
