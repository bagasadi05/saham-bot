"""/validasi menampilkan t HAC bila artefak riset memuatnya."""
from __future__ import annotations

import bot


def _payload(t_hac):
    return {
        "meta": {"start": "2017-09-07", "end": "2026-10-09",
                 "n_tickers": 44, "n_rows": 100_000, "horizon": 20},
        "score": {
            "ic": {"mean_ic": 0.0115, "t": 2.68, "t_hac": t_hac},
            "ic_timeseries": {"mean_ic": -0.0207, "t": -2.33},
        },
        "ratings": [],
        "walk_forward": [],
        "quintiles": [],
    }


def test_format_validasi_menampilkan_hac():
    txt = bot.format_validasi(_payload(1.4))
    assert "HAC t=1.4" in txt


def test_format_validasi_tanpa_hac_tetap_jalan():
    txt = bot.format_validasi(_payload(None))
    assert "HAC t=" not in txt
    assert "IC antar-emiten" in txt
