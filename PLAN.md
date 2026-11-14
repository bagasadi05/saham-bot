# Rencana Perbaikan & Pengembangan — SahamBuddy

> **Dibuat:** 2026-11-12 · **Direview & disesuaikan:** 2026-11-14 (checkpoint Wave 2A `8489697`)
> **Baseline terukur saat ini:** `653 passed` (checkpoint Wave 2A di merge `8489697`, naik dari 586 pra-Wave 2A & 517 pada 2026-11-12) · `ruff check .` bersih ·
> coverage gate `>= 75%` (`--cov=core --cov=handlers --cov=bot --cov=config`) · `bot.py` 1.458 baris +
> 12 modul `handlers/` (analisis, cek, info, jobs, jurnal, market, menu, portfolio, sektor,
> sinyal, trading, watchlist) + 33 modul `core/` (`SCHEMA_VERSION = 6`).
>
> **Prinsip utama:** bot ini adalah alat bantu riset dan belajar saham BEI yang
> jujur terhadap bukti statistik — setiap sinyal, ukuran posisi, dan klaim
> performa harus konsisten antara `research/`, `core/`, dan output bot.

---

## 0. Hasil review plan (baca ini dulu)

Plan versi pertama ditulis dari review kode, lalu repo bergerak lebih cepat
daripada dokumennya. Setelah diaudit baris per baris, inilah selisihnya:

| Klaim plan lama | Kenyataan saat direview | Bukti |
|---|---|---|
| "277 pytest passed" | **406** sebelum sesi ini, **435** sesudah | `pytest -q` |
| Fase 1.1 `auto_adjust` masih `False` | sudah `True` | `core/screener.py:78` |
| Fase 1.1 `tick_size` belum dipakai `/saran` | sudah dipakai, helper bersama ada | `core/pricing.py:21`, `core/saran.py:52,61` |
| Fase 1.1 biaya harus disatukan ke **0,50%** | **0,40%** yang benar; 0,50% adalah dobel hitung pajak | `config.py:82-87`, `research/portfolio.py:52-58` |
| Fase 1.2 masih `date.today()` | sudah WIB semua | `core/marketcal.py:114` |
| Fase 1.3 urutan `cooldown_ok` salah | sudah validasi-dulu | `bot.py:3109` |
| Fase 2.1 `scan_history` belum ada | sudah ada + `core/scorecard.py` + `/jejak` | `core/store.py:86-106` |
| Fase 2.2 turnover 73,6% | **21,7%** satu arah (**43,4%** dua arah) dengan `hold=0,6` + `min_hold=2` + `sma50_200_half` (sebelumnya 50,6% pada `hold=0,4`) | `data/validation.json` → `portfolio` |
| Fase 2.3 `lam=10.0` statis | sudah CV deret waktu di dalam train | `research/evaluate.py:ridge_fit/choose_lam_cv` |
| Fase 4.1 "5 formatter rupiah duplikat" | masih duplikat (belum disentuh) | `bot.py:87,875,2608`, `core/analysis.py:165`, `core/saran.py:445` |
| Fase 3 P12/P13 | belum; **diselesaikan sesi ini** | `bot.py:send_with_retry`, `rencana.budget_plan` |
| Fase 3 P14, P17, D2 | belum; **diselesaikan sesi ini** | `core/cek.py`, `core/portofolio.py`, `bot.build_application` |
| Fase 3 P15, P16 & formatter | belum; **diselesaikan sesi ini** | `core/kualitas.py`, `alerts.buy_zones`, `textutil.rupiah` |
| Plan menyebut `build_verdict` | fungsi itu **tidak ada**; yang dipakai `saran.build_candidate` / `rencana.enrich` | `core/saran.py:66`, `core/rencana.py:124` |

**Konsekuensi:** tiga hal di plan lama tidak lagi relevan (biaya 0,50%,
`auto_adjust=False`, `build_verdict`). Sisa pekerjaan yang benar-benar terbuka
ada di Fase 3 (P14–P18) dan Fase 4 (arsitektur & operasional).

### Angka riset yang harus dibaca dengan benar

`data/validation.json` (evaluasi 2026-11-12 & portofolio 2026-11-14, 45 emiten LQ45,
91.395 baris, 2017-09-07 → 2026-10-09, horizon 20 hari bursa, biaya BEI **0,40%** pulang-pergi):

