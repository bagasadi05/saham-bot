"""Pengambil berita emiten & pasar dari Google News RSS + analisis sentimen
lexicon Bahasa Indonesia untuk berita pasar modal. Tanpa API key, tanpa biaya."""
from __future__ import annotations

import email.utils
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
_last_queries: dict[str, str] = {}
_news_lock = threading.Lock()


def clear_cache():
    """Kosongkan cache berita (dipakai test / manual)."""
    with _news_lock:
        _news_cache.clear()
        _last_queries.clear()


GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
MARKET_NEWS_QUERY = (
    '"IHSG" OR "Bursa Efek Indonesia" OR "BEI" OR "pasar saham Indonesia"'
)
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# ------------------------------------------------------------------ lexicon
# Bobot positif / negatif khas berita saham Indonesia. Frasa majemuk dicocokkan
# lebih dulu per klausa sebelum kata tunggal agar "laba turun", "rugi menyusut",
# "gagal menembus", atau "batal bagi dividen" tidak keliru arah.

POS_PHRASES: dict[tuple[str, ...], float] = {
    ("rugi", "menyusut"): 1.6,
    ("rugi", "berkurang"): 1.6,
    ("rugi", "menurun"): 1.6,
    ("rugi", "turun"): 1.6,
    ("kerugian", "menyusut"): 1.6,
    ("kerugian", "berkurang"): 1.6,
    ("kerugian", "menurun"): 1.6,
    ("tekanan", "jual", "mereda"): 1.4,
    ("tekanan", "mereda"): 1.2,
    ("berbalik", "untung"): 2.0,
    ("balik", "untung"): 2.0,
    ("berbalik", "laba"): 2.0,
    ("balik", "laba"): 2.0,
    ("cetak", "laba"): 1.5,
    ("raih", "laba"): 1.5,
    ("kantongi", "laba"): 1.5,
    ("pendapatan", "naik"): 1.5,
    ("pendapatan", "tumbuh"): 1.5,
    ("pendapatan", "melonjak"): 2.0,
    ("pendapatan", "meningkat"): 1.5,
    ("kinerja", "positif"): 1.5,
    ("kinerja", "solid"): 1.5,
    ("kinerja", "cemerlang"): 1.8,
    ("kinerja", "apik"): 1.5,
    ("cetak", "rekor"): 1.8,
    ("rekor", "tertinggi"): 1.8,
    ("all", "time", "high"): 1.8,
    ("net", "buy"): 1.6,
    ("beli", "bersih"): 1.6,
    ("asing", "beli"): 1.4,
    ("asing", "borong"): 1.6,
    ("borong", "saham"): 1.5,
    ("dana", "asing", "masuk"): 1.6,
    ("tebar", "dividen"): 1.5,
    ("bagikan", "dividen"): 1.4,
    ("bagi", "dividen"): 1.4,
    ("dividen", "jumbo"): 1.8,
    ("dividen", "naik"): 1.6,
    ("dividen", "meningkat"): 1.6,
    ("pangkas", "suku", "bunga"): 1.2,
    ("suku", "bunga", "turun"): 1.2,
    ("suku", "bunga", "dipangkas"): 1.2,
    ("penurunan", "suku", "bunga"): 1.2,
    ("target", "harga", "naik"): 1.5,
    ("naikkan", "target"): 1.5,
    ("rekomendasi", "beli"): 1.4,
    ("rekomendasi", "buy"): 1.4,
    ("bebas", "sanksi"): 1.2,
    ("lepas", "suspensi"): 1.4,
    ("cabut", "suspensi"): 1.4,
    ("suspensi", "dibuka"): 1.4,
}

