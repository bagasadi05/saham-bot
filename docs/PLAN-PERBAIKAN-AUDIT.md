# Rencana Perbaikan SahamBuddy (`bagasadi05/saham-bot`)

> Hasil audit kode, riset, dan konfigurasi — disusun 24 November 2026.
> Dasar audit: clone repo (125 commit), membaca `core/`, `handlers/`, `research/`, `bot.py`,
> `config.py`, Docker, serta menjalankan `pytest` (lulus) dan `ruff` (bersih).
> Angka pada bagian "Bukti" dihasilkan dengan menjalankan fungsi repo secara langsung
> (data sintetis untuk uji VaR).

## 0. Cara membaca dokumen ini

**Prioritas**

| Kode | Arti |
|------|------|
| **P0** | Angka uang/risiko yang ditampilkan salah atau menyesatkan. Kerjakan dulu. |
| **P1** | Klaim riset/keamanan yang lemah atau default berbahaya. |
| **P2** | Inkonsistensi dan ketahanan operasional. |
| **P3** | Kebersihan dan pemeliharaan. |

**Estimasi usaha:** S (< 0,5 hari) · M (0,5–2 hari) · L (> 2 hari).

## 1. Ringkasan eksekutif

- Fondasi baik: tes lulus, lint bersih, tidak ada secret di 125 commit, SQL ter-parameter,
  allowlist ditegakkan juga pada tombol dan teks bebas, metodologi riset memakai
  purged split + CV di dalam train + HAC t-stat.
- Masalah terbesar ada di **angka yang dipakai untuk keputusan uang**:
  ukuran posisi `/saran` tanpa batas, VaR yang terlalu kecil, dan klaim "lolos uji OOS"
  yang lebih lemah dari kesan README.
- Ada **satu tenggat tetap**: jendela aturan ARA/ARB di `core/pricing.py` berakhir
  **31 Desember 2026**.

## 2. Daftar temuan

| ID | Temuan | Prioritas | File utama | Usaha |
|----|--------|-----------|------------|-------|
| F-01 | `/saran` & paper trading tanpa batas ukuran posisi | P0 | `core/saran.py` | M |
| F-02 | VaR terlalu kecil bila ada posisi berriwayat pendek | P0 | `core/risk.py` | M |
| F-03 | "Pelanggaran VaR" tautologis (bukan backtest) | P1 | `core/risk.py` | M |
| F-04 | VaR/ES dihitung dari 20 hari; parametrik normal | P1 | `core/risk.py` | S |
| F-05 | Masa tahan paper 40 hari vs klaim/backtest 20 hari | P1 | `core/saran.py` | S |
| F-06 | Exit SL di harga SL walau harga gap (optimistis) | P1 | `core/saran.py` | M |
| F-07 | `update_paper` hanya cek bar terakhir | P2 | `core/saran.py`, `handlers/jobs.py` | M |
| F-08 | Fallback senyap entry `fill_pending` | P1 | `core/saran.py` | S |
| F-09 | Batas posisi berbeda antar modul (25% vs 20%) | P2 | `core/risk.py`, `core/rencana.py` | S |
| F-10 | Model yang diuji ≠ model produksi (bobot rata-rata) | P1 | `core/relatif.py`, `research/` | L |
| F-11 | Bobot dilatih di 45 emiten, dipakai ke ±944 emiten | P1 | `core/screener.py`, `handlers/sinyal.py` | M–L |
| F-12 | Kuintil tidak monoton, win-rate < 50%, fold 2025 negatif | P1 | `data/validation.json`, README | S |
| F-13 | Survivorship belum diukur; embargo = 0; loader `bias.py` rusak | P1 | `research/bias.py` | M |
| F-14 | DSR dihitung dengan `n_trials` = 20 (320 trial ablasi) | P1 | `research/experiments.py`, `portfolio.py` | S–M |
| F-15 | Backtest tanpa spread/slippage; sampel 3 emiten | P1 | `backtest_real.py` | M |
| F-16 | Default bot publik; `/status` terbuka | P1 | `bot.py`, Docker, `.env.example` | S |
| F-17 | Cooldown tidak merata, in-memory, per-user saja | P1 | `core/guards.py`, `handlers/*` | M |
| F-18 | Dependensi `cloudscraper` bertentangan dengan ToS BEI | P1 | `core/bandar.py`, `tools/build_universe.py` | S + keputusan |
| F-19 | Unduhan model tanpa pin commit & checksum | P1 | `download_model.py`, `core/news_ml.py` | S |
| F-20 | Parsing RSS tanpa `defusedxml` dan tanpa batas ukuran | P2 | `core/news.py` | S |
| F-21 | 154 `except Exception`; beberapa menelan error | P2 | `core/saran.py`, `screener.py`, `jurnal.py`, `cek.py`, `rencana.py`, `data.py` | M |
| F-22 | Backup SQLite satu volume, tidak terenkripsi | P2 | `tools/backup_db.py`, compose | S |
| F-23 | `normalize_ticker` memakai `isalnum()` (menerima Unicode) | P3 | `core/data.py` | S |
| F-24 | Aturan regulasi di-hardcode, tidak bisa diverifikasi dari kode | P1 | `core/pricing.py` | S + tenggat |
| F-25 | Fixture tes sintetis; perubahan format upstream tak tertangkap | P2 | `tests/fixtures/` | M |
| F-26 | `bot.py` 1.463 baris; README sangat padat | P3 | `bot.py`, `README.md` | L |