| Metrik | Nilai | Cara baca |
|---|---|---|
| IC skor antar-emiten | **0,0115**, `t` naif 2,68, **`t_hac` 0,85** | Setelah koreksi autokorelasi Newey-West, **tidak signifikan**. Jangan dipakai sebagai klaim prediksi. |
| IC time-series per emiten | −0,0207 (t −2,33) | Label "beli sekarang" justru sedikit berlawanan. |
| Walk-forward model 12 faktor | rata-rata **0,0304** vs bobot-sama **−0,0061**; menang **4/5** tahun | Model ridge CV lebih baik dari baseline naif (kuintil OOS Q5 **+0,400%** vs Q1 **−0,204%**), tetapi tipis dan 2025 negatif (−0,025). |
| Turnover & eksposur portofolio | **21,7%** satu arah (**43,4%** dua arah) · eksposur **79,8%** · MDD **−20,0%** | Turun dari 73,6% → 50,6% → 21,7% lewat `hold=0,6`, `min_hold=2`, dan filter rezim kausal `sma50_200_half`. |
| Alpha bruto → neto | aktif **+0,534%** (total **+0,416%**) → neto **+0,33%** (+0,330%) /periode, median **+0,385%**, **16/20** fase positif | CI 95% moving-block **[+0,243%; +0,522%]** — di atas nol setelah biaya BEI 0,40% pulang-pergi. |
| Deflated Sharpe & PSR | **DSR 0,86** (`best_sr` 0,279 vs `sr0` 0,142 pada `n_trials=20` fase; **320 trial** pada ablasi `research/experiments.py`) | Tetap di bawah 0,95: bobot `core/relatif.py` dibekukan (disinkronkan dengan `validation.json` dan diverifikasi `tools/check_sync.py`). PSR menguji `SR*=0` per fase tanpa penalti multi-trial. |
| vs IHSG | **+1,19%**/periode, **20/20** fase positif | IHSG berbobot kapitalisasi vs universe sama-bobot — **bukan alpha murni**; hanya konteks. |

---

## Ringkasan Fase

| Fase | Fokus | Status | Prioritas |
|---|---|---|---|
| **Fase 1** | Bug, regresi & ketahanan runtime | ✅ selesai | P0 |
| **Fase 2** | Kalibrasi riset & catatan sinyal live | ✅ selesai | P1 |
| **Fase 3** | Backlog UX (`P12`–`P18`) | ✅ 7 dari 7 (P12–P18) | P1–P2 |
| **Fase 4** | Arsitektur, CI & operasional | ✅ selesai (modularisasi, CI, Docker, backup, sync check) | P2 |

---

## Fase 1 — Fondasi & Regresi (P0) ✅

### 1.1 Konsistensi harga & fraksi bursa BEI
- [x] **`auto_adjust=True` di whole-market screener** — `core/screener.py:78`;
  regresi: `tests/test_screener.py`.
- [x] **Fraksi harga BEI di `/saran`** — helper tunggal `core/pricing.py`
  (`tick_size`, `round_tick`, `ceil_tick`), dipakai `saran.py`, `rencana.py`,
  `backtest_real.py`; tes: `tests/test_pricing.py`.
- [x] **Konstanta biaya transaksi IDX** — `config.py:82-87`
  (`IDX_BUY_FEE=0,15%`, `IDX_SELL_FEE=0,25%` **sudah termasuk** pajak jual
  0,10%; round-trip **0,40%**). `research/portfolio.py` tidak lagi menambah
  `SELL_TAX` (dulu 0,50% = dobel hitung).

### 1.2 Zona waktu WIB & regresi handler
- [x] **Semua tanggal WIB** — `marketcal.now_wib()`; `Dockerfile` menyetel
  `TZ=Asia/Jakarta`. Tidak ada lagi `date.today()` di jalur produksi.
- [x] **Default alert = `alerts.ALL_KINDS`** — `bot.py:1186`, prompt manual
  dibangun dari konstanta yang sama (`bot.py:680`); tes:
  `tests/test_alert_defaults.py`.
- [x] **`/bandar` tidak lagi terpotong 200 emiten** — `max_stocks=1000` +
  pagination (`core/bandar.py:80-110`); tes: `tests/test_bandar_paging.py`.

### 1.3 Ketahanan runtime & cache
- [x] **`cooldown_ok` setelah validasi** — `/scan` (`bot.py:3109`), `/rencana`
  (handler baru memvalidasi kode & anggaran dulu).
- [x] **TTL `plan_cache`** — kunci memuat `scanned_at`/`trade_date`
  (`store.py:680-696`); PENDING & `_last_used` dibersihkan berkala.
- [x] **Lock `banding._mkt_cache`** — `core/banding.py:17`.
- [x] **ML fallback tidak permanen** — kegagalan inferensi hanya menggagalkan
  batch itu (`core/news_ml.py:126-133`); `failed` hanya untuk torch tidak ada.

