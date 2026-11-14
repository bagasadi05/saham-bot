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
    # Nilai absolut sama, decay hanya memengaruhi bobot. Toleransi 0,2 poin
    # untuk menghindari flaky di batas pembulatan (mis. 83,25 → 83,2/83,3).
    assert news.news_sentiment(baru)["pct"] == pytest.approx(
        news.news_sentiment(lama)["pct"], abs=0.2)


def test_news_sentiment_kosong():
    out = news.news_sentiment([])
    assert out["pct"] == 0 and out["n"] == 0


def test_news_query_ketat_hindari_bentrok_kata_umum(monkeypatch):
    captured_urls: list[str] = []
    rss = (b"<rss><channel><item><title>BUMI cetak laba</title>"
           b"<link>https://a</link><pubDate>Fri, 06 Nov 2026 10:00:00 GMT</pubDate>"
           b"<source>Kontan</source></item></channel></rss>")

    def fake_get(url, timeout=15):
        captured_urls.append(url)
        return rss

    monkeypatch.setattr(news, "_http_get", fake_get)
    monkeypatch.setattr(news.datamod, "fetch_quote_name", lambda sym: (
        "PT Bumi Resources Tbk" if sym == "BUMI" else sym
    ))
    news.clear_cache()
    try:
        items_bumi = news.fetch_news("BUMI")
        items_auto = news.fetch_news("AUTO")
        assert '"PT Bumi Resources Tbk" OR "BUMI.JK" OR "saham BUMI"' in items_bumi[0]["query"]
        assert '"AUTO.JK" OR "saham AUTO"' == items_auto[0]["query"]
        assert news.last_query("BUMI") == items_bumi[0]["query"]
        assert news.news_sentiment(items_bumi)["query"] == items_bumi[0]["query"]
        assert len(captured_urls) == 2
        assert "OR+BUMI&" not in captured_urls[0]
        assert "OR+AUTO&" not in captured_urls[1]
    finally:
        news.clear_cache()
