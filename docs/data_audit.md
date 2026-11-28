# Laporan Audit Data & Baseline — Fase 0 & Fase 1 SahamBot

> **Tanggal audit:** 2026-11-28  
> **Baseline dibekukan di:** `data/baseline_v0.json` (salinan persis `data/validation.json` per 2026-11-24)  
> **Register multiple testing:** `research/experiment_log.csv`  
> **Skrip audit Fase 1:** `research/phase1_audit.py`

---

## 1. Hasil Audit Kode Aktual (Fase 0)

Plan perbaikan disusun dari pembacaan README; hasil pemeriksaan langsung pada kode sumber (`research/evaluate.py`, `research/portfolio.py`, `core/data.py`, `core/fundamental.py`) menunjukkan:

| Area Audit | Status Aktual di Kode | Bukti & Catatan Keterbatasan |
|---|---|---|
| **Baseline beku** | ✅ Dibekukan | Disalin ke `data/baseline_v0.json` (`45` emiten LQ45 2026, `91.428` baris, `2017-09-26` s.d. `2026-10-27`, horizon 20 hari bursa). |
| **Look-ahead di `research/evaluate.py`** | ✅ Bersih pada fitur & CV | `_build_ticker_frame` murni kausal (`<= i`); `zscore_by_date` strictly per tanggal (`groupby("date")`); `choose_lam_cv` memilih `lam_rel` lewat expanding-window CV di dalam train dengan purge buffer `ceil(horizon * 7 / 5) + 2 + embargo_days`. **Catatan:** target `fwd` memakai `close[i+20]/close[i]-1` (eksekusi di close sinyal, bukan open `t+1`; perbaikan realisme eksekusi dicatat untuk Fase 4). |
| **Penyesuaian harga (`Adj Close`)** | ✅ `auto_adjust=True` + `repair=True` | `core/data.py` & `core/screener.py` memakai `auto_adjust=True` dan `download_yf_with_safe_repair`. Pada audit 11 sampel cache 1 tahun (`temp/cache/*_1y.csv`, 240 bar/emiten): 10/11 bersih (`ANTM`, `ASGR`, `ASII`, `BBCA`, `BBRI`, `BMRI`, `BRIS`, `CTRA`, `PWON`, `UNTR`), dan 1/11 (`MPXL`) terdeteksi `{'volume_nol': 1, 'unexplained_jump': 1}` oleh `core/data_quality.py`. |
| **Survivorship bias** | ⚠️ Keterbatasan nyata | `data/validation.json` memakai `LQ45_2026` (45 emiten aktif) dan `data/idx_universe.json` (944 emiten aktif) tanpa data konstituen historis (`universe_membership.json`) dan tanpa emiten *delisted* 2017–2026. Skrip pengukur `research/bias.py` sudah tersedia, menunggu pasokan daftar keanggotaan historis. |
| **Lag publikasi fundamental** | ✅ Dijaga & tidak bocor ke OOS | `core/fundamental.py` mengambil snapshot fundamental terkini (bukan *point-in-time* historis) dan menampilkan `filing_note` POJK 14/POJK.04/2022. Karena itu fundamental **tidak** dimasukkan ke bobot OOS 12 faktor (`core/relatif.py`). |
| **Biaya transaksi & turnover** | ✅ Sudah terpasang di `research/portfolio.py` | Biaya BEI all-in **0,40%** pulang-pergi (`IDX_BUY_FEE=0,15%`, `IDX_SELL_FEE=0,25%` termasuk pajak jual 0,10%; tidak dobel hitung), dihitung lintas semua 20 fase non-overlapping dengan hysteresis `hold=0,6`, `min_hold=2`. |

---

## 2. Hasil Evaluasi Fase 1 (Biaya, Likuiditas, Kapasitas & Benchmark Naif)

