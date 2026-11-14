# Changelog — SahamBuddy

Semua perubahan penting pada repo ini. Format mengikuti [Keep a Changelog](https://keepachangelog.com/id/1.1.0/);
repo belum memakai nomor rilis, jadi entri dikelompokkan per tanggal kerja.

Baseline sebelum rangkaian ini: commit `b11fcd2` (PLAN.md awal, 277 tes).
Rangkaian di bawah: **25 commit**, `e93e2d0` … `202ec2b` (2026-11-12).

---

## [Unreleased] — 2026-11-14

Ringkasan: Track A (kontrol risiko rezim IHSG defensif + hysteresis turnover),
Track B (ablasi & walk-forward multi-horizon 20/40/60 hari), Track C (risiko
portofolio P23/P24, jurnal eksekusi P28, forensik arus kas), serta **Wave 2A**
(merge `8489697`: perbaikan akurasi ROE/mata uang non-IDR/penalti forensik,
panduan exit Chandelier/Scale-out/Time-stop/ARB 7%, cache per-period &
`repair=True`, perbaikan `Dockerfile` `COPY handlers/` & `vendor/`, serta
sinkronisasi `core/relatif.py` & `tools/check_sync.py`). Checkpoint Wave 2A:
**653 tes lulus** offline (`pytest -q`), `ruff check .` bersih.

### Ditambahkan

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
  `scorecard.live_ic()`. Total tes pasca-merge Wave 2A (`8489697`): **653 tes lulus** offline.

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
(checkpoint terbaru setelah Wave 2A `8489697`: **653 tes lulus** offline).

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
