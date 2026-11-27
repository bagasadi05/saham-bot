# Rencana Perbaikan Tindak Lanjut Review Eksternal — SahamBuddy (`saham-bot`)

> **Tanggal penyusunan:** 26 November 2026  
> **Baseline repo teraudit:** `HEAD` `c0e45fc` (`bot.py` 1.482 baris, `45` file `core/`, `15` modul `handlers/`, SQLite `SCHEMA_VERSION = 6`; catatan tes terakhir di `PLAN.md`/`AGENTS.md`: `983 passed` / coverage `87,37%`, dan saat audit ada WIP lokal desktop yang belum di-*commit*).  
> **Tujuan dokumen:** menerjemahkan review eksternal menjadi rencana kerja konkret yang sudah **disinkronkan dengan kondisi kode aktual** (memisahkan poin review yang sudah selesai/usang dari celah yang benar-benar masih terbuka) dan diselaraskan dengan temuan audit internal [`docs/PLAN-PERBAIKAN-AUDIT.md`](PLAN-PERBAIKAN-AUDIT.md).

---

## 0. Kalibrasi Review Eksternal vs Fakta Kode Aktual (26 Nov 2026)

Sebelum mengeksekusi perbaikan, setiap kritik pada review eksternal perlu dicocokkan langsung dengan isi repositori saat ini agar tidak mengerjakan ulang hal yang sudah selesai dan langsung membidik akar masalah yang masih terbuka:

| Poin Review Eksternal | Status Aktual di Repo | Bukti Kode & Fakta Nyata | Sisa Celah yang Perlu Dikerjakan |
|---|---|---|---|
| **1. Sumber data rapuh** *(bergantung Yahoo 429 & `idx.co.id` 403; cadangan lemah)* | 🟠 **Masih valid (bottleneck utama)** | [`core/data.py`](../core/data.py) sudah punya cache memori TTL (`SAHAMBOT_TTL=300`), cache disk per `(interval, period)`, retry backoff, serta fallback opt-in `EODHD_API_KEY` & `ALPHAVANTAGE_API_KEY` (dengan cooldown 402/429). Namun screener 944 emiten, [`core/fundamental.py`](../core/fundamental.py), dan agenda masih 100% Yahoo; [`core/bandar.py`](../core/bandar.py) memakai `cloudscraper` yang rawan 403 & terbentur ToS BEI (`F-18`). | Kurangi panggilan Yahoo lewat budgeter/cache persisten fundamental; tambah cadangan harga & fundamental legal; selesaikan keputusan ToS `cloudscraper` (`F-18`). |
| **2. Kualitas prediksi tipis & campur sinyal vs alat bantu** | 🟠 **Valid (inti arah produk)** | [`data/validation.json`](../data/validation.json) (rilis `2026-11-24`, `91.428` baris `LQ45_2026`): IC skor `+0,0125` (`t_hac=0,93`), `ic_timeseries=-0,0182`, walk-forward 12 faktor `+0,0294` (2025 `-0,0243`), Q2 `<` Q1, DSR `0,75 < 0,95`, `embargo_days=0`. Skrip riset `quality_combo.py`, `regime_eval.py`, `train_lgbm.py`, `bias.py` sudah ada, **tetapi** [`data/quality_combo.json`](../data/quality_combo.json) masih kosong (`n_evaluated: 0`) dan uji survivorship 10 thn tertahan limit Yahoo. | Isi `quality_combo.json` + uji gabungan *quality + regime + ranking*; selesaikan audit statistik (`embargo >= 28`, bobot produksi OOS, `bias.py`); pertegas pemisahan UX/copy **"alat analisis & risiko"** vs **"sinyal kandidat tipis"**. |
| **3. Arsitektur monolitik** *(`bot.py` gemuk)* & **`store.json`** | 🟡 **Sebagian basi, masih ada monolit baru** | **`store.json` sudah usang:** repo memakai SQLite (`SCHEMA_VERSION = 6`) terpecah menjadi 5 modul ([`core/store.py`](../core/store.py) 563 baris + `store_portfolio.py` 719 + `store_profile.py` 350 + `store_scan.py` 305 + `store_watchlist.py` 222). `bot.py` juga turun dari >1.800 baris menjadi **1.482 baris**, dan `desktop.py` dipecah ke `desktop_ui/`. | `bot.py` (1.482 baris), [`handlers/menu.py`](../handlers/menu.py) (1.391 baris), dan `store_portfolio.py` (719 baris) masih besar; ada dua cabang UI desktop (`desktop.py` + WIP `desktop_modern.py`/`desktop_web/`); state runtime masih in-memory. |
| **4a. Rate limiting per user & keamanan publik** | 🟢 **Sudah banyak dibangun; perlu tutup celah** | [`bot.py`](../bot.py#L1397-L1407) sudah *fail-closed* (`ALLOWED_USER_IDS` wajib kecuali `ALLOW_PUBLIC=1` / `REQUIRE_ALLOWLIST=0`), memakai `AIORateLimiter(max_retries=2)`, [`core/guards.py`](../core/guards.py) memiliki `cooldown_ok` atomik (`asyncio.Lock`) + `GLOBAL_MAX_HEAVY` semaphore + `_pending_owner`. 11 modul `handlers/` sudah memanggil `cooldown_ok`. | [`handlers/menu.py`](../handlers/menu.py) (`menu_callback`, `menu_text_input`) belum punya pembatas burst tombol/teks sebelum memanggil aksi berat; cooldown hilang saat restart; `defusedxml` belum dipin di `requirements.txt`. |
| **4b. Persistensi proses VM restart** | 🟢 **Sudah aman di Docker VPS** | [`docker-compose.yml`](../docker-compose.yml) memakai `restart: unless-stopped` + volume `./data:/app/data`; [`Dockerfile`](../Dockerfile) punya `HEALTHCHECK` `heartbeat.txt` tiap 5 menit. | Watchdog/notifikasi eksternal jika VM/container mati lama; persistensi/enkripsi backup off-site (`F-22`); state `PENDING`/`STATS`/`JOB_STATUS` masih reset saat restart. |
| **5. Model sentimen ML sering jatuh ke lexicon** | 🟡 **Sudah di-bundle di Docker; ada celah verifikasi & visibilitas** | [`Dockerfile`](../Dockerfile) default (`SKIP_MODEL=0`) membundel `indo-fin-roberta` (~500 MB) ke lokal; [`core/news_ml.py`](../core/news_ml.py) mendukung ONNX Runtime & INT8 dinamis (`SAHAMBOT_ML_QUANTIZE=1`). | [`download_model.py`](../download_model.py#L34-L38) mendefinisikan `verify_sha256()`, **tetapi belum memanggilnya di `fetch()`/`main()`**, serta URL masih `resolve/main`; runtime belum melaporkan rasio ML vs lexicon secara menonjol di `/status` dan `/berita`. |
| **Tambahan Kritis (Tenggat Regulasi)** | 🔴 **Tenggat tetap 31 Des 2026** | [`core/pricing.py`](../core/pricing.py#L31-L37) (`F-24`): jendela `DEFAULT_ARA_END` & `DEFAULT_ARB_ASYM_END` berakhir **31 Desember 2026**. | Wajib verifikasi aturan BEI terbaru sebelum **15 Desember 2026** dan siapkan pembaruan env/kode sebelum tutup tahun. |

---

## 1. Sasaran Strategis Perbaikan

1. **Mengubah fondasi data dari "rapuh terhadap Yahoo 429" menjadi "hemat permintaan + tangguh multi-sumber"** tanpa melanggar ToS BEI/broker.
2. **Menjawab kelemahan *edge* secara ilmiah**: berhenti mengandalkan ranking teknikal murni (IC tipis) dan menguji kombo *Quality Fundamental Filter + 3-Tier Regime + Liquid Universe* dengan metrik OOS tanpa kebocoran.
3. **Memosisikan produk secara jernih**: inti bot adalah **asisten riset, validasi kelayakan, sizing lot, & kontrol risiko portofolio**; fitur sinyal diposisikan eksplisit sebagai penyaring awal berisiko terukur.
4. **Menutup seluruh celah ketahanan publik, *supply-chain* ML, dan tenggat regulasi BEI 2026** sebelum pemakaian lebih luas atau dibuka ke publik.

---

## 2. Rencana Kerja Terstruktur (4 Gelombang)

### Gelombang P1 — Ketahanan Data, Kepatuhan ToS & Operasional Publik *(Prioritas Tertinggi / Jangka Pendek)*

#### R1.1 · Hemat Permintaan Yahoo & Persistensi Cache Fundamental (`core/data.py`, `core/fundamental.py`, `core/screener.py`)
- **Masalah:** Saat ini baru OHLCV yang punya cache disk (`temp/cache/`); data fundamental (`_fund_cache` TTL 6 jam di memori) serta daftar/pengecekan `/scan` memukul Yahoo berulang kali setelah container restart atau saat memindai banyak emiten.
- **Langkah:**
  1. Tambahkan cache disk / tabel SQLite ringan di `core/store_scan.py` (atau file JSON cache di `SAHAMBOT_CACHE`) untuk snapshot fundamental harian agar `/sinyal beli bagus`, `/cek`, `/fundamental`, `/banding`, dan Desktop tidak menembak `.info` & `quarterly_income_stmt` ulang pasca-restart.
  2. Terapkan *rate budgeter* / jeda adaptif saat Yahoo mengembalikan 429 beruntun (circuit breaker jangka pendek + pesan jujur sisa cooldown ke user, bukan retry membabi-buta di setiap handler paralel).
  3. Standarkan jalur *multi-provider* di `core/data.py` (mis. interface penyedia harga `1d` yang rapi untuk EODHD / Alpha Vantage / sumber legal terizin atau impor CSV/Parquet lokal untuk backtest) agar penambahan provider baru tidak menduplikasi parser OHLCV.
- **Kriteria selesai:** restart container tetap meload cache fundamental yang masih segar (provenance `"cache"` + timestamp WIB); saat circuit-breaker Yahoo aktif, handler merespons ramah <1 detik memakai cache disk yang tersedia atau status "tidak tersedia".

#### R1.2 · Keputusan & Penyelarasan ToS BEI / `cloudscraper` (`F-18`: `core/bandar.py`, `tools/build_universe.py`, `handlers/sektor.py`, `handlers/pasar.py`)
- **Masalah:** `cloudscraper` terus memicu Cloudflare 403 di IP datacenter sekaligus bertentangan dengan pernyataan ToS BEI di README & `docs/KENDALA.md`.
- **Langkah:**
  1. Putuskan arsitektur final akses BEI:
     - **Opsi A (Disarankan untuk repo publik):** Hapus ketergantungan scraping aktif Cloudflare (`cloudscraper`), ganti `tools/build_universe.py` menjadi berbasis ekspor resmi/manual CSV/JSON IDX (pola `--from-file` yang sudah ada), dan jadikan `/bandar` / `/sektor` berbasis data terizin atau sektor Yahoo (`data/idx_sectors.json`) + data ringkasan legal.
     - **Opsi B (Jika tetap memakai sumber BEI resmi):** Gunakan feed/API berizin resmi atau jalankan fetcher di luar VPS produksi lalu impor ke SQLite/cache secara aman.
  2. Sinkronkan `README.md`, `requirements.txt`, pesan `/bandar` + `/sektor`, serta tes offline agar konsisten dengan keputusan yang dipilih.
- **Kriteria selesai:** tidak ada kontradiksi antara isi kode, `requirements.txt`, dan kebijakan ToS di dokumentasi.

#### R1.3 · Pengetatan Rate Limit Publik, Concurrency & Keamanan Input (`core/guards.py`, `handlers/menu.py`, `bot.py`, `requirements.txt`)
- **Masalah:** Jika bot suatu saat dijalankan dengan `ALLOW_PUBLIC=1`, pengguna dapat membanjiri tombol inline (`CallbackQuery`) / chat natural beruntun, cooldown in-memory hilang saat restart, dan `defusedxml` belum terpasang di `requirements.txt` (baru berupa `try/except ImportError` di [`core/news.py`](../core/news.py#L12-L15)).
- **Langkah:**
  1. Tambahkan *per-user burst limiter* ringan pada `menu_callback` & `menu_text_input` (mis. batas klik aksi berat per jendela 10 detik) sebelum masuk ke handler jaringan.
  2. Audit dan wajibkan pemakaian `core/guards.global_heavy_semaphore()` pada seluruh pekerjaan berat konkuren (`/scan`, `/saran`, `/paket`, `/banding`, `/analisis` multi-ticker).
  3. Simpan timestamp cooldown perintah berat (`/scan`, `/backtest`, `/saran`) ke SQLite `chat_settings` atau pertimbangkan token bucket persisten jika mode publik dipakai.
  4. Pastikan `ADMIN_IDS` diisi otomatis/wajib diverifikasi saat `ALLOW_PUBLIC=1` supaya `/status` tidak terbuka untuk user acak (`F-16`).
  5. Tambahkan `defusedxml` yang dipin versinya ke `requirements.txt` + `requirements-dev.txt` agar guard `MAX_RSS_BYTES` + parsing XML aman di [`core/news.py`](../core/news.py) selalu aktif.
- **Kriteria selesai:** tes burst callback/teks menolak spam secara atomik; `pip check` bersih; `core.news` terverifikasi memakai `defusedxml` di runtime.

#### R1.4 · Tenggat Regulasi BEI 31 Des 2026 (`F-24`) & Ketahanan Backup/Watchdog (`F-22`)
- **Langkah:**
  1. **Tenggat tetap sebelum 15 Desember 2026:** verifikasi pengumuman terbaru BEI terkait kelanjutan `Kep-00136/BEI/09-2026` (floor Rp1) dan `Kep-00158/BEI/10-2026` (ARB 7% asimetris) yang jendela default-nya berakhir **31 Des 2026** di [`core/pricing.py`](../core/pricing.py#L31-L37). Perbarui konstanta default, contoh uji resmi BEI, dan panduan `.env` (`IDX_ARA_WINDOW_END`, `IDX_ARB_WINDOW_END`).
  2. Tambahkan opsi *watchdog* eksternal ringan di `heartbeat()` (mis. ping opsional ke `HEALTHCHECK_PING_URL` saat sehat) serta skrip/panduan enkripsi & salin backup SQLite ke lokasi luar VPS (`tools/backup_db.py` + `docs/deploy.md`, `F-22`).
- **Kriteria selesai:** aturan harga BEI 2027 siap sebelum 31 Des 2026 tanpa kedaluwarsa mendadak; proses restore terenkripsi teruji.

---

### Gelombang P2 — Peningkatan Edge Statistik & Pemisahan Posisi Produk *(Jangka Menengah)*

#### R2.1 · Buktikan Edge "Quality Filter + Regime + Ranking" (`research/quality_combo.py`, `research/regime_eval.py`, `research/evaluate.py`)
- **Masalah:** Reviewer luar benar bahwa ranking momentum/relatif saja punya IC tipis (`+0,0294` walk-forward, `Q2 < Q1`, 2025 negatif). File [`research/quality_combo.py`](../research/quality_combo.py) sudah ada, tetapi [`data/quality_combo.json`](../data/quality_combo.json) masih `n_evaluated: 0`.
- **Langkah:**
  1. Jalankan & sempurnakan `research/quality_combo.py` pada dataset historis/universe `expanded` + `syariah` (termasuk membandingkan return & drawdown *Q4/Q5 mentah* vs *Q4/Q5 + filter `kualitas.judge` sehat + filter `vol_breadth_3tier`*).
  2. Ubah validasi agar mengevaluasi **kombo berjenjang** (Rezim 3-tier → Saringan Likuiditas & Kualitas → Peringkat Relatif), karena bukti sementara (`regime_eval.json`) sudah menunjukkan rem `vol_breadth_3tier` menekan max drawdown historis dari `-48,57%` ke `-33,16%` (Calmar `0,38` → `0,457`).
  3. Jangan naikkan bobot faktor sembarangan di `core/relatif.py` sebelum lolos uji OOS & gerbang `DSR >= 0,95`.
- **Kriteria selesai:** `data/quality_combo.json` terisi dengan hasil terukur; keputusan promosi/pembekuan bobot berdasar angka OOS neto biaya BEI `0,40%` + slippage.

#### R2.2 · Tutup Utang Metodologi Riset (`F-10` s.d. `F-15` di `docs/PLAN-PERBAIKAN-AUDIT.md`)
- **Langkah:**
  1. **`F-10` Model Produksi = Model Teruji:** evaluasi bobot rata-rata ekspansif + holdout out-of-sample murni yang belum pernah dipakai menentukan bobot.
  2. **`F-13` Embargo & Survivorship PIT:** jalankan `research/evaluate.py` dengan `--embargo >= 28` hari kalender (bukan `embargo_days: 0` seperti di `validation.json` saat ini) serta selesaikan pengukuran `research/bias.py` memakai `lq45_membership_2016-2026.json` dari dataset 10 tahun yang disimpan lokal/Colab (hindari rate-limit VM).
  3. **`F-14` & `F-15`:** laporkan DSR dengan total trial penuh (mencerminkan seluruh ablasi & grid, bukan `20` fase saja), dan lengkapi backtest dengan sensitivitas slippage (`0% / 0,1% / 0,3%`) pada ≥ 20 emiten likuid.
  4. **`F-11` Peringatan Ekstrapolasi Universe:** tandai eksplisit pada `/sinyal beli` & `/scan` bila emiten berada di luar kelas likuiditas/LQ45 teruji (`nilai transaksi < Rp2 miliar/hari` → label "di luar sampel uji LQ45").
- **Kriteria selesai:** `data/validation.json` diperbarui lewat `research/publish.py`, `python tools/check_sync.py` lulus, dan seluruh angka di `/validasi` + `README.md` cocok 100%.

#### R2.3 · Pisahkan Tegas "Alat Analisis & Risiko" vs "Sinyal Trading" (`handlers/`, `core/verdict.py`, `README.md`, Desktop UI)
- **Masalah:** Meski disclaimer statistik sudah ada, kata "BELI BERKUALITAS" / "Sinyal BELI" dapat membuat pemula menganggap bot sebagai generator sinyal otomatis alih-alih alat analisis.
- **Langkah:**
  1. Kelompokkan UX menu (Telegram & Desktop) menjadi dua peran jelas:
     - **Alat Analisis & Kontrol Risiko (Inti):** `/cek`, `/fundamental`, `/rencana`, `/portofolio`, `/jurnal`, `/pasar`.
     - **Penyaring Kandidat Statistik (Sekunder, berdisclaimer tipis):** `/scan`, `/sinyal`, `/saran`, `/paket`.
  2. Pada hasil `/sinyal` & `/saran`, arahkan tombol utama selalu ke **`🛡️ Cek 6 Poin & Risiko`** sebelum tombol eksekusi manual (`exec:buy` hanya muncul setelah user meninjau risiko/rencana, bukan dorongan impulsif).
  3. Perjelas copy di `/validasi` & hasil `/sinyal` untuk menyebut metrik jujur: win-rate per kuintil `< 50%` dan keunggulan bersumber dari rasio *reward-to-risk* + rem risiko, bukan tingkat akurasi tebakan.
- **Kriteria selesai:** alur pemula/expert, Desktop, dan `README.md` konsisten memisahkan fitur analisis risiko vs penyaring kandidat.

---

### Gelombang P3 — Keandalan & Supply-Chain Model Sentimen ML

#### R3.1 · Pin Revisi & Wajib Verifikasi SHA-256 Model (`F-19`: `download_model.py`, `core/news_ml.py`)
- **Masalah:** [`download_model.py`](../download_model.py#L15-L38) sudah memiliki tabel `EXPECTED_SHA256` & `verify_sha256()`, **tetapi fungsi tersebut belum pernah dipanggil** saat `fetch()` selesai atau pada file yang sudah ada di `main()`, serta URL `BASE` masih menunjuk cabang bergerak `/resolve/main`.
- **Langkah:**
  1. Panggil `verify_sha256(out)` di `fetch()` (setelah `os.replace`) dan saat mengecek file existing di `main()`; jika hash tidak cocok, hapus file korup dan gagal keras saat `docker build`.
  2. Pin `BASE` di `download_model.py` dan default `MODEL_REVISION` di `core/news_ml.py` ke commit SHA spesifik Hugging Face (`will702/indo-roBERTa-financial-sentiment-v2`).
  3. Tambahkan opsi memblokir unduhan Hub liar saat runtime produksi (`SAHAMBOT_LOCAL_ONLY=1` default di Docker) agar kontainer produksi selalu memakai model terverifikasi atau fallback lexicon secara eksplisit.
- **Kriteria selesai:** uji offline `tests/test_download_model_range.py` ditambah kasus hash tidak cocok (`verify_sha256 == False` menolak file korup).

#### R3.2 · Transparansi Engine ML vs Lexicon & Jalur Ringan ONNX INT8 (`core/news_ml.py`, `core/news.py`, `handlers/info.py`)
- **Langkah:**
  1. Lacak metrik penggunaan mesin sentimen (`ml_pytorch`, `ml_onnx`, `lexicon_fallback`) agar `/status` menampilkan secara transparan mesin aktif + jumlah fallback lexicon (bukan sekadar "loaded/failed").
  2. Pada `/berita`, `/cek`, dan Desktop UI, tampilkan label singkat `[ML]` atau `[Lexicon fallback]` jika mode ML tidak tersedia.
  3. Sediakan & uji resep build opsional dengan `onnxruntime` / `SAHAMBOT_ML_QUANTIZE=1` di `docs/deploy.md` supaya VPS RAM 1–2 GB tetap bisa memakai indo-roBERTa tanpa jatuh ke lexicon.
- **Kriteria selesai:** `/status` & `/berita` jujur membedakan inferensi ML vs lexicon; tersedia tes unit untuk fallback/pelaporan status.

---

### Gelombang P4 — Kebersihan Arsitektur, Konsolidasi UI & Pemeliharaan

#### R4.1 · Rampingkan Sisa File Gemuk (`bot.py`, `handlers/menu.py`, `core/store_portfolio.py`)
- **Masalah:** Meski `store.py` dan `desktop.py` sudah dipecah, `bot.py` (1.482 baris) masih menampung banyak helper/wrapper lama; `handlers/menu.py` mencapai 1.391 baris; `core/store_portfolio.py` 719 baris.
- **Langkah:**
  1. Pindahkan sisa helper formatting/startup (`check_dividend_reminders`, builder pesan ringkasan scan, `tokens`, `reply_long`, dll.) dari `bot.py` ke `handlers/jobs.py` / `handlers/info.py` / `core/reports.py` **satu domain per commit** sambil menjaga alias kompatibilitas tes di `bot.py` (`AGENTS.md` aturan #6). Target `bot.py` < 700 baris.
  2. Pecah [`handlers/menu.py`](../handlers/menu.py) menjadi modul terfokus (mis. `handlers/menu_pickers.py` + `handlers/menu_actions.py` mendampingi `handlers/menu_guide.py` yang sudah ada).
- **Kriteria selesai:** setiap refactor terpisah per commit; seluruh tes monkeypatch `bot.*` tetap hijau 100%.

#### R4.2 · Konsolidasi Antarmuka Desktop & Cegah Duplikasi Logika (`desktop.py`, `desktop_modern.py`, `desktop_web/`, `core/desktop_helpers.py`)
- **Masalah:** Saat ini terdapat shell Tkinter modular (`desktop.py` + `desktop_ui/`) **sekaligus** WIP baru (`desktop_modern.py` + `desktop_web/`). Memelihara dua UI paralel memperbesar biaya maintenance & risiko *drift* aturan risiko.
- **Langkah:**
  1. Tetapkan satu front-end desktop resmi (atau jadikan keduanya memanggil layanan facade yang sama di `core/desktop_monitor.py` & `core/desktop_helpers.py` tanpa duplikasi perhitungan SL/TP/verdict/sizing).
  2. Pastikan `AGENTS.md`, `.github/workflows/ci.yml`, dan `README.md` mencatat cakupan CI & dukungan desktop aktif secara sinkron.

#### R4.3 · Jaga Sinkronisasi Dokumen, Fixture Asli & Kebersihan Repo (`F-25`, `F-26`)
- **Langkah:**
  1. Rekam fixture nyata non-sensitif sekali di mesin berjaringan sehat (`python tools/record_fixtures.py --ticker BBCA --ohlcv-rows 120 --rows 5`) + pertahankan `python tools/record_fixtures.py --verify` (`F-25`).
  2. Arsipkan dokumen audit/plan historis lama yang sudah selesai ke `docs/` dan konsolidasikan ringkasan `README.md` agar cepat diverifikasi terhadap kode (`F-26`).

---

## 3. Urutan Eksekusi yang Disarankan

| Tahap | Fokus Paket | Estimasi | Output Utama |
|---|---|---|---|
| **Minggu 1** *(Segera)* | **R1.3 + R3.1 + persiapan R1.4** | 1–2 hari | Burst guard `menu_callback`/`text`, pin `defusedxml`, aktifkan `verify_sha256` & commit SHA di `download_model.py`, cek aturan BEI **Des 2026**. |
| **Minggu 2** | **R1.1 + R1.2** | 2–3 hari | Cache disk/SQLite fundamental (tahan restart & kurangi 429), budgeter Yahoo, keputusan resmi & bersih `cloudscraper` / ToS BEI (`F-18`). |
| **Minggu 3** | **R2.1 + R2.3 + R3.2** | 2–4 hari | Isi `data/quality_combo.json`, validasi kombo Quality+Regime, perjelas pemisahan UI *Alat Analisis* vs *Sinyal*, metrik ML vs lexicon di `/status`. |
| **Minggu 4** *(Lanjutan)* | **R2.2 + R1.4 + R4.1–R4.3** | 3–5 hari | Riset embargo≥28h + survivorship PIT + DSR jujur, backup off-site terenkripsi + watchdog, perampingan bertahap `bot.py`/`menu.py` & konsolidasi Desktop UI. |

**Tenggat keras kalender:** verifikasi pembaruan aturan ARA/ARB BEI di [`core/pricing.py`](../core/pricing.py) sebelum **15 Desember 2026** (berakhir 31 Des 2026).

---

## 4. Definisi Selesai (DoD Wajib)

Mengikuti aturan proyek di [`AGENTS.md`](../AGENTS.md):
1. `pytest` lulus penuh **100% offline** (`0 failed`, `0 skipped`, tanpa melemahkan asersi lama) dengan gerbang coverage CI tetap terjaga (`>= 86%` saat ini).
2. `ruff check .` bersih (`0` error/warning), `python tools/check_sync.py` dan `python tools/record_fixtures.py --verify` berstatus `PASS`.
3. Tidak ada perubahan bobot `core/relatif.py` tanpa pembaruan `data/validation.json` yang nyata dijalankan dan lolos `DSR >= 0,95`.
4. Seluruh angka/disclaimer di pesan Telegram, Desktop UI, `README.md`, `CHANGELOG.md`, dan `PLAN.md` konsisten dengan kode yang berjalan.

---

## 5. Log Progres Eksekusi Lokal (26 Nov 2026)

| Paket | Status Eksekusi | Ringkasan Implementasi Kode & Sisa Pekerjaan |
|---|---|---|
| **R1.1** | ✅ **Selesai (jalur inti & offline)** | Cache disk fundamental persisten + fallback stale berstempel waktu ([`core/fundamental.py`](../core/fundamental.py)), `fetch_cached_history` / `fetch_cached_fundamentals` offline-only, serta *circuit-breaker* Yahoo 429 (`YF_COOLDOWN_SECONDS`) agar retry paralel tidak membanjiri API saat rate-limit ([`core/data.py`](../core/data.py)). Provider legal berizin tambahan tetap opsional via `EODHD_API_KEY` / `ALPHAVANTAGE_API_KEY`. |
| **R1.2** | 🟡 **Sebagian (saklar ToS siap)** | Ditambahkan saklar env `IDX_DIRECT_FETCH` (`0/off/false/no`) di [`core/bandar.py`](../core/bandar.py) untuk mematikan scraping langsung Cloudflare `idx.co.id` saat mode kepatuhan ToS diaktifkan; keputusan permanen penghapusan `cloudscraper` / `README.md` mengikuti kebijakan rilis repositori. |
| **R1.3** | ✅ **Selesai (runtime + tes)** *(kecuali `defusedxml` pin online)* | `core/guards.burst_ok()` dipasang di `menu_callback` (limit 30 aksi berat / 10 dtk) & input teks bebas `parse_tanya` (`handlers/menu.py`); `global_heavy_semaphore()` dipasang ke `do_saran`, `do_paket`, `scan_with_guard`, `do_analisis`, `cmd_banding`; `/status` dikunci bila `ALLOW_PUBLIC=1` tanpa `ADMIN_IDS`. Catatan: `core/news.py` memblokir `<!DOCTYPE`/`<!ENTITY` + `MAX_RSS_BYTES`, sementara penambahan `defusedxml` ke `requirements.txt` menunggu lingkungan online (`pip check`). |
| **R1.4** | ✅ **Selesai (peringatan + watchdog + verify)** | `core/pricing.window_expiry_warning()` memperingatkan sebelum & sesudah batas **31 Des 2026** di `/status`; `heartbeat()` mendukung `HEALTHCHECK_PING_URL` fail-soft ([`handlers/jobs.py`](../handlers/jobs.py)); `tools/backup_db.py --verify` memverifikasi `PRAGMA integrity_check`. Verifikasi pengumuman BEI Des 2026 & salinan off-site terenkripsi tetap manual di VPS. |
| **R2.1** | 🟡 **Harness siap offline** *(data live menunggu mesin berjaringan)* | `research/quality_combo.py` kini mendukung `--offline`, membaca konteks `data/regime_eval.json` read-only (`regime_context`), dan memperbaiki `summarize_combo([])` agar bebas `KeyError`. Pengisian angka produksi `data/quality_combo.json` menunggu eksekusi online di mesin non-rate-limit / Colab. |
| **R2.2** | 🟡 **Transparansi kode selesai** *(riset 10-thn butuh jaringan)* | `bot.format_validasi` kini menampakkan peringatan eksplisit saat `embargo_days == 0` (`--embargo >=28`), dan `/sinyal` menandai emiten berlikuiditas tipis `< Rp2 M/hari` (`di luar sampel LQ45`, `F-11`). Audit statistik `evaluate.py --embargo >=28` & `bias.py` membutuhkan data historis berjaringan. |
| **R2.3** | ✅ **Selesai (Telegram UX)** | [`handlers/menu.py`](../handlers/menu.py) (`menu_text("expert")` dan submenu `ide`) memisahkan eksplisit **Alat Analisis & Kontrol Risiko** (`/cek`, `/analisis`, `/rencana`, `/portofolio`, `/jurnal`) dari **Penyaring Kandidat Statistik Tipis** (`/sinyal`, `/saran`, `/paket`) sambil menjaga layar `/start` pemula tetap ringkas. |
| **R3.1** | ✅ **Selesai** | [`download_model.py`](../download_model.py) memanggil `verify_sha256` di `_finalize_file()` & `main()` (hapus file korup + unduh ulang), mendukung pin `SAHAMBOT_MODEL_REVISION`; `core/news_ml.py` & `Dockerfile` mendukung `SAHAMBOT_LOCAL_ONLY=1` untuk memblokir pull Hub liar di produksi. |
| **R3.2** | ✅ **Selesai** | [`core/news_ml.py`](../core/news_ml.py) & [`core/news.py`](../core/news.py) mencatat metrik runtime `usage` (`ml` vs `lexicon`) secara thread-safe dan melaporkannya pada `/status` ([`handlers/info.py`](../handlers/info.py)). |
| **R4.1–R4.3** | ⏸️ **Dijadwalkan bertahap** | Pemecahan lanjutan `handlers/menu.py` & `store_portfolio.py` serta konsolidasi Desktop WIP (`desktop.py` vs `desktop_modern.py`) dipertahankan terpisah agar tidak bercampur dengan WIP lokal yang sedang berjalan. |