NEG_PHRASES: dict[tuple[str, ...], float] = {
    ("gagal", "bayar"): 2.5,
    ("ekuitas", "negatif"): 2.5,
    ("pemantauan", "khusus"): 2.0,
    ("notasi", "khusus"): 1.8,
    ("dihentikan", "sementara"): 2.0,
    ("suspensi", "perdagangan"): 2.0,
    ("peringatan", "tertulis"): 1.6,
    ("unusual", "market", "activity"): 1.4,
    ("rugi", "bersih"): 2.2,
    ("berbalik", "rugi"): 2.2,
    ("balik", "rugi"): 2.2,
    ("cetak", "rugi"): 2.2,
    ("catat", "rugi"): 2.2,
    ("derita", "rugi"): 2.2,
    ("laba", "turun"): 1.8,
    ("laba", "menurun"): 1.8,
    ("laba", "merosot"): 2.2,
    ("laba", "anjlok"): 2.5,
    ("laba", "tergerus"): 2.0,
    ("laba", "menyusut"): 1.8,
    ("laba", "susut"): 1.8,
    ("laba", "terpangkas"): 1.8,
    ("laba", "bersih", "turun"): 2.0,
    ("laba", "bersih", "merosot"): 2.2,
    ("laba", "bersih", "anjlok"): 2.5,
    ("laba", "bersih", "tergerus"): 2.0,
    ("pendapatan", "turun"): 1.6,
    ("pendapatan", "merosot"): 2.0,
    ("pendapatan", "anjlok"): 2.2,
    ("kinerja", "melambat"): 1.5,
    ("kinerja", "lesu"): 1.5,
    ("kinerja", "tertekan"): 1.5,
    ("net", "sell"): 1.6,
    ("jual", "bersih"): 1.6,
    ("asing", "jual"): 1.4,
    ("asing", "kabur"): 1.8,
    ("dana", "asing", "keluar"): 1.6,
    ("tekanan", "jual"): 1.5,
    ("aksi", "jual"): 1.4,
    ("profit", "taking"): 1.0,
    ("batal", "dividen"): 1.6,
    ("batal", "bagi", "dividen"): 1.6,
    ("batal", "bagikan", "dividen"): 1.6,
    ("tidak", "bagi", "dividen"): 1.6,
    ("tidak", "bagikan", "dividen"): 1.6,
    ("tunda", "dividen"): 1.4,
    ("absen", "dividen"): 1.5,
    ("pangkas", "dividen"): 1.5,
    ("dividen", "dipangkas"): 1.5,
    ("dividen", "turun"): 1.4,
    ("kenaikan", "suku", "bunga"): 1.2,
    ("suku", "bunga", "naik"): 1.2,
    ("target", "harga", "turun"): 1.5,
    ("target", "harga", "dipangkas"): 1.5,
    ("pangkas", "target"): 1.5,
    ("rekomendasi", "jual"): 1.4,
    ("rekomendasi", "sell"): 1.4,
    ("hindari", "saham"): 1.5,
    ("bukan", "saatnya", "beli"): 1.5,
    ("belum", "layak", "beli"): 1.5,
    ("tidak", "layak", "beli"): 1.5,
    ("jangan", "beli"): 1.5,
    ("gagal", "menembus"): 1.5,
    ("gagal", "rebound"): 1.6,
    ("gagal", "naik"): 1.5,
    ("gagal", "bertahan"): 1.4,
    ("gagal", "breakout"): 1.6,
    ("batal", "akuisisi"): 1.5,
    ("batal", "buyback"): 1.5,
    ("batal", "ekspansi"): 1.5,
    ("tunda", "ekspansi"): 1.3,
    ("beban", "utang"): 1.2,
    ("utang", "menumpuk"): 1.8,
    ("utang", "membengkak"): 1.8,
    ("rekor", "terendah"): 2.0,
}