**Kriteria selesai Fase 1:** ✅ semua bug P0 punya tes regresi, `pytest` 100%.

---

## Fase 2 — Riset Kuantitatif & Catatan Sinyal Live (P1) ✅

### 2.1 Live signal ledger
- [x] **`scan_history`** — tabel `scan_history(trade_date, ticker, …)` dengan
  `closed_at`, `bars`, `fwd_ret` (`core/store.py:86-106`), ditulis idempoten
  oleh `screener.scan` (`core/screener.py:345`), ditutup otomatis setelah
  horizon (`store.close_scan_history`).
- [x] **Evaluasi live** — `core/scorecard.py` menghitung IC & excess Q5 dari
  data live; ditampilkan `/jejak` (bukan `/validasi`, yang tetap memuat
  artefak backtest).

### 2.2 Turnover & validasi portofolio
- [x] **Turnover berbobot + hysteresis + rezim** — `research/portfolio.py`
  (`turnover_cost`, `--hold 0.6 --min-hold 2 --regime sma50_200_half`):
  73,6% → 50,6% (`hold=0,4`) → **21,7%** satu arah (**43,4%** dua arah),
  MDD −35,0% → **−20,0%**, eksposur rata-rata **79,8%**.
- [x] **Bootstrap CI** — moving-block (blok 4 fase) + pembanding iid:
  `ci95_alpha_net` = **[+0,243; +0,522]** (alpha neto **+0,33%**, **16/20** fase positif,
  **DSR 0,86** pada `n_trials=20` fase dan **320 trial** pada `research/experiments.py`).
- [x] **Pembanding IHSG** — `ihsg_period_returns` (`^JKSE`, **+1,19%**/periode,
  **20/20** fase positif), dilaporkan sebagai konteks, bukan alpha murni.

### 2.3 Metodologi model & filter `/rencana`
- [x] **Regularisasi ridge via CV** — `choose_lam_cv` memilih `lam_rel` dari
  grid {0,001 … 1,0} dengan purge horizon; dilaporkan per tahun.
- [x] **Baseline bobot-sama** — `ic_eq` + `walk_forward_summary`; model menang
  4/5 tahun (0,0304 vs −0,0061).
- ⛔ **Survivorship bias & universe transfer (45 LQ45 → 944 BEI)** — **di luar
  cakupan** (keputusan pemilik, 2026-11-12). Prasyarat bila suatu saat
  dikerjakan: `universe_membership` + `delistings` dari unduhan manual IDX
  Stock List atau EODHD; tanpa itu angka apa pun hanya bias cuplikan.
- ⛔ **Faktor non-teknikal (PBV/ROE/earnings yield, low-vol, foreign flow)** —
  **di luar cakupan** (keputusan pemilik). Prasyarat: fundamental
  *point-in-time*; Yahoo hanya memberi snapshot terkini, jadi IC historisnya
  tidak bisa dihitung tanpa sumber data lain.
- ⛔ **Backtest end-to-end pipeline `/rencana`** (ranking + likuiditas +
  fundamental + sentimen sebagai satu paket) — **di luar cakupan** (keputusan
  pemilik). Prasyarat: fundamental + berita historis; yang bisa diuji tanpa
  data itu hanya subset teknikal + likuiditas.

**Kriteria selesai Fase 2:** ✅ `scan_history` jalan & terbaca; artefak riset
memuat CI, hysteresis, dan CV. ⛔ Uji filter gabungan & bias universe
dinyatakan **di luar cakupan** karena butuh data historis yang tidak
tersedia (keputusan pemilik, 2026-11-12).

---

## Fase 3 — Backlog Produk & UX (`P12`–`P18`)

- [x] **P12 — Retry `NetworkError`/`TimedOut` saat kirim pesan & chart**
  - `send_with_retry()` (`bot.py`), dipakai `reply_long`, `_safe_edit`,
    pengiriman chart (`reply_photo` membuka ulang berkas tiap percobaan), dan
    hasil scan di job.
  - **Temuan penting:** `telegram.error.BadRequest` adalah **subclass**
    `NetworkError`, jadi retry mengecualikannya eksplisit — kalau tidak, pesan
    ber-HTML salah dikirim 3×.
  - Tes: `tests/test_send_retry.py`.
