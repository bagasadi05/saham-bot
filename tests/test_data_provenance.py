"""Provenance data: sidecar cache, penanda penyesuaian, dan penolakan cache salah."""
from __future__ import annotations

import json

from core import data
from tests.conftest import make_prices


def test_save_cache_writes_sidecar_and_marks_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "_CACHE_DIR", str(tmp_path))
    df = make_prices([100, 101, 102])

    data._save_cache("BBCA.JK", df)

    meta = json.loads((tmp_path / "BBCA.JK.json").read_text(encoding="utf-8"))
    assert meta["adjusted"] is True
    assert meta["provider"] == "yfinance"
    assert meta["rows"] == 3

    loaded = data._load_cache("BBCA.JK")
    assert loaded is not None
    assert loaded.attrs["provenance"] == "cache"
    assert loaded.attrs["adjusted"] is True
    assert loaded.attrs["stale"] is True
    assert "quality" in loaded.attrs


def test_cache_with_mismatched_adjustment_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "_CACHE_DIR", str(tmp_path))
    make_prices([100, 101, 102]).to_csv(tmp_path / "BBCA.JK.csv")
    (tmp_path / "BBCA.JK.json").write_text(
        json.dumps({"adjusted": False}), encoding="utf-8")

    assert data._load_cache("BBCA.JK", adjusted=True) is None


def test_legacy_cache_is_accepted_but_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "_CACHE_DIR", str(tmp_path))
    make_prices([100, 101, 102]).to_csv(tmp_path / "BBCA.JK.csv")

    loaded = data._load_cache("BBCA.JK", adjusted=True)

    assert loaded is not None
    assert loaded.attrs["provenance"] == "cache-legacy"
    assert "tanpa penanda" in loaded.attrs["stale_note"]


def test_live_fetch_sets_provider_adjusted_and_quality(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(data, "_MAX_RETRIES", 1)
    frames = make_prices([100, 101, 102])
    monkeypatch.setattr(data.yf, "download", lambda *a, **k: frames.copy())

    out = data.fetch_history("BBCA")

    assert out is not None
    assert out.attrs["provenance"] == "live"
    assert out.attrs["provider"] == "yfinance"
    assert out.attrs["adjusted"] is True
    assert out.attrs["repaired_count"] == 0
    assert out.attrs["last_bar_date"] == out.attrs["as_of"]
    assert out.attrs["fetched_at"]
    assert isinstance(out.attrs["quality"], dict)


def test_live_fetch_tracks_and_strips_repaired_column(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(data, "_MAX_RETRIES", 1)
    captured: dict = {}
    frames = make_prices([100, 101, 102, 103])
    frames["Repaired?"] = [False, True, False, True]

    def fake_download(*_a, **kwargs):
        captured.update(kwargs)
        return frames.copy()

    monkeypatch.setattr(data.yf, "download", fake_download)
    out = data.fetch_history("BBCA")

    assert captured["auto_adjust"] is True
    assert captured["repair"] is True
    assert out is not None
    assert out.attrs["repaired_count"] == 2
    assert "repaired?" not in [str(c).lower() for c in out.columns]
    csv_text = (tmp_path / "BBCA.JK_1y.csv").read_text(encoding="utf-8")
    assert "repaired" not in csv_text.lower()
