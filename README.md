# SahamBuddy 🇮🇩📈

Bot Telegram analisis saham Indonesia (BEI) dengan **tiga pin analisis**:

| Pin | Sumber | Bobot di PowerScore |
|---|---|---|
| Teknikal | MA, RSI, MACD, Bollinger, StochRSI, OBV, ATR (Yahoo Finance, ticker `.JK`) | 50% |
| Fundamental | PER, PBV, ROE, div-yield, DER (data laporan keuangan) | 30% |
| Sentimen berita | Google News RSS + model ML indo-roBERTa finansial lokal (fallback lexicon) | 20% |

![chart](temp/bbca.png)

## Fitur

- **/analisis BBCA** — PowerScore (gabungan 3 pin) + chart 4 panel + konfirmasi
   indikator pro (ATR/Bollinger/StochRSI/OBV) + 2 headline berita paling berimbas.
   Bisa multi emiten: `/analisis BBCA TLKM BBRI` (maks 5).
- **/skor TLKM BBRI** — ringkasan cepat skor teknikal tanpa chart.
- **/berita BBCA** — 7 berita terbaru + skor sentimen per judul (ML lokal, fallback lexicon).
- **/fundamental BBRI** — PER/PBV/ROE/dividen/DER + penilaian valuasi, plus
   **tren 4 kuartal terakhir** pendapatan & laba bersih + tanggal data
   ("data per Q3 2026", jujur soal delay laporan keuangan)
- **/backtest BBCA** — dua-duanya: backtest statistik 5 tahun + **simulasi
   transaksi nyata** (`backtesting.py`): biaya 0,25%/transaksi, fill bar berikutnya,
   entry sinyal BUY (skip jebakan momentum >15%/bulanan), SL 1×ATR, TP 2×ATR,
   maksimal tahan 20 hari. CLI: `python backtest.py BBCA` & `python backtest_real.py BBCA`.
- **Watchlist & alert** — `/watch`, `/alert add BBCA jump breakout`: dicek tiap 30 menit saat bursa buka (Senin–Jumat 09:00–16:00 WIB).
- **/saran** — pemindaian harian seluruh watchlist (atau universe default LQ45-ish): kandidat BUY dengan **entry / SL 1×ATR / TP 2×ATR**, disimpan sebagai posisi latihan (paper trading) dan dilacak otomatis — kalau TP/SL kena, bot kabari beserta hasil %-nya. `/saran list` = rekap.
- **/saran lq45** — pemindaian seluruh universe LQ45 (±45 emiten, 4–6 menit, progres bertahap tiap 10 emiten).
- **/modal 10000000** — atur modal latihan (default Rp10 juta); sizing tiap kandidat = **risiko 1% modal per trade** (dibulatkan ke bawah per lot 100) + nilai posisi.
- **/banding BBCA BBRI BMRI** — tabel pembanding PER/PBV/ROE/dividend yield/return 1 thn/20 hari/PowerScore + fakta "paling murah/mahal menurut PBV" (deskriptif). Data pembanding yang gagal dilaporkan jujur.
- **/agenda BBCA** — ex-date dividen (terakhir/berikutnya) + split & aksi korporasi dari Yahoo (bisa telat — cek juga situs emiten).
- **/belajar RSI** — kamus istilah dalam bahasa sederhana (18 topik: RSI, MACD, MA20/50/200, Bollinger, ATR, OBV, StochRSI, PER, PBV, ROE, dividend yield, DER, volume, breakout, IHSG, PowerScore + alias).
- **/auto on** — ringkasan `/saran` + rekap watchlist dikirim otomatis tiap hari bursa ±08:30 WIB (Senin–Jumat, default off).
- Setiap sinyal di `/analisis` kini diberi **penjelasan 1 baris** ("MA20 < MA50 → tren jangka pendek di bawah tren menengah — harga sedang downtrend") + laporan menyertakan **konteks IHSG** (20 hari & 1 tahun, berapa poin unggul/tertinggal).
- **/bandar [TICKER]** — data **resmi BEI (idx.co.id)**: IHSG & indeks sektor, top gainer/loser; per emiten: OHLC + nilai transaksi + field asing (bila tersedia). Catatan: idx.co.id dilindungi Cloudflare — dari IP datacenter/VPS kadang diblok; bot memberitahu dengan jujur dan menyuruh pakai `/analisis` sebagai cadangan.
  | Jenis alert | Pemicu |
  |---|---|
  | `jump` | harga naik ≥5% sehari |
  | `drop` | harga turun ≤-5% sehari |
  | `breakout` | close tembus high 20 hari |
  | `breakdown` | close jatuh di bawah low 20 hari |
  | `oversold` / `overbought` | RSI <30 / >70 |
  | `ma_golden` / `ma_death` | MACD golden/death cross |
  | `vol_spike` | volume ≥3x rata-rata 20 hari |