NEUTRAL_PHRASES: set[tuple[str, ...]] = {
    ("laporan", "laba", "rugi"),
    ("laba", "rugi"),
    ("untung", "rugi"),
    ("laporan", "laba"),
    ("pengumuman", "laba"),
}
_LOSS_NOUNS = {"rugi", "kerugian", "defisit"}
_DEBT_NOUNS = {"utang", "hutang"}
_SHRINK_VERBS = {
    "menyusut", "susut", "berkurang", "menurun", "turun", "terpangkas", "menipis",
}
_GROW_VERBS = {
    "naik", "meningkat", "melonjak", "melejit", "melesat", "meroket",
    "bertambah", "membengkak", "melebar", "menumpuk", "tumbuh",
}
_CLAUSE_BREAKERS = {
    "dan", "serta", "atau", "usai", "setelah", "akibat", "karena", "sebab",
    "sementara", "sedangkan", "saat", "ketika", "namun", "tapi", "tetapi",
    "padahal", "meski", "meskipun", "walau", "walaupun", "membuat",
    "picu", "memicu", "sehingga",
}
_OTHER_METRICS = {
    "pendapatan", "laba", "untung", "keuntungan", "saham", "harga",
    "margin", "volume", "ihsg", "penjualan", "omzet", "ekuitas",
    "aset", "dividen", "produksi", "kinerja", "nilai",
}
ITEM_SENT_THRESHOLD = 0.25

POS = {  # kata tunggal: bobot
    "naik": 1.0, "kenaikan": 1.0, "menguat": 1.0, "penguatan": 1.0,
    "melejit": 2.0, "melonjak": 2.0, "melesat": 2.0, "meroket": 2.0,
    "lonjakan": 2.0, "kencang": 1.0, "rebound": 1.5, "bangkit": 1.5,
    "pulih": 1.2, "pemulihan": 1.2, "ara": 2.0, "cuan": 1.5,
    "meningkat": 1.2, "peningkatan": 1.2, "positif": 1.0,
    "untung": 2.0, "keuntungan": 1.5, "laba": 0.2, "surplus": 1.5,
    "tumbuh": 1.5, "pertumbuhan": 1.2, "percepatan": 1.0,
    "memperbaiki": 1.0, "membaik": 1.2, "menembus": 1.0,
    "breakout": 2.0, "akumulasi": 1.5, "buy": 1.0, "buyback": 1.2,
    "inflow": 1.2, "ekspansi": 1.5, "akuisisi": 1.0, "rekor": 1.0,
    "reli": 1.4, "rally": 1.4, "bullish": 1.5, "upgrade": 1.5,
    "outperform": 1.4, "overweight": 1.2, "oversubscribed": 1.5,
    "terbang": 1.6, "gemilang": 1.5, "solid": 1.0, "optimis": 1.0,
    "optimistis": 1.0, "mengilap": 1.4,
}
NEG = {
    "turun": 1.0, "penurunan": 1.0, "menurun": 1.0, "melemah": 1.5,
    "pelemahan": 1.5, "melemahnya": 1.4, "anjlok": 2.5, "anjloknya": 2.5,
    "ambruk": 2.5, "merosot": 2.0, "merosotnya": 2.0, "terkoreksi": 1.0,
    "koreksi": 0.8, "ambles": 2.0, "jeblok": 2.0, "longsor": 2.0,
    "boncos": 2.0, "tergerus": 1.5, "tertekan": 1.2, "susut": 1.2,
    "menyusut": 1.2, "pangkas": 1.0, "terpangkas": 1.2, "merugi": 2.0,
    "rugi": 2.0, "kerugian": 2.0, "defisit": 1.5, "negatif": 1.0,
    "sanksi": 2.0, "denda": 1.5, "gagal": 1.5, "batal": 1.2, "redam": 1.0,
    "tekanan": 1.0, "divestasi": 1.5, "dilusi": 1.5, "suspend": 2.0,
    "suspensi": 2.0, "digembok": 2.0, "pailit": 2.5, "pkpu": 2.0,
    "default": 2.0, "delisting": 2.5, "arb": 2.0, "outflow": 1.2,
    "probe": 1.5, "bocor": 1.0, "terjun": 2.0, "terpuruk": 2.0,
    "bearish": 1.5, "downgrade": 1.5, "underperform": 1.4,
    "underweight": 1.2, "lesu": 1.2, "melambat": 1.2, "perlambatan": 1.2,
    "runtuh": 2.2, "khawatir": 1.0, "kekhawatiran": 1.0, "ancaman": 1.2,
    "gugatan": 1.5, "investigasi": 1.5, "korupsi": 2.0, "fraud": 2.5,
}
_PURE_NEGATORS = {"tidak", "bukan", "belum", "tak", "tanpa", "kurang", "jangan"}
_ACTION_NEGATORS = {"gagal", "batal", "urung", "tunda"}
_NEGATORS = _PURE_NEGATORS | _ACTION_NEGATORS
_NEG_BRIDGES = {
    "kunjung", "pernah", "jadi", "mampu", "berhasil", "terbukti",
    "juga", "sempat", "bisa", "dapat", "lagi", "ada", "banyak",
    "terlalu", "saatnya", "layak",
}
_CLAUSE_SPLIT_RE = re.compile(r"(?:(?<!\d)[,.]|[,.](?!\d))|[;:!?()\[\]]+")
_SOURCE_SUFFIX_RE = re.compile(r"\s+[-|—]\s+[A-Z0-9][A-Za-z0-9.&'/ ]{1,40}$")
_MAX_PHRASE_LEN = max(
    *(len(k) for k in POS_PHRASES),
    *(len(k) for k in NEG_PHRASES),
    *(len(k) for k in NEUTRAL_PHRASES),
)


