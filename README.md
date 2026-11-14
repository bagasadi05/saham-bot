# SahamBuddy 🇮🇩📈

[![CI](https://github.com/bagasadi05/saham-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/bagasadi05/saham-bot/actions/workflows/ci.yml)

Bot Telegram analisis saham Indonesia (BEI) dengan **tiga pin analisis**:

| Pin | Sumber | Bobot di PowerScore |
|---|---|---|
| Teknikal | MA, RSI, MACD, Bollinger, StochRSI, OBV, ATR (Yahoo Finance, ticker `.JK`) | 50% |
| Fundamental | PER, PBV, ROE, div-yield, DER (data laporan keuangan) | 30% |
| Sentimen berita | Google News RSS + model ML indo-roBERTa finansial lokal (fallback lexicon) | 20% |

## Fitur

- **Menu interaktif ramah pemula (`/start`, `/menu`, `/help`)** — layar awal
  ringkas tanpa tumpukan jargon, tersusun berdasarkan tujuan (*Cek Layak Beli*,
  *Analisis Lengkap*, *Ide & Sinyal*, *Hitung Modal & Lot*, *Watchlist & Alarm*,
  *Portofolio Saya*, *Belajar Istilah*, *Panduan Pemula*) serta laci *Fitur
  Lanjutan* untuk alat riset/scan. `/help` menyajikan panduan *3 Langkah Mulai*
  beserta tombol coba cepat BBCA, `/belajar` dilengkapi tombol istilah klik
  langsung, pengatur modal punya pilihan cepat (`Rp 2/5/10/25 Juta`), dan
  pertanyaan bebas seperti `"BBCA beli gak?"` langsung dijawab tanpa perintah `/`
  (implementasi di `handlers/menu.py`).
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
- **/saran** — pemindaian harian seluruh watchlist (atau universe default LQ45-ish): kandidat BUY dengan **entry / SL 1×ATR / TP 2×ATR**, dibulatkan ke **fraksi harga BEI** (tick 1/2/5/10/25) supaya levelnya bisa langsung dipasang. Urutan kandidat memakai **skor relatif 12 faktor** (`core/relatif.py`) yang lolos uji walk-forward (fallback ke PowerScore bila universe < 8 emiten) — lihat `/validasi`. Ukuran risiko otomatis ikut **rezim IHSG** (`^JKSE SMA50 < SMA200` memangkas risiko per trade dari 1% menjadi 0,5%; bila data `<200` bar ditulis "tidak tersedia"), dan posisi terbuka ditinjau terhadap **band tahan top 60%** (`TAHAN`/`ROTASI`) untuk menekan biaya rotasi. Posisi dicatat **realistis**: masuk antrean `pending` dan baru dibeli di **harga open sesi berikutnya**, PnL dihitung setelah biaya beli 0,15% & jual 0,25%, ditutup otomatis setelah ±20 hari bursa, maksimal 5 posisi terbuka & 2 per sektor. `/saran list` = rekap posisi + rata-rata hasil net; riwayat menyimpan **20 posisi selesai terakhir** per chat agar rekap tidak membengkak.
- **/scan** — pindai **seluruh pasar BEI** (±944 kode aktif, daftar API resmi IDX) secara borongan (batch Yahoo, ±2–3 menit). Hasil disimpan sebagai snapshot SQLite. Berjalan otomatis **16:15 WIB** setiap hari bursa untuk chat `/auto on` (matikan dengan `FULL_SCAN_AUTO=off`). Emiten tanpa data harga cukup (delisted/suspensi baru) dilewati dan jumlahnya dilaporkan — tidak diisi data palsu.
- **/sinyal** — baca snapshot seluruh pasar: ringkasan **BELI / JUAL / TAHAN**, `/sinyal beli`, `/sinyal jual`, detail `/sinyal BBCA`, unduh `/sinyal csv`.
  Kandidat BELI diberi **penanda kualitas fundamental** (✅ sehat / ⚠️ tidak lolos / ❔ belum dinilai) dari PER/PBV/ROE, dan **`/sinyal beli bagus`** hanya menampilkan yang lolos saringan (laba positif, ROE ≥ 8%, PER ≤ 25×, PBV ≤ 4×). Fundamental hanya diambil untuk kandidat BELI yang ditampilkan, dengan cache harian. Sinyal = filter tren (MA20/MA50/MACD/RSI) + peringkat relatif 12 faktor yang sama dengan `/saran`. Ini peringkat statistik tipis (IC OOS ±0,03), **bukan jaminan**; JUAL berarti hindari/kurangi bila punya — bukan instruksi short. Daftar universe + provenance ada di `data/idx_universe.json`; perbarui dengan `python tools/build_universe.py`.
- **/rencana BBCA 5000000** — rencana beli **satu emiten** dengan anggaran sendiri: jumlah lot yang benar-benar terjangkau (fee ikut dihitung), total biaya, % modal, **rugi maksimal dalam rupiah kalau kena SL**, untung bersih di TP, dan rasio untung:rugi. Anggaran di bawah harga 1 lot dijawab jujur (harga 1 lot + fee), bukan 0 lot karangan. Aturan risiko 1% modal tetap ditampilkan sebagai pembanding: "5 lot = 2,4% modal, aturan hanya 3 lot".
- **/paket** (alias **/stockbit**) — paket beli **multi-emiten** siap eksekusi dari snapshot `/scan`: kandidat BELI terbaik, saring **likuiditas ≥ Rp2 miliar/hari**, harga ≥ Rp50, bukan papan Pemantauan Khusus, veto fundamental/berita yang jelas bermasalah. Hasilnya **grade A/B/C** (kelengkapan syarat, bukan probabilitas), entry **limit**, SL 1×ATR, TP 2×ATR, **lot & rupiah sesuai modal `/modal`** (normal: risiko maks 1%/posisi, nilai posisi maks 20%/emiten, total alokasi maks 90%; saat **rezim IHSG defensif** `SMA50 < SMA200`: otomatis dipangkas setengah menjadi 0,5% / 10% / 45%, sisanya cash), plus panduan Stockbit dan aturan tahan top 60% (`±20–40` hari bursa). `/paket 5` untuk 5 kandidat, `/paket refresh` untuk membangun ulang cache. Kalau tidak ada yang lolos, bot mengatakan tidak ada — bukan memaksa beli.
- **/cek BBCA** — ceklist **satu layar** sebelum menekan beli: tren harian+mingguan, valuasi vs sektor + **audit forensik arus kas / jebakan dividen** (`OCF`, `FCF`, `current ratio`, `payout ratio`), aliran asing (kalau data BEI tersedia), berita, agenda 14 hari, dan likuiditas — ditutup verdict **LAYAK / TUNGGU / HINDARI** dengan **Sudut Pandang Pro (Bull) vs Risiko (Bear)**. Bagian yang datanya kosong ditulis "tidak tersedia"; kalau terlalu banyak yang kosong, verdict-nya "TIDAK CUKUP DATA" (bukan HINDARI karangan).
- **/portofolio** — buku catatan **uang sungguhan** (terpisah dari paper trading): `/portofolio beli BBCA 5 9000`, `/portofolio jual BBCA 2 9500`, `/portofolio riwayat`, `/portofolio batas [20 35|reset]` (atur batas maks posisi & sektor), dan `/portofolio` untuk PnL berjalan, bobot posisi, konsentrasi sektor, serta **risiko total bila semua SL (mengutamakan SL eksekusi tersimpan, fallback 1×ATR dibulatkan ke fraksi tick BEI) tersentuh**, dilengkapi **Chandelier trailing stop, rencana scale-out 50% di TP1 + BEP+0,40%, peringatan time-stop >=20 hari bursa, dan risiko gap ARB 7%**.
  Sejak 14 Nov 2026 juga menampilkan **volatilitas tahunan, drawdown berjalan,
  VaR 95% 1 hari, beta vs IHSG, korelasi, dan target bobot seimbang (*inverse-volatility*)** antar posisi (bagian
  "Risiko portofolio"). Bot tidak pernah menaruh order dan tidak menyimpan kredensial Stockbit.
- **/jurnal** (alias **/journal**) — **jurnal eksekusi manual Stockbit** & realisasi nyata (terpisah antara **ASLI** vs **PAPER**): ketuk tombol **"Sudah beli di Stockbit"** setelah `/rencana`, `/cek`, `/beli`, atau `/sinyal KODE` (cukup ketik harga atau `<lot> <harga>`), atau catat lewat `/jurnal beli BBCA 5 9050 [sinyal]`, `/jurnal jual BBCA 5 9500 [sinyal]`, dan `/jurnal riwayat`. Bot menghitung **slippage nyata** terhadap harga sinyal, **win rate & profit factor realisasi** bersih setelah biaya BEI `0,40%` (`0,15%` beli + `0,25%` jual), diagnostik **MAE / MFE & R-multiple** neto (`core/jurnal.py`), serta ringkasan mingguan otomatis tiap Jumat `16:45 WIB` bagi akun `/auto on` yang sudah mencatat eksekusi. Data sinyal yang tidak tersedia tetap ditulis "tidak tersedia". Bot tidak pernah login ke Stockbit atau menaruh order.
- **/validasi** — menampilkan hasil uji statistik yang tersimpan: IC skor lama (antar-emiten & dalam satu emiten), excess return per rating, IC walk-forward per tahun, kalibrasi kuintil OOS, dan hasil uji Kronos. Angkanya hasil `research/evaluate.py`, bukan klaim. Untuk hasil **sinyal yang benar-benar keluar** (bukan simulasi riset), pakai `/jejak`.
- **/saran lq45** — pemindaian seluruh universe LQ45 (±45 emiten, **±1–2 menit** — 4 emiten diproses paralel, progres bertahap tiap 10 emiten).
- **/modal 10000000** — atur modal latihan (default Rp10 juta); sizing tiap kandidat = **risiko 1% modal per trade** (dibulatkan ke bawah per lot 100) + nilai posisi.
- **/sektor** — kekuatan relatif **sektor BEI** hari itu dari indeks resmi
  `GetIndexSummary`: sektor terkuat & terlemah, berapa yang naik/turun. Kalau
  idx.co.id diblokir Cloudflare atau payload-nya tidak memuat angka yang bisa
  dihitung, bot berkata tidak tersedia — bukan menampilkan 0%.
- **/banding BBCA BBRI BMRI** — tabel pembanding PER/PBV/ROE/dividend yield/return 1 thn/20 hari/PowerScore + fakta "paling murah/mahal menurut PBV" (deskriptif). Data pembanding yang gagal dilaporkan jujur.
- **/agenda BBCA** — ex-date dividen (terakhir/berikutnya) + split & aksi korporasi dari Yahoo (bisa telat — cek juga situs emiten).
- **/belajar RSI** — kamus istilah dalam bahasa sederhana (18 topik: RSI, MACD, MA20/50/200, Bollinger, ATR, OBV, StochRSI, PER, PBV, ROE, dividend yield, DER, volume, breakout, IHSG, PowerScore + alias).
- **/jejak** (alias **/track**) — rekam jejak nyata: setiap snapshot harian kini disimpan di tabel `scan_history`, lalu setelah **20 hari bursa** (ubah dengan `SCORECARD_HORIZON`) bot menghitung hasilnya — return rata-rata, win-rate, dan **R-multiple** untuk BELI (berapa kali TP 2R, SL −1R, atau keluar waktu), evaluasi akuntabilitas terpisah untuk **JUAL** (`TURUN`/`NAIK`/`DATAR`, bukan instruksi short), dengan TAHAN sebagai pembanding pasar, plus **IC live** (korelasi peringkat vs hasil nyata beserta `t_hac` Newey-West) yang bisa dibandingkan dengan IC riset. Baris yang harga historisnya berubah karena split (>25%) ditandai `ADJ` dan tidak dihitung, sedangkan pergeseran eks-dividen (>0,5% pada data `auto_adjust=True`) ditandai `div_adjusted`. Angka ini pengukuran, bukan janji; sampel kecil mudah didominasi kebetulan.
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
| `ADMIN_CHAT_ID` | chat tujuan notifikasi job gagal (kosong = `ADMIN_IDS` pertama) | kosong |
| `LOG_JSON` | `1` = log satu baris JSON (untuk `docker logs \| jq`) | mati |
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
| `FULL_SCAN_MAX_STALE_DAYS` | tolak emiten dengan bar terakhir basi > N hari bursa di `/scan` | `5` |
| `SCORECARD_HORIZON` | jumlah hari bursa sebelum hasil sinyal `/jejak` dihitung | `20` |
| `PLAN_MIN_VALUE` | minimum nilai transaksi harian (Rp) untuk `/rencana` & `/paket` | `2000000000` |
| `PLAN_MIN_PRICE` | filter harga minimum kualitas likuiditas `/rencana` (lantai reguler BEI Rp1) | `50` |
| `PLAN_MAX_POS_PCT` | nilai maksimum satu posisi terhadap modal | `0.20` |
| `PLAN_MAX_PLANS` | jumlah rencana default (1–5) | `3` |
| `PLAN_POOL` | kandidat yang diperkaya fundamental/berita | `16` |
| `SAHAMBOT_CACHE` | folder cache harga per-period (fallback saat Yahoo mati) | `temp/cache` |
| `SAHAMBOT_LOCAL_MODEL` | folder model sentimen lokal | `models/indo-fin-roberta` |
| `DOWNLOAD_CHUNK` | ukuran chunk unduhan model sentimen (MB) | `4` |
| `STORE_DB` | database SQLite data pribadi (`SCHEMA_VERSION = 6`) | `store.db` |
| `STORE_FILE` | `store.json` lama — dimigrasikan sekali lalu jadi `.migrated` | `store.json` |
| `BACKUP_DIR` | folder cadangan harian SQLite (`tools/backup_db.py`) | `backups` |
| `BACKUP_KEEP` | jumlah cadangan harian SQLite yang dipertahankan | `7` |
| `BACKUP_AUTO` | cadangan otomatis SQLite tiap 23:30 WIB (`off` = mati) | `on` |
| `HEARTBEAT_FILE` | file heartbeat untuk `HEALTHCHECK` Docker (kosong = nonaktif) | `heartbeat.txt` |
| `MARKET_HOLIDAYS` | libur bursa tambahan, mis. `2026-03-19,2026-03-20` | kosong |
| `MARKET_HOLIDAYS_FILE` | file JSON daftar libur tambahan | `market_holidays.json` |
| `IDX_ARA_WINDOW_END` | tanggal akhir jendela aturan ARA BEI (ISO `YYYY-MM-DD`) | `2026-12-31` |
| `IDX_ARB_WINDOW_END` | tanggal akhir jendela ARB asimetris 7% Kep-00158 (ISO `YYYY-MM-DD`) | `2026-12-31` |
| `EODHD_API_KEY` | kunci API opsional fallback OHLCV `.JK` saat Yahoo gagal/429 | kosong (mati) |
| `ALPHAVANTAGE_API_KEY` | kunci API opsional kedua fallback OHLCV `.JK` saat Yahoo gagal/429 | kosong (mati) |

Saat Yahoo memblokir IP (429), bot otomatis pakai cache harga terakhir dan
menandai laporan dengan ⚠️ + umur cache — jujur, tidak membual data segar.

## Batasan data & lisensi

- **Yahoo Finance** lewat `yfinance`: API-nya dinyatakan untuk penggunaan pribadi.
  Bot ini alat bantu belajar; untuk layanan komersial atau banyak pengguna, pakai
  penyedia data berlisensi.
- **Daftar emiten** di `data/idx_universe.json` berasal dari data publik BEI.
  Data BEI tidak boleh dipakai untuk tujuan komersial tanpa izin tertulis, dan
  Syarat Penggunaan BEI melarang web scraping/crawling. `tools/build_universe.py`
  mengakses endpoint publik IDX — pakai secukupnya; untuk produk komersial urus
  izin datanya.
- **Jangan menambah scraper** ke Stockbit atau halaman IDX yang melarang
  robot/ekstraksi otomatis.
- Kalau `idx.co.id` memblokir (Cloudflare), bot melaporkan gagal — tidak mengisi
  angka karangan.
- Alternatif non-komersial: repo
  [Dataset-Saham-IDX](https://github.com/wildangunawan/Dataset-Saham-IDX)
  (CC BY-NC 4.0, data milik BEI).

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

Hasil uji 5 tahun pada BBCA/TLKM/ANTM (Nov 2026): **sinyal BUY berdiri sendiri
masih belum profitable** setelah biaya (win-rate 29-32%, PF 0,48-0,64) — meski
kalah lebih aman daripada buy & hold pada BBCA/TLKM yang sedang downtrend.
Pemakaian paling sehat: **pelindung** (skip sinyal buruk, alarm pergerakan),
bukan mesin buy otomatis.

### Uji statistik 45 emiten (2017–2026, horizon 20 hari)

Setelah skor diuji ulang secara cross-sectional + walk-forward
(`research/`, ringkasnya di `/validasi` & `data/validation.json`):

| Metrik | Hasil | Arti |
|---|---|---|
| IC skor antar-emiten | **+0,0115** (`t=2,68` naif, **`t_hac=0,85`**) | praktis nol & tidak signifikan setelah koreksi Newey-West |
| IC skor dalam satu emiten | **−0,0207** (`t=−2,33`) | sedikit berlawanan arah |
| Excess STRONG BUY | **+0,461%** / 20 hari | tipis (menang 46,2%) |
| Excess STRONG SELL | **−0,008%** / 20 hari | label ekstrem bawah tidak informatif |
| Walk-forward 12 faktor (CV ridge) | **+0,0492 / +0,0490 / +0,0206 / −0,0250 / +0,0584** | rata-rata **+0,0304** vs bobot-sama **−0,0061** (4/5 tahun positif) |
| Kuintil OOS Q5 vs Q1 | **+0,400%** vs **−0,204%** | selisih **+0,604%** / 20 hari (dispersi P25–P75 IQR dicatat di riset) |
| Portofolio walk-forward (`hold=0,6`, `min_hold=2`, `sma50_200_half`) | turnover **21,7%** (2-arah **43,4%**) · eksposur **79,8%** · MDD **−20,0%** | alpha aktif bruto **+0,534%** → neto **+0,33%** (**16/20** fase positif, CI **[+0,243%; +0,522%]**, **DSR 0,86**) |

Konsekuensinya di kode:

1. **Skor teknikal tidak lagi dipakai memilih saham.** Di `/analisis` labelnya
   ditampilkan sebagai `trend_label` deskriptif dengan disclaimer eksplisit bahwa
   IC harian ≈ nol (`t_hac = 0,85`).
2. **`/saran` memeringkat kandidat dengan skor relatif** 12 faktor
   (`core/relatif.py`) — bobot dan kuintil OOS disinkronkan persis dengan
   `data/validation.json` (dibekukan dari walk-forward CV, diverifikasi lewat
   `python tools/check_sync.py`), bukan PowerScore. Kalau universe < 8 emiten
   (statistik cross-sectional tak berarti), bot kembali ke PowerScore dan
   mengatakannya.
3. **Tetap tipis.** IC 0,0304 bukan mesin uang (`DSR 0,86 < 0,95` pada 20 fase
   dan 320 trial ablasi `research/experiments.py`); biaya transaksi IDX (**0,40%
   pulang-pergi**: `0,15%` beli + `0,25%` jual all-in) memakan sebagian selisih
   kuintil, sehingga histeresis `hold=0,6` + `min_hold=2` wajib dipakai.
   Perlakukan sebagai pemeringkat kandidat, bukan jaminan.

## 📚 Sumber terbuka yang mendukung bot ini

- [idx-bei](https://github.com/nichsedge/idx-bei) — toolkit BEI resmi (bandarmology, aliran asing, tindakan korporasi) → ide lanjutan
- [saham-fundamental-toolkit](https://github.com/Kuroburns2/SAHAM-ANALISA-saham-analyzer-TP-SL-create) — berita GDELT + analisis laporan resmi IDX
- [sentimeter](https://github.com/snowfluke/sentimeter) — format /saran dengan entry/SL/TP
- [pandas-ta-classic](https://github.com/xgboosted/pandas-ta-classic) & [backtesting.py](https://kernc.github.io/backtesting.py/) — indikator & mesin backtest
- model sentimen: [will702/indo-roBERTa-financial-sentiment-v2](https://huggingface.co/will702/indo-roBERTa-financial-sentiment-v2) (F1 92,8%, MIT)

## 🧪 Pengembangan & tes

> **Agent AI / kontributor baru:** baca [`AGENTS.md`](AGENTS.md) dulu (cara kerja, konvensi `deps`, aturan kejujuran data, status terakhir). Riwayat perubahan: [`CHANGELOG.md`](CHANGELOG.md).

```bash
pip install -r requirements-dev.txt
ruff check .                             # lint
pytest -q                                # 668 tes offline (0 skipped, coverage 83% target CI; tanpa Yahoo/Google News/HF/EODHD)
python tools/check_sync.py               # audit sinkronisasi relatif.py <-> validation.json, LQ45, umur data & backup DB
python tools/record_fixtures.py --verify # audit struktur fixture offline
make coverage                            # tes + gerbang coverage 75% (core, handlers, bot, config)
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

**Fixture & replay.** `tests/fixtures/` menyimpan sampel bentuk kolom yang
dipakai bot (OHLCV ter-normalisasi, ringkasan saham & indeks BEI) supaya
perubahan bentuk data upstream cepat ketahuan. Sampel itu sintetis; untuk
merekam respons asli jalankan sekali di mesin berjaringan:

```bash
python tools/record_fixtures.py --ticker BBCA --ohlcv-rows 120 --rows 5
python tools/record_fixtures.py --verify
```

**CI riset.** `.github/workflows/research.yml` menjalankan evaluasi IC
sampel (12 emiten, 3 tahun) setiap Senin dan **gagal** bila IC rata-rata
< 0 — alarm dini tanpa menunggu ada yang membuka repo. Laporan tetap
diunggah sebagai artefak.

**Lisensi.** Kode repo ini MIT (lihat `LICENSE`); data pasar tetap terikat
ketentuan Yahoo/BEI.

CI GitHub Actions menjalankan lint + seluruh tes offline pada tiap push/PR.
Build Docker bisa dicek lokal: `docker build --build-arg SKIP_MODEL=1 -t saham-bot:slim .`

## ⚠️ Disclaimer

Ini alat bantu edukasi, bukan saran investasi. Selalu DYOR dan pertimbangkan
konsultasi dengan Perencana Keuangan yang berizin.
