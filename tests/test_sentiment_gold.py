"""Audit sentimen berita terhadap dataset emas offline, negasi, CAMPURAN & cakupan mesin."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core import news, news_ml
from research import evaluate_sentiment

GOLD_PATH = Path(__file__).resolve().parent / "fixtures" / "news_sentiment_gold.json"


@pytest.fixture(autouse=True)
def paksa_lexicon(monkeypatch):
    """Pastikan tes berjalan 100% offline tanpa mengunduh model HuggingFace."""
    monkeypatch.setattr(news_ml, "get_model", lambda: (None, None))


def test_gold_fixture_lengkap_dan_seimbang():
    raw = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    cases = raw["cases"]
    assert len(cases) >= 50
    counts = {"positive": 0, "negative": 0, "neutral": 0}
    ids = set()
    for c in cases:
        assert c["id"] not in ids
        ids.add(c["id"])
        assert c["label"] in counts
        assert c["title"].strip()
        counts[c["label"]] += 1
    assert counts["positive"] >= 15
    assert counts["negative"] >= 15
    assert counts["neutral"] >= 15


def test_evaluasi_lexicon_pada_gold_set_melampaui_ambang():
    cases = evaluate_sentiment.load_gold(GOLD_PATH)
    res = evaluate_sentiment.evaluate_cases(cases, engine="lexicon")
    assert res["n"] == len(cases)
    assert res["accuracy"] >= 0.90, f"kesalahan: {res['errors']}"
    assert res["macro_f1"] >= 0.90, f"kesalahan: {res['errors']}"
    assert res["by_category"]["negation_neg"]["accuracy"] >= 0.85
    assert res["by_category"]["negation_pos"]["accuracy"] >= 0.85
    assert res["by_category"]["phrase_recovery"]["accuracy"] >= 0.85
    for lbl in ("positive", "negative", "neutral"):
        assert res["per_class"][lbl]["f1"] >= 0.85


def test_negasi_frasa_dan_batas_klausa():
    # Negator aksi + kata positif -> negatif (tidak terbalik jadi positif)
    assert news.score_title("Saham BBCA gagal breakout dan batal ekspansi")[0] < 0
    assert news.score_title("Emiten tidak kunjung pulih dari tekanan jual")[0] < 0
    assert news.score_title("Batal bagi dividen, saham belum mampu menguat")[0] < 0

    # Frasa pemulihan & negasi negatif -> positif
    assert news.score_title("Rugi bersih menyusut tajam pada kuartal III")[0] > 0
    assert news.score_title("Emiten berbalik untung setelah tekanan jual mereda")[0] > 0
    assert news.score_title("Perusahaan bebas sanksi dan tanpa kerugian")[0] > 0

    # Batas klausa: negasi di klausa 1 tidak membocori klausa 2
    s_clause, _ = news.score_title("Isu lama bukan masalah, laba bersih naik")
    assert s_clause > 0

    # Judul faktual dengan kata 'laba'/'dividen' tidak memicu positif palsu
    assert news.score_title("Jadwal cum date dividen BBCA 2026")[0] == 0
    assert news.score_title("Jadwal pengumuman laporan laba kuartal III")[0] == 0


def test_status_campuran_vs_netral_dan_cakupan_mesin():
    now = datetime.now(timezone.utc)
    # 1 positif kuat + 1 negatif kuat -> pct dekat 0 tetapi CAMPURAN (bukan NETRAL)
    mixed = [
        {"title": "Saham BBCA melejit di sesi pertama", "link": "https://a", "date": now, "source": "A"},
        {"title": "Saham BBCA merosot pada penutupan", "link": "https://b", "date": now, "source": "B"},
    ]
    out_mix = news.news_sentiment(mixed)
    assert out_mix["label"] == "CAMPURAN"
    assert out_mix["emoji"] == "🟡"
    assert out_mix["n_pos"] == 1 and out_mix["n_neg"] == 1 and out_mix["n_neu"] == 0
    cov = out_mix["engine_coverage"]
    assert cov["total"] == 2
    assert cov["ml"] == 0 and cov["lexicon"] == 2
    assert cov["lexicon_pct"] == 100.0
    assert cov["scored"] == 2 and cov["scored_pct"] == 100.0
    assert "lexicon" in cov["summary"]

    # Semua judul faktual netral -> tetap NETRAL (⚪), bukan CAMPURAN
    flat = [
        {"title": "Rapat umum pemegang saham tahunan digelar", "link": "", "date": now},
        {"title": "Jadwal libur bursa saham Indonesia 2026", "link": "", "date": now},
    ]
    out_flat = news.news_sentiment(flat)
    assert out_flat["label"] == "NETRAL"
    assert out_flat["emoji"] == "⚪"
    assert out_flat["n_pos"] == 0 and out_flat["n_neg"] == 0 and out_flat["n_neu"] == 2
    assert out_flat["engine_coverage"]["scored"] == 0

    # Campuran mesin ML (pre-scored) + lexicon -> cakupan jujur
    hybrid = [
        {"title": "Laba naik", "link": "", "date": now, "sent": 1.6, "engine": "ml", "hits": ["ML positive:90%"]},
        {"title": "Saham menguat", "link": "", "date": now},
    ]
    out_hyb = news.news_sentiment(hybrid)
    assert out_hyb["label"] == "POSITIF"
    assert out_hyb["engine_ml"] == 1 and out_hyb["engine_lexicon"] == 1
    assert out_hyb["engine_coverage"]["ml_pct"] == 50.0
    assert "ML 1/2 + lexicon 1/2" in out_hyb["engine_coverage"]["summary"]


def test_fetch_market_news_offline_dan_cache(monkeypatch):
    rss = (
        b"<rss><channel>"
        b"<item><title>IHSG menguat ditopang saham perbankan - Kontan</title>"
        b"<link>https://a</link><pubDate>Fri, 20 Nov 2026 09:00:00 GMT</pubDate>"
        b"<source>Kontan</source></item>"
        b"<item><title>IHSG menguat ditopang saham perbankan - Bisnis</title>"
        b"<link>https://b</link><pubDate>Fri, 20 Nov 2026 09:10:00 GMT</pubDate>"
        b"<source>Bisnis</source></item>"
        b"<item><title>Asing cetak net buy di Bursa Efek Indonesia</title>"
        b"<link>https://c</link><pubDate>Fri, 20 Nov 2026 09:20:00 GMT</pubDate>"
        b"<source>CNBC Indonesia</source></item>"
        b"</channel></rss>"
    )
    calls: list[str] = []

    def fake_get(url, timeout=15):
        calls.append(url)
        return rss

    monkeypatch.setattr(news, "_http_get", fake_get)
    news.clear_cache()
    try:
        items1 = news.fetch_market_news(limit=5)
        items2 = news.fetch_market_news(limit=5)
        assert len(calls) == 1  # panggilan kedua dari cache
        assert len(items1) == 2  # duplikat judul disaring
        assert items1[0]["title"] == "IHSG menguat ditopang saham perbankan"
        assert items1[0]["query"] == news.MARKET_NEWS_QUERY
        assert items2 == items1
    finally:
        news.clear_cache()


def test_cli_evaluate_sentiment_json(capsys):
    rc = evaluate_sentiment.main(["--gold", str(GOLD_PATH), "--engine", "lexicon", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["accuracy"] >= 0.90
    assert data["macro_f1"] >= 0.90
    txt = evaluate_sentiment.format_report(data)
    assert "Evaluasi Sentimen" in txt and "Macro F1" in txt


def test_kasus_adversarial_rugi_utang_desimal_strip_dan_cek_campuran():
    from core import cek

    # 1) Rugi + klausa/metrik lain turun -> tetap NEGATIF (tidak disangka rugi menyusut)
    assert news.score_title("Rugi bersih BBCA dan pendapatan turun tajam")[0] < 0
    assert news.score_title("Catat rugi bersih usai pendapatan turun 20%")[0] < 0

    # 2) Rugi/kerugian/utang membengkak/melonjak -> NEGATIF (tidak saling meniadakan)
    assert news.score_title("Rugi emiten melonjak 80% di kuartal III")[0] < 0
    assert news.score_title("Kerugian perusahaan melejit 50% tahun ini")[0] < 0
    assert news.score_title("Utang emiten naik 40% sepanjang tahun lalu")[0] < 0
    assert news.score_title("Utang emiten menyusut usai pelunasan obligasi")[0] > 0

    # 3) Koma desimal (Rp1,2 triliun) tidak memecah klausa pemulihan rugi
    assert news.score_title("Rugi bersih Rp1,2 triliun menyusut menjadi Rp500 miliar")[0] > 0

    # 4) Kata majemuk bertanda strip tetap cocok ke frasa
    assert news.score_title("Saham BBCA cetak all-time high baru")[0] > 0
    assert news.score_title("Asing catat net-buy besar di saham bank")[0] > 0

    # 5) Ambang per-judul konsisten antara evaluate_sentiment dan news_sentiment
    now = datetime.now(timezone.utc)
    lbl, sc, _ = evaluate_sentiment.predict_lexicon(
        "Target laba BBCA tahun 2026 dibahas dalam RUPS"
    )
    assert lbl == "neutral" and abs(sc) < news.ITEM_SENT_THRESHOLD
    agg = news.news_sentiment([
        {"title": "Target laba BBCA tahun 2026 dibahas dalam RUPS", "link": "", "date": now},
        {"title": "Saham BBCA terkoreksi tipis", "link": "", "date": now},
    ])
    assert agg["n_pos"] == 0
    assert agg["label"] != "CAMPURAN"

    # 6) core/cek.py meneruskan status CAMPURAN secara jujur
    ev = cek.build(
        res=None,
        fund=None,
        fund_raw=None,
        nsent={"pct": 0.0, "n": 4, "label": "CAMPURAN", "top": "Pro kontra pasar"},
        bandar_row=None,
        agenda=None,
        agenda_days=None,
        peers=None,
        value_avg20=None,
    )
    berita_item = next(it for it in ev["items"] if it["key"] == "berita")
    assert berita_item["headline"] == "campuran"