Skor momentum dihitung dari 9 sinyal (posisi harga vs MA20/50/200, susunan MA,
MACD vs signal, zona RSI sehat, breakout 20 hari, momentum volume) dengan skor
maksimum ±90% dan rating: STRONG BUY / BUY / NETRAL / SELL / STRONG SELL.

## Instalasi

```bash
cd saham-bot
pip install -r requirements.txt
```

## Dapatkan token bot

1. Chat [@BotFather](https://t.me/BotFather) di Telegram.
2. Kirim `/newbot`, pilih nama dan username.
3. Salin token yang diberikan (format `123456:ABC-DEF...`).

## Jalankan

```bash
./run.sh          # baca BOT_TOKEN dari .env otomatis + fix NO_PROXY VPS
```

atau langsung:

```bash
BOT_TOKEN="123456:ABC-DEF..." python bot.py
```

Deployment 24/7 (Docker/systemd) → lihat **[deploy.md](deploy.md)**.

### Konfigurasi env penting

| Variabel | Arti | Default |
|---|---|---|
| `BOT_TOKEN` | token dari @BotFather — **wajib** | — |
| `ALLOWED_USER_IDS` | id Telegram yang boleh pakai bot (koma utk banyak). Kosong = publik | kosong |
| `YF_RETRIES` | percobaan ulang saat Yahoo rate-limit (429) | `3` |
| `SAHAMBOT_CACHE` | folder cache harga (fallback saat Yahoo mati) | `temp/cache` |
| `SAHAMBOT_LOCAL_MODEL` | folder model sentimen lokal | `models/indo-fin-roberta` |
| `STORE_FILE` | file data pribadi (watchlist/alert/paper) | `store.json` |

Saat Yahoo memblokir IP (429), bot otomatis pakai cache harga terakhir dan
menandai laporan dengan ⚠️ + umur cache — jujur, tidak membual data segar.

## Tips

- Jaringan: bot mengambil data historis dari Yahoo Finance (ticker `.JK`).
  Kalau server kamu di luar Indonesia kadang YF rate-limit — tunggu sebentar
  atau kurangi frekuensi pemakaian.
- Uji cepat tanpa bot: `python -c "from core import data, analysis; \
  res = analysis.analyze('BBCA', data.fetch_history('BBCA')); \
  print(res['score']['pct'], res['score']['rating'])"`
- Hapus chart sementara di folder `temp/` sesekali (otomatis dihapus setelah dikirim).

## Kejujuran hasil backtest (jarak: di database!)

Hasil uji 5 tahun pada BBCA/TLKM/ANTM (nov 2026): **sinyal BUY berdiri sendiri
masih belum profitable** setelah biaya (win-rate 29-32%, PF 0,48-0,64) — meski
kalah lebih aman daripada buy & hold pada BBCA/TLKM yang sedang downtrend.
Pemakaian paling sehat: **pelindung** (skip sinyal buruk, alarm pergerakan),
bukan mesin buy otomatis.

## 📚 Sumber terbuka yang menyuporti botini

- [idx-bei](https://github.com/nichsedge/idx-bei) — toolkit BEI resmi (bandarmology, aliran asing, tindakan korporasi) → ide lanjutan
- [saham-fundamental-toolkit](https://github.com/Kuroburns2/SAHAM-ANALISA-saham-analyzer-TP-SL-create) — berita GDELT + analisis laporan resmi IDX
- [sentimeter](https://github.com/snowfluke/sentimeter) — format /saran dengan entry/SL/TP
- [pandas-ta-classic](https://github.com/xgboosted/pandas-ta-classic) & [backtesting.py](https://kernc.github.io/backtesting.py/) — indikator & mesin backtest
- model sentimen: [will702/indo-roBERTa-financial-sentiment-v2](https://huggingface.co/will702/indo-roBERTa-financial-sentiment-v2) (F1 92,8%, MIT)

## ⚠️ Disclaimer

Ini alat bantu edukasi, bukan saran investasi. Selalu DYOR dan pertimbangkan
konsultasi dengan Perencana Keuangan yang berizin.