def strip_source_suffix(title: str, source: str | None = None) -> str:
    """Buang akhiran nama media Google News ('Judul - Bisnis.com') agar tidak mengacaukan sentimen."""
    t = re.sub(r"\s+", " ", (title or "").strip())
    if not t:
        return ""
    if source:
        src = source.strip()
        for sep in (" - ", " | ", " — "):
            suffix = sep + src
            if t.lower().endswith(suffix.lower()) and len(t) > len(suffix) + 4:
                return t[: -len(suffix)].strip()
    m = _SOURCE_SUFFIX_RE.search(t)
    if m and m.start() >= 12:
        return t[: m.start()].strip()
    return t


def _clean_wordbag(text: str) -> str:
    """Setiap kata ke lowercase, buang non-huruf (termasuk strip) menjadi spasi."""
    return re.sub(r"[^a-z\s]", " ", text.lower())


def _is_negated(words: list[str], idx: int, neg_set: set[str]) -> tuple[bool, int]:
    """Cek apakah kata pada `idx` dinegasikan (langsung atau lewat <=2 jembatan)."""
    if idx > 0 and words[idx - 1] in neg_set:
        return True, idx - 1
    if idx > 1 and words[idx - 2] in neg_set and words[idx - 1] in _NEG_BRIDGES:
        return True, idx - 2
    if (
        idx > 2
        and words[idx - 3] in neg_set
        and words[idx - 2] in _NEG_BRIDGES
        and words[idx - 1] in _NEG_BRIDGES
    ):
        return True, idx - 3
    return False, -1