---

## 3. Detail temuan dan perbaikan

### Fase 1 — Angka uang dan risiko (P0/P1)

#### F-01 · Ukuran posisi `/saran` tanpa batas (P0)

- **Masalah:** `position_size()` dipanggil di `core/saran.py` (baris ±110, ±220, ±367)
  tanpa `max_pos_pct`, sehingga default 100%. `/rencana` dan `/paket` memakai batas 20%.
- **Bukti** (modal Rp10 juta):

  | Kasus | `/saran` | `/rencana` (batas 20%) |
  |-------|----------|------------------------|
  | Harga 9.000, ATR 1,5% | 7 lot = **63%** modal | 2 lot = 18% |
  | Harga 4.000, ATR 1% | 25 lot = **100%** modal | — |

  Dengan `MAX_OPEN = 5`, total posisi paper dapat melebihi modal.
- **Dampak:** rekomendasi lot tidak konsisten antar perintah; rekap "rata-rata hasil net"
  di `/saran list` tidak realistis dan tidak sebanding dengan `/paket`.
- **Perbaikan:**
  1. Pindahkan konstanta batas (`MAX_POS_PCT`, `MAX_SECTOR_PCT`, total eksposur) ke satu
     tempat (`config.py`) agar tidak terjadi impor melingkar `rencana` ↔ `saran`.
  2. Kirim `max_pos_pct = MAX_POS_PCT * risk_scale` di ketiga pemanggil.
  3. Tambah batas **total eksposur** (selaras `/paket`: maksimal 90% modal; 45% saat IHSG defensif)
     saat memilih kandidat dan saat mengisi posisi pending.
- **Tes / kriteria selesai:**
  - Untuk ATR 1% dari harga, nilai posisi ≤ 20% modal.
  - Jumlah nilai posisi terbuka ≤ batas total eksposur untuk 5 posisi.
  - Lot `/saran` untuk kandidat yang sama sama dengan `/rencana`.

#### F-02 · VaR terlalu kecil bila ada posisi berriwayat pendek (P0)

- **Masalah:** di `portfolio_risk()`, bobot dihitung dari **total semua posisi**, tetapi
  volatilitas hanya dari posisi yang riwayatnya cukup, tanpa normalisasi ulang. Jumlah bobot
  < 1, jadi VaR terkecilkan.
- **Bukti:** dua saham dengan VaR parametrik 2,41%; menambah satu saham baru
  (sepertiga nilai portofolio, riwayat 10 hari) **menurunkan** VaR jadi 1,61%
  (Rp482 ribu, seharusnya ±Rp720 ribu).
- **Perbaikan:**
  1. Hitung vol/VaR pada subset berriwayat dengan bobot dinormalisasi ulang.
  2. Skalakan nilai Rp memakai nilai subset itu, dan tampilkan **cakupan** (mis. "VaR mencakup 67% nilai").
  3. Jika cakupan < 80%, tampilkan "tidak tersedia" alih-alih angka.
- **Tes:** menambah posisi berriwayat pendek tidak boleh menurunkan VaR% subset;
  cakupan dilaporkan; cakupan < 80% menghasilkan `None` + catatan.

#### F-03 · "Pelanggaran VaR" tautologis (P1)

