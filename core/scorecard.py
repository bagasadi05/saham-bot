"""Jejak sinyal: mengukur apakah label BELI/JUAL benar-benar terbukti.

Sejak `screener.scan` menyimpan snapshot harian ke `scan_history`, klaim
produk bisa diuji dengan kenyataan. Setiap baris diberi hasil akhir setelah
`HORIZON` hari bursa:

  - `fwd_ret`    : return dari harga entry ke penutupan hari ke-HORIZON (%)
  - `r_multiple` : khusus BELI, memakai SL/TP yang dulu ditampilkan
  - `outcome`    : BELI -> TP / SL / TIME / ADJ / NA
                   JUAL -> TURUN (peringatan tepat) / NAIK / DATAR / ADJ / NA
                   TAHAN -> NA / ADJ

Aturan yang dipilih sengaja konservatif:
  - SL dicek lebih dulu daripada TP pada bar yang sama (bisa jadi keduanya
    kena; tanpa data intraday urutannya tidak diketahui).
  - Saat SL kena, hasil dihitung di harga SL, bukan harga terburuk gap.
  - JUAL dievaluasi terpisah dari BELI (apakah harga turun setelah peringatan,
    bukan instruksi short).
  - TAHAN dipakai sebagai pembanding pasar, bukan sinyal untuk ditradingkan.

Modul ini tidak mengunduh data sendiri: `screener.scan` sudah punya frame
semua emiten, jadi penutupan hasil tidak menambah satu pun permintaan jaringan.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import os

import pandas as pd

log = logging.getLogger("sahambuddy.scorecard")

HORIZON = max(1, int(os.getenv("SCORECARD_HORIZON", "20") or 20))
SIGNALS = ("BELI", "JUAL", "TAHAN")
DIV_SHIFT_THRESHOLD = 0.005  # >0,5% s.d. 25% beda harga close vs scan -> indikasi eks-div/adjusted
SPLIT_ADJ_THRESHOLD = 0.25   # >25% beda harga close vs scan -> indikasi split/reverse split


def _num(value) -> float | None:
    """Float aman: None untuk NaN/inf/teks aneh."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def index_of(df: pd.DataFrame, trade_date: str) -> int | None:
    """Posisi bar pertama pada/ setelah `trade_date` (None bila tak ada)."""
    try:
        target = dt.date.fromisoformat(str(trade_date)[:10])
    except ValueError:
        return None
    for pos, ts in enumerate(df.index):
        d = ts.date() if hasattr(ts, "date") else ts
        if d >= target:
            return pos
    return None


def _outcome(row: dict, window: pd.DataFrame) -> dict:
    """Hasil akhir satu sinyal dari jendela bar setelah tanggal scan."""
    entry = _num(row.get("entry")) or _num(row.get("price"))
    if not entry or entry <= 0:
        return {"fwd_ret": None, "r_multiple": None, "outcome": "NA"}

    end = _num(window["close"].iloc[-1])
    if end is None:
        return {"fwd_ret": None, "r_multiple": None, "outcome": "NA"}
    fwd = (end / entry - 1.0) * 100.0
    fwd_rounded = round(fwd, 2)

    sig = row.get("signal")
    if sig == "JUAL":
        # JUAL bukan short: ukur akuntabilitas apakah harga benar-benar turun
        # setelah peringatan vs malah naik.
        if fwd_rounded < 0:
            jual_outcome = "TURUN"
        elif fwd_rounded > 0:
            jual_outcome = "NAIK"
        else:
            jual_outcome = "DATAR"
        return {"fwd_ret": fwd_rounded, "r_multiple": None, "outcome": jual_outcome}

    sl, tp = _num(row.get("sl")), _num(row.get("tp"))
    if sig != "BELI" or sl is None or sl >= entry:
        # TAHAN (atau BELI tanpa rencana SL valid): cukup return, tanpa R.
        return {"fwd_ret": fwd_rounded, "r_multiple": None, "outcome": "NA"}

    risk = entry - sl
    lows = pd.to_numeric(window["low"], errors="coerce")
    highs = pd.to_numeric(window["high"], errors="coerce")
    for low, high in zip(lows, highs, strict=True):
        low, high = _num(low), _num(high)
        if low is not None and low <= sl:
            return {"fwd_ret": round((sl / entry - 1.0) * 100.0, 2),
                    "r_multiple": -1.0, "outcome": "SL"}
        if tp is not None and high is not None and high >= tp:
            return {"fwd_ret": round((tp / entry - 1.0) * 100.0, 2),
                    "r_multiple": round((tp - entry) / risk, 2),
                    "outcome": "TP"}
    return {"fwd_ret": fwd_rounded,
            "r_multiple": round((end - entry) / risk, 2), "outcome": "TIME"}


