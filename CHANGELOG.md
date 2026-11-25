# Changelog — SahamBuddy

Semua perubahan penting pada repo ini. Format mengikuti [Keep a Changelog](https://keepachangelog.com/id/1.1.0/);
repo belum memakai nomor rilis, jadi entri dikelompokkan per tanggal kerja.

Baseline sebelum rangkaian ini: commit `b11fcd2` (PLAN.md awal, 277 tes).
Rangkaian di bawah: **25 commit**, `e93e2d0` … `202ec2b` (2026-11-12).

---

## [Unreleased] — 2026-11-25

- **Desktop App (`desktop.py`, `core/desktop_monitor.py`) — Perbaikan bug produksi & peningkatan UI/UX 8 tab:**
  - Memperbaiki rendering grafik Cek Emiten yang sebelumnya memanggil `chartmod.render_chart` yang tidak ada menjadi `chartmod.build_chart` dengan penanda level `entry`/`sl`/`tp`, pratinjau `PIL.ImageTk` responsif, pemilih rentang `3mo/6mo/1y/2y`, dan ekspor PNG.
  - Memperbaiki pemanggilan modul fundamental menjadi `fetch_fundamentals`/`score_fundamentals` (dengan fallback kompatibel untuk mock uji lama), meneruskan `"price"` & `"sector"` yang benar ke `rencana.budget_plan`, membaca `buy_cost`/`value` yang valid, menambahkan `bandar.call_with_stale` + aliran asing BEI pada panel Pasar, serta menyambungkan `bars_by_ticker` & `bench_close` untuk statistik MAE/MFE & Alpha vs IHSG di tab Jurnal.
  - Memperbarui antarmuka terminal gelap (`THEME`), angka monospace tabular (`Consolas`), profil persisten (`"desktop"` / `chat_id` Telegram), tombol `⚡ Scan BEI (~944)` di Sinyal Pasar, filter Syariah (ISSI) persisten di Watchlist/Sinyal/Paket, dialog alert cepat (`ALL_KINDS`), rotasi sektor IDX-IC, KPI & batas risiko kustom + VaR95/ES95 di Portofolio, pencatat eksekusi Stockbit (`record_desktop_execution`), kolom terurut klik-header (`▲`/`▼`), menu klik kanan, pintasan papan ketik, ekspor CSV terproteksi formula, tiket order Stockbit (`Rp` bertitik), notifikasi `NotifyIcon` WinForms & HiDPI, serta tes offline baru di `tests/test_desktop_features.py`.
  - Memperbaiki bug runtime lanjutan pada `desktop.py`: pemanggilan `fetch_market_pulse(include_idx=True, include_news=True)`, format skor float berita (`news["pct"]`), ekstraksi sentimen `_news_item_tag`, pratinjau grafik PNG cek emiten saat panel belum terukur, antrean klik asinkron agar pilihan emiten tidak terbuang saat sibuk, tampilan veto verdict, format `None` di Jurnal diagnostics & portofolio tanpa harga, serta lebar label `Switch` pada skala DPI tinggi.

- **Perbaikan lanjutan hasil audit menyeluruh (`docs/audit_sahambot.md`)**:
  - **Presisi finansial & mikrostruktur BEI**:
    - Teruskan `unit=roe_unit` ke `fundamental.normalize_roe` di `core/rencana.py`, `core/banding.py`, dan `handlers/info.py` (cegah skala ganda 100×).
    - Bulatkan level `entry_pullback`, `sl`, `tp`, `chase_above` (`core/rencana.py`) dan `tp1` (`core/saran.py:scale_out_plan`) sesuai pita tick masing-masing; perbaiki `arb_floor` untuk rentang harga Rp11–14 pada `saran.arb_gap_risk`.
    - Terapkan penalti forensik `utang_gt_2x_kas` (`+15`) saat `total_cash <= 0 and total_debt > 0` (`core/fundamental.py`), selaraskan `core/portofolio.atr14` ke Wilder RMA, hindari label `-Rp0M`/`Rp0M` pada nominal `< Rp1 miliar` (`core/reports.py:fmt_compact_amount`), perpanjang deteksi volume intraday belum lengkap hingga `16:00 WIB` (`core/analysis.py`), saring DataFrame `ind` kosong (`core/relatif.py`), dan abaikan pasangan korelasi `NaN` (`core/risk.py`).
  - **Handler Telegram, job terjadwal & keamanan**:
    - Hilangkan double-escape HTML `&lt;i&gt;` pada `quality_note` melalui `core/data_quality.plain_note` di `core/verdict.py`, `handlers/cek.py`, `handlers/market.py`, dan `handlers/sinyal.py`.
    - Sambungkan kembali `@job_guard` pada `backup_job` dan `full_market_scan_job` (`handlers/jobs.py`), serta saring `get_auto_chats()` terhadap `ALLOWED_IDS` saat allowlist aktif.
    - Aktifkan pengambilan `bars_by_ticker` untuk MAE/MFE pada `/jurnal` (via `core/data.fetch_cached_history` agar bebas lonjakan jaringan) dan `weekly_journal_report`, serta teruskan `market_regime` + guard `atr` di `handlers/trading.do_tanya`.
    - Terapkan `cooldown_ok` pada `do_berita`, `do_fundamental`, `cmd_agenda`, `cmd_jejak`, `do_skor`, `do_tanya`, `do_watch_list`; batasi jumlah emiten/kondisi `/alert` + gunakan `reply_long`.
    - Tambah isolasi pemilik `PENDING` (`_pending_owner`), guard `effective_user is None`, filter `UpdateType.MESSAGE` pada `MessageHandler`, dan pembersihan `PENDING` otomatis saat perintah `/` dipanggil (`bot.py`, `core/guards.py`, `handlers/menu.py`).
  - **Cache, konkurensi, sentimen, desktop & riset**:
    - Invalidsi cache `screener.scan` pra-16:00 WIB saat dipanggil sesudah 16:00 WIB pada hari yang sama; tambahkan `bandar.call_with_stale` lintas `asyncio.to_thread` untuk `/sektor` dan `/pasar` + batas ukuran `bandar._cache`.
    - Cegah false-positive `"ara"` pada nama tokoh di `core/news.py` (wajib `\bARA\b` kapital / `"auto reject atas"`), hapus `"ekuitas"` dari `_OTHER_METRICS`, jaga `.get("positive")`/`.get("negative")`, dan baca `config.json` `id2label` pada inferensi ONNX (`core/news_ml.py`).
    - Amankan migrasi `store.json -> SQLite` agar rename hanya terjadi sesudah `COMMIT`, tambah guard atomik `rowcount == 1` pada `_reduce_real_position_conn`, lock `_yf_dl_lock` + batas cache memori di `core/data.py`, parsing aman `.env` di `run.sh`, User-Agent netral di `tools/build_sectors.py`, import opsional `pystray` di `desktop.py`, serta `nlags=max(1, h // 2)` (`research/horizons.py`) dan `ddof=1`/clip `[-3, 3]` (`research/evaluate_sector.py`).
    - Tambah tes regresi offline di `tests/test_audit_fixes_regression.py`.

## [Unreleased] — 2026-11-24

- **Filter anti "falling knife" di `/rencana` & `/paket`** (dari kasus BRIS: death cross + dead-cat bounce lolos entry):
  - `core/rencana.py:enrich()` kini mengekstrak `sma20/sma50/sma200/ret5d` dari snapshot dan menambah:
    - **Death cross**: `close < SMA50` AND `SMA50 < SMA200` → **veto** untuk `/paket` (`require_rel=True`), **warning keras + penalti grade −2** untuk `/rencana <KODE>` (user memang sengaja minta saham itu).
    - **Dead-cat bounce**: `ret20d < −10%` AND `ret5d > +8%` → warning "pantulan tajam dalam downtrend — tunggu pullback" + penalti grade −1.
    - **RSI lemah**: `RSI < 40` → warning baru (sebelumnya hanya RSI > 70 yang diingatkan).
    - `harga masih di bawah MA50` (tanpa death cross) → warning ringan + penalti grade −1.
  - `core/rencana.py:prefilter()` kini **menolak** kandidat dengan death cross (`harga < MA50` & `MA50 < MA200`) — tidak masuk pool `/paket` sama sekali.
  - `core/rencana.py:grade()` menurunkan grade bila ada warning downtrend/bounce (grade A butuh tanpa `trend_gap`).
  - `core/rencana.py:price_levels()` menambah field **`entry_pullback`**: bila harga >2% di atas SMA20 (pantulan), saran entry di level MA20 (round-tick), bukan mengejar harga saat ini. Tersedia di `make_plan()` & `budget_plan()`.
  - `core/analysis.py`: `compute_indicators()` kini menghitung **`ret5d`** dan `last_snapshot()` menyertakannya.
  - 10 tes baru di `tests/test_rencana.py` (death cross veto/warning, dead-cat bounce, RSI lemah, prefilter, entry_pullback) — total **921 tes**.

- **Redesain UI SahamBot Desktop (`desktop.py`)** menjadi tema dark "trading terminal":
  - Custom title bar frameless (drag judul, minimize, maximize ke work area layar via `SystemParametersInfo`, close → minimize ke tray) + ikon jendela dari logo SahamBot, dengan edge-resize tipis di keempat sisi.
  - Kartu KPI: IHSG (chip `▲/▼` hijau/merah), rezim IHSG, kondisi pasar, dan update terakhir — masing-masing dengan strip aksen warna dan teks status berwarna.
  - Tab segmented custom (menggantikan `ttk.Notebook`): pill aktif biru, hover, dan hint di kanan.
  - Tabel (watchlist, sinyal, paket, portofolio) dengan zebra striping + pewarnaan status (BELI/SIAP BELI/JUAL/TAHAN, tesis GUGUR/MELEMAH/MENGUAT).
  - Panel teks (laporan pasar, jurnal, cek emiten) dirender dengan pewarnaan: header biru, section muted, verdict/kata kunci status berwarna; log alert menjadi panel teks berwarna per jenis alert.
  - Kontrol header: toggle switch pill untuk Auto-Pantau & Toast, tombol Terapkan/Refresh (accent)/Keluar (danger), status bar bawah dengan dot indikator + chip `AUTO-PANTAU ON/OFF · interval`.
  - Perilaku & isi data tidak berubah (tetap jujur "tidak tersedia" saat data kurang). Diverifikasi offline: screenshot semua 8 tab + uji maximize/drag/resize/minimize-to-tray; `pytest` **910 passed**, `ruff check .` bersih.
- **Penyempurnaan tata letak setelah review screenshot (2026-11-24)**:
  - Tab Kondisi Pasar kini dua kolom: laporan teks di kiri + panel kanan visual (bar breadth berwarna naik/turun/datar, kartu penggerak naik/turun, kartu sentimen berita dengan chip & daftar judul); teks lama kini menunjuk ke panel kanan agar tidak duplikat.
  - Footer ringkasan di bawah tabel: Radar Watchlist (chip `BELI/SIAP BELI/TAHAN/JUAL n`) dan Sinyal & Scan (chip `BELI/JUAL/TAHAN n` + info snapshot & filter aktif); Portofolio menampilkan jumlah posisi terbuka.
  - Empty state terarah di semua tab kosong (watchlist, sinyal, paket, portofolio, log alert) dengan petunjuk langkah berikutnya, bukan area kosong polos.
  - Tombol lebih semantik & menyambung: "Hapus Terpilih" dan "Catat Jual" bergaya danger (merah), garis pemisah antara pengaturan (profil/modal/interval/switch) dan tombol aksi (Terapkan/Refresh/Keluar) di header, hint "catat manual sesuai eksekusi Stockbit PC" di tab Portofolio.
  - Pewarnaan teks laporan meluas: `DEFENSIVE`/`BULL`/`BEAR`, `POSITIF`/`NEGATIF`/`CAMPURAN` ikut diwarnai seperti kata kunci status lain.
  - Diverifikasi lagi via screenshot offline semua tab; `pytest` **921 passed**, `ruff check .` bersih.

## [Unreleased] — 2026-11-21

- **Housekeeping skill & tooling (W0)**: `.agents/skills/alphaear-*` diabaikan di `.gitignore`, `pyproject.toml` (`extend-exclude` & `norecursedirs`), serta didokumentasikan di `AGENTS.md` tanpa menambah dependensi ke `requirements*.txt`.
- **Audit & perbaikan sentimen berita (W1)**:
  - Dataset emas offline 60 judul berita IDX (`tests/fixtures/news_sentiment_gold.json`), harness `research/evaluate_sentiment.py`, dan tes regresi `tests/test_sentiment_gold.py`.
  - Perbaikan lexicon & negasi di `core/news.py`: frasa majemuk pasar modal (`POS_PHRASES`/`NEG_PHRASES`), frasa akuntansi netral (`NEUTRAL_PHRASES` seperti `laporan laba rugi`), pemulihan `rugi ... menyusut`, batas klausa agar negasi tidak bocor, status **`CAMPURAN` (`🟡`)** saat berita positif & negatif saling tarik-menarik, serta metrik **cakupan mesin** (`engine_lexicon`, `engine_coverage`) yang ditampilkan di `/berita`.
- **Laporan kondisi pasar `/pasar` (`/market`) (W2)**:
  - Tambah `fetch_market_news` di `core/news.py`, logika domain `core/pasar.py` (IHSG & rezim `^JKSE`, breadth snapshot `/scan`, rotasi sektor BEI, arus asing opsional, sentimen berita pasar, dengan degradasi jujur `"tidak tersedia"` saat sumber gagal/diblokir), dan handler `handlers/pasar.py`.
  - Tombol menu `🌐 Kondisi Pasar` (`run:pasar`) di Mode Expert, `sub:ide`, `sub:more`, dan `sub:allcmd`; job harian penutupan **16:15 WIB** (`closing-market-report`, terbungkus `job_guard`) ke chat `/auto on`, serta ringkasan kondisi pasar ringkas di `morning_scan`.
  - Tes baru `tests/test_pasar.py` & pembaruan `tests/test_main_wiring.py` (32 `CommandHandler` + callback/teks = 34 handler, 8 job terdaftar).
- **Pelacak tesis posisi (W3)**:
  - Modul `core/tesis.py` mengklasifikasikan posisi asli menjadi **`GUGUR` / `MELEMAH` / `MENGUAT` / `TETAP`** (atau `TIDAK_TERSEDIA` bila harga kosong) berdasarkan SL/Chandelier, tren & MA50, peringkat relatif vs pita tahan 60%, time-stop, berita, forensik fundamental, dan rezim IHSG.
  - Status tesis tersimpan persisten per `(chat_id, ticker)` di `chat_settings` (`thesis_state:<TICKER>`, tanpa perlu menaikkan `SCHEMA_VERSION = 6`, dan otomatis dibersihkan saat posisi terjual habis), tampil di `/portofolio` (termasuk transisi `dari <sebelumnya>`), serta memicu alert exit di `scan_alerts` **hanya saat status berubah** (`tests/test_tesis.py`).
- **Risiko portofolio lebih jujur**: bobot inv-vol dibatasi per posisi **dan per sektor**
  (water-filling dua lapis; sisa yang tak teralokasi dilaporkan sebagai tunai), plus
  **kontribusi risiko** per posisi. `/portofolio` menampilkan VaR historis 95%,
  Expected Shortfall, jumlah hari, dan pelanggaran VaR vs ekspektasi.
- **Jurnal menampilkan alpha vs IHSG** per siklus tutup (entry→exit), dengan caveat
  sampel kecil; bila riwayat IHSG tidak tersedia, ditulis "tidak tersedia" — bukan nol.

## [Unreleased] — 2026-11-20

- **Gerbang coverage dinaikkan 75% → 86%** dan disamakan di CI, Makefile, dan README
  (realitas 86,73%; `bot.py` sebelumnya 79%).
- **Job terjadwal berteriak saat gagal**: `job_guard` mengirim alert admin
  (best-effort, tidak melempar error) dan mencatat status per job; `/status`
  menampilkan job bermasalah beserta error terakhirnya.
- **Tes wiring `main()`**: 31 perintah, callback/teks, 7 job, error handler, dan
  polling dipastikan terdaftar tanpa memanggil Telegram atau jaringan.

## [Unreleased] — 2026-11-17

Peringkat relatif per sektor — z-score di dalam sektornya sendiri (Track C
bagian sinyal, dipilih pertama 16 Nov 2026):

- **Pemetaan ticker→sektor baru: `data/idx_sectors.json`** — 916 emiten, 11
  sektor gaya yfinance, diambil 17 Nov 2026 dari daftar publik
  tweenvest.com (API idx.co.id diblokir 403 dari VM ini dan Yahoo sedang
  rate-limit 429, jadi rute yfinance/`fzn0x/idx` tidak bisa dipakai hari
  itu). 29 kode universe tidak terpetakan (kemungkinan sudah tidak aktif)
  dan 1 kode peta (XSPI, DIRE) tidak ada di universe. `core/universe.py`
  kini punya `sector_of()` ("" bila tak terpetakan — tidak pernah ditebak)
  dan `sectors_meta()`; `.gitignore` dikecualikan seperti dua snapshot data
  lainnya.
- **`core/relatif.py` `score_universe()` kini menghitung juga peringkat DI
  DALAM sektor** (`rel_z_sector`/`rel_pct_sector`/`rel_quintil_sector` +
  `rel_sector_ok`, `rel_sector`, `rel_sector_n`) — statistik (mean/std)
  dihitung per kelompok sektor, hanya bila kelompok itu berisi ≥8 emiten
  usable; kelompok yang terlalu kecil atau tanpa sektor mendapat
  `rel_sector_ok=False` dan nilai sektor None. **Peringkat global tidak
  berubah** (bobot, kuintil OOS, dan urutan kandidat tetap sama — ada tes
  yang mengunci ini).
- **`/saran` dan `/sinyal` (via `core/screener.py`)** menempelkan sektor ke
  tiap item sebelum di-skor; baris hasil scan membawa `sector`,
  `rel_pct_sector`, `rel_quintil_sector`. Alasan BELI/JUAL kini menampilkan
  persentil sektor bila tersedia ("di sektornya 88/100") — **sinyal tetap
  memakai ambang persentil global** yang tervalidasi OOS; skor sektor belum
  punya validasi OOS sendiri, jadi ia konteks, bukan penentu sinyal.
- Tes baru (offline, data sintetis): 4 di `tests/test_relatif.py`
  (peringkat dalam sektor, grup kecil, tanpa sektor, global tak berubah),
  1 di `tests/test_screener.py` (tampilan persentil sektor + sinyal tetap
  global), 1 di `tests/test_universe.py` (peta sektor). Total 794 tes:
  793 lulus, 1 gagal — `test_news_ml_cache_thread_safe` (sudah gagal di
  tree bersih sebelum perubahan ini; torch tidak terpasang di venv).
- Tindak lanjut (belum dikerjakan): builder regenerasi
  `tools/build_sectors.py` (saat ini file hanya dilacak di git; sumber
  tweenvest butuh render JS sehingga builder urllib tidak bisa — kandidat
  sumber regenerasi adalah yfinance `.info` bila rate-limit reda).

Keanggotaan LQ45 point-in-time — Track B langkah 1 (riset, belum ada angka
bias):

- **`research/lq45_membership_2016-2026.json`** — 128 interval, 98 emiten
  unik, 45 masih anggota (`end: null`), dirangkai mundur dari daftar
  Nov 2026 lalu maju lagi dengan asersi keras (tiap langkah tepat 45
  anggota). Provenance per periode di
  `research/lq45_membership_SOURCES.md` (pengumuman BEI bernomor, daftar
  lengkap kontemporer, Siaran Pers BEI 28 Mar 2024).
- **Caveat jujur (dicatat di SOURCES.md, bukan diisi tebak-tebakan)**:
  Mei 2019 MISSING (3x pencarian tanpa hasil) → diperlakukan sebagai
  no-change karena selisih Feb→Agu 2019 cocok delta Agu 2019 (inferensi,
  bukan fakta); tanggal efektif Nov 2024 belum pasti (dipakai 1 Nov 2024,
  hari bursa pertama — inferensi); pasangan BUKA masuk/SMRA keluar
  29 Sep 2021 adalah deduksi eliminasi dari lampiran BEI (bukan berita
  eksplisit); baseline Feb 2016 left-censored (42 ticker mungkin sudah
  anggota jauh sebelumnya); daftar lampiran skripsi Undiksha mengandung
  kesalahan transkripsi dan TIDAK dipakai mentah-mentah.
- **`research/bias.py` diperbaiki satu baris**: loader kini menerima
  `end: null` (= masih anggota) — sebelumnya menolak file ini
  (`baris 0: field wajib hilang: end`). `ticker`/`start` yang hilang
  tetap ditolak keras. Tes baru di `tests/test_bias_guard.py`. Total kini
  795 tes: 794 lulus, 1 gagal lama (`test_news_ml_cache_thread_safe`).
- **Belum ada angka bias**: pengukuran butuh (a) `build_dataset` di
  `research/evaluate.py` difilter per tanggal keanggotaan, (b) evaluasi
  10 tahun dijalankan dua kali (statis vs point-in-time), (c) data harga
  10 tahun — saat ini TIDAK bisa jalan dari VM ini (Yahoo rate-limit 429;
  `temp/cache` hanya berisi 1y/6mo, tidak ada dataset 10y tersimpan).
  Langkah (a)–(c) menunggu rate-limit reda atau dijalankan dari host.

## [Unreleased] — 2026-11-16

Perbaikan dari review independen (REVIEW-IMPROVE.md, 8 temuan):

- **Pengingat dividen tidak lagi hilang saat jaringan gagal**: pengiriman memakai
  `send_with_retry` dan tanda dedup baru ditulis setelah sukses; cakupan watchlist
  naik dari 15 menjadi `MAX_WATCH` (30) untuk pengingat dividen dan autoscan pagi.
- **`download_model.py`** memulai ulang unduhan bila server mengabaikan header
  `Range` (HTTP 200), mencegah `model.safetensors` korup senyap.
- **`reply_long`**: fallback teks polos meng-unescape entity HTML dan hanya dipakai
  untuk `BadRequest`; `NetworkError` diteruskan ke `on_error`.
- **`/status`** ikut menghitung callback tombol dan input teks (dibungkus `timed`),
  dan re-sort BELI yang redundan dihapus.
- **`/rencana` menampilkan tiket order limit**: harga limit, lot, SL/TP,
  patokan penutupan, serta kapan dipasang/dibatalkan (termasuk jam bursa),
  supaya order bisa langsung disalin ke Stockbit.
- Dokumen: komentar requirements disinkronkan ke 764 tes / 86,05%, typo CHANGELOG
  diperbaiki, README menjelaskan cakupan pengingat dividen.

## [Unreleased] — 2026-11-14

Ringkasan: Track A (kontrol risiko rezim IHSG defensif + hysteresis turnover),
Track B (ablasi & walk-forward multi-horizon 20/40/60 hari), Track C (risiko
portofolio P23/P24, jurnal eksekusi P28, forensik arus kas), **Wave 2A**
(merge `8489697`: perbaikan akurasi ROE/mata uang non-IDR/penalti forensik,
panduan exit Chandelier/Scale-out/Time-stop/ARB 7%, cache per-period &
`repair=True`, perbaikan `Dockerfile` `COPY handlers/` & `vendor/`, serta
sinkronisasi `core/relatif.py` & `tools/check_sync.py`), dan **Wave 2B**
(integrasi provenance WIB & kualitas data di `handlers/` + `bot.py`, panduan
beli & jual/exit portofolio-aware, exit alert otomatis portofolio asli di
`scan_alerts`, diagnostik MAE/MFE `/jurnal`, target harga `/alert`, menu
`Cek Jual / Tinjau Posisi`, serta sinkronisasi penuh `.agents/skills/` &
dokumentasi proyek), plus hotfix `yfinance` adaptif tanpa `scipy`, cooldown
kuota/premium EODHD & Alpha Vantage, perbaikan unduhan chunk `download_model.py`,
dan audit + perbaikan seluruh menu tombol & callback inline (`sg:scan`, `_safe_edit`
pesan foto/media, input target/budget `manual:alert_add` & `manual:rencana`, serta
ketahanan `do_paket`/`do_scan`/`exec:*`), penyederhanaan menu utama +
panduan interaktif langkah demi langkah untuk pemula (`guide:start/beli/jual/cek/pemula`,
`sub:more`, dan `result_markup` kontekstual), **Mode Pemula vs Expert**
(`user_mode` & `onboard_state` di `chat_settings`, `mode:menu`, `/mode`),
**panduan linear 5-tahap** (`onboard:s1..s5` termasuk preset `Rp 500 Rb`/`Rp 1 Juta`
& evaluasi keterjangkauan 1 lot), serta **tombol tindak lanjut sambung-menyambung**
lintas seluruh hasil (`sinyal`, `market`, `cek`, `trading`, `portfolio`, `jurnal`,
`analisis`) beserta perbaikan pasca-QA (`5d283fa`). Total verifikasi akhir:
**764 tes lulus** offline (`0 skipped`, `0 failed`, coverage target CI
`core+handlers+bot+config` **86%** / `86.05%`, `bot.py` 1.757 baris),
`ruff check .` bersih.

### Ditambahkan

- **Mode Pemula vs Expert (`core/store.py`, `handlers/info.py`, `handlers/menu.py`, `bot.py`)**:
  - Persistensi `user_mode` (`"pemula"` bawaan / `"expert"`) & `onboard_state` pada tabel `chat_settings` (`normalize_user_mode`, `get_user_mode`, `has_user_mode`, `set_user_mode`, `get_onboard_state`, `set_onboard_state`, `clear_onboard_state`). Tes: `tests/test_store_mode.py`.
  - Perintah `/mode` (alias `/level`), `/start [pemula|expert]`, serta pemilih inline `mode:menu`, `mode:set:pemula`, `mode:set:expert` (terdaftar di `bot.py` `main`, `_post_init`, dan `sub:allcmd`).
  - Menu utama adaptif: `main_menu_markup("pemula")` (≤8 tombol ramah pemula, slot #8 `mode:menu`) dan `main_menu_markup("expert")` (grid akses cepat pro ke `/cek`, `/analisis`, `/rencana`, `/paket`, `/sinyal`, `/sinyal beli bagus`, `/saran`, `/sektor`, `/portofolio`, `/jurnal`, dll.).
- **Panduan linear bertahap 5-tahap (`handlers/menu.py`)**:
  - Alur `onboard:s1..s5` (`onboard:goal:*`, `onboard:modal:*`, `onboard:s4:<T>`, `onboard:s5:<T>`, `onboard:done`, `manual:onboard_modal`, `manual:onboard_ticker`) memakai helper sinkron tunggal via `asyncio.to_thread`.
  - Tahap 1 pilih tujuan (`beli`/`cek`/`jual`); Tahap 2 atur modal dengan 6 preset termasuk modal kecil **`Rp 500 Rb` & `Rp 1 Juta`** (`_MODAL_PRESETS`), info batas slot 25% & 1 lot = 100 lembar; Tahap 3 pemilihan saham adaptif + evaluasi keterjangkauan **biaya 1 lot vs modal/slot 25%** (prioritas cache `kualitas` sehat untuk `beli`, posisi aktif `lots > 0` untuk `jual`, atau watchlist/populer untuk `cek`); Tahap 4 cek kelayakan (`tick:cek`/`fundamental`/`analisis` + `tick:jual`); Tahap 5 hitung lot & catat eksekusi (`tick:rencana`/`exec:buy` atau `tick:jual`/`exec:sell`), `sub:pantau`, dan `onboard:done`.
- **Tombol tindak lanjut sambung-menyambung lintas semua hasil (`handlers/sinyal.py`, `handlers/market.py`, `handlers/cek.py`, `handlers/trading.py`, `handlers/portfolio.py`, `handlers/jurnal.py`, `handlers/analisis.py`, `handlers/menu.py`)**:
  - `handlers/sinyal.py`: info modal tersimpan & estimasi biaya 1 lot vs slot 25%/modal pada `/sinyal beli bagus`, tombol kandidat (`bagus_markup` maks 6 kandidat & `section_markup("BELI")` maks 4 kandidat dengan `tick:cek`/`tick:rencana`; `section_markup("JUAL")` dengan `tick:jual`/`tick:analisis`), `detail_markup`, `overview_markup`, dan `🏠 Menu Utama` (`from_menu`). Tes: `tests/test_sinyal_followup.py`.
  - `handlers/market.py`: `/paket` (`_paket_picks_markup` maks 4 pick `tick:cek`/`tick:rencana`/`exec:buy`, plus `_paket_empty_markup`/`_paket_no_snapshot_markup`), `/rencana` (`_rencana_small_budget_markup` saat 1 lot > slot 25% tapi muat modal penuh, `_rencana_limit_reject_markup` saat ditolak `check_limits` dengan CTA `/portofolio batas`/`sub:modal`/`run:sinyal_bagus`/`tick:cek`/`manual:rencana` tanpa membocorkan `exec:buy`), `/scan` (`_scan_followup_markup`), dan `/bandar` (`_bandar_markup`).
  - `handlers/cek.py` & `handlers/trading.py`: evaluasi modal tersimpan user & biaya 1 lot di `_cek_text`, guard `allow_buy` di `/cek` (`HINDARI`/`TIDAK CUKUP DATA`), `_tanya_allows_buy` di `do_tanya`, `_saran_list_markup`, `_saran_followup_markup` (tombol alert jual & kandidat beli hanya jika `lots > 0`), perbaikan rumus modal minimum 1 lot di `saran_scan_text`, `result_markup("backtest", ...)`, serta `result_markup("rencana")` yang selalu menyertakan `tick:analisis` tanpa membocorkan `exec:buy`. Tes: `tests/test_market_cek_followup.py`.
  - `handlers/portfolio.py`, `handlers/jurnal.py`, `handlers/analisis.py` & `handlers/menu.py`: `_empty_porto_markup`, `_batas_markup`, `_riwayat_markup`, `_exec_followup_markup`, `exec_done_markup`, `journal_markup`, `_riwayat_jurnal_markup`, `confirm_exec_markup`, `_banding_markup`, `_safe_result_markup` pada `/analisis` & `/skor` (termasuk guard `primary_allow_buy=False` saat `score["pct"]` `None`/non-numerik atau verdict `HINDARI`/`JUAL`/`SELL`), perluasan `result_markup` (`skor`, `berita`, `backtest`, `rencana`), `_modal_saved_markup`, serta `ticker_pick_markup(extra_tickers=...)`. Tes: `tests/test_jurnal_port_followup.py`, `tests/test_menu_beginner.py`.

- **Filter rezim pasar kausal `core/regime.py`** — mendeteksi fase IHSG (`^JKSE`
  `SMA50` vs `SMA200`, murni kausal bar `<= t`). Bila `SMA50 < SMA200`,
  `exposure_scale` menjadi `0,5` (`DEFENSIVE`); bila data `<200` bar atau
  Yahoo gagal, dilaporkan jujur sebagai `NA` (`"tidak tersedia"`,
  `exposure_scale=1,0`, tidak dikarang). Tes: `tests/test_regime.py`.
- **Harness ablasi & multi-horizon `research/experiments.py`** — menguji
  16 varian / 320 trial (`hold`, `min_hold`, EMA skor, invers volatilitas,
  ukuran kuintil, filter rezim IHSG) serta perbandingan walk-forward horizon
  `--horizons 20 40 60` (`run_horizon_comparison`) beserta akuntansi DSR jujur
  dan sensitivitas slippage (`+0..+60 bps`). Tes: `tests/test_experiments.py`.
- **Skill riset lokal** — `.agents/skills/idx-quant-research/` berisi aturan
  gerbang kausalitas, biaya BEI all-in `0,40%`, pemisahan alpha aktif vs beta
  kas, dan gate `DSR >= 0,95`.
- **Menu ramah pemula (`handlers/menu.py`, `bot.py`, `handlers/info.py`)** —
  merombak `/start` & `/menu` dari daftar 22 perintah teknis menjadi menu utama
  berbasis tujuan (8 tombol terkelompok: *Cek Layak Beli*, *Analisis Lengkap*,
  *Ide & Sinyal*, *Hitung Modal & Lot*, *Watchlist & Alarm*, *Portofolio Saya*,
  *Belajar Istilah*, *Panduan Pemula* + laci *Fitur Lanjutan*).
- **Alur pemula interaktif (`handlers/menu.py`)** — `/help` terpisah menjadi
  panduan *3 Langkah Mulai* + tombol 1-tap *Coba Cek BBCA*, kamus `/belajar`
  mendukung tombol istilah langsung (`PER`, `PBV`, `ROE`, `RSI`, `MACD`,
  `MA50`, `ATR`, `IHSG`, `PowerScore`), pengatur `/modal` punya tombol cepat
  (`Rp 2/5/10/25 Juta` + ketik manual), label `/beli`/`/jual` diperjelas sebagai
  pertanyaan (*Tanya: Layak Beli/Jual?*), dan seluruh hasil dari menu menyertakan
  tombol *Menu Utama* (`from_menu=True`). Urutan perintah `/` (`_post_init`)
  mendahulukan perintah pemula. Tes baru: `tests/test_menu_beginner.py`.
- **Jurnal eksekusi manual Stockbit & slippage nyata — P28 (`core/jurnal.py`,
  `handlers/jurnal.py`, `core/store.py` v5)** — mengukur realitas eksekusi
  pemilik tanpa auto-trading / tanpa login Stockbit:
  - Skema SQLite naik `v4 -> v5` (tabel `signal_context` & `real_executions`,
    migrasi `_migrate_v5` idempoten) beserta helper atomik bersama
    `_add_real_position_conn` & `_reduce_real_position_conn` agar pencatatan
    beli/jual jurnal sinkron ke portofolio asli tanpa duplikasi logika.
  - Konteks sinyal (harga sinyal, rencana lot, SL/TP, sumber) otomatis
    disimpan saat memanggil `/rencana`, `/cek`, `/beli`, `/jual`, dan
    `/sinyal KODE`; tombol **Sudah beli di Stockbit** (`exec:buy:<KODE>`)
    memungkinkan konfirmasi cepat dengan mengetik `<harga>` atau `<lot> <harga>`.
  - Perintah `/jurnal` (alias `/journal`, submenu *Fitur Lanjutan* & `/portofolio`):
    `/jurnal beli`, `/jurnal jual`, `/jurnal riwayat`, slippage per transaksi
    dan rata-rata (`+` mahal vs sinyal / `-` hemat, `"tidak tersedia"` bila
    tanpa harga sinyal), win rate & profit factor realisasi bersih setelah biaya
    BEI `0,40%`, serta tabel terpisah **Uang Sungguhan (ASLI)** vs **Paper
    Trading (Latihan)**. `/portofolio jual` otomatis menutup entry jurnal yang
    masih terbuka.
  - Ringkasan jurnal mingguan (`handlers/jobs.weekly_journal_report`, Jumat
    `16:45 WIB` pada hari bursa) dikirim hanya ke chat `/auto on` yang memiliki
    catatan eksekusi.
  - Pemarser nominal `parse_rupiah` disatukan di `core/textutil.py`. Tes baru:
    `tests/test_jurnal.py` (**575 tes lulus**, coverage `core`+`handlers` `83%`,
    `ruff` bersih).
- **Skill & audit forensik fundamental + debat Pro/Risiko (`idx-equity-research`)**:
  - Skill lokal `.agents/skills/idx-equity-research/SKILL.md` (protokol riset
    *point-in-time*, forensik kas & *dividend trap*, debat Bull vs Bear, serta
    kepatuhan batas risiko BEI).
  - `core/fundamental.analyze_cashflow_quality()` mengambil `operatingCashflow`,
    `freeCashflow`, `netIncomeToCommon`, `totalCash`, `totalDebt`,
    `currentRatio`, dan `payoutRatio` dari respons `yfinance.info` yang sama
    (tanpa panggilan jaringan tambahan), memisahkan bank vs non-bank dan jujur
    `"tidak tersedia"` bila kosong; ditampilkan di `/fundamental`, `/rencana`,
    dan `/cek` (`tests/test_fundamental_forensic.py`).
  - `/cek` (`core/cek.py`, `handlers/cek.py`) kini menyertakan **Sudut Pandang
    Pro vs Risiko** (`bull_points` vs `bear_points`).
- **Batas risiko portofolio kustom (P23 lanjutan) & pembobotan *inverse-volatility***:
  - Skema SQLite naik `v5 -> v6` (`chat_settings`, migrasi `_migrate_v6`),
    helper `store.get_risk_limits` / `store.set_risk_limits`, validasi
    `risk.validate_limits`, dan perintah `/portofolio batas [pos% sektor%|reset]`
    yang terhubung ke `/rencana`, `/cek`, dan `/portofolio`.
  - `core/risk.inverse_vol_weights()` menghitung target bobot penyeimbang risiko
    (`1/vol` dengan *water-filling cap*) ketika `>= 2` posisi memiliki riwayat
    return memadai, dan ditampilkan di laporan risiko `/portofolio`. Tes baru:
    `tests/test_risk_advanced.py`.
- **Panduan exit/jual terpadu, BEI 2026 & diagnostik MAE/MFE (Wave 2A — `core/saran.py`, `core/pricing.py`, `core/jurnal.py`, `core/portofolio.py`, `core/store.py`)**:
  - `saran.build_exit_guidance()`, `chandelier_trailing_stop()` (`High_max(22) - 3×ATR14` via `pandas_ta_classic.ce` + fallback murni Python, *ratcheting* monoton naik), `scale_out_plan()` (jual 50% di `TP1 = +1,5×ATR` + geser SL sisa lot ke `breakeven_stop` `BEP + 0,40%` biaya BEI), dan `time_stop_status()` (batas horizontal López de Prado `>= 20` hari bursa).
  - Aturan **BEI 2026** di `core/pricing.py` & `saran.arb_gap_risk`: **Kep-00136/BEI/09-2026** (lantai papan reguler Rp1 sejak 28 Sep 2026; Rp50 dipertegas sebagai filter kualitas likuiditas/FCA) dan **Kep-00158/BEI/10-2026** (ARB asimetris 7% papan reguler, `arb_floor_price`, `check_arb_gap_risk`, `auto_reject_status`).
  - `core/jurnal.py`: `compute_trade_excursions()`, `jurnal_diagnostics()`, dan `format_jurnal_diagnostics()` menghitung MAE (`mae_r`), MFE (`mfe_r`), capture ratio, dan R-multiple bruto & neto setelah biaya BEI `0,40%`.
  - `core/store.py`: `add_real_position` & `reduce_real_position` mencatat otomatis ke `real_executions` (`record_exec=True`, `fee_rp`, `net_realized`, `net_realized_pct`); `signal_context` memisahkan kolom `intent` (`"beli"` vs `"jual"`). Tes baru: `tests/test_phase2_exit_risk.py`.
- **Alat audit sinkronisasi & verifikasi fixture offline (Wave 2A — `tools/check_sync.py`, `tools/record_fixtures.py`, `research/*`)**:
  - `tools/check_sync.py` memeriksa keselarasan `core/relatif.py` (`WEIGHTS` & `OOS_QUINTILES`) vs `data/validation.json`, kelengkapan `core.saran.LQ45` di `data/idx_universe.json`, umur artefak data, serta uji round-trip backup -> restore SQLite (`tools/backup_db.py` + `PRAGMA integrity_check`).
  - `tools/record_fixtures.py` mendukung `--ohlcv-rows` dan `--verify` (audit struktur fixture offline).
  - `research/portfolio.py` & `research/experiments.py` menambah **Probabilistic Sharpe Ratio (PSR)** (`probabilistic_sharpe`, `best_phase_psr`) dan pencatatan eksplisit `experiments_trials` (`320` trial vs `20` fase); `research/evaluate.py` & `core/relatif.py` menambah pita dispersi **P25–P75 IQR** (`summarize_quintile_bands`, `quintile_dispersion`). Tes baru: `tests/test_phase2_research_tools.py`.
- **Integrasi UX jual/exit portofolio-aware, exit alert otomatis, provenance WIB & skills (Wave 2B — `bot.py`, `handlers/*`, `core/reports.py`, `.agents/skills/*`)**:
  - **Exit alert portofolio asli (`handlers/jobs.py:scan_alerts`)**: memantau `real_positions` + `real_executions` secara otomatis untuk SL tersentuh, TP1 scale-out, Chandelier trailing SL jebol, time-stop `>= 20` hari bursa, dan pengingat rezim IHSG `DEFENSIVE` dengan dedup harian di `chat_settings`.
  - **Target harga `/alert` (`handlers/watchlist.py`)**: mendukung alert level harga langsung (`/alert BBCA 9500`, `/alert BBCA <9000`) di samping kondisi indikator.
  - **Menu & tombol aksi jual (`handlers/menu.py`, `handlers/portfolio.py`, `handlers/jurnal.py`, `handlers/sinyal.py`, `handlers/cek.py`)**: tombol utama `"💰 Cek Jual / Tinjau Posisi"` (`pick:jual`), tombol cepat `"🤔 Cek layak jual?"` (`tick:jual`) & `"💸 Sudah jual di Stockbit"` (`exec:sell`), penyembunyian tombol konfirmasi beli ketika `/cek` menghasilkan `HINDARI` (`allow_buy=False`), peringatan konteks sinyal basi di callback jurnal, serta kelengkapan daftar perintah `sub:allcmd`.
  - **Sinkronisasi `.agents/skills/` & dokumentasi proyek**: pembaruan `.agents/skills/idx-quant-research/` (`SKILL.md` & `references/gates.md`) dan `.agents/skills/idx-equity-research/SKILL.md` (Pillar 4b Exit Discipline, aturan BEI 2026 Kep-00136/Kep-00158, checklist provenance & CLI offline) beserta seluruh dokumen proyek. Tes baru: `tests/test_phase2_handlers_ux.py`.

### Diubah

- **`/paket` (`core/rencana.py`, `handlers/market.py`)** — menyuntikkan
  rezim IHSG saat memfinalisasi paket beli: saat `DEFENSIVE`, risiko maks/posisi
  dipangkas `1% -> 0,5%`, batas per emiten `20% -> 10%`, dan batas alokasi total
  `90% -> 45%` (sisanya cash), disertai banner rezim dan panduan menahan
  posisi top 60% (`±20–40` hari bursa) untuk menekan biaya `0,40%`.
- **`/saran` (`core/saran.py`, `handlers/trading.py`)** — menyelaraskan
  sizing risiko (`1% -> 0,5%` saat `DEFENSIVE`), menambah `hold_review`
  (`TAHAN` bila posisi terbuka masih `rel_pct >= 40` / top 60%, `ROTASI`
  bila turun di bawahnya), serta banner rezim di ringkasan scan harian.
- **`research/portfolio.py` & `data/validation.json`** — berbagi logika
  `select_names`/`max_drawdown_pct`/`core.regime`, menambah `--min-hold`,
  `--regime`, dan `--offline`. Regenerasi `data/validation.json` via
  `portfolio.py --hold 0.6 --min-hold 2 --regime sma50_200_half --offline`
  dan `research/publish.py`:
  - `avg_turnover_pct` turun `50,6% -> 21,7%`, `max_drawdown_mean_pct` turun
    `-35,0% -> -20,0%`, `sharpe_mean 0,174`, `avg_exposure_pct 79,8%`.
  - `alpha_net_mean_pct +0,330%` (`16/20` fase positif, `CI [+0,243; +0,522]`)
    dengan `alpha_active_gross_mean_pct +0,534%` (terpisah dari kas rezim)
    dan `DSR 0,86` (`n_trials=20`, tetap `< 0,95`; bobot `core/relatif.py`
    dipertahankan beku).
  - Uji `--horizons 20 40 60` (`research/experiments.py --offline --horizons 20 40 60`):
    melatih ulang 12 faktor pada `40d`/`60d` menurunkan WF IC dari `+0,0265`
    (`20d`, `t_hac 1,80`) menjadi `-0,0032` (`40d`) dan `-0,0134` (`60d`,
    `t_hac -0,48`), sehingga horizon prediksi dibiarkan `20d` dan efisiensi
    masa tahan dicapai lewat hysteresis `hold=0,6` + `min_hold=2` (bukan ubah
    target model).

### Diperbaiki (Wave 2A — merge `8489697`)

- **Unit ROE desimal & mata uang laporan non-IDR (`core/fundamental.py`, `core/kualitas.py`, `core/cek.py`, `core/reports.py`)** —
  `_fetch_uncached` mengonversi `returnOnEquity * 100.0` dengan tag `roe_unit="pct"` dan `_v=2`
  (mencegah ROE desimal `0,18` terkena veto `ROE < 2%` di `/cek` dan `/sinyal beli bagus`,
  sekaligus mencegah ROE kecil `0,2%`–`2,9%` dikali 100 dua kali); membaca `financialCurrency`
  agar emiten berlaporan `USD` tidak dilabeli `Rp` secara buta; mengintegrasikan `forensic_penalty`
  ke `score_fundamentals` & `powerscore.combine`; serta memisahkan valuasi `"Murah (PER & PBV)"`
  vs `"Campuran"` dan menampilkan `trend_label` deskriptif dengan disclaimer `IC harian ±0,01`
  (`tests/test_phase2_honesty.py`).
- **Penyatuan SL `1×ATR`, rezim single-stock & masa tahan (`core/portofolio.py`, `core/rencana.py`, `core/saran.py`)** —
  `portofolio.STOP_ATR` diselaraskan dari `2.0` ke `1.0` (`1×ATR` tick-rounded, memprioritaskan
  `sl` eksekusi tersimpan dari `real_executions`); `rencana.budget_plan` & `finalize` menghormati
  batas risiko kustom per chat, eksposur `real_positions`, dan pemangkasan `0,5×` saat rezim
  IHSG `DEFENSIVE`; `saran.fill_pending` meneruskan `risk_scale` rezim dan `hold_review`
  menghormati `MIN_HOLD_CYCLES = 2`.
- **Cache disk per-period, `yfinance repair=True`, screener & `Dockerfile` (`core/data.py`, `core/screener.py`, `Dockerfile`, `.github/workflows/ci.yml`)** —
  memisahkan cache disk berdasarkan `(symbol, interval, period)` agar unduhan `1mo` tidak
  menimpa cache `1y`; mengaktifkan `repair=True` pada `yf.download` + provenance WIB
  (`core/data_quality.py`); memfilter kualitas data & bar basi di `core/screener.py` serta
  membulatkan `entry`/`sl`/`tp` ke fraksi tick BEI; memperbaiki bug fatal `Dockerfile` yang
  sebelumnya tidak menyalin `COPY handlers/ ./handlers/` dan `COPY vendor/ ./vendor/`; serta
  menambah `--cov=handlers` dan job `docker-smoke` di CI (`tests/test_phase2_data_infra.py`).
- **Sinkronisasi `core/relatif.py` & akuntabilitas `/jejak` (`core/relatif.py`, `core/scorecard.py`)** —
  menyelaraskan `WEIGHTS` & `OOS_QUINTILES` di `core/relatif.py` 100% ke `data/validation.json`;
  mengevaluasi sinyal `JUAL` secara terpisah sebagai `TURUN`/`NAIK`/`DATAR` (`r_multiple=None`);
  serta menambahkan `t_hac` Newey-West dan deteksi pergeseran eks-dividen (`div_adjusted`) pada
  `scorecard.live_ic()`.
- **Penyederhanaan menu utama & panduan interaktif langkah demi langkah pemula (`handlers/menu.py`)** —
  - Merampingkan `menu_text()` dan `main_menu_markup()` dari 10 tombol menjadi 7 tombol berbahasa sehari-hari (*Pandu Aku Langkah demi Langkah*, *Saham Cocok Dibeli*, *Cek Saham Jual*, *Cek Nilai Saham*, *Portofolio Saya*, *Belajar Pemula*, dan *Menu Lengkap* `sub:more` yang menjaga akses ke seluruh alat analisis & submenu lanjutan).
  - Menambahkan `_guide_content` (`guide:start`, `guide:beli`, `guide:cek`, `guide:jual`, `guide:pemula`) beserta loader lokal (`_load_guide_beli_data`, `_load_guide_jual_data`) yang membaca snapshot scan & posisi aktif secara instan tanpa memanggil jaringan, menyertakan penafian jujur (*bukan jaminan untung*, *bukan instruksi short*), serta tombol cepat ke kandidat `BELI` teratas (bila ada) atau `/saran`/`/scan` cepat (bila snapshot kosong).
  - Memperkaya `result_markup()` dengan langkah lanjutan kontekstual (`Rincian Nilai`, `Hitung Lot`, `Cek 6 Poin`, `Grafik & Analisis`) sembari tetap menyembunyikan tombol beli (`exec:buy`, `Hitung Lot`) pada status `HINDARI` (`allow_buy=False`) dan layar `jual`/`tanya_jual`. Total tes terbaru: **678 tes lulus** offline (`0 skipped`, coverage target CI **84%** / `84.01%`, `handlers/menu.py` **93%**).
- **Audit & perbaikan menyeluruh tombol menu, callback inline & input manual (`handlers/sinyal.py`, `handlers/menu.py`, `handlers/market.py`, `handlers/jurnal.py`, `bot.py`)** —
  - `handlers/sinyal.py` (`nav`): memindahkan cabang `sg:scan` ke sebelum guard `if not rows:` dan meneruskan `["paksa"]` + `from_menu=True` ke `_do_scan` agar tombol `"🔄 Scan pasar sekarang"` (saat snapshot kosong) maupun `"🔄 Scan ulang"` (saat snapshot hari ini sudah ada) benar-benar memicu scan, bukan menelan `"Message is not modified"` secara diam-diam; serta fallback ke ringkasan bila ada `sg:*` tak dikenal.
  - `handlers/menu.py` (`safe_edit`, `_is_media_message`) & `bot.py` (`_safe_edit`): mendeteksi pesan foto/media (mis. tombol `"🏠 Menu Utama"`, `"🔄 Perbarui"`, `"🤔 Cek layak jual?"` di bawah grafik `/analisis`) maupun error `There is no text in the message to edit` / `Message can't be edited` dan otomatis fallback ke `reply_text` tanpa crash.
  - `handlers/menu.py` (`menu_text_input`, `_parse_menu_tokens`, `run_menu_action`, `menu_callback`): mendukung target harga & jenis pada `manual:alert_add` (mis. `BBCA <9000`, `BBCA 9500`, `BBCA oversold`) dan anggaran opsional pada `manual:rencana` (`BBCA 5jt`), membersihkan `PENDING` saat mengetuk `run:*` atau `exec:*`, serta fallback ramah untuk callback tak dikenal.
  - `handlers/market.py` (`do_paket`, `do_rencana`, `do_scan`): memakai `_fetch_regime_safe(deps)` pada `do_paket` agar kegagalan IHSG tidak meledak, menyertakan `reply_markup=rm` saat rencana ditolak batas risiko, dan menjaga `job_queue is None` di `do_scan`. Total tes terbaru: **676 tes lulus** offline (`tests/test_menu_beginner.py` mencakup regresi & audit seluruh `callback_data` menu/submenu/picker/belajar).
- **Perbaikan crash `yfinance(..., repair=True)` tanpa `scipy`, cooldown kuota EODHD & isolasi `.env` di tes (`core/data.py`, `core/screener.py`, `tests/conftest.py`)** —
  `yfinance==1.8.0` mengimpor `scipy` secara internal saat `repair=True`, menyebabkan
  unduhan live gagal total di environment tanpa `scipy` (`requirements.txt` memang tidak
  menyertakan `scipy`) dan memicu spam fallback EODHD. Ditambahkan `supports_yf_repair()` &
  `download_yf_with_safe_repair()` di `core/data.py` (dipakai bersama oleh `core/screener.py`)
  yang memakai `repair=True` hanya bila `scipy` tersedia serta mengulang dengan `repair=False`
  bila `ModuleNotFoundError` muncul; cooldown terpadu (`EODHD_COOLDOWN_SECONDS=900`,
  `ALPHAVANTAGE_COOLDOWN_SECONDS=900`) saat HTTP `402`/`429` maupun respons
  `"Information"`/`"Note"` premium/rate-limit Alpha Vantage agar batch tidak spam kuota;
  serta `monkeypatch.delenv` di `tests/conftest.py` agar suite offline tetap terisolasi
  meski `.env` lokal menyimpan API key live; serta perbaikan loop `download_model.fetch`
  agar hanya menghitung kegagalan (tidak memutus unduhan `model.safetensors` 498 MB pada
  60×4 MB = 251 MB). Total terbaru: **670 tes lulus** offline
  (`0 skipped`, `ruff` bersih).
- **Integrasi provenance WIB, `_verdict_jual` portofolio-aware & async SQLite (`core/reports.py`, `bot.py`, `handlers/*` — Wave 2B)** —
  `reports.provenance_line` meneruskan DataFrame ke `data_quality.format_provenance` tanpa
  tertahan token pendek `live`/`cache`; `handlers/cek.py` mengisi `res["sl"]` & `res["prev_close"]`
  sebelum `cek.build` dan merender `provenance_line`, `quality_note`, serta `out["warnings"]`;
  `_verdict_jual` & `build_verdict(..., focus="jual")` di `bot.py` memakai posisi nyata
  (`real_positions` + `open_executions_by_ticker`), `saran.build_exit_guidance`, pita tahan
  `HOLD_BAND`, penalti forensik kas, dan rezim IHSG tanpa menampilkan baris `"Kalau beli — SL/TP"`
  atau label hiperbolik `"STRONG BUY 🚀"` / `"STRONG SELL 🔻"`; perluasan `_TANYA_STOP` agar kata
  umum seperti `"lot"` tidak terbaca sebagai ticker; pembungkusan seluruh panggilan sinkron di
  `handlers/trading.py` dengan `asyncio.to_thread`; serta tampilan diagnostik MAE/MFE di `/jurnal`.
  Total tes pasca-Wave 2B: **666 tes lulus** offline (`0 skipped`, coverage `core+handlers` **84%**).

---

## [Unreleased] — 2026-11-12

Ringkasan: seluruh backlog produk (P12–P18) selesai, riset dikalibrasi ulang,
`bot.py` dipecah menjadi paket `handlers/`, dan operasional (log JSON, backup,
notifikasi admin, CI riset) dipasang. **521 tes lulus**, `ruff` bersih,
coverage 80–81%.

### Ditambahkan

- **`/rencana <KODE> [anggaran]`** — rencana beli satu emiten: lot yang
  benar-benar terjangkau, fee, % modal, rugi maksimal (termasuk fee jual),
  untung di TP, RR, dan peringatan bila melebihi aturan risiko 1%.
  Level harga satu sumber: `rencana.price_levels()` dipakai `make_plan` dan
  `budget_plan`. (`e93e2d0`, `core/rencana.py`)
- **`/paket [1-5|refresh]`** — perilaku paket multi-emiten lama, dipisah dari
  `/rencana`. (`e93e2d0`)
- **`/cek <KODE>`** — ceklist satu layar: tren harian+mingguan, valuasi vs
  sektor, aliran asing, berita, agenda 14 hari, likuiditas; verdict
  LAYAK/TUNGGU/HINDARI, atau TIDAK CUKUP DATA bila ≥3 bagian kosong.
  (`ed90f42`, `core/cek.py`, `handlers/cek.py`)
- **`/portofolio`** — portofolio **uang sungguhan** terpisah dari paper
  trading: `beli`, `jual`, `riwayat`; PnL, bobot, konsentrasi sektor, risiko
  bila semua SL (awalnya 2×ATR pada `44318da`; diselaraskan ke **1×ATR** tick-rounded
  + prioritas SL eksekusi tersimpan di Wave 2A) tersentuh. Skema store v3.
  (`44318da`, `core/portofolio.py`, `handlers/portfolio.py`)
- **`/sinyal beli bagus`** + penanda kualitas fundamental (✅/⚠️/❔) pada
  kandidat BELI; fundamental hanya diambil untuk kandidat yang ditampilkan,
  cache harian. (`297d4fa`, `core/kualitas.py`)
- **Alert zona beli watchlist** — pantulan support, RSI pulih melewati 30,
  breakout + volume ≥1,5×; pesan hanya dikirim saat **masuk** zona (state di
  tabel `zone_state`, skema v4), ikut `/auto on|off`. (`297d4fa`)
- **`/sektor`** — kekuatan relatif sektor dari indeks BEI, parser defensif:
  hanya kode sektor yang dikenal; kode indeks lain (mis. `IDXLQ45LCL`)
  diabaikan; bila tidak ada yang bisa dihitung, bot berkata tidak tersedia.
  (`911e84e`, `2c46edb`)
- **Log JSON opsional** — `LOG_JSON=1` menghasilkan satu baris JSON per
  kejadian (ts, level, logger, msg, `cmd`, `chat_id`, `duration_ms`, `ok`,
  `exc`); `timed()` menulis field itu hanya saat mode JSON. (`bdcdfc3`)
- **Notifikasi admin** — `notify_admin()` untuk kegagalan screener harian atau
  >100 kode gagal; tujuan `ADMIN_CHAT_ID` (fallback `ADMIN_IDS[0]`).
  (`297d4fa`)
- **Fixture kontrak data + tes replay** — `tests/fixtures/` (OHLCV
  ter-normalisasi, ringkasan saham/indeks BEI) dan
  `tests/test_fixtures_replay.py`; alat perekam `tools/record_fixtures.py`
  untuk mengambil respons asli sekali di mesin berjaringan. (`47eb472`)
- **CI riset mingguan** — `.github/workflows/research.yml` menjalankan
  evaluasi IC sampel (12 emiten, 3 tahun) tiap Senin dan gagal bila IC < 0
  (`research/evaluate.py --min-ic`). (`47eb472`)
- **`LICENSE`** — MIT untuk kode, dengan catatan lisensi data pasar. (`47eb472`)

### Diubah

- **`/sinyal` ringkas + tombol navigasi** — ringkasan satu layar kini hanya
  intinya (nama, harga, arah sebulan terakhir, satu peringatan kata biasa);
  rel/RSI/entry/fundamental pindah ke tampilan rincian. Navigasi lewat tombol
  (`sg:*`): semua BELI/JUAL, BELI berkualitas, detail saham (pemilih emiten),
  unduh CSV, scan ulang, dan kembali ke ringkasan. `reply_long` kini menerima
  `reply_markup` yang dipasang hanya di potongan terakhir.
- **Retry pengiriman Telegram** — `send_with_retry()` untuk
  `NetworkError`/`TimedOut` di `reply_long`, `_safe_edit`, chart, dan hasil
  scan. `BadRequest` dikecualikan eksplisit karena di PTB ia subclass
  `NetworkError` (kalau tidak, pesan ber-HTML salah dikirim 3×). (`e93e2d0`)
- **Biaya transaksi disatukan** — `config.IDX_BUY_FEE=0,15%`,
  `IDX_SELL_FEE=0,25%` (sudah termasuk pajak jual 0,10%), round-trip 0,40%.
  Angka 0,50% lama adalah dobel hitung dan dihapus dari riset. (`e93e2d0`)
- **Riset dikalibrasi ulang** (`e93e2d0`, `data/validation.json`):
  - `lam` ridge dipilih CV deret waktu (`choose_lam_cv`), bukan `10.0` statis;
  - baseline anti-overfit bobot-sama (`ic_eq`);
  - CI 95% moving-block untuk alpha neto; pembanding IHSG;
  - hasil: turnover 50,6%, alpha neto +0,264% [CI +0,148; +0,390],
    model 0,0304 vs bobot-sama −0,0061 (menang 4/5 tahun);
  - IC skor: t naif 2,68 tetapi **t_hac 0,85** → tidak signifikan.
- **Label `/sinyal`** — `KANDIDAT BELI`/`HINDARI` menjadi **Peringkat Atas** /
  **Peringkat Bawah** + catatan IC OOS ±0,03 (bukan probabilitas, JUAL bukan
  instruksi short). (`86df5e3`)
- **Formatter rupiah disatukan** di `core/textutil.py` (`rupiah`,
  `rupiah_desimal`); `bot.fmt_rp`, `bot._money`, `analysis.money_fmt`,
  `saran.fmt` mendelegasikan. (`297d4fa`)
- **`bot.py` dipecah** (4.179 → 1.630 baris) menjadi:
  - `handlers/analisis.py` — `/analisis`, `/skor`, `/banding`
  - `handlers/cek.py` — `/cek`
  - `handlers/info.py` — `/start`, `/menu`, `/help`, `/id`, `/validasi`,
    `/status`, `/berita`, `/fundamental`, `/agenda`, `/jejak`, `/belajar`
  - `handlers/jobs.py` — alert 30 menit, autoscan, screener 16:15, backup,
    heartbeat
  - `handlers/market.py` — `/scan`, `/paket`, `/rencana`, `/bandar`
  - `handlers/portfolio.py` — `/portofolio`
  - `handlers/sektor.py` — `/sektor`
  - `handlers/sinyal.py` — `/sinyal`
  - `handlers/trading.py` — `/saran`, `/backtest`, `/beli`, `/jual`
  - `handlers/watchlist.py` — `/watch`, `/alert`, `/modal`, `/auto`
  - `core/reports.py` — `build_report`, `market_context_line`, `esc`,
    `fmt_rp`, `_rp`, `_trend_line`, dll.
  - `core/guards.py` — cooldown & PENDING
  - Pola **`deps`**: handler menerima modul `bot` sebagai parameter dan
    mengakses `deps.<nama>`; ini menjaga `monkeypatch.setattr(bot, ...)` di
    tes tetap bekerja tanpa impor melingkar.
  - `bot.py` menyisakan menu/aksi, markup, helper aplikasi, dan entrypoint.
- **Semua akses SQLite dari jalur async lewat `asyncio.to_thread`** —
  `handlers/*.py` dan `bot.py`; satu pengecualian terdokumentasi:
  `_snapshot_row` sinkron dan selalu dipanggil via `to_thread`. (`00feb74`)
- **Konkurensi update** — `bot.build_application()`: `concurrent_updates(4)`,
  `connection_pool_size(8)`, `AIORateLimiter(max_retries=2)`. (`ed90f42`)

### Diperbaiki

- `/sektor` tidak lagi melabeli indeks non-sektor sebagai sektor, dan nomor
  urut tidak lagi berubah jadi "1," akibat replace titik. (`2c46edb`)
- `/cek` tidak lagi menyebut "murah" ketika ROE hilang atau nyaris nol:
  ROE tidak tersedia ditulis terbuka; ROE < 2% menjadi veto "laba nyaris
  nol". (`2c46edb`)
- `_scan_summary_text` menjadi async karena membaca snapshot; pemanggilnya
  (`handlers/jobs.py`, `handlers/market.py`) memakai `await`. (`00feb74`)

### Operasional & dokumentasi

- `PLAN.md` diaudit ulang: klaim usang dikoreksi (auto_adjust, tick_size,
  biaya 0,50%, `build_verdict`), status per fase diberi bukti, dan 4 item
  dinyatakan **⛔ di luar cakupan** atas keputusan pemilik (survivorship,
  faktor non-teknikal, backtest end-to-end `/rencana`, `universe_membership`).
- `deploy.md`: bagian **Uji live setelah deploy** + templat hasil uji chat.
- `README.md` disinkronkan: `/rencana`, `/paket`, `/cek`, `/portofolio`,
  `/sinyal beli bagus`, `/sektor`, env `ADMIN_CHAT_ID` & `LOG_JSON`, LICENSE,
  fixture, CI riset.
- `KENDALA.md` tetap menjadi catatan kendala historis; lihat `AGENTS.md` untuk
  status terkini.

### Diverifikasi (bukan diklaim)

- `pytest -q` → **521 passed**; `ruff check .` → bersih; coverage 80–81%
  (gerbang CI 75%).
- Uji data live di mesin dev (Yahoo/BEI, tanpa polling Telegram):
  - `/status` OK;
  - `/sektor` jujur "tidak bisa dibaca" karena idx.co.id memblokir IP;
  - `/cek BBCA` OK, ROE yang gagal diambil ditandai;
  - `/sinyal beli bagus` benar-benar tidak menemukan kandidat BELI pada
    sampel 12 emiten (semua TAHAN oleh gate tren pasar).
- **Belum**: uji tingkat chat di host (butuh `BOT_TOKEN` host). Templat hasil
  ada di `deploy.md`; PLAN menandainya ⛔ diblokir akses, bukan selesai.

---

## [Unreleased] — 2026-11-14 — Risiko portofolio (P23/P24)

### Ditambahkan

- **`core/risk.py`** — metrik risiko portofolio asli: eksposur, konsentrasi
  sektor, **drawdown berjalan** (bobot sekarang, rebalance harian),
  **volatilitas tahunan**, **VaR 95% 1 hari**, **beta vs IHSG**, dan
  **korelasi** rata-rata/tertinggi. Semua asumsi ditulis di docstring; bagian
  tanpa riwayat ditandai `None` + peringatan, bukan diisi nol.
- **`/portofolio` menampilkan bagian "Risiko portofolio"** — eksposur (Rp dan
  % modal), vol, drawdown, beta, VaR, korelasi, plus peringatan bila satu
  posisi >25% modal, satu sektor >40%, korelasi >0,80, beta >1,30, atau
  drawdown < −15%.
- **Batas portofolio di `/rencana`** — rencana yang membuat satu posisi >25%
  modal atau satu sektor >40% **ditolak** dengan alasan nominal + jumlah lot
  maksimum yang masih lolos. Tanpa anggaran eksplisit, `/rencana` memakai
  **25% modal** (bukan seluruh modal).
- **`/cek` mengingatkan posisi yang sudah melewati batas** — bila emiten yang
  dicek sudah >25% modal atau sektornya >40%, peringatan muncul di kesimpulan.
- **`core/risk.position_fit()`** — helper yang dipakai `/cek`.

### Tes

- `tests/test_risk.py` (11): vol/drawdown/VaR/beta/korelasi, peringatan
  konsentrasi, `check_limits` (posisi, sektor, lot maksimum), `position_fit`.
- `tests/test_rencana_single.py`: happy path memakai modal 25 jt (5 jt = 20%),
  tes baru penolakan batas 25%.
- `tests/test_portofolio.py`: memastikan bagian risiko tampil.
- `tests/test_cek_handler.py`: peringatan posisi di atas batas.

Total (saat komit P23/P24): **567 tes lulus**, `ruff` bersih, coverage 82%
(total terbaru pasca-Wave 2B: **666 tes lulus** offline, coverage `core+handlers` **84%**).

## Catatan kompatibilitas

- Skema SQLite kini berada di **`SCHEMA_VERSION = 6`** (`v3`: `real_positions`/`real_trades`;
  `v4`: `zone_state`; `v5`: `signal_context` dengan kolom `intent` & `real_executions`;
  `v6`: `chat_settings`). Migrasi berjalan otomatis dan idempoten saat start;
  backup harian tetap memakai API backup SQLite WAL-safe (`tools/backup_db.py`) dan
  diuji round-trip restore oleh `python tools/check_sync.py`.
- `Dockerfile` kini menyalin `COPY handlers/ ./handlers/` dan `COPY vendor/ ./vendor/`
  serta menyetel `BACKUP_DIR=/app/data/backups` (selaras dengan `docker-compose.yml`).
- `bot.py` tetap menjadi entrypoint (`python bot.py`); Docker/run.sh tidak
  berubah. Nama-nama lama (`cmd_*`, `_do_*`, `_money`, `_sinyal_row`, dst.)
  masih tersedia sebagai wrapper/alias untuk tes dan pemanggil lama.
- Dependensi baru: `tenacity==9.2.1` + extra `[rate-limiter]` untuk
  `AIORateLimiter` (dipin di `requirements.txt`).
