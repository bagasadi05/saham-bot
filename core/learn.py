"""P1 — Mode belajar: penjelasan sinyal & kamus istilah untuk pembelajar.

Struktur teks selalu: apa itu → cara membacanya → jebakan umum.
Semua isi statis (tanpa data dinamis), aman untuk mode HTML Telegram
asalkan dilewatkan html.escape terlebih dahulu di caller.
"""
from __future__ import annotations

# --------------------------------------------------------------- sinyal ----
# Mapping teks sinyal (diproduksi analysis.score_from_rows) -> penjelasan.
# Dicocokkan dengan 'in' (substring) berurutan; entri paling spesifik dulu.
SIGNAL_NOTES: list[tuple[str, str]] = [
    ("MA50 > MA200", "struktur tren panjang sedang naik (golden cross jarak jauh) — sejarahnya lebih ramah untuk membeli secara bertahap"),
    ("MA50 < MA200", "struktur tren panjang sedang turun (death cross) — pendanaan biasanya butuh waktu untuk pulih dari situasi ini"),
    ("MA20 > MA50", "tren jangka pendek sedang berada di atas tren menengah — momentum jangka pendek naik"),
    ("MA20 < MA50", "tren jangka pendek sedang berada di bawah tren menengah — harga sedang dalam downtrend jangka pendek"),
    ("Harga di atas MA200", "harga di atas rata-rata 200 hari — tren besarnya masih naik"),
    ("Harga di bawah MA200", "harga di bawah rata-rata 200 hari — tren besarnya masih turun; waspada 'pisau jatuh'"),
    ("Harga di atas MA50", "harga di atas rata-rata 50 hari — bias jangka menengah naik"),
    ("Harga di bawah MA50", "harga di bawah rata-rata 50 hari — bias jangka menengah turun"),
    ("Harga di atas MA20", "harga di atas rata-rata 20 hari — dalam waktu dekat pembeli sedang menang"),
    ("Harga di bawah MA20", "harga di bawah rata-rata 20 hari — tekanan jualan masih terlihat"),
    ("MACD bullish", "momentum naik: garis MACD menembus ke atas garis sinyal"),
    ("MACD bearish", "momentum turun: garis MACD berada di bawah garis sinyal"),
    ("RSI sehat", "RSI 50–70: momentumnya naik tapi belum terlalu ramai — zona terlalu sehat"),
    ("RSI ekstrem", "RSI di luar zona sehat (>70 jenuh beli, <30 jenuh jual) — bisa mirroring atau berbalik arah"),
    ("Harga di dekat/atas puncak 20 hari", "harga berada di dekat/atas puncak 20 hari — momentum kuat, tapi ini belum konfirmasi breakout; waspada breakout palsu"),
    ("Belum di dekat puncak", "harga masih di dalam kisaran 20 hari — tren belum jelas, bersabar menunggu"),
    ("Volume di atas rata-rata", "volume transaksi mendukung kenaikan harga — banyak investor yang antusias"),
    ("Volume biasa", "partisipasi pasar tidak terlalu aktif — kenaikan/penurunan mudah berbalik arah"),
]

# Penjelasan untuk konfirmasi indikator pro (analysis.extra_confirmations)
PRO_NOTES: list[tuple[str, str]] = [
    ("Bollinger SQUEEZE", "rentang Bollinger mempersempit — biasanya diikuti pergerakan besar (arahnya belum diketahui, tunggu sinyal)"),
    ("di luar Bollinger atas", "harga ekstrem di atas pita atas — biasanya menyusul jeda (koreksi)"),
    ("di bawah Bollinger bawah", "harga ekstrem di bawah pita bawah — overshoot yang mirip, bisa memantul atau terus jatuh"),
    ("Bollinger: harga di", "posisi harga dalam rentang pita Bollinger (0% = terendah, 100% = tertinggi)"),
    ("ATR 14", "ATR = rata-rata gerakan harian; SL 1×ATR berarti memberi ruang gerakan normal gaya hidup sebelum cut loss"),
    ("StochRSI", "osilator lebih cepat dari RSI; <20 = jenuh jual pendek, >80 = jenuh beli pendek (sering terlalu dini untuk bergerak sendirian)"),
    ("OBV 20 hari", "OBV menjumlahkan volume: naik = akumulasi (dana masuk), turun = distribusi (dana keluar)"),
]

NOT_FOUND = "(penjelasan tidak tersedia untuk sinyal ini)"