def resolve_rows(rows: list[dict], frames: dict[str, pd.DataFrame],
                 horizon: int = HORIZON) -> list[dict]:
    """Hitung hasil akhir untuk baris histori yang bar-nya sudah lengkap.

    Baris yang datanya belum sampai `horizon` hari bursa dilewati — pemanggil
    akan mencobanya lagi pada scan berikutnya.
    """
    out: list[dict] = []
    for row in rows:
        df = frames.get(row.get("ticker") or "")
        if df is None or len(df) < 2:
            continue
        pos = index_of(df, row.get("trade_date", ""))
        if pos is None:
            continue
        ref = _num(row.get("price"))
        bar_close = _num(df["close"].iloc[pos])
        rel_shift = abs(bar_close / ref - 1.0) if (ref and bar_close) else 0.0
        if ref and bar_close and rel_shift > SPLIT_ADJ_THRESHOLD:
            # Harga historis disesuaikan aksi korporasi (split/reverse split)
            # setelah scan, jadi entry lama tidak sebanding. Tutup sebagai ADJ
            # agar baris tidak dicoba selamanya dan tidak mengotori statistik.
            out.append({"trade_date": row["trade_date"], "ticker": row["ticker"],
                        "signal": row.get("signal"), "bars": 0, "fwd_ret": None,
                        "r_multiple": None, "outcome": "ADJ",
                        "div_adjusted": False})
            continue
        window = df.iloc[pos + 1: pos + 1 + horizon]
        if len(window) < horizon:
            continue
        got = _outcome(row, window)
        if got.get("fwd_ret") is None and got.get("outcome") != "ADJ":
            continue
        div_shifted = bool(ref and bar_close and rel_shift > DIV_SHIFT_THRESHOLD)
        got.update({
            "trade_date": row["trade_date"],
            "ticker": row["ticker"],
            "signal": row.get("signal"),
            "bars": len(window),
            "div_adjusted": div_shifted,
        })
        if div_shifted and ref and bar_close:
            got["adj_shift_pct"] = round((bar_close / ref - 1.0) * 100.0, 2)
        out.append(got)
    return out


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _pct(part: int, total: int) -> float | None:
    return round(100.0 * part / total, 1) if total else None


def _newey_west_t(values: list[float], nlags: int) -> float | None:
    """t-stat rata-rata dengan koreksi Newey-West (kernel Bartlett)."""
    x = [float(v) for v in values if v == v]
    n = len(x)
    if n < 3 or nlags <= 0:
        return None
    mean = sum(x) / n
    e = [v - mean for v in x]
    var = sum(v * v for v in e) / n
    eff_lags = min(int(nlags), n - 1)
    for lag in range(1, eff_lags + 1):
        gamma = sum(e[i] * e[i - lag] for i in range(lag, n)) / n
        var += 2.0 * (1.0 - lag / (eff_lags + 1.0)) * gamma
    if var <= 0:
        return None
    se = math.sqrt(var / n)
    return round(mean / se, 2) if se > 0 else None


