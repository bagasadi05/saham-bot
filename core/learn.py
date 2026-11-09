"""P1 — Mode belajar: penjelasan sinyal & kamus istilah untuk pembelajar.

Struktur teks selalu: apa itu → cara membacanya → jebakan umum.
Semua isi statis (tanpa data dinamis), aman untuk mode HTML Telegram
asalkan dilewatkan html.escape terlebih dahulu di caller.
"""
from __future__ import annotations

# --------------------------------------------------------------- sinyal ----
# Mapping teks sinyal (diproduksi analysis.score_from_rows) -> penjelasan.
# Dicocokkan dengan 'in' (substring, case-insensitive) berurutan; spesifik dulu.
SIGNAL_NOTES: list[tuple[str, str]] = [
    ("MA50 > MA200", "struktur tren panjang sedang naik (golden cross jarak jauh) — lebih ramah untuk akumulasi bertahap"),
    ("MA50 < MA200", "struktur tren panjang sedang turun (death cross) — tren besar biasanya butuh waktu untuk pulih"),
    ("MA20 > MA50", "tren jangka pendek sedang berada di atas tren menengah — momentum jangka pendek naik"),
    ("MA20 < MA50", "tren jangka pendek berada di bawah tren menengah — harga sedang downtrend jangka pendek"),
    ("Harga di atas MA200", "harga di atas rata-rata 200 hari — tren besarnya masih naik"),
    ("Harga di bawah MA200", "harga di bawah rata-rata 200 hari — tren besarnya masih turun; waspada 'pisau jatuh'"),
    ("Harga di atas MA50", "harga di atas rata-rata 50 hari — bias jangka menengah naik"),
    ("Harga di bawah MA50", "harga di bawah rata-rata 50 hari — bias jangka menengah turun"),
    ("Harga di atas MA20", "harga di atas rata-rata 20 hari — dalam jangka pendek pembeli unggul"),
    ("Harga di bawah MA20", "harga di bawah rata-rata 20 hari — tekanan jual jangka pendek masih terlihat"),
    ("MACD bullish", "momentum naik: garis MACD menembus ke atas garis sinyal"),
    ("MACD bearish", "momentum turun: garis MACD berada di bawah garis sinyal"),
    ("RSI sehat", "RSI 50–70: momentum naik yang sehat dan belum jenuh beli"),
    ("RSI overbought", "RSI >70 (jenuh beli) — rawan koreksi/konsolidasi jangka pendek"),
    ("RSI oversold", "RSI <30 (jenuh jual) — bisa memantul, tapi di downtrend kuat sering terus turun"),
    ("RSI lemah", "RSI di zona 30–50 — momentum melemah, belum ada dorongan beli kuat"),
    ("RSI ekstrem", "RSI di luar zona sehat (>70 jenuh beli, <30 jenuh jual) — waspada pembalikan atau jeda"),
    ("Harga di dekat/atas puncak 20 hari", "harga berada di dekat/atas puncak 20 hari — momentum kuat, tapi waspada breakout palsu"),
    ("Belum di dekat puncak", "harga masih di dalam kisaran 20 hari — tren breakout belum terbentuk, bersabar"),
    ("Volume di atas rata-rata, tapi harga turun", "volume tinggi saat harga turun menandakan tekanan jual kuat — jangan terburu-buru masuk"),
    ("Volume di atas rata-rata dengan harga naik", "kenaikan harga didukung volume transaksi besar — partisipasi pasar nyata"),
    ("Volume di atas rata-rata", "volume lebih besar dari rata-rata 20 hari — perhatikan arah harganya"),
    ("Harga naik, tapi volume di bawah rata-rata", "kenaikan harga kurang didukung volume — rawan kehabisan tenaga"),
    ("volume di bawah rata-rata", "partisipasi pasar sepi — pergerakan harga mudah berbalik arah"),
    ("Volume biasa", "partisipasi pasar tidak terlalu aktif — kenaikan/penurunan mudah berbalik arah"),
]

