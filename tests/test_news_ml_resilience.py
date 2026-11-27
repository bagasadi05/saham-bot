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


def test_local_only_skips_hub_when_model_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SAHAMBOT_LOCAL_ONLY", "1")
    monkeypatch.setattr(news_ml, "LOCAL_MODEL_DIR", str(tmp_path / "missing_model"))
    monkeypatch.setattr(
        news_ml,
        "_state",
        {"model": None, "tokenizer": None, "failed": False, "engine": None, "quantized": False},
    )
    info = news_ml.model_info()
    assert info["local_only"] is True
    model, tok = news_ml.get_model()
    assert (model, tok) == (None, None)
    assert news_ml._state["failed"] is True


def test_news_sentiment_records_ml_and_lexicon_usage(monkeypatch):
    from datetime import datetime, timezone
    from core import news

    news_ml.clear_usage()
    monkeypatch.setattr(
        news_ml,
        "classify_batch",
        lambda titles: [None, {"label": "positive", "score": 0.92}],
    )
    now = datetime.now(timezone.utc)
    items = [
        {"title": "Laba BBCA tumbuh kuat", "date": now, "source": "Test", "link": "https://x.invalid/1"},
        {"title": "Ekspansi kredit sehat", "date": now, "source": "Test", "link": "https://x.invalid/2"},
    ]
    res = news.news_sentiment(items)
    assert res["engine_coverage"]["ml"] == 1
    assert res["engine_coverage"]["lexicon"] == 1
    usage = news_ml.model_info()["usage"]
    assert usage == {"ml": 1, "lexicon": 1}