- [x] **P13 — Pisahkan `/rencana` (satu emiten) dan `/paket` (multi-emiten)**
  - `/rencana <KODE> [anggaran]` → `rencana.budget_plan()`: lot terjangkau,
    fee beli, % modal, rugi maksimal (termasuk fee jual), untung di TP, RR,
    plus peringatan bila melebihi aturan risiko 1%.
  - Level harga satu sumber: `rencana.price_levels()` dipakai `make_plan` dan
    `budget_plan`, jadi `/rencana BBCA` dan `/paket` tidak mungkin berbeda.
  - Anggaran < 1 lot dijawab jujur (harga 1 lot + fee), bukan 0 lot karangan;
    salah ketik membalas contoh perintah. `/paket [1-5|refresh]` =
    perilaku lama.
  - Parser anggaran menerima `5000000`, `5.000.000`, `5jt`, `1,5jt`, `500rb`.
  - Tes: `tests/test_rencana_single.py` (24 tes).
- [x] **P14 — `/cek <KODE>`** checklist 1 layar — `core/cek.py` (aturan
  poin & veto bisa dibaca di docstring modul) + `bot._cek_gather` yang
  memakai fetcher lama secara paralel. Enam bagian, verdict
  LAYAK/TUNGGU/HINDARI, dan **TIDAK CUKUP DATA** bila ≥3 bagian kosong —
  bukan HINDARI karangan. Bagian tanpa data ditulis apa adanya. Tes:
  `tests/test_cek.py` (15), `tests/test_cek_handler.py` (5).
- [x] **P15 — penanda kualitas + `/sinyal beli bagus`** — `core/kualitas.py`
  (ambang: ROE ≥ 8%, PER ≤ 25, PBV ≤ 4; ditulis di modul). Fundamental
  hanya diambil untuk kandidat BELI yang ditampilkan (maks 20 untuk filter,
  6 untuk daftar, 3 untuk overview) dengan cache harian per tanggal WIB di
  `TMP_DIR`. Emiten tanpa data ditandai ❔ "belum dinilai" dan **tidak**
  ikut lolos/ditolak. Tes: `tests/test_sinyal_kualitas.py` (9).
- [x] **P16 — Alert zona beli watchlist** — `alerts.buy_zones()` (support,
  RSI pulih 30, breakout + volume ≥1,5×) + `check_zone_alerts` di job 30
  menit. State per (chat, emiten) di tabel `zone_state` (skema v4), jadi
  pesan hanya terkirim saat MASUK zona; ikut `/auto on/off`. Pesan memuat
  level SL (1×ATR) & TP (2×ATR). Tes: `tests/test_zone_alerts.py` (8).
- [x] **P17 — `/portofolio` (posisi asli)** — tabel `real_positions` &
  `real_trades` (skema v3, migrasi bernomor; **terpisah** dari paper).
  `/portofolio beli BBCA 5 9000`, `/portofolio jual BBCA 2 9500`,
  `/portofolio riwayat`. Ringkasan (`core/portofolio.py`): PnL per posisi &
  total, bobot, konsentrasi sektor, risiko bila SL (mengutamakan SL eksekusi
  tersimpan dari `real_executions`, fallback **1×ATR** tick-rounded; disatukan
  dengan `saran.stop_price` di Wave 2A) tersentuh, dilengkapi Chandelier
  trailing stop, scale-out TP1, time-stop 20 hari bursa, dan risiko gap ARB 7%.
  Posisi tanpa harga tidak dihitung sebagai nol. Tes:
  `tests/test_portofolio.py` (15).
- [x] **P18 — `/sektor`** — `core/sektor.py` membaca indeks sektor BEI dengan
  parser defensif: pakai field persen bila ada, kalau tidak hitung dari
  `IndexClose`/`IndexPrevious`, kalau tidak dari `IndexChange` (poin).
  Baris tanpa angka dibuang; indeks luas (COMPOSITE/LQ45/…) tidak dianggap
  sektor. Bila tidak ada yang bisa dihitung, bot berkata **tidak tersedia**
  — bukan 0%. Cloudflare diblokir juga dilaporkan jujur. Tes:
  `tests/test_sektor.py` (11).
- [x] **Selaraskan label `BELI`/`JUAL`** — `/sinyal` memakai **Peringkat
  Atas** / **Peringkat Bawah** + catatan "IC OOS ±0,03, bukan probabilitas;
  JUAL bukan instruksi short". Tes: `tests/test_bot_handlers.py`.

---

## Fase 4 — Arsitektur, CI & Operasional

### 4.1 Modularisasi `bot.py` (3.513 baris)
- [x] **Satukan formatter rupiah** — `textutil.rupiah()` dan
  `textutil.rupiah_desimal()` jadi satu implementasi; `bot.fmt_rp`,
  `bot._money`, `analysis.money_fmt`, `saran.fmt` kini mendelegasikan.
  (`bot._rp` sengaja beda: tanpa awalan Rp, untuk tabel sinyal.)