def score_title(title: str, source: str | None = None) -> tuple[float, list[str]]:
    """Skor sentimen judul berita satu kalimat. Return (skor, kata_ketemuan).

    Mencocokkan frasa pasar modal lebih dulu per klausa (dipisah koma/titik dua
    supaya negasi tidak bocor lintas klausa), lalu kata tunggal dengan deteksi
    negasi hingga 2 kata jembatan ('tidak kunjung naik', 'belum mampu menguat',
    'gagal menembus').
    """
    cleaned = strip_source_suffix(title, source)
    if not cleaned:
        return 0.0, []
    score = 0.0
    hits: list[str] = []

    for clause in _CLAUSE_SPLIT_RE.split(cleaned):
        words = re.findall(r"[a-z]+", _clean_wordbag(clause))
        if not words:
            continue
        consumed = [False] * len(words)

        # 0) Frasa akuntansi netral (mis. "laporan laba rugi") -> konsumsi tanpa skor
        n = len(words)
        i = 0
        while i < n:
            if consumed[i]:
                i += 1
                continue
            matched_neu = False
            for ln in range(min(3, n - i), 1, -1):
                if tuple(words[i: i + ln]) in NEUTRAL_PHRASES:
                    for k in range(i, i + ln):
                        consumed[k] = True
                    i += ln
                    matched_neu = True
                    break
            if not matched_neu:
                i += 1

        # 0b) Pola perubahan rugi/utang dalam subjek yang sama (berhenti di kata
        #     penghubung atau metrik lain agar "rugi bersih dan pendapatan turun"
        #     tidak salah dibaca sebagai rugi menyusut)
        stop_words = _CLAUSE_BREAKERS | _OTHER_METRICS
        for i, w in enumerate(words):
            if consumed[i] or (w not in _LOSS_NOUNS and w not in _DEBT_NOUNS):
                continue
            is_loss = w in _LOSS_NOUNS
            j_start = i + 2 if (is_loss and i + 1 < n and words[i + 1] == "bersih") else i + 1
            j_end = min(n, j_start + (6 if is_loss else 4))
            for j in range(j_start, j_end):
                if consumed[j]:
                    continue
                wj = words[j]
                if wj in stop_words:
                    break
                if wj in _SHRINK_VERBS:
                    negated, neg_idx = _is_negated(words, j, _PURE_NEGATORS)
                    if negated and neg_idx >= 0 and not consumed[neg_idx]:
                        consumed[neg_idx] = True
                    delta = (-1.5 if is_loss else -1.0) if negated else (1.6 if is_loss else 1.2)
                    score += delta
                    hits.append(("tidak-" if negated else "") + f"{w}-{wj}")
                    consumed[i] = True
                    if j_start == i + 2:
                        consumed[i + 1] = True
                    consumed[j] = True
                    break
                if wj in _GROW_VERBS:
                    negated, neg_idx = _is_negated(words, j, _PURE_NEGATORS)
                    if negated and neg_idx >= 0 and not consumed[neg_idx]:
                        consumed[neg_idx] = True
                    delta = (1.4 if is_loss else 1.0) if negated else (-2.2 if is_loss else -1.6)
                    score += delta
                    hits.append(("tidak-" if negated else "") + f"{w}-{wj}")
                    consumed[i] = True
                    if j_start == i + 2:
                        consumed[i + 1] = True
                    consumed[j] = True
                    break

        # 1) Frasa majemuk (longest-match first)
        i = 0
        while i < n:
            if consumed[i]:
                i += 1
                continue
            matched = False
            max_len = min(_MAX_PHRASE_LEN, n - i)
            for ln in range(max_len, 1, -1):
                if any(consumed[i: i + ln]):
                    continue
                span = tuple(words[i: i + ln])
                if span in POS_PHRASES:
                    negated, neg_idx = _is_negated(words, i, _NEGATORS)
                    if negated and neg_idx >= 0 and not consumed[neg_idx]:
                        consumed[neg_idx] = True
                    delta = POS_PHRASES[span] * (-1.0 if negated else 1.0)
                    score += delta
                    hits.append(("tidak-" if negated else "") + " ".join(span))
                    for k in range(i, i + ln):
                        consumed[k] = True
                    i += ln
                    matched = True
                    break
                if span in NEG_PHRASES:
                    negated, neg_idx = _is_negated(words, i, _PURE_NEGATORS)
                    if negated and neg_idx >= 0 and not consumed[neg_idx]:
                        consumed[neg_idx] = True
                    delta = NEG_PHRASES[span] * (0.8 if negated else -1.0)
                    score += delta
                    hits.append(("tidak-" if negated else "") + " ".join(span))
                    for k in range(i, i + ln):
                        consumed[k] = True
                    i += ln
                    matched = True
                    break
            if not matched:
                i += 1

        # 2) Kata tunggal yang belum dikonsumsi frasa
        # Jika _ACTION_NEGATORS (gagal/batal/tunda) membalik kata POS setelahnya,
        # tandai negator tersebut agar tidak ikut dihitung ganda sebagai NEG tunggal.
        action_used: set[int] = set()
        for idx, w in enumerate(words):
            if consumed[idx]:
                continue
            if w in POS and POS[w]:
                negated, neg_idx = _is_negated(words, idx, _NEGATORS)
                if negated and neg_idx >= 0 and words[neg_idx] in _ACTION_NEGATORS:
                    action_used.add(neg_idx)
                delta = POS[w] * (-1.0 if negated else 1.0)
                score += delta
                hits.append(("tidak-" if negated else "") + w)

        for idx, w in enumerate(words):
            if consumed[idx] or idx in action_used:
                continue
            if w in NEG and NEG[w]:
                negated, _ = _is_negated(words, idx, _PURE_NEGATORS)
                delta = NEG[w] * (0.8 if negated else -1.0)
                score += delta
                hits.append(("tidak-" if negated else "") + w)

    return round(score, 4), hits