# Penjelasan untuk konfirmasi indikator pro (analysis.extra_confirmations)
PRO_NOTES: list[tuple[str, str]] = [
    ("Bollinger SQUEEZE", "rentang Bollinger menyempit — biasanya diikuti pergerakan besar (arahnya belum diketahui, tunggu konfirmasi)"),
    ("di luar Bollinger atas", "harga ekstrem di atas pita atas — sering disusul jeda atau koreksi"),
    ("di bawah Bollinger bawah", "harga ekstrem di bawah pita bawah — bisa memantul atau lanjut melemah (walking the band)"),
    ("Bollinger: harga di", "posisi harga dalam rentang pita Bollinger (0% = terendah, 100% = tertinggi)"),
    ("ATR 14", "ATR = rata-rata rentang gerak harian; SL 1×ATR memberi ruang napas volatilitas normal sebelum cut loss"),
    ("StochRSI", "osilator lebih cepat dari RSI; <20 = jenuh jual pendek, >80 = jenuh beli pendek (sering memberi sinyal dini)"),
    ("OBV 20 hari", "OBV menjumlahkan volume: naik = akumulasi (dana masuk), turun = distribusi (dana keluar)"),
]

NOT_FOUND = "(penjelasan tidak tersedia untuk sinyal ini)"

# Penjelasan rating skor teknikal (deskripsi kondisi, BUKAN perintah eksekusi)
RATING_NOTES = {
    "STRONG BUY": "banyak sinyal naik menyala bersamaan — kondisi teknikal kuat; tetap waspada di puncak lonjakan",
    "BUY": "skor positif: mayoritas sinyal teknikal mengarah naik — tetap cek risiko & berita",
    "NETRAL": "sinyal naik & turun saling mengimbangi — pasar ragu-ragu; menunggu juga keputusan valid",
    "SELL": "mayoritas sinyal teknikal melemah — hindari spekulasi tanpa konfirmasi balik",
    "STRONG SELL": "banyak sinyal turun serempak menunjukkan tekanan kuat — hindari menangkap pisau jatuh",
}


def signal_note(signal: str) -> str | None:
    """Penjelasan 1 baris dalam Bahasa Indonesia untuk teks sinyal tertentu."""
    low = signal.lower()
    for key, note in SIGNAL_NOTES:
        if key.lower() in low:
            return note
    return None


def pro_note(line: str) -> str | None:
    low = line.lower()
    for key, note in PRO_NOTES:
        if key.lower() in low:
            return note
    return None


