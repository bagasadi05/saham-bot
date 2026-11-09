"""Test pembuatan chart — termasuk pemakaian dari banyak thread sekaligus."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from core import analysis, chart


def test_build_chart_menghasilkan_png(uptrend_df, tmp_path):
    ind = analysis.compute_indicators(uptrend_df)
    out = tmp_path / "chart.png"
    path = chart.build_chart("BBCA", ind, 25, str(out))
    assert os.path.exists(path)
    assert os.path.getsize(path) > 10_000


def test_build_chart_aman_dari_beberapa_thread(uptrend_df, tmp_path):
    # Regresi: pyplot menyimpan state global -> chart bersamaan bisa rusak.
    ind = analysis.compute_indicators(uptrend_df)

    def render(i):
        path = tmp_path / f"chart_{i}.png"
        return chart.build_chart(f"T{i}", ind, i, str(path))

    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(render, range(4)))

    assert len(set(paths)) == 4
    for p in paths:
        assert os.path.getsize(p) > 10_000
