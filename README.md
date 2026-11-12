# SahamBuddy 🇮🇩📈

[![CI](https://github.com/bagasadi05/saham-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/bagasadi05/saham-bot/actions/workflows/ci.yml)

Bot Telegram analisis saham Indonesia (BEI) dengan **tiga pin analisis**:

| Pin | Sumber | Bobot di PowerScore |
|---|---|---|
| Teknikal | MA, RSI, MACD, Bollinger, StochRSI, OBV, ATR (Yahoo Finance, ticker `.JK`) | 50% |
| Fundamental | PER, PBV, ROE, div-yield, DER (data laporan keuangan) | 30% |
| Sentimen berita | Google News RSS + model ML indo-roBERTa finansial lokal (fallback lexicon) | 20% |

## Fitur

- **/analisis BBCA** — PowerScore (gabungan 3 pin) + chart 4 panel + konfirmasi
   **tren mingguan (1wk vs MA20 + peringatan pantulan)** + pembanding **IHSG & emiten
   se-sektor** (20h & 1th) + indikator pro + 2 headline berita. Multi emiten hingga 5 kode.
- **/skor TLKM BBRI** — ringkasan cepat skor teknikal tanpa chart.
- **/berita BBCA** — 7 berita terbaru + skor sentimen per judul (ML lokal, fallback lexicon).
- **/fundamental BBRI** — PER/PBV/ROE/dividen/DER + penilaian valuasi, plus
   **tren 4 kuartal terakhir** pendapatan & laba bersih + tanggal data
   ("data per Q3 2026", jujur soal delay laporan keuangan)
- **/backtest BBCA** — dua-duanya: backtest statistik 5 tahun + **simulasi
   transaksi nyata** (`backtesting.py`): biaya **beli 0,15% / jual 0,25% all-in — sama
   dengan `/saran`** (pajak jual 0,10% sudah termasuk di 0,25%; riset portofolio
   memakai asumsi yang sama, bukan 0,50%), fill bar berikutnya, entry sinyal BUY (skip jebakan
   momentum >15%/bulanan), SL 1×ATR, TP 2×ATR, maksimal tahan 20 hari. Imbal
   hasil backtest statistik kini **net setelah biaya**.
   Hasil simulasi 5 tahun disimpan di cache `temp/` (24 jam) sehingga `/beli`/`/jual`
   bisa mengutip rekam jejak historis secara instan.
   CLI: `python backtest.py BBCA` & `python backtest_real.py BBCA`.
- **Watchlist & alert** — `/watch`, `/alert add BBCA jump breakout`: dicek tiap 30 menit saat bursa buka (hari bursa XIDX, 09:00–16:00 WIB). Alert **hanya dikirim saat kondisi berubah** (False → True), tidak lagi diulang tiap 30 menit selama kondisi bertahan.
- **/saran** — pemindaian harian seluruh watchlist (atau universe default LQ45-ish): kandidat BUY dengan **entry / SL 1×ATR / TP 2×ATR**, dibulatkan ke **fraksi harga BEI** (tick 1/2/5/10/25) supaya levelnya bisa langsung dipasang. Urutan kandidat memakai **skor relatif 12 faktor** (`core/relatif.py`) yang lolos uji walk-forward (fallback ke PowerScore bila universe < 8 emiten) — lihat `/validasi`. Posisi dicatat **realistis**: masuk antrean `pending` dan baru dibeli di **harga open sesi berikutnya**, PnL dihitung setelah biaya beli 0,15% & jual 0,25%, ditutup otomatis setelah ±20 hari bursa, maksimal 5 posisi terbuka & 2 per sektor. `/saran list` = rekap posisi + rata-rata hasil net; riwayat menyimpan **20 posisi selesai terakhir** per chat agar rekap tidak membengkak.
- **/scan** — pindai **seluruh pasar BEI** (±944 kode aktif, daftar API resmi IDX) secara borongan (batch Yahoo, ±2–3 menit). Hasil disimpan sebagai snapshot SQLite. Berjalan otomatis **16:15 WIB** setiap hari bursa untuk chat `/auto on` (matikan dengan `FULL_SCAN_AUTO=off`). Emiten tanpa data harga cukup (delisted/suspensi baru) dilewati dan jumlahnya dilaporkan — tidak diisi data palsu.
- **/sinyal** — baca snapshot seluruh pasar: ringkasan **BELI / JUAL / TAHAN**, `/sinyal beli`, `/sinyal jual`, detail `/sinyal BBCA`, unduh `/sinyal csv`. Sinyal = filter tren (MA20/MA50/MACD/RSI) + peringkat relatif 12 faktor yang sama dengan `/saran`. Ini peringkat statistik tipis (IC OOS ±0,03), **bukan jaminan**; JUAL berarti hindari/kurangi bila punya — bukan instruksi short. Daftar universe + provenance ada di `data/idx_universe.json`; perbarui dengan `python tools/build_universe.py`.
- **/rencana** (alias **/stockbit**) — paket beli siap eksekusi: ambil kandidat BELI terbaik dari snapshot seluruh pasar, saring **likuiditas ≥ Rp2 miliar/hari**, harga ≥ Rp50, bukan papan Pemantauan Khusus, dan veto fundamental/berita yang jelas bermasalah. Hasilnya: **grade A/B/C** (kelengkapan syarat, bukan probabilitas), entry **limit**, SL 1×ATR, TP 2×ATR, **lot & rupiah sesuai modal `/modal`** (risiko maks 1%/posisi, nilai posisi maks 20% per emiten, total alokasi maks 90% modal), plus cara pasang di Stockbit. `/rencana 5` untuk 5 kandidat, `/rencana refresh` untuk membangun ulang cache. Kalau tidak ada yang lolos, bot mengatakan tidak ada — bukan memaksa beli.
- **/validasi** — menampilkan hasil uji statistik yang tersimpan: IC skor lama (antar-emiten & dalam satu emiten), excess return per rating, IC walk-forward per tahun, kalibrasi kuintil OOS, dan hasil uji Kronos. Angkanya hasil `research/evaluate.py`, bukan klaim. Untuk hasil **sinyal yang benar-benar keluar** (bukan simulasi riset), pakai `/jejak`.
- **/saran lq45** — pemindaian seluruh universe LQ45 (±45 emiten, **±1–2 menit** — 4 emiten diproses paralel, progres bertahap tiap 10 emiten).
- **/modal 10000000** — atur modal latihan (default Rp10 juta); sizing tiap kandidat = **risiko 1% modal per trade** (dibulatkan ke bawah per lot 100) + nilai posisi.
- **/banding BBCA BBRI BMRI** — tabel pembanding PER/PBV/ROE/dividend yield/return 1 thn/20 hari/PowerScore + fakta "paling murah/mahal menurut PBV" (deskriptif). Data pembanding yang gagal dilaporkan jujur.
- **/agenda BBCA** — ex-date dividen (terakhir/berikutnya) + split & aksi korporasi dari Yahoo (bisa telat — cek juga situs emiten).
- **/belajar RSI** — kamus istilah dalam bahasa sederhana (18 topik: RSI, MACD, MA20/50/200, Bollinger, ATR, OBV, StochRSI, PER, PBV, ROE, dividend yield, DER, volume, breakout, IHSG, PowerScore + alias).
- **/jejak** (alias **/track**) — rekam jejak nyata: setiap snapshot harian kini disimpan di tabel `scan_history`, lalu setelah **20 hari bursa** (ubah dengan `SCORECARD_HORIZON`) bot menghitung hasilnya — return rata-rata, win-rate, dan **R-multiple** untuk BELI (berapa kali TP 2R, SL −1R, atau keluar waktu), dengan TAHAN sebagai pembanding pasar, plus **IC live** (korelasi peringkat vs hasil nyata) yang bisa dibandingkan dengan IC riset. Baris yang harga historisnya berubah karena split ditandai `ADJ` dan tidak dihitung. Angka ini pengukuran, bukan janji; sampel kecil mudah didominasi kebetulan.
- **/beli BBCA** / **/jual BBCA** (atau ketik bebas `"BBCA beli gak?"`) — verdict + level SL/TP + rekam jejak backtest 5 thn (dari cache `/backtest`).
- **/auto on** — aktifkan pemindaian otomatis: ringkasan `/saran` + rekap watchlist ±08:30 WIB, **pengingat ex-date dividen (H-3 & H-1)**, serta **alert perubahan band PowerScore** watchlist (default off).
- **/status** — kesehatan bot: uptime, jumlah perintah/error, rata-rata waktu proses, isi store, cache harga, status bursa (khusus `ADMIN_IDS` bila diisi).
- **/id** — tampilkan ID Telegram numerik kamu untuk mengisi `ALLOWED_USER_IDS` di `.env`.
- **Kalender bursa sungguhan** — job alert & autoscan mengikuti kalender XIDX (`exchange_calendars`) plus libur tambahan via `MARKET_HOLIDAYS`, jadi tidak berisik di hari libur nasional/cuti bersama.
- **Cache harga ber-TTL** (default 5 menit, `SAHAMBOT_TTL`) — `/skor`, `/analisis`, dan `/saran` pada emiten yang sama tidak menembak Yahoo berulang, dan tiap laporan menyebut **“data per <tanggal>”**.
- Setiap sinyal di `/analisis` kini diberi **penjelasan 1 baris** ("MA20 < MA50 → tren jangka pendek di bawah tren menengah — harga sedang downtrend") + laporan menyertakan **konteks IHSG** (20 hari & 1 tahun, berapa poin unggul/tertinggal).
- **/bandar [TICKER]** — data **resmi BEI (idx.co.id)**: IHSG & indeks sektor, top gainer/loser; per emiten: OHLC + nilai transaksi + field asing (bila tersedia). Daftar emiten diambil bertahap; `/bandar KODE` berhenti mencari begitu kodenya ketemu. Catatan: idx.co.id dilindungi Cloudflare — dari IP datacenter/VPS kadang diblok; bot memberitahu dengan jujur dan menyuruh pakai `/analisis` sebagai cadangan. Hasil di-cache 10 menit (`BANDAR_TTL`) supaya beberapa permintaan berdekatan tidak memicu blokir.
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
python -m venv .venv
. .venv/bin/activate        # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# opsional — model sentimen ML (~500 MB, butuh torch/transformers):
pip install -r requirements-ml.txt && python download_model.py
```

Tanpa `requirements-ml.txt` bot tetap jalan penuh; sentimen berita otomatis
memakai lexicon Indonesia.

Uji logika inti tanpa token bot / tanpa jaringan:

```bash
pip install -r requirements-dev.txt
pytest -q        # alert, skor, sizing, paper trading, kalender, cache, store
ruff check .     # lint (F, E9, B; line-length 100)
```

## Dapatkan token bot

1. Chat [@BotFather](https://t.me/BotFather) di Telegram.
2. Kirim `/newbot`, pilih nama dan username.
3. Salin token yang diberikan (format `123456:ABC-DEF...`).

## Jalankan

```bash
./run.sh          # baca BOT_TOKEN dari .env otomatis + fix NO_PROXY VPS
```

Windows (PowerShell):

```powershell
.\run.ps1         # padanan run.sh: muat .env, pakai .venv, cek token
```

`bot.py` juga memuat `.env` sendiri (tanpa dependensi tambahan), jadi ini tetap jalan:

```bash
BOT_TOKEN="123456:ABC-DEF..." python bot.py
```

Deployment 24/7 (Docker/systemd/supervisor) → lihat **[deploy.md](deploy.md)**.

### Mengaktifkan Allowlist (disarankan untuk bot pribadi)

1. Kirim `/id` ke bot-mu untuk melihat angka ID Telegram kamu.
2. Isi di `.env`: `ALLOWED_USER_IDS=<angka_id>` (pisahkan koma jika >1 user) dan `REQUIRE_ALLOWLIST=1`.
3. Restart bot; hanya ID terdaftar yang dapat mengakses command & tombol menu.

### Konfigurasi env penting

| Variabel | Arti | Default |
|---|---|---|
| `BOT_TOKEN` | token dari @BotFather — **wajib** | — |
| `ALLOWED_USER_IDS` | id Telegram yang boleh pakai bot (koma utk banyak). Kosong = publik | kosong |
| `REQUIRE_ALLOWLIST` | `1` = bot menolak start kalau `ALLOWED_USER_IDS` kosong | mati |
| `ADMIN_IDS` | id yang boleh `/status` (kosong = semua pemakai) | kosong |
| `YF_RETRIES` | percobaan ulang saat Yahoo rate-limit (429) | `3` |
| `SAHAMBOT_TTL` | umur cache harga di memori, detik (`0` = nonaktif) | `300` |
| `SAHAMBOT_FUND_TTL` | umur cache fundamental, detik (`0` = nonaktif) | `21600` (6 jam) |
| `SAHAMBOT_NEWS_TTL` | umur cache berita Google News, detik (`0` = nonaktif) | `900` (15 menit) |
| `BANDAR_TTL` | umur cache data BEI `/bandar`, detik (`0` = nonaktif) | `600` (10 menit) |
| `SARAN_CONCURRENCY` | jumlah emiten yang dipindai paralel di `/saran` | `4` |
| `FULL_SCAN_AUTO` | screener seluruh pasar otomatis 16:15 WIB (`off` = mati) | `on` |
| `FULL_SCAN_PERIOD` | periode data batch screener | `1y` |
| `FULL_SCAN_CHUNK` | jumlah ticker per batch Yahoo | `80` |
| `FULL_SCAN_PAUSE` | jeda antar batch (detik) | `1.0` |
| `SCORECARD_HORIZON` | jumlah hari bursa sebelum hasil sinyal `/jejak` dihitung | `20` |
| `PLAN_MIN_VALUE` | minimum nilai transaksi harian (Rp) untuk `/rencana` | `2000000000` |
| `PLAN_MIN_PRICE` | harga minimum kandidat `/rencana` | `50` |
| `PLAN_MAX_POS_PCT` | nilai maksimum satu posisi terhadap modal | `0.20` |
| `PLAN_MAX_PLANS` | jumlah rencana default (1–5) | `3` |
| `PLAN_POOL` | kandidat yang diperkaya fundamental/berita | `16` |
| `SAHAMBOT_CACHE` | folder cache harga (fallback saat Yahoo mati) | `temp/cache` |
| `SAHAMBOT_LOCAL_MODEL` | folder model sentimen lokal | `models/indo-fin-roberta` |
| `STORE_DB` | database SQLite data pribadi (watchlist/alert/paper) | `store.db` |
| `STORE_FILE` | `store.json` lama — dimigrasikan sekali lalu jadi `.migrated` | `store.json` |
| `HEARTBEAT_FILE` | file heartbeat untuk `HEALTHCHECK` Docker (kosong = nonaktif) | `heartbeat.txt` |
| `MARKET_HOLIDAYS` | libur bursa tambahan, mis. `2026-03-19,2026-03-20` | kosong |
| `MARKET_HOLIDAYS_FILE` | file JSON daftar libur tambahan | `market_holidays.json` |

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
- Command mahal (`/analisis`, `/saran`, `/banding`, `/bandar`, `/backtest`) punya
  cooldown per user supaya satu akun tidak bisa membanjiri bot.

## Kejujuran hasil backtest (jarak: di database!)

Hasil uji 5 tahun pada BBCA/TLKM/ANTM (nov 2026): **sinyal BUY berdiri sendiri
masih belum profitable** setelah biaya (win-rate 29-32%, PF 0,48-0,64) — meski
kalah lebih aman daripada buy & hold pada BBCA/TLKM yang sedang downtrend.
Pemakaian paling sehat: **pelindung** (skip sinyal buruk, alarm pergerakan),
bukan mesin buy otomatis.

### Uji statistik 45 emiten (2017–2026, horizon 20 hari)

Setelah skor diuji ulang secara cross-sectional + walk-forward
(`research/`, ringkasnya di `/validasi`):

| Metrik | Hasil | Arti |
|---|---|---|
| IC skor antar-emiten | **+0,0115** (t=2,68) | praktis nol |
| IC skor dalam satu emiten | **−0,0207** (t=−2,33) | sedikit berlawanan arah |
| Excess STRONG BUY | +0,46% / 20 hari | tipis (menang 46%) |
| Excess STRONG SELL | −0,01% / 20 hari | label ekstrem bawah tidak informatif |
| Walk-forward 12 faktor | +0,047 / +0,048 / +0,025 / **−0,026** / +0,063 | 4 dari 5 tahun positif |
| Kuintil OOS Q5 vs Q1 | +0,42% vs −0,24% | selisih 0,67% / 20 hari |

Konsekuensinya di kode:

1. **Skor teknikal tidak lagi dipakai memilih saham.** Di `/analisis` labelnya
   tetap ada sebagai bacaan tren, dengan catatan eksplisit bahwa IC-nya ≈ nol.
2. **`/saran` memeringkat kandidat dengan skor relatif** 12 faktor
   (`core/relatif.py`) — bobot dibekukan dari walk-forward, bukan PowerScore.
   Kalau universe < 8 emiten (statistik cross-sectional tak berarti), bot
   kembali ke PowerScore dan mengatakannya.
3. **Tetap tipis.** IC 0,03 bukan mesin uang; biaya transaksi IDX (±0,5%
   pulang-pergi) memakan sebagian besar selisih kuintil. Perlakukan sebagai
   pemeringkat kandidat, bukan jaminan.

## 📚 Sumber terbuka yang mendukung bot ini

- [idx-bei](https://github.com/nichsedge/idx-bei) — toolkit BEI resmi (bandarmology, aliran asing, tindakan korporasi) → ide lanjutan
- [saham-fundamental-toolkit](https://github.com/Kuroburns2/SAHAM-ANALISA-saham-analyzer-TP-SL-create) — berita GDELT + analisis laporan resmi IDX
- [sentimeter](https://github.com/snowfluke/sentimeter) — format /saran dengan entry/SL/TP
- [pandas-ta-classic](https://github.com/xgboosted/pandas-ta-classic) & [backtesting.py](https://kernc.github.io/backtesting.py/) — indikator & mesin backtest
- model sentimen: [will702/indo-roBERTa-financial-sentiment-v2](https://huggingface.co/will702/indo-roBERTa-financial-sentiment-v2) (F1 92,8%, MIT)

## 🧪 Pengembangan & tes

```bash
pip install -r requirements-dev.txt
ruff check .      # lint
pytest            # 330+ tes offline (tanpa Yahoo/Google News/HuggingFace)
make coverage     # tes + gerbang coverage 75% (modul inti & bot.py)
```

Atau lewat Makefile: `make install`, `make lint`, `make test`, `make docker`.

Daftar emiten untuk `/scan` & `/sinyal` ada di `data/idx_universe.json`
(sumber + tanggal tercatat di file). Perbarui kapan saja:

```bash
python tools/build_universe.py                  # coba API resmi idx.co.id
python tools/build_universe.py --from-file x.json  # build dari respons tersimpan
```

Screener menguji tiap kode lewat data harga Yahoo; kode delisted/suspensi
dilewati dan dihitung, bukan diisi data karangan.

CI GitHub Actions menjalankan lint + seluruh tes offline pada tiap push/PR.
Build Docker bisa dicek lokal: `docker build --build-arg SKIP_MODEL=1 -t saham-bot:slim .`

## ⚠️ Disclaimer

Ini alat bantu edukasi, bukan saran investasi. Selalu DYOR dan pertimbangkan
konsultasi dengan Perencana Keuangan yang berizin.