# ------------------------------------------------------------ /belajar ------
# Panel topik. Nilai: (judul, penjelasan multi-baris). Isi hati-hati:
# teks berisi HTML harus di-escape di caller.
TOPICS: dict[str, tuple[str, str]] = {
    "rsi": (
        "RSI (Relative Strength Index)",
        "Apa itu: pengukur kecepatan & kekuatan pergerakan harga (0–100), dihitung dari 14 hari perubahan harga.\n\n"
        "Cara baca:\n"
        "• 50–70: momentum naik yang sehat\n"
        "• <30: <b>oversold</b> — jenuh jual, berpotensi memantul naik\n"
        "• >70: <b>overbought</b> — jenuh beli, berpotensi berbalik turun\n\n"
        "Panduan penggunaan: RSI bagus untuk menandai kondisi ekstrem, bukan meramal arah pasti.\n\n"
        "Jebakan umum: saham uptrend kuat bisa bertahan di atas 70 berminggu-minggu — "
        "RSI >70 saja belum cukup jadi alasan jual. Pada saham BEI yang kurang likuid, RSI bisa cepat loncat.",
    ),
    "macd": (
        "MACD (Moving Average Convergence Divergence)",
        "Apa itu: selisih dua rata-rata bergerak (EMA 12 − EMA 26), lalu digambar garis 'sinyal' (EMA 9) di atasnya. Histogram = jarak keduanya.\n\n"
        "Cara baca:\n"
        "• MACD di atas garis sinyal: momentum naik (bullish)\n"
        "• MACD di bawah garis sinyal: momentum turun (bearish)\n"
        "• Histogram membesar: momentum menguat; mengecil: mulai melemah\n\n"
        "Jebakan umum: MACD bersifat tertinggal (lagging) — sinyal muncul saat gerakan sudah berjalan. "
        "Di pasar sideways, MACD sering memberi sinyal palsu beruntun.",
    ),
    "ma20": (
        "MA20 (rata-rata bergerak 20 hari)",
        "Apa itu: harga rata-rata 20 hari perdagangan (±1 bulan bursa) — 'kompas' jangka pendek.\n\n"
        "Cara baca: harga di atas MA20 = jangka pendek lebih kuat, di bawahnya = ada tekanan jual jangka pendek.\n\n"
        "Jebakan umum: cukup berisik — harga sering keluar-masuk MA20. Padukan dengan MA50/MA200 untuk konteks tren.",
    ),
    "ma50": (
        "MA50 (rata-rata bergerak 50 hari)",
        "Apa itu: harga rata-rata 50 hari (±2,5 bulan) — acuan utama tren jangka menengah.\n\n"
        "Cara baca: harga di atas MA50 = tren jangka menengah masih naik; koreksi sehat sering memantul di sekitar MA50. "
        "Penembusan ke bawah MA50 biasanya jadi peringatan perubahan tren.\n\n"
        "Jebakan umum: pada saham berlikuiditas rendah, fluktuasi singkat bisa mematahkan MA50 berulang kali.",
    ),
    "ma200": (
        "MA200 (rata-rata bergerak 200 hari)",
        "Apa itu: harga rata-rata 200 hari (±10 bulan) — garis tren jangka panjang.\n\n"
        "Cara baca: harga di atas MA200 = tren besarnya naik; di bawahnya = tren besar turun. "
        "MA200 juga sering berfungsi sebagai support/resistance psikologis.\n\n"
        "Jebakan umum: di sekitar MA200 harga sering bolak-balik (whipsaw) — tunggu konfirmasi penutupan beberapa hari.",
    ),
    "bollinger": (
        "Bollinger Bands",
        "Apa itu: tiga garis — tengah = SMA 20, atas/bawah = ±2 standar deviasi harga. Sebagian besar waktu harga bergerak di dalam pita.\n\n"
        "Cara baca:\n"
        "• Pita sempit (SQUEEZE): volatilitas rendah, sering mendahului pergerakan besar\n"
        "• Harga menembus pita: kondisi ekstrem sementara (bukan sinyal beli/jual sendirian)\n"
        "• Posisi dalam pita 0–100%: rendah = dekat lantai, tinggi = dekat atap\n\n"
        "Jebakan umum: menyentuh pita bawah tidak otomatis murah — saat downtrend kuat, harga bisa terus menempel pita bawah (walking the band).",
    ),
    "atr": (
        "ATR (Average True Range)",
        "Apa itu: rata-rata rentang gerak harian harga (14 hari). Tidak menunjukkan arah — hanya seberapa lebar goyangannya.\n\n"
        "Cara baca: ATR 4% berarti gerak harian normal sekitar 4%. Stop-loss 1×ATR di bawah entry memberi ruang fluktuasi normal sebelum cut loss.\n\n"
        "Jebakan umum: ATR melebar saat pasar panik sehingga stop-loss harus lebih lebar — sesuaikan ukuran lot lewat batas risiko modal, bukan jumlah lot tetap.",
    ),
    "obv": (
        "OBV (On-Balance Volume)",
        "Apa itu: menjumlahkan volume — hari harga naik ditambah, hari harga turun dikurangi. Gagasan dasarnya: arus volume mendahului harga.\n\n"
        "Cara baca: OBV naik saat harga datar = indikasi akumulasi; OBV turun saat harga naik = kenaikan kurang bertenaga (distribusi).\n\n"
        "Jebakan umum: OBV sensitif terhadap gap harga & data volume Yahoo yang delayed — pakai sebagai pelengkap, bukan angka tunggal.",
    ),
    "stochrsi": (
        "StochRSI",
        "Apa itu: rumus Stochastic yang diterapkan ke nilai RSI (skala 0–100), jadi jauh lebih cepat dan sensitif daripada RSI biasa.\n\n"
        "Cara baca: <20 = oversold pendek, >80 = overbought pendek. Cocok membantu timing, bukan menentukan tren utama.\n\n"
        "Jebakan umum: terlalu sensitif — sering memicu sinyal palsu di pasar sideways; jadikan konfirmasi tambahan saja.",
    ),
    "per": (
        "PER (Price-to-Earnings Ratio)",
        "Apa itu: harga saham ÷ laba bersih per saham. PER 10 berarti harga sama dengan 10× laba tahunan perusahaan.\n\n"
        "Cara baca (bandingkan dengan industrinya):\n"
        "• PER < 10: murah (cek apakah labanya sedang turun/siklikal)\n"
        "• PER 10–20: wajar untuk kebanyakan emiten mapan BEI\n"
        "• PER > 35: mahal (pasar mengharapkan pertumbuhan laba tinggi)\n"
        "• PER negatif: perusahaan sedang merugi\n\n"
        "Jebakan umum: PER rendah bisa menipu jika laba tahun terakhir sedang puncak siklus (seperti komoditas). Selalu baca bersama PBV & ROE.",
    ),
    "pbv": (
        "PBV (Price-to-Book Value)",
        "Apa itu: harga saham ÷ nilai buku per saham (aset − kewajiban). PBV 1 = harga setara nilai bersih bukunya.\n\n"
        "Cara baca:\n"
        "• PBV < 1: harga di bawah nilai buku (potensi murah, atau prospek laba tertekan)\n"
        "• PBV 1–3: wajar untuk bisnis dengan profitabilitas normal\n"
        "• PBV > 3: pasar memberi premi tinggi — biasanya untuk emiten ber-ROE tinggi\n\n"
        "Aturan praktis: baca PBV bersama ROE. ROE 20% dengan PBV 3 bisa wajar; ROE 3% dengan PBV 3 tergolong mahal.\n\n"
        "Jebakan umum: nilai aset tercatat di neraca bisa belum mencerminkan nilai pasar terkini atau likuiditas aset sebenarnya.",
    ),
    "roe": (
        "ROE (Return on Equity)",
        "Apa itu: laba bersih ÷ ekuitas — tingkat imbal hasil dari modal pemegang saham. ROE 15% berarti tiap Rp100 ekuitas mencetak Rp15 laba/tahun.\n\n"
        "Cara baca:\n"
        "• ROE > 15% konsisten: efisiensi bisnis kuat\n"
        "• ROE 8–15%: cukup baik\n"
        "• ROE < 8% atau negatif: profitabilitas lemah\n\n"
        "Jebakan umum: ROE bisa tampak tinggi karena utang besar (DER tinggi). Cek tren ROE beberapa tahun dan bandingkan dengan DER.",
    ),
    "dividend": (
        "Dividend Yield",
        "Apa itu: total dividen 1 tahun ÷ harga saham. Yield 6% berarti saham seharga Rp1.000 membagikan Rp60 dividen setahun.\n\n"
        "Cara baca: >5% menarik untuk pendapatan pasif; 3–5% wajar; <3% kecil (emiten mungkin menahan laba untuk ekspansi).\n\n"
        "Jebakan umum: 'dividend trap' — yield terlihat besar hanya karena harga saham anjlok atau ada dividen insidentil yang tak terulang; harga biasa terkoreksi sekitar ex-date.",
    ),
    "der": (
        "DER (Debt to Equity Ratio)",
        "Apa itu: total utang ÷ ekuitas. DER 100% berarti jumlah utang sebanding dengan modal sendiri.\n\n"
        "Cara baca:\n"
        "• DER < 50%: sehat — beban bunga relatif ringan\n"
        "• DER 50–100%: moderat / umum pada emiten padat modal\n"
        "• DER > 100%: tinggi — risiko bunga dan pelunasan lebih besar\n\n"
        "Catatan khusus BANK: DER tinggi wajar bagi perbankan karena dana pihak ketiga tercatat sebagai liabilitas — cek CAR/NPL, bukan DER.\n\n"
        "Jebakan umum: utang berbunga mengambang atau valuta asing berisiko melonjak saat suku bunga/kurs berubah.",
    ),
    "volume": (
        "Volume",
        "Apa itu: jumlah lembar saham yang diperdagangkan hari itu.\n\n"
        "Cara baca:\n"
        "• Harga naik + volume di atas rata-rata: kenaikan didukung minat beli nyata\n"
        "• Harga naik + volume tipis: gerakan mudah berbalik arah\n"
        "• Volume ≥3× rata-rata: ada berita besar atau arus dana signifikan\n\n"
        "Jebakan umum: volume sering melonjak musiman di penutupan indeks/akhir bulan — baca volume bersama struktur harga.",
    ),
    "breakout": (
        "Breakout 20 hari",
        "Apa itu: harga penutupan menembus harga tertinggi 20 hari terakhir — tanda suplai penahan mulai terserap pembeli.\n\n"
        "Cara baca: breakout + volume tinggi lebih meyakinkan; breakout + volume tipis lebih rawan gagal.\n\n"
        "Jebakan umum: <b>false breakout</b> — sempat tembus tipis lalu balik turun (bull trap). Karena itu tunggu konfirmasi penutupan harian.",
    ),
    "ihsg": (
        "IHSG (Indeks Harga Saham Gabungan)",
        "Apa itu: indeks komposit seluruh saham di BEI yang dibobot berdasarkan kapitalisasi pasar — cerminan arah pasar secara umum.\n\n"
        "Cara baca: membandingkan return emiten terhadap IHSG membantu membedakan apakah kenaikan/penurunan berasal dari fundamental sahamnya atau ikut arus pasar.\n\n"
        "Jebakan umum: bobot IHSG sangat dipengaruhi emiten big-caps (bank & telekomunikasi), sehingga saham lapis kedua/ketiga bisa bergerak sangat berbeda.",
    ),
    "powerscore": (
        "PowerScore",
        "Apa itu: skor gabungan bot (−100..+100) dari tiga komponen:\n"
        "• 50% teknikal (harga/MA/MACD/RSI)\n"
        "• 30% fundamental (PER/PBV/ROE/dividen/DER)\n"
        "• 20% sentimen berita (ML lokal + lexicon)\n\n"
        "Cara baca: >+20 BUY, <−20 SELL — ini ringkasan kondisi saat ini, BUKAN perintah beli/jual.\n\n"
        "Jebakan umum: skor tinggi dapat muncul saat harga sudah naik tinggi (jebakan momentum ret20d > 15%). Gunakan peringkat relatif `/saran` dan manajemen risiko.",
    ),
}

ALIASES = {
    "div": "dividend", "divyield": "dividend", "yield": "dividend",
    "dividendyield": "dividend", "dy": "dividend",
    "ma": "ma50", "sma": "ma50", "movingaverage": "ma50",
    "bb": "bollinger", "bbands": "bollinger",
    "stocks": "breakout", "bo": "breakout",
    "vol": "volume",
    "ps": "powerscore", "score": "powerscore",
    "stoch": "stochrsi",
    "debt": "der", "roce": "roe",
    "ihsg100": "ihsg", "jci": "ihsg",
}


def list_topics() -> str:
    keys = sorted(TOPICS)
    return "\n".join(f"  • /belajar <code>{k}</code>" for k in keys)


def learn(topic: str) -> str | None:
    """Teks /belajar untuk satu topik; None jika topik tidak dikenal."""
    t = topic.lower().strip().replace(" ", "")
    t = ALIASES.get(t, t)
    t = t.replace("+", "")
    if t not in TOPICS:
        return None
    judul, isi = TOPICS[t]
    return f"🎓 <b>{judul}</b>\n\n{isi}\n\n🔒 Pendidikan pasar modal — bukan saran investasi."