def live_ic(rows: list[dict], min_n: int = 8,
            horizon: int = HORIZON) -> dict:
    """IC cross-sectional harian dari sinyal yang benar-benar keluar.

    Meniru `research/evaluate.py`, tetapi memakai baris `scan_history` — bukan
    sampel riset. Butuh minimal `min_n` emiten pada tanggal yang sama agar
    korelasi peringkat tidak sekadar derau. Melaporkan `t_hac` (Newey-West)
    untuk overlap horison serta catatan potensi pergeseran dividen/eks-div
    pada data adjusted.
    """
    by_date: dict[str, list[tuple[float, float]]] = {}
    n_adj = 0
    n_div_shifted = 0
    for row in rows:
        if row.get("outcome") == "ADJ":
            n_adj += 1
        shift_val = _num(row.get("adj_shift_pct"))
        if row.get("div_adjusted") or (shift_val is not None and abs(shift_val) > 0.5):
            n_div_shifted += 1

        score = _num(row.get("rel_pct"))
        ret = _num(row.get("fwd_ret"))
        if score is None or ret is None:
            continue
        by_date.setdefault(str(row.get("trade_date") or ""), []).append((score, ret))

    ics: list[float] = []
    for trade_date in sorted(by_date):
        pairs = by_date[trade_date]
        if len(pairs) < min_n:
            continue
        frame = pd.DataFrame(pairs, columns=["score", "ret"])
        # Spearman = Pearson pada peringkat; tidak butuh scipy.
        ic = frame["score"].rank().corr(frame["ret"].rank())
        if ic is not None and ic == ic:      # bukan NaN
            ics.append(float(ic))

    nlags = max(0, int(horizon))
    if n_div_shifted > 0 or n_adj > 0:
        adjusted_warning = (
            f"data adjusted: {n_div_shifted} baris bergeser >0,5% (potensi eks-div)"
            f" & {n_adj} split >25% (ADJ)"
        )
    elif ics or rows:
        adjusted_warning = (
            "data auto_adjust=True: dividen eks-div (<25%) dapat menggeser "
            "close historis tanpa status ADJ"
        )
    else:
        adjusted_warning = None

    if not ics:
        return {
            "n_dates": 0, "mean": None, "positive_pct": None,
            "t": None, "t_hac": None, "nlags": nlags,
            "adjusted_data": True, "n_adj": n_adj,
            "n_div_shifted": n_div_shifted,
            "adjusted_warning": adjusted_warning,
        }
    mean = sum(ics) / len(ics)
    se = None
    if len(ics) > 1:
        var = sum((x - mean) ** 2 for x in ics) / (len(ics) - 1)
        se = (var / len(ics)) ** 0.5
    return {
        "n_dates": len(ics),
        "mean": round(mean, 4),
        "positive_pct": round(100 * sum(1 for x in ics if x > 0) / len(ics), 1),
        "t": round(mean / se, 2) if se else None,
        "t_hac": _newey_west_t(ics, nlags) if nlags > 0 else None,
        "nlags": nlags,
        "adjusted_data": True,
        "n_adj": n_adj,
        "n_div_shifted": n_div_shifted,
        "adjusted_warning": adjusted_warning,
    }


def summarize(rows: list[dict], horizon: int = HORIZON) -> dict:
    """Agregasi hasil per label sinyal: return, win-rate, R, dan outcome."""
    result: dict = {
        "horizon": horizon, "n": len(rows),
        "first": "", "last": "", "signals": {},
        "div_adjusted": sum(1 for r in rows if r.get("div_adjusted")),
        "n_adj": sum(1 for r in rows if r.get("outcome") == "ADJ"),
    }
    if rows:
        dates = sorted({str(r.get("trade_date")) for r in rows if r.get("trade_date")})
        if dates:
            result["first"], result["last"] = dates[0], dates[-1]

    for sig in SIGNALS:
        subset = [r for r in rows
                  if r.get("signal") == sig and _num(r.get("fwd_ret")) is not None]
        if not subset:
            continue
        fwds = [_num(r["fwd_ret"]) for r in subset]
        if sig == "JUAL":          # "benar" = harga turun setelah label JUAL
            wins = sum(1 for f in fwds if f < 0)
        else:
            wins = sum(1 for f in fwds if f > 0)
        rs = [_num(r.get("r_multiple")) for r in subset]
        rs = [r for r in rs if r is not None]
        outcomes = {}
        for r in subset:
            key = r.get("outcome") or "NA"
            outcomes[key] = outcomes.get(key, 0) + 1
        result["signals"][sig] = {
            "n": len(subset),
            "avg_fwd": _mean(fwds),
            "win": _pct(wins, len(subset)),
            "avg_r": _mean(rs),
            "outcomes": outcomes,
        }

    beli = result["signals"].get("BELI", {}).get("avg_fwd")
    jual = result["signals"].get("JUAL", {}).get("avg_fwd")
    base = result["signals"].get("TAHAN", {}).get("avg_fwd")
    result["edge"] = round(beli - base, 2) if beli is not None and base is not None else None
    result["jual_edge"] = round(jual - base, 2) if jual is not None and base is not None else None
    result["live_ic"] = live_ic(rows, horizon=horizon)
    return result


def _stat_line(label: str, value: str) -> str:
    """Satu baris statistik dengan label rata kiri agar mudah dipindai."""
    return f"  {label:<17}: {value}"


