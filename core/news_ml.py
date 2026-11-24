"""Sentimen berita pakai model ML lokal indo-roBERTa finansial (F1 ~93%).

Model: will702/indo-roBERTa-financial-sentiment-v2 (HuggingFace, MIT).
- Berjalan sepenuhnya lokal (CPU) — tanpa API key, tanpa biaya per call.
- Lazy load: model baru dimuat saat pertama kali dipakai.
- Inferensi dilakukan BATCH (satu forward pass untuk semua judul) + cache
  per judul, karena berita sering berulang antar-emiten.
- Optimasi CPU: memakai `torch.inference_mode()`, mendukung kuantisasi dinamis
  INT8 via env `SAHAMBOT_ML_QUANTIZE=1` (hemat RAM & lebih cepat di CPU),
  serta otomatis memakai ONNX Runtime jika `model_quantized.onnx` / `model.onnx`
  dan paket `onnxruntime` tersedia di `LOCAL_MODEL_DIR`.
- Kalau gagal (torch/transformers tidak ada, RAM habis, unduhan gagal),
  caller jatuh ke mesin lexicon.

Glosarium label model: {0: positive, 1: neutral, 2: negative}
"""
from __future__ import annotations

import logging
import math
import os
import threading

log = logging.getLogger("sahambuddy.news_ml")

MODEL_ID = "will702/indo-roBERTa-financial-sentiment-v2"
MODEL_REVISION = os.environ.get("SAHAMBOT_MODEL_REVISION", "").strip() or None
LABEL_MAP = {0: "positive", 1: "neutral", 2: "negative"}

# Folder model lokal (hasil unduhan manual / build Docker) — dipakai bila ada,
# supaya bot tidak bergantung jaringan saat startup.
# Override dengan env SAHAMBOT_LOCAL_MODEL jika perlu.
LOCAL_MODEL_DIR = os.environ.get(
    "SAHAMBOT_LOCAL_MODEL",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "models", "indo-fin-roberta"))

_state = {
    "model": None,
    "tokenizer": None,
    "failed": False,
    "engine": None,
    "quantized": False,
}
_lock = threading.Lock()

_cache: dict[str, dict | None] = {}
_cache_lock = threading.Lock()
_CACHE_MAX = 500


def _want_quantize() -> bool:
    return os.environ.get("SAHAMBOT_ML_QUANTIZE", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def model_info() -> dict:
    """Metadata ringkas status model ML sentimen untuk diagnostik /status."""
    return {
        "loaded": _state["model"] is not None,
        "failed": bool(_state["failed"]),
        "engine": _state.get("engine"),
        "quantized": bool(_state.get("quantized")),
        "local_dir": LOCAL_MODEL_DIR,
    }


def _try_load_onnx(local_dir: str):
    """Muat sesi ONNX Runtime bila file `.onnx` & paket `onnxruntime` tersedia."""
    if not os.path.isdir(local_dir):
        return None
    onnx_file = None
    for cand in ("model_quantized.onnx", "model.onnx"):
        p = os.path.join(local_dir, cand)
        if os.path.exists(p):
            onnx_file = p
            break
    if not onnx_file:
        return None
    try:
        import onnxruntime as ort
        from transformers import AutoTokenizer

        tok = AutoTokenizer.from_pretrained(local_dir, local_files_only=True)
        sess = ort.InferenceSession(onnx_file, providers=["CPUExecutionProvider"])
        return sess, tok, ("quantized" in os.path.basename(onnx_file))
    except Exception:
        return None


def _softmax_rows(logits) -> list[list[float]]:
    out = []
    for row in logits:
        vals = [float(x) for x in row]
        m = max(vals)
        exps = [math.exp(x - m) for x in vals]
        s = sum(exps) or 1.0
        out.append([e / s for e in exps])
    return out


def get_model():
    """Muat model sekali saja. Return (model, tokenizer) atau None kalau gagal."""
    with _lock:
        if _state["model"] is not None or _state["failed"]:
            return _state["model"], _state["tokenizer"]

        onnx_loaded = _try_load_onnx(LOCAL_MODEL_DIR)
        if onnx_loaded is not None:
            sess, tok, is_q = onnx_loaded
            _state.update({
                "model": sess,
                "tokenizer": tok,
                "engine": "onnxruntime",
                "quantized": is_q,
            })
            return sess, tok

        try:
            import torch
            from transformers import (AutoModelForSequenceClassification,
                                      AutoTokenizer)
            use_local = (os.path.isdir(LOCAL_MODEL_DIR)
                         and os.path.exists(os.path.join(LOCAL_MODEL_DIR, "config.json"))
                         and os.path.exists(os.path.join(LOCAL_MODEL_DIR, "model.safetensors")))
            src = LOCAL_MODEL_DIR if use_local else MODEL_ID
            kw = {"local_files_only": True} if use_local else ({"revision": MODEL_REVISION} if MODEL_REVISION else {})
            tok = AutoTokenizer.from_pretrained(src, **kw)
            model = AutoModelForSequenceClassification.from_pretrained(src, **kw)
            model.eval()
            quantized = False
            if _want_quantize():
                try:
                    qmod = getattr(getattr(torch, "ao", None), "quantization", None)
                    qfn = getattr(qmod, "quantize_dynamic", None) or getattr(
                        getattr(torch, "quantization", None), "quantize_dynamic", None
                    )
                    if qfn is not None:
                        model = qfn(model, {torch.nn.Linear}, dtype=torch.qint8)
                        quantized = True
                except Exception:
                    log.warning("kuantisasi dinamis INT8 gagal — lanjut pakai FP32")
            _state.update({
                "model": model,
                "tokenizer": tok,
                "engine": "pytorch",
                "quantized": quantized,
            })
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

    if _state.get("engine") == "onnxruntime":
        try:
            id2lbl = _id_to_label(model)
            batch = [texts[i] for i in todo]
            inputs = tok(batch, return_tensors="np", truncation=True,
                         max_length=128, padding=True)
            valid_names = {inp.name for inp in model.get_inputs()}
            ort_inputs = {k: v for k, v in inputs.items() if k in valid_names}
            logits = model.run(None, ort_inputs)[0]
            probs_all = _softmax_rows(logits)
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
            return out
        except Exception:
            log.exception("inferensi ONNX sentimen gagal — fallback lexicon untuk batch ini")
            return out

    try:
        import torch
    except ImportError:
        # Lingkungan memang tanpa torch (mis. image slim): jangan ulangi tiap batch.
        _state["failed"] = True
        return out
    try:
        id2lbl = _id_to_label(model)
        batch = [texts[i] for i in todo]
        inputs = tok(batch, return_tensors="pt", truncation=True,
                     max_length=128, padding=True)
        ctx_mgr = getattr(torch, "inference_mode", torch.no_grad)
        with ctx_mgr():
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
        # Kegagalan inferensi (OOM sesaat, input aneh) tidak boleh mematikan
        # model untuk sisa umur proses — batch ini jatuh ke lexicon, batch
        # berikutnya mencoba lagi.
        log.exception("inferensi sentimen gagal — fallback lexicon untuk batch ini")
        return out
    return out


def classify(text: str) -> dict | None:
    """Klasifikasi satu judul. Return {'label','score','probs'} atau None."""
    return classify_batch([text])[0]


def is_available() -> bool:
    return classify("probe") is not None