### A. Baseline 10 Tahun Penuh (`data/baseline_v0.json`, 45 emiten LQ45, 2017–2026)
- **Walk-forward IC 12 faktor (2022–2026):** rata-rata **`+0,0294`** (mengalahkan equal-weight `-0,0045` di **4 dari 5 tahun**; 2025 negatif `-0,0243`).
- **Alpha neto setelah biaya BEI 0,40%:** rata-rata **`+0,376%` / 20 hari** (median `+0,539%`, **16/20 fase positif**, moving-block bootstrap CI 95% **`[+0,223%, +0,620%]`**, tidak mencakup 0).
- **Turnover & Risiko:** turnover satu arah **23,7%** (dua arah 47,3%), max drawdown rata-rata `-30,3%`, **PSR 0,97**, **DSR 0,75** (`n_trials=20` fase; total 400 trial ablasi tercatat di `research/experiment_log.csv`).
- **Filter Rezim (`data/regime_eval.json`):** `vol_breadth_3tier` menaikkan Sharpe `0,720 → 0,789` dan memangkas Max DD `-48,57% → -33,16%` (Calmar `0,380 → 0,457`, eksposur rata-rata 76,8%).

### B. Audit Sampel Cache Lokal — Perbandingan Benchmark Naif & Filter ADTV (`research/phase1_audit.py --offline`, 11 emiten 1y, 20 fase)

| Filter ADTV | Strategi | Alpha Net / 20h | CI 95% (Block) | Turnover 1-Arah | Fase Positif | Kapasitas 5% ADTV / Posisi |
|---|---|---:|---|---:|---:|---:|
| **ADTV ≥ Rp2 M** | Model 12 Faktor (beku) | `+0,296%` | `[-0,782%, +1,779%]` | `49,6%` | `11/20` | `Rp4,53 M` |
| **ADTV ≥ Rp2 M** | Benchmark Momentum 12-1 | `+2,639%` | `[+1,035%, +4,385%]` | `30,3%` | `15/20` | `Rp18,46 M` |
| **ADTV ≥ Rp2 M** | Benchmark Low-Vol | `-1,449%` | `[-2,041%, -0,788%]` | `40,3%` | `1/20` | `Rp13,80 M` |
| **ADTV ≥ Rp2 M** | Benchmark Acak | `-0,556%` | `[-1,643%, +0,316%]` | `43,2%` | `10/20` | `Rp7,97 M` |
| **ADTV ≥ Rp5 M** | Model 12 Faktor (beku) | `+0,517%` | `[-0,302%, +1,109%]` | `45,1%` | `15/20` | `Rp5,18 M` |
| **ADTV ≥ Rp5 M** | Benchmark Momentum 12-1 | `+0,742%` | `[-0,036%, +1,512%]` | `27,8%` | `15/20` | `Rp26,40 M` |
| **ADTV ≥ Rp5 M** | Benchmark Low-Vol | `-0,590%` | `[-1,024%, -0,226%]` | `34,6%` | `6/20` | `Rp13,74 M` |
| **ADTV ≥ Rp5 M** | Benchmark Acak | `+0,104%` | `[-0,795%, +0,977%]` | `38,3%` | `12/20` | `Rp11,19 M` |

*(Catatan: sampel cache lokal 11 emiten × 1 tahun hanya audit cepat offline untuk verifikasi mekanisme benchmark & kapasitas; keputusan statistik utama tetap mengacu pada baseline 10 tahun 45 emiten di `data/baseline_v0.json`.)*

---

## 3. Keputusan Gate Fase 1

- **Hasil Gate:** **POSITIF TIPIS & TERUKUR (`+0,376%` net/20h, CI 95% `[+0,223%, +0,620%]` di atas nol pada 10 tahun LQ45), NAMUN `DSR = 0,75 < 0,95` dan masih mengandung keterbatasan *survivorship bias*.**
- **Keputusan Operasional:**
  1. **Pertahankan bobot `core/relatif.py` dalam status BEKU** (dilarang *re-tune* sampai ada kandidat baru yang lolos `DSR >= 0,95`).
  2. **Pertahankan klaim produk yang jujur & ketat:** SahamBot diposisikan sebagai **alat disiplin risiko, ceklist forensik, & penyaring peringkat relatif ber-*edge* tipis**, bukan mesin ramal pasti untung.
  3. **Lanjut ke Fase 2:** validasi PowerScore & kalibrasi verdict Ceklist (`LAYAK`/`TUNGGU`/`HINDARI`) beserta label transparansi **Tervalidasi OOS vs Heuristik** di UI, serta catat **Momentum 12-1** (turnover lebih rendah `27,8%` vs `45,1%`) sebagai kandidat faktor utama untuk diuji di Fase 3.

---

