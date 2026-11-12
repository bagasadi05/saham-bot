"""Kegagalan inferensi ML tidak boleh mematikan model untuk sisa proses."""
from __future__ import annotations

import contextlib
import sys

from core import news_ml


class _Model:
    def __call__(self, **kwargs):
        raise RuntimeError("OOM sesaat")


class _Tok:
    def __call__(self, texts, **kwargs):
        return {}


class _Torch:
    @staticmethod
    def no_grad():
        return contextlib.nullcontext()


def test_inference_error_keeps_model_available(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _Torch)
    monkeypatch.setattr(news_ml, "_state",
                        {"model": "M", "tokenizer": "T", "failed": False})
    monkeypatch.setattr(news_ml, "get_model", lambda: (_Model(), _Tok()))
    monkeypatch.setattr(news_ml, "_id_to_label", lambda model: {0: "positive"})
    monkeypatch.setattr(news_ml, "_cache", {})

    out = news_ml.classify_batch(["judul uji inferensi yang unik"])

    assert out == [None]                      # batch ini jatuh ke lexicon
    assert news_ml._state["failed"] is False  # model tidak dimatikan
    assert news_ml._state["model"] == "M"


def test_missing_torch_marks_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)   # import torch -> ImportError
    monkeypatch.setattr(news_ml, "_state",
                        {"model": "M", "tokenizer": "T", "failed": False})
    monkeypatch.setattr(news_ml, "get_model", lambda: (_Model(), _Tok()))
    monkeypatch.setattr(news_ml, "_id_to_label", lambda model: {0: "positive"})
    monkeypatch.setattr(news_ml, "_cache", {})

    out = news_ml.classify_batch(["judul lain yang juga unik"])

    assert out == [None]
    assert news_ml._state["failed"] is True   # lingkungan memang tanpa torch
