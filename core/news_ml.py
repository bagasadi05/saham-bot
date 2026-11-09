"""Sentimen berita pakai model ML lokal indo-roBERTa finansial (F1 ~93%).

Model: will702/indo-roBERTa-financial-sentiment-v2 (HuggingFace, MIT).
- Berjalan sepenuhnya lokal (CPU) — tanpa API key, tanpa biaya per call.
- Lazy load: model baru dimuat saat pertama kali dipakai.
- Inferensi dilakukan BATCH (satu forward pass untuk semua judul) + cache
  per judul, karena berita sering berulang antar-emiten.
- Kalau gagal (torch/transformers tidak ada, RAM habis, unduhan gagal),
  caller jatuh ke mesin lexicon.

Glosarium label model: {0: positive, 1: neutral, 2: negative}
"""
from __future__ import annotations

import os
import threading

MODEL_ID = "will702/indo-roBERTa-financial-sentiment-v2"
LABEL_MAP = {0: "positive", 1: "neutral", 2: "negative"}

# Folder model lokal (hasil unduhan manual / build Docker) — dipakai bila ada,
# supaya bot tidak bergantung jaringan saat startup.
# Override dengan env SAHAMBOT_LOCAL_MODEL jika perlu.
LOCAL_MODEL_DIR = os.environ.get(
    "SAHAMBOT_LOCAL_MODEL",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "models", "indo-fin-roberta"))

_state = {"model": None, "tokenizer": None, "failed": False}
_lock = threading.Lock()

_cache: dict[str, dict | None] = {}
_cache_lock = threading.Lock()
_CACHE_MAX = 500


def get_model():
    """Muat model sekali saja. Return (model, tokenizer) atau None kalau gagal."""
    with _lock:
        if _state["model"] is not None or _state["failed"]:
            return _state["model"], _state["tokenizer"]
        try:
            import torch  # noqa: F401
            from transformers import (AutoModelForSequenceClassification,
                                      AutoTokenizer)
            use_local = (os.path.isdir(LOCAL_MODEL_DIR)
                         and os.path.exists(os.path.join(LOCAL_MODEL_DIR, "config.json"))
                         and os.path.exists(os.path.join(LOCAL_MODEL_DIR, "model.safetensors")))
            src = LOCAL_MODEL_DIR if use_local else MODEL_ID
            kw = {"local_files_only": True} if use_local else {}
            tok = AutoTokenizer.from_pretrained(src, **kw)
            model = AutoModelForSequenceClassification.from_pretrained(src, **kw)
            model.eval()
            _state["model"], _state["tokenizer"] = model, tok
            return model, tok
        except Exception:
            _state["failed"] = True
            return None, None


def _id_to_label(model) -> dict[int, str]:
    raw = getattr(getattr(model, "config", None), "id2label", None)
    if isinstance(raw, dict) and raw:
        mapped: dict[int, str] = {}
        for k, v in raw.items():
            try:
                mapped[int(k)] = str(v).strip().lower()
            except (TypeError, ValueError):
                continue
        if mapped:
            return mapped
    return LABEL_MAP


def _remember(text: str, result: dict):
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX and text not in _cache:
            _cache.pop(next(iter(_cache)))
        _cache[text] = result


def classify_batch(texts: list[str]) -> list[dict | None]:
    """Klasifikasi banyak judul sekaligus. Return list sejajar dengan input."""
    out: list[dict | None] = [None] * len(texts)
    todo: list[int] = []
    with _cache_lock:
        for i, text in enumerate(texts):
            if text in _cache:
                out[i] = _cache[text]
            else:
                todo.append(i)
    if not todo:
        return out

    model, tok = get_model()
    if model is None:
        return out
    try:
        import torch
        id2lbl = _id_to_label(model)
        batch = [texts[i] for i in todo]
        inputs = tok(batch, return_tensors="pt", truncation=True,
                     max_length=128, padding=True)
        with torch.no_grad():
            probs_all = torch.softmax(model(**inputs).logits, dim=-1).tolist()
        for i, probs in zip(todo, probs_all, strict=True):
            idx = int(max(range(len(probs)), key=lambda j: probs[j]))
            res = {
                "label": id2lbl.get(idx, str(idx)),
                "score": round(probs[idx], 3),
                "probs": {id2lbl.get(j, str(j)): round(p, 3)
                          for j, p in enumerate(probs)},
            }
            out[i] = res
            _remember(texts[i], res)
    except Exception:
        _state["failed"] = True
        _state["model"] = None
        return out
    return out


def classify(text: str) -> dict | None:
    """Klasifikasi satu judul. Return {'label','score','probs'} atau None."""
    return classify_batch([text])[0]


def is_available() -> bool:
    return classify("probe") is not None