## 4. Hasil Evaluasi Fase 2 — Validasi PowerScore & Kalibrasi Verdict Ceklist

Dijalankan melalui `python research/evaluate_powerscore.py --offline` (11 emiten cache 1y, 308 titik evaluasi, horizon 20 hari bursa):

| Varian Skor Teknikal | Mean IC | IR | t-stat | t-HAC | Kesimpulan |
|---|---:|---:|---:|---:|---|
| **Skor Momentum 9 Sinyal (`tech_9sig`)** | `-0,0883` | `-0,214` | `-1,13` | `-1,15` | Tidak punya daya beda lintas-emiten; tetap **Heuristik**. |
| **Peringkat Relatif 12 Faktor (`relatif_12f`)** | **`+0,1247`** | **`+0,466`** | **`+2,47`** | **`+2,16`** | Unggul signifikan; layak menjadi tulang punggung komponen teknikal PowerScore (`rel_pct`). |
| **Hybrid 50/50 (`rel_pct` + `tech_9sig`)** | `+0,0617` | `+0,193` | `+1,02` | `+0,97` | Memperbaiki `tech_9sig` secara nyata, tetapi lebih lemah dibanding `relatif_12f` murni. |

### Kalibrasi Verdict & Transparansi UI
- Tanpa fundamental & berita *point-in-time* historis, `cek.build` secara jujur memisahkan baris `TIDAK CUKUP DATA` (275 baris) dari baris ter-veto likuiditas `HINDARI` (33 baris). Veto likuiditas (`< Rp2 M/hari`) berfungsi sebagai **pengaman eksekusi & risiko slippage**, bukan prediksi arah harga jangka pendek.
- **Tindakan Fase 2 yang diterapkan di kode & UI:**
  1. `core/powerscore.py:combine()` kini menerima `rel_pct` (Peringkat Relatif 12F tervalidasi OOS) dan mengeluarkan `validation_status` + `validation_badge`.
  2. `core/scorecard.py` (`/jejak`) diperluas dengan `summarize_by_quintile()` — memecah histori live per kuintil (`Q1..Q5`) lengkap dengan jumlah sampel `n` dan bootstrap CI 95%.
  3. UI Desktop Modern kini menampilkan badge transparansi eksplisit: **`Rank 12F: Tervalidasi OOS`** vs **`Heuristik`** pada PowerScore dan Analisator Pro 6 Pilar.

---

## 5. Hasil Uji Ablasi Fase 3a — Kontribusi Marjinal Faktor Baru (`research/phase3_ablation.py --offline`)

| Varian | Mean IC | t-HAC | Alpha Net / 20h | Delta vs Base | Turnover 1-Arah | Fase Positif |
|---|---:|---:|---:|---:|---:|---:|
| `baseline_12f` | `+0,0987` | `1,52` | `+0,296%` | `0,000%` | `49,6%` | `11/20` |
| **`plus_mom_12_1_25pct`** | **`+0,1151`** | **`1,63`** | **`+1,151%`** | **`+0,855%`** | `49,9%` | **`13/20`** |
| `plus_low_vol_25pct` | `+0,0875` | `1,54` | `+0,440%` | `+0,144%` | `47,7%` | `10/20` |
| `plus_liquidity_15pct` | `+0,0818` | `1,28` | `+0,221%` | `-0,075%` | `47,3%` | `10/20` |
| `combo_12f_mom_liq` | `+0,1127` | `1,52` | `+0,735%` | `+0,439%` | `47,0%` | `13/20` |

- **Kesimpulan Fase 3a:** **Momentum 12-1** adalah satu-satunya kandidat yang menaikkan IC sekaligus *alpha net* secara konsisten, sedangkan `log_adtv` justru menurunkan IC & alpha (dibuang). Sesuai disiplin anti-overfitting, `core/relatif.py` tetap dibekukan sampai `plus_mom_12_1` diuji pada dataset 10 tahun penuh LQ45 dan lolos gerbang `DSR >= 0,95`.
- **Modul Produksi Baru Fase 3–6:** `core/risk_flags.py` (deteksi saham gorengan / `RISIKO_SPEKULATIF` masuk veto `/cek`), `core/risk.py` (`days_to_exit` + `stress_test_portfolio` tampil di Desktop Modern), dan `core/health.py` (Data Health Monitor terpasang di `GET /api/health`).