- [x] **Ekstraksi pertama: lapisan laporan** — `esc`, `fmt_rp`, `_rp`,
  `build_report`, `market_context_line`, `data_age_line`,
  `last_macd_cross_note` pindah ke `core/reports.py` (fungsi murni: data
  masuk, string keluar, tanpa jaringan/store). `bot.py` mengimpor balik
  nama yang sama sehingga tes & pemanggil lama tidak berubah.
- [x] **Pecah handler, 5 domain** — `handlers/portfolio.py` (P17),
  `handlers/cek.py` (P14), `handlers/sinyal.py` (P15),
  `handlers/sektor.py` (P18), dan `handlers/market.py` (/scan, /paket,
  /rencana) memakai pola **`deps`**: handler menerima
  modul `bot` sebagai parameter dan mengakses `deps._quote_posisi`,
  `deps.fetch_and_analyze`, `deps.reply_long`, dst. Dengan begitu
  `monkeypatch.setattr(bot, ...)` di tes tetap mengenai sasaran yang sama,
  tanpa impor melingkar (handler tidak mengimpor `bot`). `bot.py` kini
  menyimpan wrapper tipis + alias nama lama.
- [x] **Pecah handler, domain terakhir** — `handlers/analisis.py`
  (`/analisis`, `/skor`, `/banding`), `handlers/info.py` (`/start`,
  `/menu`, `/help`, `/id`, `/validasi`, `/status`, `/berita`,
  `/fundamental`), `handlers/jobs.py`
  (alert 30 menit, autoscan, screener 16:15, backup, heartbeat), dan
  `core/guards.py` (cooldown + PENDING). Semua memakai pola `deps`;
  `bot.py` tinggal 1.458 baris berisi **menu/aksi, markup, dan
  entrypoint** — seluruh perintah ada di 12 modul `handlers/*.py`
  (termasuk `handlers/menu.py` dan `handlers/jurnal.py`). Seam tes
  (`run_menu_action`, `build_chart`, `TMP_DIR`,
  `ALLOWED_IDS`, `MAX_WATCH`, `_screener_running`) tetap bekerja karena
  diakses lewat `deps` atau tetap di `bot`.
- [x] **Non-blocking SQLite** — seluruh panggilan `store.*` di
  `handlers/*.py` **dan** `bot.py` sekarang lewat `asyncio.to_thread`
  (grep `store\.[a-z_]+(` tidak menyisakan panggilan sinkron di jalur
  async). Satu pengecualian disengaja: `_snapshot_row` tetap fungsi
  sinkron dan selalu dipanggil lewat `asyncio.to_thread` — didokumentasi
  di docstring-nya.

### 4.2 Kualitas kode, testing & CI
- [x] **CI**: matriks 3.12/3.13, `ruff check .`, `pip check`, coverage gate
  75%, artefak `coverage.xml` (`.github/workflows/ci.yml`).
- [x] **Fixture kontrak data** — `tests/fixtures/` memuat OHLCV
  ter-normalisasi + ringkasan saham/indeks BEI, dengan tes replay
  (`tests/test_fixtures_replay.py`) yang mengunci bentuk kolom yang
  dikonsumsi. Fixture-nya **sintetis**; `tools/record_fixtures.py`
  merekam respons asli (sekali, di mesin berjaringan) tanpa menyimpan
  kredensial.
- [x] **CI riset mingguan** — `.github/workflows/research.yml` (Senin 08:00
  WIB) menjalankan `research/evaluate.py --period 3y --limit 12 --min-ic 0`;
  exit 2 bila IC rata-rata < 0, dan laporan tetap diunggah sebagai artefak.
  Ini alarm dini sampel kecil, bukan pengganti evaluasi 10 tahun.
- ⛔ **`universe_membership` + `delistings`** — **di luar cakupan** (keputusan
  pemilik, 2026-11-12). Datanya hanya bisa didapat lewat unduhan manual IDX
  Stock List atau sumber berbayar (EODHD); tanpa itu tabel keanggotaan tidak
  bisa diisi dan uji survivorship tidak bisa diklaim.

### 4.3 Operasional produksi
- [x] **Backup SQLite otomatis & verifikasi restore** — `store.backup_to()`
  (API backup SQLite WAL-safe + `PRAGMA integrity_check`), rotasi 7, job harian
  23:30 WIB (`bot.py:backup_job`), skrip manual `tools/backup_db.py`, serta
  uji otomatis round-trip backup -> restore di `tools/check_sync.py`.