- **Masalah:** `var_breaches` menghitung hari ≤ kuantil 5% dari data yang sama, jadi hasilnya
  selalu ≈ 5% (uji: 13 dari 250, harapan 12,5). Ini bukan backtest VaR.
- **Perbaikan:** ganti dengan backtest out-of-sample: untuk tiap hari t ≥ 120, VaR dihitung dari
  jendela sebelum t, bandingkan dengan return hari t. Laporkan jumlah pelanggaran dan uji
  Kupiec (POF). Jika data < 250 hari, tampilkan "tidak tersedia". Alternatif minimal: hapus label itu.
- **Tes:** pada data i.i.d. sintetis, tingkat pelanggaran mendekati 5% dengan p-value Kupiec wajar;
  pada seri dengan perubahan rezim volatilitas, pelanggaran terdeteksi lebih banyak.

#### F-04 · VaR/ES dari 20 hari; asumsi normal (P1)

- **Masalah:** `MIN_DAYS = 20` berlaku juga untuk VaR/ES historis. Uji: ES dihitung dari **1 observasi**.
  VaR parametrik memakai 1,645σ (normal) sehingga meremehkan ekor tebal dan gap ARB.
- **Perbaikan:**
  1. Pisahkan `MIN_DAYS_VOL = 60` (vol/korelasi/beta) dan `MIN_DAYS_VAR = 120` (VaR/ES historis).
  2. Syaratkan minimal ±5 observasi di ekor untuk ES.
  3. Label VaR parametrik sebagai "asumsi normal" dan tampilkan VaR historis berdampingan.
- **Tes:** < 120 hari → VaR/ES `None` dengan catatan; ES tidak pernah dari < 5 observasi.

### Fase 2 — Konsistensi paper trading (P1/P2)

#### F-05 · Masa tahan 40 hari vs 20 hari (P1)

- **Masalah:** README dan backtest memakai 20 hari bursa, sementara `_expired()` memakai
  `MAX_HOLD_DAYS × MIN_HOLD_CYCLES` = **40 hari**.
- **Perbaikan:** putuskan satu aturan.
  Rekomendasi: timeout paper = 20 hari (sesuai bukti backtest); `MIN_HOLD_CYCLES` hanya untuk
  logika band tahan/rotasi. Jika 40 hari dipertahankan, jalankan ulang backtest pada 40 hari.
  Perbarui README, pesan bot, `time_stop_status`, dan jurnal.
- **Tes:** posisi ditutup `timeout` tepat pada batas yang dipilih; teks README = konstanta di kode.

#### F-06 · Exit SL tepat di harga SL meski gap (P1)

- **Masalah:** `update_paper()` menutup di `sl` walau bar membuka di bawah SL (gap). Hasil paper optimistis.
- **Perbaikan:** sertakan `open` di `_ohlc`; bila `open <= sl`, tutup di `open`
  (dan tidak lebih tinggi dari harga yang mungkin untuk batas ARB). Catat `hit="sl_gap"`.
  TP tetap di `tp` (konservatif).
- **Tes:** bar dengan open < SL → exit di open; bar tanpa gap → exit di SL; SL dan TP tersentuh dalam satu bar → SL.

#### F-07 · Hanya bar terakhir yang diperiksa (P2)

- **Masalah:** bila bot mati beberapa hari, SL/TP yang tersentuh pada hari sebelumnya terlewat.
- **Perbaikan:** teruskan DataFrame bar sejak `filled_at`/pengecekan terakhir dan telusuri bar demi bar
  (urut tanggal) hingga kejadian pertama. Simpan `last_checked` per posisi.
- **Tes:** SL tersentuh di hari ke-2 lalu harga pulih di hari ke-3 → posisi tertutup di hari ke-2.

#### F-08 · Fallback senyap entry (P1)

- **Masalah:** `fill_pending()` — jika `df["open"].loc[bar]` gagal, entry diganti open bar **terakhir**.
- **Perbaikan:** hapus fallback; `log.warning(..., exc_info=True)` dan `continue` agar posisi tetap pending.
- **Tes:** indeks tidak cocok → posisi tetap `pending`, ada log.

#### F-09 · Batas posisi berbeda antar modul (P2)