def format_summary(stats: dict, counts: dict | None = None) -> str:
    """Teks /jejak — angka apa adanya, termasuk saat sampelnya masih kosong."""
    horizon = stats.get("horizon", HORIZON)
    counts = counts or {}
    lines = [f"📊 <b>JEJAK SINYAL</b> — hasil setelah {horizon} hari bursa"]

    if not stats.get("n"):
        pending = int(counts.get("pending") or 0)
        lines.append("")
        lines.append("Belum ada sinyal yang cukup umur untuk dinilai.")
        if pending:
            lines.append(f"Tersimpan <b>{pending}</b> baris sinyal, menunggu "
                         f"{horizon} hari bursa sejak tanggal scan.")
        lines.append("")
        lines.append("<i>Histori baru terbentuk sejak fitur ini aktif; "
                     "hasil pertama muncul setelah horizon terlampaui.</i>")
        return "\n".join(lines)

    lines.append(f"Periode: {stats.get('first', '')} → {stats.get('last', '')} "
                 f"· {stats['n']} baris sinyal")
    for sig in SIGNALS:
        s = stats["signals"].get(sig)
        if not s:
            continue
        label = f"<b>{sig}</b> (n={s['n']})"
        if sig == "TAHAN":
            label += " — pembanding, bukan sinyal"
        elif sig == "JUAL":
            label += " — evaluasi peringatan (bukan short)"
        lines.append("")
        lines.append(label)
        avg = "—" if s["avg_fwd"] is None else f"{s['avg_fwd']:+.2f}%"
        lines.append(_stat_line("Rata-rata return", avg))
        win = "—" if s["win"] is None else f"{s['win']:.1f}%"
        benar = "benar (turun)" if sig == "JUAL" else "menang"
        lines.append(_stat_line(benar.capitalize(), win))
        if sig == "BELI":
            avg_r = "—" if s["avg_r"] is None else f"{s['avg_r']:+.2f}R"
            oc = s["outcomes"]
            lines.append(_stat_line(
                "Rata-rata R",
                f"{avg_r} (TP {oc.get('TP', 0)} · SL {oc.get('SL', 0)} · "
                f"waktu {oc.get('TIME', 0)})"))
        elif sig == "JUAL":
            oc = s["outcomes"]
            status_parts = [f"turun {oc.get('TURUN', 0)}", f"naik {oc.get('NAIK', 0)}"]
            if oc.get("DATAR"):
                status_parts.append(f"datar {oc['DATAR']}")
            if oc.get("NA"):
                status_parts.append(f"NA lama {oc['NA']}")
            lines.append(_stat_line("Status harga", " · ".join(status_parts)))

    ic = stats.get("live_ic") or {}
    if ic.get("mean") is not None:
        t_parts = []
        if ic.get("t") is not None:
            t_parts.append(f"t={ic['t']}")
        if ic.get("t_hac") is not None:
            t_parts.append(f"t_hac={ic['t_hac']} (lag {ic.get('nlags', horizon)})")
        t_txt = f", {', '.join(t_parts)}" if t_parts else ""
        lines.append("")
        lines.append(f"IC live (peringkat vs hasil): {ic['mean']:+.3f} "
                     f"dari {ic['n_dates']} tanggal{t_txt}")
        if ic.get("adjusted_warning"):
            lines.append(f"  ⚠️ <i>{ic['adjusted_warning']}</i>")
    elif stats.get("div_adjusted") or stats.get("n_adj"):
        lines.append("")
        lines.append(
            f"⚠️ <i>Data adjusted: {stats.get('div_adjusted', 0)} baris bergeser "
            f"&gt;0,5% (potensi eks-div), {stats.get('n_adj', 0)} split (ADJ).</i>"
        )
    if stats.get("edge") is not None:
        lines.append("")
        lines.append(f"Selisih BELI vs TAHAN: <b>{stats['edge']:+.2f}</b> poin persen")
    if stats.get("jual_edge") is not None:
        lines.append(f"Selisih JUAL vs TAHAN: <b>{stats['jual_edge']:+.2f}</b> poin persen")
    lines.append("")
    lines.append("<i>Ini pengukuran hasil sendiri, bukan janji. Sampel kecil "
                 "mudah didominasi kebetulan; baca angkanya sebagai indikasi.</i>")
    return "\n".join(lines)