- [x] **Migrasi bernomor** — **`SCHEMA_VERSION = 6`** + `_MIGRATIONS`
  (`core/store.py`: v1–v2 dasar & `scan_history`, v3 `real_positions`/`real_trades`,
  v4 `zone_state`, v5 `signal_context` dengan kolom `intent` & `real_executions`,
  v6 `chat_settings`).
- [x] **`concurrent_updates(4)` + `AIORateLimiter`** — `bot.build_application()`:
  4 update paralel, `connection_pool_size(8)`, `AIORateLimiter(max_retries=2)`;
  `/cek` juga memindahkan baca store ke `asyncio.to_thread`. Tes:
  `tests/test_app_builder.py`. Dependensi: extra `[rate-limiter]` +
  `tenacity==9.2.1` di `requirements.txt`.
- [x] **Alert admin** — `notify_admin()` dipakai saat screener harian gagal
  atau >100 kode gagal; tujuan `ADMIN_CHAT_ID` (fallback `ADMIN_IDS[0]`).
  Tes: `tests/test_admin_alert.py` (6).
- [x] **Logging terstruktur** — `core/logsetup.py`: `JsonFormatter` satu
  baris (ts/level/logger/msg + `cmd`, `chat_id`, `duration_ms`, `ok`,
  `exc`); `timed()` menulis baris itu hanya saat `LOG_JSON=1`, jadi log
  teks tetap seperti dulu. Tes: `tests/test_logsetup.py` (7).
- [x] **`LICENSE`** — MIT untuk kode repo (copyright 2026 Bagas Adi), dengan
  catatan bahwa data pasar tetap terikat ketentuan Yahoo/BEI dan model
  pihak ketiga mengikuti lisensi pembuatnya. Bisa diganti kapan saja.

---

## Track A & B — Kontrol Risiko Rezim & Riset Turnover/Horizon (2026-11-14)

- [x] **Filter rezim IHSG (`core/regime.py`)** — aturan kausal `^JKSE`
  `SMA50 < SMA200` memangkas eksposur & risiko ke `50%` (`NA` bila `<200` bar).
  Dipakai di `/paket` (`0,5%/10%/45%`), `/saran` (`0,5%` + tinjauan band tahan
  top 60% `TAHAN`/`ROTASI`), serta simulasi `research/portfolio.py` &
  `research/experiments.py`. Tes baru: `tests/test_regime.py`.
- [x] **Riset turnover & horizon (`research/experiments.py`, `data/validation.json`)**
  — `research/experiments.py --offline --horizons 20 40 60` menguji 16 varian
  (320 trial) plus perbandingan walk-forward 20d/40d/60d. Bukti memvalidasi
  untuk **tetap memakai horizon prediksi 20 hari** (WF IC `+0,0265` vs
  `-0,0032` di 40d & `-0,0134` di 60d) sambil memangkas turnover & drawdown
  lewat `hold=0,6` + `min_hold=2` + rezim `sma50_200_half` (MDD `-35,0% -> -20,0%`,
  `alpha_active_gross +0,534%`, `alpha_net +0,330%`, `DSR 0,86 < 0,95`). Bobot
  `core/relatif.py` tetap beku karena gerbang DSR `0,95` belum tembus.

---

## Track C — Manajemen Risiko Portofolio (P23/P24, 2026-11-14)

- [x] **P24 — Laporan risiko portofolio** (`core/risk.py`,
  `handlers/portfolio.py`) — `/portofolio` menampilkan eksposur, konsentrasi
  sektor, drawdown berjalan, volatilitas tahunan, VaR 95% 1 hari, beta vs
  IHSG, dan korelasi rata-rata/tertinggi. Asumsi (bobot sekarang, rebalance
  harian) ditulis di docstring; data yang tidak tersedia tidak diisi nol.
- [x] **P23 — Batas portofolio** (`core/risk.check_limits`,
  `handlers/market.py`, `handlers/cek.py`) — batas 25% modal/posisi dan 40%
  modal/sektor; `/rencana` menolak rencana yang melanggar dengan alasan
  nominal + lot maksimum yang lolos; `/cek` memperingatkan posisi yang sudah
  melewati batas. Tanpa anggaran eksplisit, `/rencana` memakai 25% modal.
