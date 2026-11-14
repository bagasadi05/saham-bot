"""Aturan harga saham BEI: fraksi tick, batas ARA/ARB 2026, & pembulatan level order.

Satu sumber kebenaran supaya level entry/SL/TP yang ditampilkan bot bisa
benar-benar dipasang di aplikasi broker (Stockbit dkk).

Fraksi harga IDX (Peraturan BEI):
    < 200        → 1
    200–<500     → 2
    500–<2.000   → 5
    2.000–<5.000 → 10
    ≥ 5.000      → 25

Aturan Auto Rejection & Batas Harga Terendah BEI 2026:
  - Kep-00136/BEI/09-2026 (berlaku 28 Sep 2026): floor papan reguler turun
    dari Rp50 menjadi Rp1.
  - Jendela ARA 19 Okt–31 Des 2026: 35% (Rp11–200), 25% (>200–5.000), 20% (>5.000);
    harga Rp1–10 memakai batas absolut Rp1.
  - Kep-00158/BEI/10-2026 (20 Okt–31 Des 2026): ARB asimetris 7% (Rp11–14 &
    Rp1–10 memakai langkah absolut Rp1); di luar jendela itu kembali ke aturan
    simetris 35%/25%/20% atau Papan Pemantauan Khusus 10%.
"""
from __future__ import annotations

import datetime as _dt
import math
import os
from zoneinfo import ZoneInfo

_WIB = ZoneInfo("Asia/Jakarta")

DEFAULT_ARA_START = _dt.date(2026, 10, 19)
DEFAULT_ARA_END = _dt.date(2026, 12, 31)
DEFAULT_ARB_ASYM_START = _dt.date(2026, 10, 20)
DEFAULT_ARB_ASYM_END = _dt.date(2026, 12, 31)
REGULER_FLOOR_RP1_START = _dt.date(2026, 9, 28)

# Dipertahankan sebagai konstanta default kompatibilitas balik; fungsi di bawah
# membaca jendela dinamis via env IDX_ARA_WINDOW_END / IDX_ARB_WINDOW_END dan
# melaporkan status kedaluwarsa secara eksplisit (tidak diam-diam expired).
_ARA_WINDOW = (DEFAULT_ARA_START, DEFAULT_ARA_END)


def _today_wib() -> _dt.date:
    return _dt.datetime.now(_WIB).date()


def _coerce_date(val) -> _dt.date | None:
    if val is None:
        return None
    if isinstance(val, _dt.datetime):
        return val.date()
    if isinstance(val, _dt.date):
        return val
    try:
        return _dt.date.fromisoformat(str(val)[:10])
    except ValueError:
        return None


def _env_date(var_name: str, default: _dt.date) -> _dt.date:
    raw = (os.getenv(var_name, "") or "").strip()
    if not raw:
        return default
    try:
        return _dt.date.fromisoformat(raw[:10])
    except ValueError:
        return default


def ara_window() -> tuple[_dt.date, _dt.date]:
    """Jendela ARA yang diketahui (bisa diperpanjang via env `IDX_ARA_WINDOW_END`)."""
    end = _env_date("IDX_ARA_WINDOW_END", _ARA_WINDOW[1])
    start = _ARA_WINDOW[0]
    return (start, max(start, end))


def arb_asym_window() -> tuple[_dt.date, _dt.date]:
    """Jendela ARB asimetris 7% (Kep-00158/BEI/10-2026, bisa diperpanjang via env)."""
    end = _env_date(
        "IDX_ARB_WINDOW_END",
        _env_date("IDX_ARA_WINDOW_END", DEFAULT_ARB_ASYM_END),
    )
    start = DEFAULT_ARB_ASYM_START
    return (start, max(start, end))