- **Masalah:** `risk.py` memakai 25%/40%; `rencana.py` memakai `PLAN_MAX_POS_PCT` 20%.
- **Perbaikan:** satu sumber di `config.py` (digabung dengan F-01); `/portofolio batas` meng-override per chat.

### Fase 3 — Validitas riset dan klaim (P1)

#### F-10 · Model yang diuji ≠ model produksi (P1)

- **Masalah:** metrik OOS berasal dari model yang di-refit per tahun uji. Produksi memakai
  `WEIGHTS` rata-rata fold — model yang tidak pernah dievaluasi OOS sebagai satu kesatuan,
  dan rata-ratanya memuat informasi dari fold yang lebih baru.
- **Perbaikan:**
  1. Evaluasi "bobot rata-rata ekspansif" secara forward-only: bobot tahun uji *y* = rata-rata fold < *y*.
  2. Sisihkan holdout terakhir (±12 bulan) yang tidak dipakai menentukan bobot; laporkan IC/kuintil di sana.
  3. Update `data/validation.json` lewat `research/publish.py`; jalankan `tools/check_sync.py`.
- **Kriteria selesai:** README menyatakan angka OOS berasal dari bobot yang benar-benar dipakai produksi.

#### F-11 · Transfer universe 45 → ±944 emiten (P1)

- **Masalah:** bobot dilatih pada LQ45 tetapi `/scan`/`/sinyal` memeringkat seluruh pasar,
  termasuk saham kecil/tidak likuid. Tercatat "di luar cakupan" di `PLAN.md`, tetapi tidak ada peringatan di UI.
- **Perbaikan (pilih salah satu atau keduanya):**
  - Jangka pendek: batasi `/sinyal beli` ke emiten likuid (≥ Rp2 miliar/hari, sama seperti `/paket`)
    dan beri label "belum diuji" untuk sisanya.
  - Jangka menengah: evaluasi ulang pada universe lebih luas dengan filter likuiditas.
- **Tes:** emiten di bawah batas likuiditas tidak muncul sebagai "BELI" dengan klaim peringkat tervalidasi.

#### F-12 · Klaim "lolos uji OOS" terlalu kuat (P1)

- **Bukti** (`core/relatif.py`): kuintil Q1–Q5 = −0,204 / **−0,391** / −0,100 / +0,292 / +0,400 (%/20 hari);
  win-rate 44,0 / 43,3 / 44,9 / 47,2 / 47,2 (semua < 50%); IC fold 2025 = −0,025.
- **Perbaikan:**
  1. Tambah uji monotonisitas (korelasi peringkat rata-rata kuintil) beserta CI bootstrap.
  2. Ubah teks dari "satu-satunya bagian skor yang lolos uji OOS" menjadi "tipis, tidak monoton,
     belum terbukti setelah biaya dan slippage".
  3. Tampilkan CI pada `/validasi`.

#### F-13 · Survivorship, embargo, dan loader `bias.py` (P1)

- **Masalah:** `validation.json` mencatat `universe: "LQ45_2026"` dan `embargo_days: 0`;
  angka survivorship belum ada. `research/lq45_membership_SOURCES.md` mengakui loader
  menolak `end: null`.
- **Perbaikan:**
  1. Perbaiki loader (`end` boleh `null` = masih anggota).
  2. Jalankan `python research/bias.py --membership research/lq45_membership_2016-2026.json --run`.
  3. Simpan selisih IC statis vs point-in-time di `validation.json`.
  4. Jalankan ulang walk-forward dengan `--embargo` ≥ 28 hari kalender (≈ horizon 20 hari bursa).
- **Catatan:** emiten delisted yang tidak ada di Yahoo harus dilaporkan eksplisit.

#### F-14 · DSR dengan jumlah trial yang tepat (P1)

- **Masalah:** `deflated_sharpe.n_trials = 20` di `validation.json`, sementara README menyebut 320 trial ablasi.
- **Perbaikan:** hitung ulang DSR dengan seluruh percobaan yang benar-benar dicoba (320 + 20 fase, atau
  tentukan definisi trial yang konsisten), laporkan nilainya dan asumsi korelasi antar-trial.

#### F-15 · Realisme backtest (P1)

- **Masalah:** hanya komisi tetap (0,15%/0,25%); entry di open berikutnya; SL/TP dari OHLC; tidak ada
  spread/slippage; hasil simulasi dilaporkan untuk BBCA/TLKM/ANTM saja.