- [x] **P23 lanjutan** (`core/store.py` v6 `chat_settings`, `/portofolio batas`,
  `core/risk.validate_limits`, `core/risk.inverse_vol_weights`) — batas posisi
  (5–50%, bawaan 25%) & sektor (10–80%, bawaan 40%) dapat diubah/reset per chat
  dan otomatis berlaku di `/rencana`, `/cek`, dan `/portofolio`; laporan
  portofolio menampilkan target bobot penyeimbang volatilitas (*inverse-vol* ala
  Riskfolio-Lib).
- [x] **Forensik arus kas & debat Pro/Risiko** (`.agents/skills/idx-equity-research/SKILL.md`,
  `core/fundamental.analyze_cashflow_quality`, `core/cek.py`, `handlers/cek.py`,
  `handlers/info.py`, `core/rencana.py`) — audit OCF/laba bersih (akrual), FCF,
  kas bersih, current ratio, dan deteksi *dividend trap* (memisahkan aturan
  sektor bank vs non-bank, jujur `"tidak tersedia"` bila kosong), plus ringkasan
  **Sudut Pandang Pro (Bull) vs Risiko (Bear)** di `/cek`.
- [x] **P28 — Jurnal eksekusi manual Stockbit & slippage nyata** (`core/jurnal.py`,
  `handlers/jurnal.py`, `core/store.py` v5, `handlers/jobs.py`,
  `handlers/menu.py`, `bot.py`) — konfirmasi 1-tap **"Sudah beli di Stockbit"**
  setelah `/rencana`, `/cek`, `/beli`, dan `/sinyal KODE`, perintah `/jurnal`
  (`beli`/`jual`/`riwayat`), slippage vs harga sinyal (`"tidak tersedia"` bila
  tanpa acuan), win rate & profit factor realisasi setelah biaya BEI `0,40%`,
  pemisahan tegas ASLI vs PAPER, sinkronisasi ke `/portofolio` & exit lewat
  `/portofolio jual`, serta ringkasan mingguan Jumat `16:45 WIB` (`/auto on`).
  Tanpa auto-trading, login Stockbit, atau kredensial.
- [ ] **P19–P22, P25–P27, P29–P31** — menunggu keputusan D0–D3 (sumber data,
  biaya, kriteria paper trading, VPS) dan/atau item berikutnya di batch 6
  (catatan: untuk P25, adapter fallback OHLCV `.JK` opt-in berbasis env
  `EODHD_API_KEY` / `ALPHAVANTAGE_API_KEY` sudah disiapkan di `core/data.py`,
  nonaktif secara default tanpa kunci).

---

## Wave 2A — Akurasi Fundamental, Panduan Exit Terpadu, Ketahanan Infra & Sinkronisasi Riset (2026-11-14, merge `8489697`)

- [x] **Kejujuran & akurasi fundamental (`core/fundamental.py`, `core/kualitas.py`, `core/cek.py`, `core/powerscore.py`, `core/reports.py`)** —
  normalisasi `returnOnEquity * 100.0` saat ambil data dengan penanda `roe_unit="pct"` dan
  versi cache `_v=2` (mencegah bug ROE desimal `0,18` terkena veto `< 2%` sekaligus menjaga
  ROE kecil `0,2%`–`2,9%` tidak dikali 100 dua kali); pembacaan `financialCurrency` agar laporan
  keuangan non-IDR (mis. `USD`) tidak dilabeli `Rp`; integrasi `forensic_penalty` arus kas ke
  `score_fundamentals` & `powerscore.combine` + `filing_note` POJK 14; pemisahan valuasi
  `"Murah (PER & PBV)"` vs `"Campuran"`, label `"Kinerja harga 20h vs peers"`, serta
  `trend_label` deskriptif dengan disclaimer `IC harian ±0,01`.
- [x] **Standar risiko & panduan exit/jual terpadu (`core/saran.py`, `core/rencana.py`, `core/portofolio.py`, `core/risk.py`, `core/jurnal.py`, `core/store.py`)** —
  penyatuan initial SL `1×ATR` tick-rounded (`STOP_ATR=1.0`, memprioritaskan `sl` eksekusi
  tersimpan dari `real_executions`), **Chandelier Trailing Exit** (`High_max(22) - 3×ATR14`,
  ratcheting), **Scale-out 50% di TP1 (`+1,5×ATR`) + geser SL sisa lot ke BEP+0,40% fee BEI**,
  **Time-stop >= 20 hari bursa** (`time_stop_status` & `MIN_HOLD_CYCLES=2`), aturan **BEI 2026
  Kep-00136 (floor reguler Rp1 vs filter likuiditas Rp50) & Kep-00158 (ARB asimetris 7%)**,
  isolasi `intent="beli"` vs `"jual"` di `signal_context`, serta diagnostik **MAE / MFE &
  R-multiple** neto setelah fee `0,40%` di `/jurnal`.
