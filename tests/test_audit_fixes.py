"""Unit test untuk perbaikan hasil audit: dedup emoji /cek, wiring daily_snapshots, dan cakupan news_ml ONNX/INT8."""
from __future__ import annotations

import sys
import types as pytypes

from core import analysis, cek, news_ml, store
from handlers import jobs


def test_cek_warning_tidak_gandakan_emoji(uptrend_df):
    res = analysis.analyze("BBCA", uptrend_df)
    res["stale_note"] = "⚠️ <i>Data terakhir 2026-11-10 (lama).</i>"
    out = cek.build(
        res=res, fund=None, fund_raw=None, nsent=None,
        bandar_row=None, agenda=None, agenda_days=None,
        peers=None, value_avg20=5e9,
    )
    assert out["warnings"]
    for w in out["warnings"]:
        assert not w.startswith("⚠️")


def test_jobs_record_daily_snapshots(tmp_store):
    res = {
        "meta": {"trade_date": "2026-11-20"},
        "rows": [
            {"ticker": "BBCA", "price": 9500.0, "volume": 40_000_000.0, "per": 21.5},
            {"ticker": "BBRI", "price": 3080.0, "volume": 144_971_400.0, "pbv": 1.37},
        ],
    }
    n = jobs._record_daily_snapshots(res)
    assert n == 2
    saved = store.load_daily_snapshots(since="2026-11-20")
    assert {r["ticker"] for r in saved} == {"BBCA", "BBRI"}
    assert jobs._record_daily_snapshots({}) == 0


def test_news_ml_onnx_path_dan_want_quantize(monkeypatch, tmp_path):
    # Test _want_quantize env parsing
    monkeypatch.setenv("SAHAMBOT_ML_QUANTIZE", "1")
    assert news_ml._want_quantize() is True
    monkeypatch.setenv("SAHAMBOT_ML_QUANTIZE", "0")
    assert news_ml._want_quantize() is False

    # Test _id_to_label edge cases
    class _BadCfg:
        config = type("C", (), {"id2label": {"bad": "x"}})()
    assert news_ml._id_to_label(_BadCfg()) == news_ml.LABEL_MAP

    # Test ONNX Runtime inference branch cleanly offline
    class _FakeInput:
        def __init__(self, name):
            self.name = name

    class _FakeSession:
        def get_inputs(self):
            return [_FakeInput("input_ids"), _FakeInput("attention_mask")]

        def run(self, _out, ort_inputs):
            n = len(ort_inputs["input_ids"])
            return [[[4.0, 0.0, 0.0]] * n]

    def _fake_tok(batch, **_kw):
        return {"input_ids": [[1, 2]] * len(batch), "attention_mask": [[1, 1]] * len(batch), "unused": [0]}

    monkeypatch.setattr(
        news_ml, "_state",
        {"model": _FakeSession(), "tokenizer": _fake_tok, "failed": False, "engine": "onnxruntime", "quantized": True},
    )
    monkeypatch.setattr(news_ml, "get_model", lambda: (news_ml._state["model"], news_ml._state["tokenizer"]))
    news_ml._cache.clear()
    res = news_ml.classify_batch(["Laba BBRI melonjak tajam"])
    assert len(res) == 1 and res[0]["label"] == "positive"
    assert news_ml.model_info()["engine"] == "onnxruntime"
    assert news_ml.model_info()["quantized"] is True

    # Test _try_load_onnx when file exists with mocked onnxruntime & transformers
    (tmp_path / "model_quantized.onnx").write_bytes(b"fake")
    fake_ort = pytypes.ModuleType("onnxruntime")
    fake_ort.InferenceSession = lambda path, providers=None: _FakeSession()
    fake_tf = pytypes.ModuleType("transformers")
    fake_tf.AutoTokenizer = type("T", (), {"from_pretrained": staticmethod(lambda *a, **k: _fake_tok)})
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)
    monkeypatch.setitem(sys.modules, "transformers", fake_tf)
    loaded = news_ml._try_load_onnx(str(tmp_path))
    assert loaded is not None and loaded[2] is True
