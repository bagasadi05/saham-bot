"""Pengambil berita emiten dari Google News RSS + analisis sentimen lexicon
Bahasa Indonesia untuk berita pasar modal. Tanpa API key, tanpa biaya."""
from __future__ import annotations

import os
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from core import data as datamod

# Cache berita: judul Google News jarang berubah dalam 15 menit, sementara
# fetch_news dipanggil untuk setiap emiten di setiap pemindaian /saran.
_NEWS_TTL = int(os.getenv("SAHAMBOT_NEWS_TTL", "900") or 0)
_news_cache: dict[tuple[str, int], tuple[float, list[dict]]] = {}
_news_lock = threading.Lock()


def clear_cache():
    """Kosongkan cache berita (dipakai test / manual)."""
    with _news_lock:
        _news_cache.clear()


GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# ------------------------------------------------------------------ lexicon
# Bobot positif / negatif khas berita saham Indonesia. Kata di-match sebagai
# frasa (bukan substring mentah) supaya "lapa" tidak tertukar dengan "lapangan".

POS = {  # kata: bobot
    "naik": 1.0, "menguat": 1.0, "melejit": 2.0, "melonjak": 2.0,
    "lonjakan": 2.0, "kencang": 1.0,
    "untung": 2.0, "laba": 1.5, "profit": 1.5, "surplus": 1.5,
    "tumbuh": 1.5, "percepatan": 1.0, "memperbaiki": 1.0, "menembus": 1.0,
    "breakout": 2.0, "dividen": 1.0, "akumulasi": 1.5, "buy": 1.0,
    "ekspansi": 1.5, "akuisisi": 1.0, "rekor": 1.0,
}
NEG = {
    "turun": 1.0, "menurun": 1.0, "melemah": 1.5, "anjlok": 2.5,
    "merosot": 2.0, "koreksi": 0.5, "ambles": 2.0, "rugi": 2.0,
    "defisit": 1.5, "sanksi": 2.0, "denda": 1.5, "gagal": 1.5,
    "redam": 1.0, "tekanan": 1.0, "divestasi": 1.5, "dilusi": 1.5,
    "hutang": 0.5, "suspend": 2.0, "probe": 1.5, "bocor": 1.0,
}


def _clean_wordbag(text: str) -> str:
    """Setiap kata ke lowercase, buang non-huruf kecuali spasi & dash."""
    return re.sub(r"[^a-z\-\s]", " ", text.lower())


def score_title(title: str) -> tuple[float, list[str]]:
    """Skor sentimen judul berita satu kalimat. Return (skor, kata_ketemuan)."""
    text = _clean_wordbag(title)
    words = re.findall(r"[a-z\-]+", text)
    score = 0.0
    hits: list[str] = []
    # negasi paling sederhana: kata "tidak/bukan/belum" sebelum kata pos -> flips.
    negators = {"tidak", "bukan", "belum"}
    for i, w in enumerate(words):
        negated = i > 0 and words[i - 1] in negators
        if w in POS and POS[w]:
            delta = POS[w] * (-1 if negated else 1)
            score += delta
            hits.append(("tidak-" if negated else "") + w)
        elif w in NEG and NEG[w]:
            delta = NEG[w] * (1 if negated else -1)
            score += delta
            hits.append(("tidak-" if negated else "") + w)
    return score, hits


# ------------------------------------------------------------------ fetch

def _http_get(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_news(ticker: str, limit: int = 12) -> list[dict]:
    """Berita Google News untuk emiten (cache 15 menit, max `limit`)."""
    symbol = datamod.normalize_ticker(ticker).replace(".JK", "")
    key = (symbol, int(limit))
    if _NEWS_TTL > 0:
        with _news_lock:
            hit = _news_cache.get(key)
            if hit and time.time() - hit[0] < _NEWS_TTL:
                # salinan: news_sentiment menulis 'sent'/'engine' ke item
                return [dict(it) for it in hit[1]]
    name = datamod.fetch_quote_name(symbol)
    query = f"{name} OR {symbol}"
    qs = urllib.parse.urlencode({
        "q": query,
        "hl": "id",
        "gl": "ID",
        "ceid": "ID:id",
    })
    xml_bytes = _http_get(f"{GOOGLE_NEWS_RSS}?{qs}")
    root = ET.fromstring(xml_bytes)
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        source = None
        src_el = item.find("source")
        if src_el is not None and src_el.text:
            source = src_el.text.strip()
        if not title:
            continue
        dt = None
        for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
            try:
                dt = datetime.strptime(pub, fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        items.append({"title": title, "link": link, "date": dt, "source": source})
        if len(items) >= limit:
            break
    if _NEWS_TTL > 0:
        with _news_lock:
            _news_cache[key] = (time.time(), items)
    return [dict(it) for it in items]


def news_sentiment(items: list[dict]) -> dict:
    """Agregat sentimen berita: skor -100..100 + rincian per judul.
    Berita yang lebih baru bobot lebih besar (decay 7 hari).
    Model ML lokal dijalankan sekali untuk semua judul (batch); kalau tidak
    tersedia, jatuh ke mesin lexicon (setiap item diberi catatan mesinnya)."""
    now = datetime.now(timezone.utc)

    # klasifikasi ML sekali jalan untuk judul yang belum punya skor
    pending = [it for it in items
               if not ("sent" in it and it.get("engine") is not None)]
    ml_results: list[dict | None] = [None] * len(pending)
    if pending:
        try:
            from core import news_ml
            ml_results = news_ml.classify_batch([it["title"] for it in pending])
        except Exception:
            ml_results = [None] * len(pending)
    ml_iter = iter(ml_results)

    total_v, total_w = 0.0, 0.0
    details = []
    for it in items:
        if "sent" in it and it.get("engine") is not None:
            s, hits, engine = it["sent"], it.get("hits", []), it["engine"]
        else:
            res = next(ml_iter, None)
            if res is not None:
                # skala ±1 (ML) dipetakan ke skala lexicon-ish ±2
                s = (res["probs"]["positive"] - res["probs"]["negative"]) * 2.0
                hits = [f"ML {res['label']}:{res['score']:.0%}"]
                engine = "ml"
            else:
                s, hits = score_title(it["title"])
                engine = "lexicon"
        it.update({"sent": s, "hits": hits, "engine": engine})

        age_days = 7.0
        if it["date"]:
            d = it["date"]
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            age_days = max((now - d).total_seconds() / 86400, 0)
        weight = 1.0 / (1.0 + age_days)  # berita segar bobot lebih besar
        total_v += s * weight
        total_w += weight
        details.append({**it, "age_days": round(age_days, 1)})
    raw = total_v / total_w if total_w else 0.0  # rata² terbobot

    pct = max(-100.0, min(100.0, raw * 33.3))  # skala praktis: 3 poin = 100%
    if pct >= 25:
        label, emoji = "POSITIF", "🟢"
    elif pct <= -25:
        label, emoji = "NEGATIF", "🔴"
    else:
        label, emoji = "NETRAL", "⚪"
    n_ml = sum(1 for d in details if d.get("engine") == "ml")
    return {
        "pct": round(pct, 1),
        "label": label,
        "emoji": emoji,
        "n": len(items),
        "engine_ml": n_ml,
        "details": details,
    }