def auto_reject_status(day: _dt.date | None = None) -> dict:
    """Status eksplisit jendela aturan ARA/ARB BEI agar tidak diam-diam expired."""
    d = _coerce_date(day) or _today_wib()
    a_start, a_end = ara_window()
    b_start, b_end = arb_asym_window()
    asym = b_start <= d <= b_end
    if d < a_start:
        return {
            "date": d.isoformat(),
            "active": False,
            "expired": False,
            "before_window": True,
            "asym_arb": asym,
            "window": (a_start, a_end),
            "rule": "pra-jendela-2026",
            "note": (
                f"Tanggal {d.isoformat()} sebelum jendela aturan ARA/ARB "
                f"({a_start.isoformat()} s.d. {a_end.isoformat()})."
            ),
        }
    if d <= a_end:
        return {
            "date": d.isoformat(),
            "active": True,
            "expired": False,
            "before_window": False,
            "asym_arb": asym,
            "window": (a_start, a_end),
            "rule": "Kep-00158/BEI/10-2026 (ARB 7%)" if asym else "BEI Q4-2026",
            "note": "",
        }
    return {
        "date": d.isoformat(),
        "active": False,
        "expired": True,
        "before_window": False,
        "asym_arb": asym,
        "window": (a_start, a_end),
        "rule": "pasca-jendela-2026",
        "note": (
            f"Jendela aturan ARA/ARB BEI ({a_start.isoformat()} s.d. {a_end.isoformat()}) "
            "sudah lewat — verifikasi pengumuman BEI terbaru atau set IDX_ARA_WINDOW_END."
        ),
    }


def tick_size(price: float) -> float:
    """Fraksi harga IDX untuk satu harga."""
    p = float(price)
    if p < 200:
        return 1.0
    if p < 500:
        return 2.0
    if p < 2000:
        return 5.0
    if p < 5000:
        return 10.0
    return 25.0


def round_tick(price: float, tick: float | None = None) -> float:
    """Bulatkan ke tick terdekat (half-up, bukan banker's rounding)."""
    t = tick if tick else tick_size(price)
    return math.floor(float(price) / t + 0.5) * t


def ceil_tick(price: float, tick: float | None = None) -> float:
    """Bulatkan ke atas ke tick berikutnya (untuk batas 'jangan kejar' & floor ARB)."""
    t = tick if tick else tick_size(price)
    return math.ceil(float(price) / t - 1e-9) * t


def auto_reject_pct(
    price: float,
    day: _dt.date | None,
    *,
    allow_expired: bool = False,
) -> float | None:
    """Batas ARA (%) untuk hari itu, atau None bila di luar jendela yang diketahui.

    Pita harga: 11–200 → 35%, >200–5.000 → 25%, >5.000 → 20%.
    Harga Rp1–10 memakai batas absolut Rp1 (tidak bisa dinyatakan persen).
    """
    d = _coerce_date(day)
    if d is None:
        return None
    start, end = ara_window()
    if d < start:
        return None
    if d > end and not allow_expired:
        return None
    p = float(price)
    if p <= 10:
        return None
    if p <= 200:
        return 35.0
    if p <= 5000:
        return 25.0
    return 20.0


def is_ara_locked(price: float, prev_close: float, day: _dt.date | None) -> bool:
    """True bila harga sudah mentok ARA (tidak realistis dibeli di harga itu)."""
    pct = auto_reject_pct(price, day)
    if pct is None or not prev_close or prev_close <= 0:
        return False
    return float(price) >= float(prev_close) * (1.0 + pct / 100.0) * 0.999


def _is_special_board(board: str | None) -> bool:
    b = (board or "").strip().lower()
    return any(tok in b for tok in ("pemantauan", "khusus", "fca"))


def min_board_floor(day: _dt.date | None = None, *, board: str = "regular") -> float:
    """Batas harga terendah papan BEI: Rp1 (Kep-00136 / Papan Khusus) vs Rp50 lama."""
    if _is_special_board(board):
        return 1.0
    d = _coerce_date(day) or _today_wib()
    return 1.0 if d >= REGULER_FLOOR_RP1_START else 50.0


def arb_pct(
    price: float,
    dt: _dt.date | None = None,
    *,
    board: str = "regular",
) -> float | None:
    """Persentase batas ARB harian BEI sesuai papan & tanggal (None untuk Rp1–10)."""
    p = float(price)
    if p <= 10:
        return None
    if _is_special_board(board):
        return 10.0
    d = _coerce_date(dt) or _today_wib()
    b_start, b_end = arb_asym_window()
    if b_start <= d <= b_end:
        return 7.0
    if p <= 200:
        return 35.0
    if p <= 5000:
        return 25.0
    return 20.0


