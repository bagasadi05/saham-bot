"""Sentimen berita pakai model ML lokal indo-roBERTa finansial (F1 ~93%).

Model: will702/indo-roBERTa-financial-sentiment-v2 (HuggingFace, MIT).
- Berjalan sepenuhnya lokal (CPU) — tanpa API key, tanpa biaya per call.
- Lazy load: model baru diunduh saat pertama kali dipakai.
- Kalau gagal (torch/transformers tidak ada, RAM habis, unduhan gagal),
  caller jatuh ke mesin lexicon sebagai pendopang — LEBIH BAIK ADA PENGUKURAN
  BERBASIS KODE daripada tidak sama sekali.

Glosarium label model: {0: positive, 1: neutral, 2: negative}
"""
from __future__ import annotations

import os
import threading

MODEL_ID = "will702/indo-roBERTa-financial-sentiment-v2"
LABEL_MAP = {0: "positive", 1: "neutral", 2: "negative"}

# Folder koper lokal (hasil unduhan manual) — dipakai bila ada, supaya bot
# enggak danak tergantung jaringan yang tidak stabil saat startup.
LOCAL_MODEL_DIR = os.environ.get(
    "SAHAMBOT_LOCAL_MODEL", "/tmp/opencode/hfmodel/indo-fin-roberta")

_state = {"model": None, "tokenizer": None, "failed": False}
_lock = threading.Lock()


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


def classify(text: str) -> dict | None:
    """Klasifikasi satu judul. Return {'label','score','probs'} atau None."""
    model, tok = get_model()
    if model is None:
        return None
    try:
        import torch
        with torch.no_grad():
            inputs = tok(text, return_tensors="pt", truncation=True, max_length=128)
            logits = model(**inputs).logits[0]
            probs = torch.softmax(logits, dim=-1).tolist()
        idx = int(max(range(len(probs)), key=lambda i: probs[i]))
        return {
            "label": LABEL_MAP.get(idx, str(idx)),
            "score": round(probs[idx], 3),
            "probs": {LABEL_MAP.get(i, str(i)): round(p, 3) for i, p in enumerate(probs)},
        }
    except Exception:
        _state["failed"] = True
        _state["model"] = None
        return None


def is_available() -> bool:
    return classify("probe") is not None
