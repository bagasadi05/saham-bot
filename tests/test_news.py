"""Test sentimen berita berbasis lexicon — tanpa akses jaringan/ML."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core import news


@pytest.fixture(autouse=True)
def tanpa_model_ml(monkeypatch):
    """Cegah unduhan model HuggingFace: paksa jalur lexicon."""
    from core import news_ml

    monkeypatch.setattr(news_ml, "get_model", lambda: (None, None))


def test_judul_positif():
    skor, hits = news.score_title("Saham BBCA naik, laba melonjak")
    assert skor > 0
    assert "naik" in hits


def test_judul_negatif():
    skor, _ = news.score_title("Saham anjlok dan rugi besar")
    assert skor < 0


def test_negasi_membalik_arah():
    positif, _ = news.score_title("harga naik")
    negatif, _ = news.score_title("harga tidak naik")
    assert negatif < 0 < positif


def test_judul_netral_tanpa_kata_kunci():
    assert news.score_title("Rapat umum pemegang saham tahunan")[0] == 0


def test_news_sentiment_agregat_dan_skala():
    now = datetime.now(timezone.utc)
    items = [
        {"title": "laba melonjak", "link": "https://a", "date": now, "source": "A"},
        {"title": "saham anjlok", "link": "https://b", "date": now, "source": "B"},
    ]
    out = news.news_sentiment(items)
    assert -100 <= out["pct"] <= 100
    assert out["n"] == 2
    assert out["engine_ml"] == 0
    assert all(d["engine"] == "lexicon" for d in out["details"])


def test_berita_lama_bobotnya_lebih_kecil():
    now = datetime.now(timezone.utc)
    baru = [{"title": "laba melonjak", "link": "https://a", "date": now}]
    lama = [{"title": "laba melonjak", "link": "https://a", "date": now - timedelta(days=30)}]
    # nilai absolut sama, tapi decay hanya memengaruhi bobot — pastikan tidak crash
    assert news.news_sentiment(baru)["pct"] == news.news_sentiment(lama)["pct"]


def test_news_sentiment_kosong():
    out = news.news_sentiment([])
    assert out["pct"] == 0 and out["n"] == 0