# Penjelasan rating skor teknikal (deskripsi kondisi, BUKAN perintah eksekusi)
RATING_NOTES = {
    "STRONG BUY": "sangat banyak sinyal naik yang menyala bersamaan — kondisi teknikal sedang kuat; tetap hati-hati di puncak kenaikan",
    "BUY": "skor positif: mayoritas sinyal teknikal menunjukkan kondisi naik — tetap cek risiko & berita",
    "NETRAL": "sinyal naik & turun saling meniadakan — pasar sedang ragu; kamu juga boleh menunggu",
    "SELL": "mayoritas sinyal teknikal menunjukkan kondisi turun — bukan momen berspekulasi menangkap",
    "STRONG SELL": "banyak sinyal serempak menunjukkan tekanan kuat — hindari menangkap pisau jatuh",
}


def signal_note(signal: str) -> str | None:
    """Penjelasan 1 baris dalam Bahasa Indonesia untuk teks sinyal tertentu."""
    for key, note in SIGNAL_NOTES:
        if key in signal:
            return note
    return None


def pro_note(line: str) -> str | None:
    for key, note in PRO_NOTES:
        if key in line:
            return note
    return None


# ------------------------------------------------------------ /belajar ------
# Panel topik. Nilai: (judul, penjelasan multi-baris). Isi hati-hati:
# teks berisi HTML harus di-escape di caller.
TOPICS: dict[str, str] = {
    "rsi": (
        "RSI (Relative Strength Index)",
        "Apa itu: pengukur kecepatan & kekuatan pergerakan harga (0–100), dihitung dari 14 hari perubahan harga.\n\n"
        "Cara baca:\n"
        "• 50–70: momentum naik yang sehat\n"
        "• <30: <b>oversold</b> — jenuh jual, berpotensi memantul naik\n"
        "• >70: <b>overbought</b> — jenuh beli, berpotensi berbalik turun\n\n"
        "Panduan penggunaan: RSI bagus untuk memberitahu keadaan ekstrem, bukan untuk memprediksi arah dengan teliti.\n\n"
        "Jebakan umum: saham kuat bisa bertahan di atas 70 berminggu-minggu (tren naik kuat) — "
        "RSI >70 saja tidak cukup untuk menjual begitu saja. Pada saham BEI yang likuiditasnya kecil, RSI bisa jauh lembur.",
    ),
    "macd": (
        "MACD (Moving Average Convergence Divergence)",
        "Apa itu: selisih dua rata-rata bergerak (EMA 12 − EMA 26), lalu digambar garis 'sinyal' (EMA 9) di atasnya. Histogram = jarak keduanya.\n\n"
        "Cara baca:\n"
        "• MACD di atas garis sinyal: momentum naik (bullish)\n"
        "• MACD di bawah garis sinyal: momentum turun (bearish)\n"
        "• Histogram membesar: momentum menguat; mengecil: melemah (divergence)\n\n"
        "Jebakan umum: MACD tertinggal (lagging) — sinyal muncul saat gerakan sudah melewati setengahnya. "
        "Pada saham yang sering berbalik arah, MACD bisa memberi sinyal palsu beruntun.",
    ),
    "ma20": (
        "MA20 (rata-rata bergerak 20 hari)",
        "Apa itu: harga rata-rata 20 hari perdagangan (±1 bulan bursa) — 'kompas' jangka pendek.\n\n"
        "Cara baca: harga di atas MA20 = jangka pendek lebih kuat, di bawahnya = ada tekanan jual di jangka pendek.\n\n"
        "Jebakan umum: berisik — harga cukup keluar-masuk MA20 hampir setiap hari. Gunakan dengan MA50/200 untuk konteks, jangan berdiri sendiri.",
    ),
    "ma50": (
        "MA50 (rata-rata bergerak 50 hari)",
        "Apa itu: harga rata-rata 50 hari (±2,5 bulan) — nada baca untuk tren jangka menengah.\n\n"
        "Cara baca: harga di atas MA50 = tren jangka menengah masih naik; harga menjauh "
        "dari MA50 lalu memantul kembali ke atasnya dengan volatilitas kecil = tren sehat. "
        "Pelaku jangka menengah memandang penembusan ke bawah MA50 sebagai peringatan tren.\n\n"
        "Jebakan umum: pada saham yang tidak likuid, sedikit transaksi besar bisa menggeser MA50 dengan sangat drastis.",
    ),
    "ma200": (
        "MA200 (rata-rata bergerak 200 hari)",
        "Apa itu: harga rata-rata 200 hari (±10 bulan) — garis 'musim' tren panjang.\n\n"
        "Cara baca: harga di atas MA200 = musim tren masih di atas; di bawahnya = musim masih turun. "
        "MA200 juga sering jadi support/resistance pencapaian.\n\n"
        "Jebakan umum: sekitar MA200 harga sering turun-naik beberapa kali (whipsaw) — jenderal berbagi angka tepat di sana, tunggu konfirmasi.",
    ),
    "bollinger": (
        "Bollinger Bands",
        "Apa itu: tiga garis — tengah = SMA 20, atas/bawah = ±2 standar deviasi harga. 90%+ waktu harga bergerak di dalam pita.\n\n"
        "Cara baca:\n"
        "• Pita sempit (SQUEEZE): volatilitas rendah, biasanya awal pergerakan besar\n"
        "• Harga menembus pita: kondisi ekstrem (bukan sinyal beli/jual sendirian)\n"
        "• Posisi dalam pita 0–100: rendah = dekat lantai, tinggi = dekat atap\n\n"
        "Jebakan umum: 'sentuh pita bawah = beli' itu tidak selalu benar — di tren turun kuat, harga bisa bertahun-tahun menempel di pita bawah (walking the band).",
    ),
    "atr": (
        "ATR (Average True Range)",
        "Apa itu: rata-rata jangkauan gerakan harga per hari (14 hari), dalam persen harga. Tidak menunjukkan arah — hanya 'amplitudonya'.\n\n"
        "Cara baca: ATR 4% berarti harga normal naik-turun sekitar 4% per hari. SL 1×ATR di bawah entry = memberi ruang gerakan normal sebelum cut loss.\n\n"
        "Jebakan umum: ATR melebar cepat saat panik/berita — SL berbasis ATR menyesuaikan menjadi lebih lebar; "
        "ukuran posisi ditentukan berdasarkan rasio risiko, bukan jumlah lot yang sama.",
    ),
    "obv": (
        "OBV (On-Balance Volume)",
        "Apa itu: menjumlahkan volume — hariharga naik ditambah, hari turun dikurangi. Gagasan temuan: volume mendahului harga.\n\n"
        "Cara baca: OBV naik sementara harga nyaris bergerak = akumulasi (tangan besar mengambil posisi diam-diam); OBV turun saat harga naik = distribusi (pump tanpa dana).\n\n"
        "Jebakan umum: OBV sensitif pada gap harga & data Yahoo yang delayed — pakai sebagai penilaian arus dana kasar, bukan angka presisi real-time.",
    ),
    "stochrsi": (
        "StochRSI",
        "Apa itu: RSI yang diterapkan pada dirinya sendiri — RSI dari RSI, jadi lebih cepat/tajam (0–100).\n\n"
        "Cara baca: <20 = oversold pendek, >80 = overbought pendek. Cocok untuk timing jangka pendek, bukan penentu arah besar.\n\n"
        "Jebakan umum: terlalu renyah — sinyal palsu bermunculan pada pasar samping; gunakan sebagai konfirmasi (diharapkan temu), bukan pemicu utama.",
    ),
    "per": (
        "PER (Price-to-Earnings Ratio)",
        "Apa itu: harga saham ÷ laba bersih per saham. PER 10 = kamu membayar 10× laba tahunan perusahaan.\n\n"
        "Cara baca (kasar, bandingkan dengan industrinya):\n"
        "• PER < 10: murah (bisa juga labanya sedang sekarat — cek kenapa murah)\n"
        "• PER 10–20: wajar untuk bank & sebagian besar IDX\n"
        "• PER > 35: mahal (efisien di pertumbuhan besar — masih rugi jika tidak teps)\n"
        "• PER negatif: perusahaan sedang rugi\n\n"
        "Jebakan umum: PER rendah ≠ murah — bisa jadi laba bekal pasti sedang memuncak (siklus komoditas). Pakai PBV/ROE bersamaan.",
    ),
    "pbv": (
        "PBV (Price-to-Book Value)",
        "Apa itu: harga saham ÷ nilai buku per saham (aset − kewajiban). PBV 1 = harga sama dengan nilai bersih bukunya.\n\n"
        "Cara baca:\n"
        "• PBV < 1: harga di bawah nilai buku (potensi undervalued — atau asetnya bermasalah)\n"
        "• PBV 1–3: wajar untuk perusahaan profitabilitas standar\n"
        "• PBV > 3: pasar membayar premium — biasanya untuk ROE tinggi\n\n"
        "Aturan terbaik: PBV harus dibaca bersama ROE. ROE 20% dengan PBV 3 = wajar; ROE 3% dengan PBV 3 = mahal.\n\n"
        "Jebakan umum: aset di laporan keuangan bisa tidak terupdate (tanah lama) atau tidak cair (rekening macet).",
    ),
    "roe": (
        "ROE (Return on Equity)",
        "Apa itu: laba bersih ÷ ekuitas — 'bunga' yang dihasilkan perusahaan dari modal pemegang saham. ROE 15% = tiap Rp100 ekuitas membuat Rp15 laba/tahun.\n\n"
        "Cara baca:\n"
        "• ROE > 15% konsisten: kualitas bisnis bagus\n"
        "• ROE 8–15%: cukup\n"
        "• ROE < 8% atau negatif: profitabilitas lemah\n\n"
        "Jebakan umum: ROE bisa 'dipompa' dengan utang (DER tinggi). ROE tinggi + DER 200% = jauh lebih berisiko daripada ROE sama dengan DER 30%. Lihat trennya 3–5 tahun, bukan 1 kuartal.",
    ),
    "dividend": (
        "Dividend Yield",
        "Apa itu: total dividen dalam 1 tahun ÷ harga saham. Yield 6% = tiap Rp1.000 saham, dapat Rp60 dividen setahun.\n\n"
        "Cara baca: >5% bagus untuk pendapatan pasif; 3–5% wajar; <3% rendah (sedang tumbuh? atau emiten jarang bagi hasil).\n\n"
        "Jebakan umum: yield tinggi bisa tiba-tiba jadi 'dividen jebakan' — harga turun besar saat ex-date akan menyurutkan yield, dan dividen saham yang sedang berhenti-atas tidak dijamin terulang. Cek payout ratio & stabilitas laba.",
    ),
    "der": (
        "DER (Debt to Equity Ratio)",
        "Apa itu: total utang ÷ ekuitas. DER 100% = utang sama besar dengan modal sendiri.\n\n"
        "Cara baca:\n"
        "• DER < 50%: sehat — bunga tidak menggerus laba\n"
        "• DER 50–100%: umum di bank & infrastruktur\n"
        "• DER > 100%: berat — kewajiban bunga terasa berat terhadap laba\n\n"
        "Untuk BANK, DER tinggi itu alamiah (tabungan orang = utang bank) — gunakan CAR/LDR, bukan DER.\n\n"
        "Jebakan umum: DER yang sama bisa lebih berisiko saat suku bunga naik; selalu cek arah suku bunga & apakah utangnya dalam mata uang asing.",
    ),
    "volume": (
        "Volume",
        "Apa itu: jumlah lembar saham yang berpindah tangan hari itu.\n\n"
        "Cara baca:\n"
        "• Harga naik + volume di atas rata-rata: kenaikan didukung dana nyata (sehat)\n"
        "• Harga naik + volume tipis: mudah berbalik (pump tipis)\n"
        "• Volume 3×+ rata-rata: ada berita/aliran dana besar masuk\n\n"
        "Jebakan umum: BEI sering mengalami spike volume di akhir bulan (window dressing) atau saat jatuh tempo — jangan baca volume 1 hari tanpa konteksnya.",
    ),
    "breakout": (
        "Breakout 20 hari",
        "Apa itu: harga hari ini menembus harga tertinggi 20 hari terakhir — tanda suplai penahan habis & tren naik dimulai.\n\n"
        "Cara baca: breakout + volume naik = lebih responsif; breakout + volume tipis = rawan gagal.\n\n"
        "Jebakan umum: <b>false breakout</b> — menembus 1–2% lalu kembali ke rentang (bull trap). Karena itu bot menunggu konfirmasi close di atas high sebelum bertanda breakout.",
    ),
    "ihsg": (
        "IHSG (Indeks Harga Saham Gabungan)",
        "Apa itu: indeks gabungan seluruh saham di BEI — 'stiker' arah pasar secara keseluruhan. Dikerahkan dari market cap saham yang mengalun.\n\n"
        "Cara baca: paling sering, saat IHSG naik banyak saham cocok naik juga; sebaliknya. Perbandingan BBCA vs IHSG memberitahu apakah gerakan saham itu milik emiten atau milik pasar.\n\n"
        "Jebakan umum: IHSG didominasi bank & telkom — gerakan BBCA/BBRI/TLKM mempengaruhi indeks besar-besaran; saham kecil bisa bergerak berbeda dari IHSG.",
    ),
    "powerscore": (
        "PowerScore",
        "Apa itu: skor gabungan bot (−100..+100) dari tiga komponen:\n"
        "• 50% teknikal (harga/MA/MACD/RSI — dibacktest)\n"
        "• 30% fundamental (PER/PBV/ROE/dividen/DER)\n"
        "• 20% sentimen berita (ML lokal + lexicon)\n\n"
        "Cara baca: >+20 BUY, <-20 SELL — namun ini gambaran keadaan saat ini, "
        "BUKAN perintah beli/jual.\n\n"
        "Jebakan umum: skor tinggi bisa terjadi di puncak lonjakan (jebakan momentum — filter ret20d>15% sudah mengurangi); skor rendah bisa di dasar bawah yang sedang dekat membalik.",
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
    keys = sorted(set(TOPICS) | set())
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