- [x] **Ketahanan data, screener, Docker & CI (`core/data.py`, `core/data_quality.py`, `core/pricing.py`, `core/screener.py`, `core/news.py`, `Dockerfile`, `.github/workflows/ci.yml`)** —
  pemisahan cache disk per `period` (agar fetch `1mo` tidak menimpa cache `1y`), `yfinance`
  `repair=True` + provenance WIB (`format_provenance`), filter kualitas & bar basi di screener
  + pembulatan tick BEI, query berita ketat (`"{nama}" OR "{KODE}.JK" OR "saham {KODE}"`),
  perbaikan fatal `Dockerfile` (`COPY handlers/ ./handlers/` & `COPY vendor/ ./vendor/`),
  penambahan `--cov=handlers`, dan job CI `docker-smoke`.
- [x] **Sinkronisasi riset & audit offline (`core/relatif.py`, `core/scorecard.py`, `research/*`, `tools/check_sync.py`, `tools/record_fixtures.py`)** —
  penyelarasan 100% `WEIGHTS` & `OOS_QUINTILES` di `core/relatif.py` ke `data/validation.json`,
  evaluasi terpisah sinyal `JUAL` (`TURUN`/`NAIK`/`DATAR`) & `live_ic` `t_hac` Newey-West di
  `/jejak`, penambahan **PSR** (`probabilistic_sharpe`) & pita **P25–P75 IQR**, serta alat
  verifikasi `python tools/check_sync.py` dan `python tools/record_fixtures.py --verify`.
  Checkpoint tes pasca-merge Wave 2A (`8489697`): **`653 passed`** (100% offline).

---

## Urutan kerja yang disarankan (setelah sesi ini)

| # | Pekerjaan | Kenapa sekarang | Ukuran selesai |
|---|---|---|---|
| 1 | **Uji live tingkat chat di host** | ⛔ **diblokir akses**: sesi ini tidak punya `BOT_TOKEN` host (env kosong, tidak ada di riwayat git), dan pemilik memilih menjalankan sendiri | Empat balasan ditempel di chat; templat di `deploy.md` |
| 2 | **Menu & markup tetap di `bot.py`** | Bagian aplikasi (callback, tombol, entrypoint); bukan logika perintah | Tidak perlu dipindah |
| 3 | **Rekam fixture asli** | Fixture sekarang sintetis (`--verify` lulus offline) | `python tools/record_fixtures.py --ticker BBCA --ohlcv-rows 120 --rows 5` lalu commit hasilnya |
| 4 | **Push & deploy** | Commit lokal belum di-push | `git push` + restart bot, cek `/status` |

## Verifikasi runtime (diblokir akses, bukan pekerjaan kode)

Uji tingkat chat — `/status`, `/sektor`, `/cek BBCA`, `/sinyal beli bagus`
benar-benar terkirim di Telegram — **belum bisa dijalankan dari sesi ini**:

- `BOT_TOKEN` di mesin ini kosong (panjang 0); `getMe` menghasilkan
  `InvalidToken`. Token tidak ada di env, `.env`, maupun riwayat git.
- Uji data-level sudah dijalankan (Yahoo/BEI sungguhan) dan hasilnya dicatat
  di `deploy.md`: `/status` OK, `/sektor` jujur "tidak bisa dibaca" (BEI
  memblokir IP ini), `/cek BBCA` OK dengan ROE hilang ditandai, `/sinyal beli
  bagus` benar-benar tidak menemukan kandidat BELI.
- Pemilik memilih menjalankan checklist chat di host (2026-11-12); templat
  hasil ada di `deploy.md` bagian "Templat hasil uji chat".

Status ini **tidak** dihitung selesai sampai empat balasan host ditempel.

**Jangan dilakukan:** menaikkan bobot faktor karena `t` naif 2,68 (yang
bertahan hanya `t_hac` 0,85); mengklaim survivorship bias tanpa tabel
keanggotaan; menambah scraper IDX/Stockbit; refactor `bot.py` sekaligus (satu domain per commit).

---

## Definisi "selesai" yang dipakai

Satu item selesai hanya jika: (a) `pytest` lulus dengan jumlah tes naik,
(b) `ruff check .` bersih dan coverage tidak turun dari 75%, (c) ada bukti
output perintah nyata (bukan klaim), dan (d) angka di `data/validation.json`
konsisten dengan kode yang berjalan. Kalau salah satu tidak terpenuhi, item
dilaporkan **belum selesai**.