- **Perbaikan:**
  1. Tambah model spread/slippage (mis. 1 tick per sisi, atau persentase per kelas likuiditas) dan
     tampilkan **tabel sensitivitas** (0% / 0,1% / 0,3%).
  2. Pakai exit di open saat gap (selaras F-06) dan batas ARB.
  3. Perluas sampel menjadi ≥ 20 emiten likuid dan laporkan distribusi (bukan satu angka rata-rata).

### Fase 4 — Keamanan dan operasional

#### F-16 · Default tidak aman (P1)

- **Masalah:** `ALLOWED_USER_IDS` kosong = publik; `REQUIRE_ALLOWLIST` default mati; `ADMIN_IDS` kosong = semua orang bisa `/status`.
- **Perbaikan:**
  1. Fail-closed: bot menolak start tanpa allowlist kecuali `ALLOW_PUBLIC=1`.
  2. `ADMIN_IDS` kosong → hanya `ALLOWED_USER_IDS` yang boleh `/status`.
  3. Atur `REQUIRE_ALLOWLIST=1` di compose; aktifkan di `.env.example`.
- **Tes:** start tanpa allowlist gagal dengan pesan jelas; `/status` ditolak untuk non-admin.

#### F-17 · Cooldown tidak merata (P1)

- **Masalah:** `cooldown_ok` hanya ada di sebagian command. `/berita` (Google News + model ML di CPU),
  `/fundamental`, `/skor`, `/agenda`, `/jejak`, `/beli`, `/jual` tidak dibatasi. Cooldown in-memory, per-user.
- **Perbaikan:**
  1. Tambah cooldown untuk command yang belum.
  2. Tambah batas global (semaphore/token bucket) untuk pekerjaan berat dan panggilan Yahoo.
  3. Opsional: simpan status cooldown agar tidak hilang saat restart.
- **Tes:** pemanggilan beruntun ditolak; banyak user bersamaan tidak melampaui batas global.

#### F-18 · `cloudscraper` dan ToS BEI (P1, perlu keputusan)

- **Masalah:** `requirements.txt` memuat `cloudscraper` (menembus Cloudflare di idx.co.id) untuk `/bandar`
  dan `tools/build_universe.py`, padahal README dan `KENDALA.md` menyatakan ToS BEI melarang scraping.
- **Keputusan yang diperlukan:**
  - **Opsi A:** hapus `/bandar`, `cloudscraper`, dan akses langsung IDX; bangun universe dari berkas yang diunduh manual.
  - **Opsi B:** urus izin tertulis/lisensi data BEI, lalu dokumentasikan.
- **Perbaikan setelah keputusan:** sinkronkan README, `requirements.txt`, dan tes.

#### F-19 · Rantai pasok model (P1)

- **Masalah:** `download_model.py` memakai `.../resolve/main/...` (cabang bergerak), tanpa pin commit dan
  tanpa checksum; `core/news_ml.py:127-129` dapat mengunduh dari Hub saat runtime tanpa `revision=`;
  `research/evaluate_kronos.py` juga tidak mem-pin revisi.
- **Perbaikan:** pin commit hash di URL dan `revision=`; verifikasi SHA-256 tiap berkas
  (simpan daftar hash di repo); hapus atau pin fallback runtime ke Hub.
- **Sudah baik:** format `safetensors`, `local_files_only=True`, tanpa `trust_remote_code`.

#### F-20 · Parsing RSS (P2)

- **Masalah:** `core/news.py` memakai `xml.etree.ElementTree` dan `resp.read()` tanpa batas ukuran.
- **Perbaikan:** `defusedxml`, batasi baca (mis. 2 MB), pertahankan timeout.

#### F-21 · Error yang ditelan (P2)

- **Masalah:** 154 `except Exception`; beberapa langsung `pass`/`return None`
  (`saran.py`, `screener.py`, `jurnal.py`, `cek.py`, `rencana.py`, `data.py`); kegagalan tulis cache diabaikan.
- **Perbaikan:** ganti dengan `log.warning(..., exc_info=True)`; bedakan "data tidak ada" dari "bug";
  hitung kegagalan per modul di `/status`; log kegagalan cache (dibatasi laju).

#### F-22 · Backup (P2)

- **Perbaikan:** salin backup ke lokasi lain dengan enkripsi (mis. `age`/`gpg`), uji pemulihan berkala,
  dokumentasikan di `deploy.md`.

