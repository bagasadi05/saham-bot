"""Uji gabungan faktor kualitas fundamental (`core/kualitas.py`) + skor relatif.

Mengukur secara cross-sectional apakah menambahkan saringan kesehatan
fundamental (ROE, PER, PBV, DER) di atas kandidat skor relatif menekan
false positive, baik pada seluruh universe maupun khusus Saham Syariah (ISSI).

Pemakaian:
  python research/quality_combo.py --universe expanded --out data/quality_combo.json
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import data as datamod  # noqa: E402
from core import fundamental, kualitas, relatif, syariah  # noqa: E402
from research import train_lgbm  # noqa: E402


def summarize_combo(rows: list[dict]) -> dict:
    """Ringkas statistik kualitas & return 20h untuk seluruh kandidat vs lolos saringan."""
    if not rows:
        return {
            "n": 0,
            "n_pass": 0,
            "n_fail": 0,
            "pass_rate_pct": 0.0,
            "avg_ret20d_all": None,
            "avg_ret20d_pass": None,
            "avg_ret20d_fail": None,
            "top_quality_syariah": [],
        }

    passed = [r for r in rows if r.get("quality_ok")]
    failed = [r for r in rows if not r.get("quality_ok")]

    def _avg(items: list[dict], key: str) -> float | None:
        vals = [float(x[key]) for x in items if x.get(key) is not None and np.isfinite(x[key])]
        return round(float(np.mean(vals)), 2) if vals else None

    return {
        "n": len(rows),
        "n_pass": len(passed),
        "n_fail": len(failed),
        "pass_rate_pct": round(len(passed) / len(rows) * 100.0, 1),
        "avg_ret20d_all": _avg(rows, "ret20d"),
        "avg_ret20d_pass": _avg(passed, "ret20d"),
        "avg_ret20d_fail": _avg(failed, "ret20d"),
        "top_quality_syariah": [
            r["ticker"]
            for r in sorted(
                [x for x in passed if x.get("is_syariah")],
                key=lambda x: -(x.get("rel_pct") or 0.0),
            )[:10]
        ],
    }


def _load_regime_context(path: Path) -> dict | None:
    """Muat konteks evaluasi rezim secara read-only bila file hasil riset tersedia."""
    if not path or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return {
            "source": path.name,
            "best_variant": data.get("best_variant") or data.get("recommended"),
            "modes": data.get("modes") if isinstance(data.get("modes"), dict) else None,
        }
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _fetch_one(ticker: str, *, offline: bool = False) -> dict | None:
    try:
        df = (datamod.fetch_cached_history(ticker, period="1y")
              if offline else datamod.fetch_history(ticker, period="1y"))
        if df is None or len(df) < 60:
            return None
        from core import analysis

        ind = analysis.compute_indicators(df)
        f_map = relatif.factors_from_ind(ind)
        fund = (fundamental.fetch_cached_fundamentals(ticker)
                if offline else fundamental.fetch_fundamentals(ticker))
        if offline and fund is None:
            return None
        q = kualitas.judge(fund)
        ret20d = float(ind["ret20d"].iloc[-1]) if "ret20d" in ind.columns else None
        return {
            "ticker": syariah.normalize_code(ticker),
            "is_syariah": syariah.is_syariah(ticker),
            "factors": f_map,
            "ret20d": ret20d,
            "quality_ok": bool(q and q.get("ok")),
            "quality_status": "sehat" if (q and q.get("ok")) else "waspada",
        }
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Uji gabungan kualitas fundamental + skor relatif.")
    ap.add_argument("--universe", default="expanded", choices=("lq45", "syariah", "expanded"))
    ap.add_argument("--max-tickers", type=int, default=60)
    ap.add_argument("--out", default="data/quality_combo.json")
    ap.add_argument("--offline", action="store_true",
                    help="Hanya baca cache lokal OHLCV & fundamental tanpa jaringan.")
    ap.add_argument("--regime-file", default="data/regime_eval.json",
                    help="Path opsional ringkasan evaluasi rezim read-only.")
    args = ap.parse_args(argv)

    tickers = train_lgbm.select_universe(args.universe, max_tickers=args.max_tickers)
    mode_lbl = "offline/cache" if args.offline else "live"
    print(
        f"[quality_combo] Mengevaluasi {len(tickers)} emiten ({args.universe}, {mode_lbl})...",
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=6) as pool:
        fetched = [
            r for r in pool.map(lambda t: _fetch_one(t, offline=args.offline), tickers)
            if r is not None
        ]

    if len(fetched) >= relatif.MIN_UNIVERSE:
        scored = relatif.rank_universe({r["ticker"]: r["factors"] for r in fetched})
        for r in fetched:
            s = scored.get(r["ticker"]) or {}
            r["rel_pct"] = s.get("pct")
            r["quintile"] = s.get("quintile")

    top_q = [r for r in fetched if (r.get("quintile") or 0) >= 4]
    sy_rows = [r for r in fetched if r.get("is_syariah")]
    sy_top_q = [r for r in top_q if r.get("is_syariah")]

    report = {
        "universe": args.universe,
        "offline": bool(args.offline),
        "n_evaluated": len(fetched),
        "all_stocks": summarize_combo(fetched),
        "top_quintiles_q4_q5": summarize_combo(top_q),
        "syariah_all": summarize_combo(sy_rows),
        "syariah_top_quintiles_q4_q5": summarize_combo(sy_top_q),
    }
    reg_ctx = _load_regime_context(ROOT / args.regime_file)
    if reg_ctx is not None:
        report["regime_context"] = reg_ctx

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print("\n=== HASIL GABUNGAN KUALITAS + SKOR RELATIF ===")
    for sec in ("all_stocks", "top_quintiles_q4_q5", "syariah_top_quintiles_q4_q5"):
        m = report[sec]
        print(
            f"  {sec:28s} | n={m['n']:2d} | lolos={m['pass_rate_pct']:5.1f}% | "
            f"ret20d lolos={m['avg_ret20d_pass']} vs gagal={m['avg_ret20d_fail']}"
        )
    print(f"Ditulis ke {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