def arb_floor_price(
    prev_close: float,
    dt: _dt.date | None = None,
    *,
    board: str = "regular",
) -> float:
    """Hitung harga batas bawah ARB harian BEI sesuai fraksi tick & aturan papan.

    - Di jendela Kep-00158/BEI/10-2026: ARB asimetris 7% (untuk Rp11–14 dan Rp1–10
      penurunan harian maksimal Rp1 per langkah tick).
    - Di luar jendela Kep-00158: aturan simetris umum (35%/25%/20%) atau 10%
      untuk Papan Pemantauan Khusus.
    - Floor minimum mengikuti Kep-00136/BEI/09-2026 (Rp1 papan reguler sejak
      28 Sep 2026; Rp50 sebelumnya; Rp1 untuk Papan Pemantauan Khusus).
    """
    try:
        prev = float(prev_close)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(prev) or prev <= 0:
        return 0.0

    d = _coerce_date(dt) or _today_wib()
    floor_min = min_board_floor(d, board=board)
    if prev <= floor_min:
        return float(floor_min)

    if prev <= 10:
        return float(max(floor_min, math.floor(prev) - 1))

    pct = arb_pct(prev, d, board=board)
    if pct is None:
        return float(floor_min)

    if pct == 7.0 and 11 <= prev <= 14:
        return float(max(floor_min, math.floor(prev) - 1))

    raw = prev * (1.0 - pct / 100.0)
    floor_p = ceil_tick(raw)
    if floor_p >= prev:
        step = tick_size(max(floor_min, prev - 1.0))
        floor_p = prev - step
    return float(max(floor_min, floor_p))


def check_arb_gap_risk(
    entry: float,
    sl: float,
    prev_close: float | None = None,
    dt: _dt.date | None = None,
    *,
    board: str = "regular",
) -> dict:
    """Peringatkan risiko terkunci ARB bila SL di bawah floor harian atau > -7%.

    Return dict: {risk, at_risk, below_floor, deeper_than_7pct, floor, sl_pct,
                  arb_pct, board_floor, window_note, warning}.
    """
    try:
        e = float(entry)
        s = float(sl)
    except (TypeError, ValueError):
        return {
            "risk": False, "at_risk": False, "below_floor": False,
            "deeper_than_7pct": False, "floor": 0.0, "sl_pct": 0.0,
            "arb_pct": None, "board_floor": 1.0, "window_note": "", "warning": "",
        }
    if not (math.isfinite(e) and math.isfinite(s) and e > 0 and s > 0):
        return {
            "risk": False, "at_risk": False, "below_floor": False,
            "deeper_than_7pct": False, "floor": 0.0, "sl_pct": 0.0,
            "arb_pct": None, "board_floor": 1.0, "window_note": "", "warning": "",
        }

    d = _coerce_date(dt) or _today_wib()
    ref = float(prev_close) if (prev_close and float(prev_close) > 0) else e
    floor = arb_floor_price(ref, dt=d, board=board)
    board_floor = min_board_floor(d, board=board)
    pct_arb = arb_pct(ref, d, board=board)
    sl_pct = (s / e - 1.0) * 100.0
    below_floor = s < floor
    deeper_than_7pct = sl_pct < -7.0
    risk = below_floor or deeper_than_7pct
    status = auto_reject_status(d)
    window_note = status["note"] if status.get("expired") else ""

    reasons: list[str] = []
    if below_floor:
        reasons.append(
            f"SL Rp{s:,.0f} di bawah batas ARB harian Rp{floor:,.0f}"
        )
    if deeper_than_7pct:
        reasons.append(
            f"jarak SL {sl_pct:.1f}% lebih dalam dari batas ARB asimetris -7%"
        )

    warning = ""
    if reasons:
        warning = (
            "Risiko terkunci ARB: " + " dan ".join(reasons)
            + " — order jual SL berisiko tidak terisi dalam 1 hari jika saham gap down/ARB."
        )
        if window_note:
            warning += f" ({window_note})"

    return {
        "risk": risk,
        "at_risk": risk,
        "below_floor": below_floor,
        "deeper_than_7pct": deeper_than_7pct,
        "floor": floor,
        "sl_pct": round(sl_pct, 2),
        "arb_pct": pct_arb,
        "board_floor": board_floor,
        "window_note": window_note,
        "warning": warning,
    }