#### F-23 · Validasi kode saham (P3)

- **Perbaikan:** `symbol.isascii() and symbol.isalnum()` di `normalize_ticker`, tambahkan batas panjang.

#### F-24 · Aturan regulasi di-hardcode (P1, ada tenggat)

- **Masalah:** `core/pricing.py` memuat Kep-00136/BEI/09-2026, Kep-00158/BEI/10-2026, ARA 35/25/20%,
  ARB 7%, floor Rp1. Auditor tidak dapat memverifikasi isi aturan ini dari repo. Kesalahan membaca aturan
  berarti level order yang diketik pengguna di broker salah. Jendela berakhir **31 Des 2026**.
- **Perbaikan:**
  1. Cantumkan tautan sumber resmi dan "terakhir diverifikasi" di docstring.
  2. Tambah tes berbasis contoh resmi BEI.
  3. **Verifikasi pengumuman BEI terbaru paling lambat 15 Des 2026**; perbarui `IDX_ARA_WINDOW_END`/`IDX_ARB_WINDOW_END`
     atau aturan pasca-jendela.
  4. Pastikan setiap output yang memakai `auto_reject_pct` menampilkan catatan kedaluwarsa.
- **Sudah baik:** `auto_reject_status()` melaporkan status kedaluwarsa secara eksplisit; tick size
  (1/2/5/10/25) sesuai aturan fraksi.

#### F-25 · Fixture tes (P2)

- **Perbaikan:** rekam fixture nyata (`tools/record_fixtures.py`), tambah tes kontrak untuk bentuk data
  Yahoo/BEI, dan job CI mingguan non-blocking yang memeriksa skema upstream.

#### F-26 · Pemeliharaan (P3)

- Pecah `bot.py` (1.463 baris) dan konsolidasikan README (sangat padat, sulit diverifikasi terhadap kode).
- Kurangi dokumen meta (`PROMPT-PERBAIKAN-BOT.md`, `REVIEW*.md`) atau arsipkan ke `docs/`.

---

## 4. Urutan kerja yang disarankan

| Minggu | Isi | Output |
|--------|-----|--------|
| **1** (24–30 Nov) | F-01, F-02, F-08, F-16 | Angka posisi/VaR benar; default aman |
| **2** (1–7 Des) | F-03, F-04, F-05, F-06, F-09, F-19, F-20, F-23 | Paper trading konsisten; model dipin |
| **3** (8–14 Des) | F-07, F-17, F-21, F-12 (teks), **F-24 verifikasi aturan** | Ketahanan; klaim dijujurkan |
| **4** (15–21 Des) | F-18 (keputusan), F-22, F-25 | Kepatuhan data; backup; fixture nyata |
| **Setelah itu** | F-10, F-11, F-13, F-14, F-15 | Riset ulang dan laporan validasi baru |

Tenggat tetap: **F-24 sebelum 31 Des 2026**.

## 5. Definisi selesai (berlaku untuk semua)

- `pytest -q` lulus dan gerbang coverage 86% tetap terpenuhi; `ruff check .` bersih.
- Setiap perbaikan angka punya tes yang gagal sebelum perbaikan dan lulus sesudahnya.
- `python tools/check_sync.py` lulus setelah perubahan riset/bobot.
- README, `CHANGELOG.md`, dan pesan bot menyatakan hal yang sama dengan konstanta di kode.

## 6. Cakupan dan keterbatasan audit

**Belum diperiksa mendalam:** `handlers/jobs.py` (penjadwal), isi penuh `core/cek.py` dan `core/rencana.py`,
tokenisasi/inferensi sentimen, dan analisis taint penuh untuk escape HTML (pemeriksaan dilakukan lewat
pembacaan kode dan pencarian pola). Uji VaR memakai data sintetis, bukan data pasar nyata. Isi aturan BEI
bertanggal September–Oktober 2026 tidak dapat saya verifikasi.

**Koreksi atas review awal:** allowlist tidak bocor pada tombol/teks; jendela ARA/ARB tidak kedaluwarsa
secara diam-diam; survivorship sudah ditangani sebagian (data keanggotaan ada, hasil belum dipublikasikan).

> Dokumen ini bukan saran investasi; ia hanya mencakup kualitas teknis dan metodologi perangkat lunak.
