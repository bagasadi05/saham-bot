"""Evaluasi offline akurasi & macro-F1 sentimen berita (`core/news.py` / `core/news_ml.py`).

Mengukur performa mesin sentimen terhadap dataset emas berbahasa Indonesia
(`tests/fixtures/news_sentiment_gold.json`) tanpa memanggil jaringan.

Pakai:
    python research/evaluate_sentiment.py
    python research/evaluate_sentiment.py --engine lexicon --json
    python research/evaluate_sentiment.py --gold tests/fixtures/news_sentiment_gold.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import news  # noqa: E402

DEFAULT_GOLD = ROOT / "tests" / "fixtures" / "news_sentiment_gold.json"
LABELS = ("positive", "negative", "neutral")


def load_gold(path: Path | str = DEFAULT_GOLD) -> list[dict]:
    """Muat daftar kasus uji emas dari berkas JSON."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"dataset emas kosong atau tidak valid: {path}")
    return cases


def predict_lexicon(
    title: str,
    source: str | None = None,
    *,
    threshold: float = news.ITEM_SENT_THRESHOLD,
) -> tuple[str, float, list[str]]:
    """Klasifikasikan satu judul menggunakan mesin lexicon `core.news.score_title`."""
    score, hits = news.score_title(title, source)
    if score >= threshold:
        return "positive", score, hits
    if score <= -threshold:
        return "negative", score, hits
    return "neutral", score, hits


def _predict_case(case: dict, engine: str, threshold: float) -> tuple[str, float, str, list[str]]:
    title = str(case.get("title") or "")
    source = case.get("source")
    if engine in ("ml", "auto"):
        try:
            from core import news_ml
            clean = news.strip_source_suffix(title, source)
            res = news_ml.classify(clean)
            if res is not None:
                lbl = str(res.get("label") or "neutral").lower()
                if lbl in LABELS:
                    probs = res.get("probs") or {}
                    sc = round((float(probs.get("positive", 0.0))
                                - float(probs.get("negative", 0.0))) * 2.0, 4)
                    return lbl, sc, "ml", [f"ML {lbl}:{res.get('score', 0):.0%}"]
        except Exception:
            pass
        if engine == "ml":
            raise RuntimeError("model ML lokal tidak tersedia untuk --engine ml")
    lbl, sc, hits = predict_lexicon(title, source, threshold=threshold)
    return lbl, sc, "lexicon", hits


def evaluate_cases(
    cases: list[dict],
    *,
    engine: str = "lexicon",
    threshold: float = news.ITEM_SENT_THRESHOLD,
) -> dict:
    """Hitung metrik akurasi, precision/recall/F1 per kelas, confusion matrix, dan per kategori."""
    if engine not in ("lexicon", "ml", "auto"):
        raise ValueError(f"engine tidak dikenal: {engine}")

    confusion = {t: {p: 0 for p in LABELS} for t in LABELS}
    by_cat: dict[str, dict[str, int]] = {}
    errors: list[dict] = []
    engine_counts: dict[str, int] = {"ml": 0, "lexicon": 0}
    correct = 0

    for case in cases:
        gold = str(case.get("label") or "").strip().lower()
        if gold not in LABELS:
            raise ValueError(f"label gold tidak valid pada {case.get('id')}: {gold!r}")
        pred, score, used_engine, hits = _predict_case(case, engine, threshold)
        confusion[gold][pred] += 1
        engine_counts[used_engine] = engine_counts.get(used_engine, 0) + 1
        cat = str(case.get("category") or "uncategorized")
        st = by_cat.setdefault(cat, {"n": 0, "correct": 0})
        st["n"] += 1
        if pred == gold:
            correct += 1
            st["correct"] += 1
        else:
            errors.append({
                "id": case.get("id"),
                "category": cat,
                "gold": gold,
                "pred": pred,
                "score": score,
                "hits": hits,
                "title": case.get("title"),
            })

    n = len(cases)
    accuracy = round(correct / n, 4) if n else 0.0
    per_class: dict[str, dict] = {}
    f1s: list[float] = []
    for lbl in LABELS:
        tp = confusion[lbl][lbl]
        fp = sum(confusion[other][lbl] for other in LABELS if other != lbl)
        fn = sum(confusion[lbl][other] for other in LABELS if other != lbl)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
        per_class[lbl] = {
            "support": tp + fn,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1": round(f1, 4),
        }
        f1s.append(f1)

    macro_f1 = round(sum(f1s) / len(f1s), 4) if f1s else 0.0
    cat_summary = {
        cat: {
            "n": st["n"],
            "correct": st["correct"],
            "accuracy": round(st["correct"] / st["n"], 4) if st["n"] else 0.0,
        }
        for cat, st in sorted(by_cat.items())
    }
    return {
        "engine_requested": engine,
        "engine_counts": engine_counts,
        "n": n,
        "correct": correct,
        "accuracy": accuracy,
        "macro_f1": macro_f1,
        "per_class": per_class,
        "confusion": confusion,
        "by_category": cat_summary,
        "errors": errors,
    }


def format_report(res: dict) -> str:
    """Format ringkasan hasil evaluasi sentimen menjadi teks CLI yang ringkas."""
    lines = [
        f"Evaluasi Sentimen ({res['engine_requested']}) — n={res['n']}",
        f"Akurasi : {res['accuracy'] * 100:.1f}% ({res['correct']}/{res['n']})",
        f"Macro F1: {res['macro_f1'] * 100:.1f}%",
        f"Mesin   : lexicon={res['engine_counts'].get('lexicon', 0)}, "
        f"ml={res['engine_counts'].get('ml', 0)}",
        "",
        "Per kelas:",
    ]
    for lbl in LABELS:
        c = res["per_class"][lbl]
        lines.append(
            f"  {lbl:8s} n={c['support']:2d}  "
            f"P={c['precision']:.3f}  R={c['recall']:.3f}  F1={c['f1']:.3f}"
        )
    if res["errors"]:
        lines.append("")
        lines.append(f"Salah klasifikasi ({len(res['errors'])}):")
        for e in res["errors"][:15]:
            lines.append(
                f"  - [{e['id']}] gold={e['gold']} pred={e['pred']} "
                f"(score={e['score']:+.2f}, hits={e['hits']}): {e['title']}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gold", default=str(DEFAULT_GOLD))
    ap.add_argument("--engine", choices=("lexicon", "ml", "auto"), default="lexicon")
    ap.add_argument("--threshold", type=float, default=news.ITEM_SENT_THRESHOLD)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    cases = load_gold(args.gold)
    res = evaluate_cases(cases, engine=args.engine, threshold=args.threshold)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(format_report(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