# ------------------------------------------------------------------ fetch

def _http_get(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def build_query(symbol: str, name: str | None = None) -> str:
    """Bangun query Google News yang ketat agar ticker tidak bentrok kata umum."""
    sym = (symbol or "").strip().upper().removesuffix(".JK")
    clean_name = re.sub(r"\s+", " ", (name or "").replace('"', "")).strip()
    parts: list[str] = []
    if clean_name and clean_name.upper() != sym:
        parts.append(f'"{clean_name}"')
    parts.append(f'"{sym}.JK"')
    parts.append(f'"saham {sym}"')
    return " OR ".join(parts)


def last_query(ticker: str) -> str | None:
    """Query Google News terakhir yang dipakai untuk emiten ini (untuk audit/metadata)."""
    try:
        symbol = datamod.normalize_ticker(ticker).replace(".JK", "")
    except ValueError:
        symbol = (ticker or "").strip().upper().removesuffix(".JK")
    with _news_lock:
        return _last_queries.get(symbol)


def _parse_rss_items(xml_bytes: bytes, query: str, limit: int) -> list[dict]:
    """Urai RSS Google News menjadi list dict berita terdeduplikasi."""
    root = ET.fromstring(xml_bytes)
    items: list[dict] = []
    seen_titles: set[str] = set()
    for item in root.iter("item"):
        raw_title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        source = None
        src_el = item.find("source")
        if src_el is not None and src_el.text:
            source = src_el.text.strip()
        title = strip_source_suffix(raw_title, source)
        if not title:
            continue
        t_key = title.lower()
        if t_key in seen_titles:
            continue
        seen_titles.add(t_key)
        dt = None
        if pub:
            try:
                parsed = email.utils.parsedate_to_datetime(pub)
                if parsed is not None:
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    dt = parsed.astimezone(timezone.utc)
            except (TypeError, ValueError):
                dt = None
        items.append({
            "title": title, "link": link, "date": dt,
            "source": source, "query": query,
        })
        if len(items) >= limit:
            break
    return items


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
    query = build_query(symbol, name)
    with _news_lock:
        _last_queries[symbol] = query
    qs = urllib.parse.urlencode({
        "q": query,
        "hl": "id",
        "gl": "ID",
        "ceid": "ID:id",
    })
    xml_bytes = _http_get(f"{GOOGLE_NEWS_RSS}?{qs}")
    items = _parse_rss_items(xml_bytes, query, int(limit))
    if _NEWS_TTL > 0:
        with _news_lock:
            _news_cache[key] = (time.time(), items)
    return [dict(it) for it in items]


def fetch_market_news(limit: int = 10, *, query: str | None = None) -> list[dict]:
    """Berita pasar saham Indonesia (IHSG/BEI) dari Google News RSS (cache 15 menit)."""
    q = (query or MARKET_NEWS_QUERY).strip() or MARKET_NEWS_QUERY
    lim = max(1, int(limit))
    key = (f"__MARKET__:{q}", lim)
    if _NEWS_TTL > 0:
        with _news_lock:
            hit = _news_cache.get(key)
            if hit and time.time() - hit[0] < _NEWS_TTL:
                return [dict(it) for it in hit[1]]
    qs = urllib.parse.urlencode({
        "q": q,
        "hl": "id",
        "gl": "ID",
        "ceid": "ID:id",
    })
    xml_bytes = _http_get(f"{GOOGLE_NEWS_RSS}?{qs}")
    items = _parse_rss_items(xml_bytes, q, lim)
    if _NEWS_TTL > 0:
        with _news_lock:
            _news_cache[key] = (time.time(), items)
    return [dict(it) for it in items]


def news_sentiment(items: list[dict]) -> dict:
    """Agregat sentimen berita: skor -100..100 + rincian per judul.

    Berita yang lebih baru bobot lebih besar (decay 7 hari).
    Model ML lokal dijalankan sekali untuk semua judul (batch); kalau tidak
    tersedia, jatuh ke mesin lexicon (setiap item diberi catatan mesinnya).
    Status agregat:
      - POSITIF (🟢): pct >= 25
      - NEGATIF (🔴): pct <= -25
      - CAMPURAN (🟡): -25 < pct < 25 namun ada berita positif dan negatif
      - NETRAL (⚪): lainnya / tidak ada berita bersinyal bertolak belakang
    """
    now = datetime.now(timezone.utc)

    # klasifikasi ML sekali jalan untuk judul yang belum punya skor
    pending = [it for it in items
               if not ("sent" in it and it.get("engine") is not None)]
    ml_results: list[dict | None] = [None] * len(pending)
    if pending:
        try:
            from core import news_ml
            clean_titles = [
                strip_source_suffix(it["title"], it.get("source")) for it in pending
            ]
            ml_results = news_ml.classify_batch(clean_titles)
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
                s, hits = score_title(it["title"], it.get("source"))
                engine = "lexicon"
        it.update({"sent": s, "hits": hits, "engine": engine})

        age_days = 7.0
        if it.get("date"):
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
    n_total = len(details)
    n_pos = sum(1 for d in details if float(d.get("sent") or 0.0) >= ITEM_SENT_THRESHOLD)
    n_neg = sum(1 for d in details if float(d.get("sent") or 0.0) <= -ITEM_SENT_THRESHOLD)
    n_neu = n_total - n_pos - n_neg

    if n_total == 0:
        label, emoji = "NETRAL", "⚪"
    elif pct >= 25:
        label, emoji = "POSITIF", "🟢"
    elif pct <= -25:
        label, emoji = "NEGATIF", "🔴"
    elif n_pos > 0 and n_neg > 0:
        label, emoji = "CAMPURAN", "🟡"
    else:
        label, emoji = "NETRAL", "⚪"

    n_ml = sum(1 for d in details if d.get("engine") == "ml")
    n_lex = sum(1 for d in details if d.get("engine") == "lexicon")
    n_scored = sum(
        1 for d in details
        if d.get("engine") == "ml" or abs(float(d.get("sent") or 0.0)) >= ITEM_SENT_THRESHOLD
    )
    ml_pct = round(n_ml / n_total * 100.0, 1) if n_total else 0.0
    lex_pct = round(n_lex / n_total * 100.0, 1) if n_total else 0.0
    scored_pct = round(n_scored / n_total * 100.0, 1) if n_total else 0.0
    if n_total == 0:
        engine_summary = "tidak ada berita"
    elif n_ml == n_total:
        engine_summary = f"ML lokal ({n_ml}/{n_total})"
    elif n_ml > 0:
        engine_summary = f"ML {n_ml}/{n_total} + lexicon {n_lex}/{n_total}"
    else:
        engine_summary = f"lexicon ({n_lex}/{n_total}; {n_scored} bersinyal)"

    query = next((d.get("query") for d in details if d.get("query")), None)
    return {
        "pct": round(pct, 1),
        "label": label,
        "emoji": emoji,
        "n": n_total,
        "n_pos": n_pos,
        "n_neg": n_neg,
        "n_neu": n_neu,
        "engine_ml": n_ml,
        "engine_lexicon": n_lex,
        "engine_coverage": {
            "total": n_total,
            "ml": n_ml,
            "lexicon": n_lex,
            "ml_pct": ml_pct,
            "lexicon_pct": lex_pct,
            "scored": n_scored,
            "scored_pct": scored_pct,
            "summary": engine_summary,
        },
        "query": query,
        "details": details,
    }
