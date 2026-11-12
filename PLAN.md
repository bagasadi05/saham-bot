# Rencana Perbaikan & Pengembangan — SahamBuddy

> **Dibuat:** 2026-11-12  
> **Baseline:** commit `2e303ae` (`main`) · **277 pytest passed**  
> **Prinsip utama:** bot ini adalah alat bantu riset dan belajar saham BEI yang jujur terhadap bukti statistik — setiap sinyal, ukuran posisi, dan klaim performa harus konsisten antara `research/`, `core/`, dan output bot.

---

## Ringkasan Fase

| Fase | Fokus | Target Waktu | Prioritas |
| :--- | :--- | :--- | :--- |
| **Fase 1** | Perbaikan bug, regresi & ketahanan runtime | Minggu 1 | **P0** |
| **Fase 2** | Kalibrasi riset kuantitatif & catatan sinyal live | Minggu 2–3 | **P1** |
| **Fase 3** | Penyelesaian backlog UX (`P12`–`P18`) & pemisahan `/rencana` | Minggu 3–4 | **P1–P2** |
| **Fase 4** | Refactor arsitektur `bot.py`, CI/linting & operasional | Berjalan terus | **P2** |

---

## Fase 1 — Perbaikan Fondasi & Regresi (P0)

Masalah pada fase ini memengaruhi kebenaran angka sinyal, harga, tanggal, dan stabilitas bot saat ini.

### 1.1 Konsistensi Harga & Fraksi Bursa BEI
- [ ] **Samakan `auto_adjust=True` pada whole-market screener**
  - **Target:** [`core/screener.py`](file:///d:/Coding/SahamBot/core/screener.py#L74-L80)
  - **Masalah:** `_download_chunk` memakai `auto_adjust=False`, berbeda dengan [`core/data.py`](file:///d:/Coding/SahamBot/core/data.py#L158-L159) dan [`research/evaluate.py`](file:///d:/Coding/SahamBot/research/evaluate.py) (`auto_adjust=True`). Stock split/dividen dalam jendela 1 tahun merusak `sma20/50/200`, `ret20d`, `atr14`, dan z-score 12-faktor.
  - **Perubahan:** Ubah ke `auto_adjust=True` dan tambahkan test regresi di `tests/test_screener.py`.
- [ ] **Terapkan pembulatan fraksi harga BEI (`tick_size`) di `/saran`**
  - **Target:** [`core/saran.py`](file:///d:/Coding/SahamBot/core/saran.py#L73-L74), [`core/saran.py`](file:///d:/Coding/SahamBot/core/saran.py#L236-L237), [`core/rencana.py`](file:///d:/Coding/SahamBot/core/rencana.py#L299-L307)
  - **Masalah:** `rencana.py` sudah membulatkan entry/SL/TP memakai `tick_size()`, tetapi `saran.py` masih memakai `round(x, 0)` sehingga level SL/TP bisa jatuh di luar fraksi harga Stockbit.
  - **Perubahan:** Ekstrak `tick_size`, `_round_tick`, `_ceil_tick` ke helper bersama (mis. `core/marketcal.py` atau `core/textutil.py` / `core/pricing.py`) dan pakai di `saran.py` maupun `rencana.py`.
- [ ] **Satukan konstanta biaya transaksi IDX**
  - **Target:** [`config.py`](file:///d:/Coding/SahamBot/config.py), [`core/saran.py`](file:///d:/Coding/SahamBot/core/saran.py#L41-L42), [`core/rencana.py`](file:///d:/Coding/SahamBot/core/rencana.py), [`research/portfolio.py`](file:///d:/Coding/SahamBot/research/portfolio.py#L40-L43)
  - **Masalah:** `saran.py` memakai total `0,40%` round-trip (`BUY_FEE=0.0015`, `SELL_FEE=0.0025`), sedangkan `research/portfolio.py` memakai `0,50%` (termasuk `SELL_TAX=0.0010`).
  - **Perubahan:** Definisikan `IDX_BUY_FEE`, `IDX_SELL_FEE`, `IDX_SELL_TAX` di `config.py` (total `0,50%`) agar kalkulasi paper trade, rencana beli, dan riset memakai asumsi biaya yang sama.

### 1.2 Zona Waktu WIB & Regresi Handler
- [ ] **Ganti seluruh `date.today()` ke tanggal WIB (`marketcal.now_wib().date()`)**
  - **Target:** [`core/marketcal.py`](file:///d:/Coding/SahamBot/core/marketcal.py#L84), [`core/saran.py`](file:///d:/Coding/SahamBot/core/saran.py#L322), [`bot.py`](file:///d:/Coding/SahamBot/bot.py#L213), [`Dockerfile`](file:///d:/Coding/SahamBot/Dockerfile#L6-L8)
  - **Perubahan:** Pakai `marketcal.now_wib().date()` untuk default tanggal di `last_trading_day()`, `update_paper()`, dan cek umur cache di `fetch_and_analyze()`. Tambahkan `TZ=Asia/Jakarta` di `Dockerfile`.
- [ ] **Kembalikan default alert ke `alerts.ALL_KINDS`**
  - **Target:** [`bot.py`](file:///d:/Coding/SahamBot/bot.py#L1134-L1136), [`bot.py`](file:///d:/Coding/SahamBot/bot.py#L631), [`core/alerts.py`](file:///d:/Coding/SahamBot/core/alerts.py#L32)
  - **Masalah:** `_do_alert_add` meng-hardcode 7 jenis alert sehingga `breakdown` dan `vol_spike` hilang lagi dari default.
  - **Perubahan:** Gunakan `set(alerts.ALL_KINDS)` dan perbarui `_MANUAL_PROMPT["alert_add"]` secara dinamis dari `alerts.ALL_KINDS`.
- [ ] **Perbaiki cakupan `/bandar` yang terpotong di 200 emiten**
  - **Target:** [`bot.py`](file:///d:/Coding/SahamBot/bot.py#L1167), [`core/bandar.py`](file:///d:/Coding/SahamBot/core/bandar.py)
  - **Perubahan:** Pastikan saat user mencari ticker spesifik (`/bandar <KODE>`) atau ringkasan pasar, pagination mengambil seluruh universe (atau filter parameter ticker/paging penuh) tanpa berhenti di `max_stocks=200`.

### 1.3 Ketahanan Runtime & Cache
- [ ] **Urutan `cooldown_ok`** — [`bot.py`](file:///d:/Coding/SahamBot/bot.py#L636), [`bot.py`](file:///d:/Coding/SahamBot/bot.py#L2881), [`bot.py`](file:///d:/Coding/SahamBot/bot.py#L2972)
  - Pindahkan `cooldown_ok` **setelah** validasi input, cek ketersediaan snapshot/cache, dan cek `_screener_running` agar user tidak terkena cooldown saat error/status.
- [ ] **TTL untuk `plan_cache`, `PENDING`, dan `_last_used`** — [`bot.py`](file:///d:/Coding/SahamBot/bot.py#L2850), [`core/store.py`](file:///d:/Coding/SahamBot/core/store.py#L90-L95)
  - Sertakan `marketcal.now_wib().date().isoformat()` atau umur maksimum (mis. 18 jam) di invalidasi `plan_cache`, serta bersihkan entry lama pada `PENDING` & `_last_used` (> 1 jam).
- [ ] **Thread safety & ML fallback resilience**
  - Tambah `threading.Lock` pada `banding._mkt_cache` di [`core/banding.py`](file:///d:/Coding/SahamBot/core/banding.py#L15).
  - Pisahkan error load model vs error inferensi di [`core/news_ml.py`](file:///d:/Coding/SahamBot/core/news_ml.py#L116-L119) agar exception sementara di satu batch tidak mematikan model ML secara permanen.

**Kriteria selesai Fase 1:**
- Seluruh bug P0 punya unit test regresi (termasuk mock jam `02:00 WIB` vs UTC, default `ALL_KINDS`, konsistensi `auto_adjust=True`, dan `tick_size` di `saran.py`).
- `pytest` lulus 100%.

---

## Fase 2 — Riset Kuantitatif & Catatan Sinyal Live (P1)

Berdasarkan [`data/validation.json`](file:///d:/Coding/SahamBot/data/validation.json#L196-L205), strategi Q5 saat ini menghasilkan `alpha_gross_mean +0,443%` tetapi **habis tergerus biaya transaksi** (`avg_turnover 73,6%`, `cost 0,50%` → `alpha_net_mean +0,076%`, median `-0,050%`, hanya `9/20` fase positif).

### 2.1 Live Signal Ledger (Bukti Tanpa Overfitting)
- [ ] **Catat snapshot peringkat `/scan` setiap hari bursa ke SQLite**
  - **Target:** [`core/store.py`](file:///d:/Coding/SahamBot/core/store.py), [`core/screener.py`](file:///d:/Coding/SahamBot/core/screener.py), [`bot.py`](file:///d:/Coding/SahamBot/bot.py) (`/validasi`)
  - **Desain:** Tambah tabel `scan_history (trade_date, ticker, close, rel_pct, tech_score, signal, PRIMARY KEY (trade_date, ticker))`. Setiap `/scan` harian selesai, simpan snapshot secara idempotent.
  - **Evaluasi otomatis:** Hitung realized 20-day forward IC & excess return Q5 vs universe dari data live di `scan_history`, lalu tampilkan di output `/validasi` bersama hasil backtest historis.

### 2.2 Tekan Turnover & Perbaiki Validasi Portofolio
- [ ] **Simulasi hysteresis (entry/exit band) & holding horizon lebih panjang**
  - **Target:** [`research/portfolio.py`](file:///d:/Coding/SahamBot/research/portfolio.py), [`research/horizons.py`](file:///d:/Coding/SahamBot/research/horizons.py)
  - **Eksperimen:**
    1. *Hysteresis band:* masuk jika peringkat di top-20%, tahan posisi sampai turun di bawah top-40% (target: pangkas turnover dari ~74% menjadi < 35–40%).
    2. Uji horizon `40` dan `60` hari bursa + *overlapping staggered tranches*.
    3. Hitung *bootstrap confidence interval* (mis. 1.000 resample blok) untuk `alpha_net` dan bandingkan dengan benchmark IHSG & LQ45 equal-weight.

### 2.3 Perketat Metodologi Model & Uji Filter `/rencana`
- [ ] **Perbaiki regularisasi Ridge & uji kesetaraan bobot** — [`research/evaluate.py`](file:///d:/Coding/SahamBot/research/evaluate.py#L288-L291)
  - Ganti `lam = 10.0` statis dengan cross-validation time-series (`lam` berskala terhadap `N`, atau Ridge sklearn/manual grid) dan bandingkan performanya dengan *equal-weight z-score* sebagai baseline anti-overfit.
- [ ] **Atasi survivorship bias & universe transfer (45 LQ45 → ~944 BEI)** — [`research/evaluate.py`](file:///d:/Coding/SahamBot/research/evaluate.py), [`core/relatif.py`](file:///d:/Coding/SahamBot/core/relatif.py)
  - Uji evaluasi pada universe yang lebih luas dengan filter likuiditas (nilai transaksi harian minimum seperti `core/rencana.py`), serta validasi dampak filter kondisi pasar (contoh: IHSG > SMA200).
- [ ] **Eksplorasi faktor non-teknikal (sebagai hipotesis teruji)**
  - Uji incremental IC dari faktor fundamental/kualitas (PBV, ROE, earnings yield), *low-volatility*, dan *net foreign flow* (data dari `core/bandar.py`) sebelum dipertimbangkan masuk skor.
- [ ] **Backtest end-to-end pipeline `/rencana`**
  - Uji kombinasi lengkap filter `/rencana` (ranking relatif + filter likuiditas + fundamental + sentimen) pada data OOS agar klaim produk tervalidasi sebagai satu kesatuan.

**Kriteria selesai Fase 2:**
- `scan_history` berjalan otomatis pada `/scan` dan terbaca di `/validasi`.
- Laporan `research/` terbaru ([`data/validation.json`](file:///d:/Coding/SahamBot/data/validation.json)) memuat bootstrap CI net alpha, perbandingan hysteresis turnover, dan hasil uji filter gabungan.

---

## Fase 3 — Backlog Produk & UX (`P12`–`P18`)

Menuntaskan backlog di [`PROMPT-PERBAIKAN-BOT.md`](file:///d:/Coding/SahamBot/PROMPT-PERBAIKAN-BOT.md) tanpa mengorbankan kejujuran statistik.

- [ ] **Pisahkan `/rencana` (P13) dan `/paket`**
  - **Target:** [`bot.py`](file:///d:/Coding/SahamBot/bot.py#L2830-L2884), [`core/rencana.py`](file:///d:/Coding/SahamBot/core/rencana.py)
  - `/rencana <KODE> <anggaran>` → hitung lot, total biaya, % modal, max loss (Rp), dan R:R untuk satu emiten dari `build_verdict` (sesuai P13).
  - `/paket [1-5|refresh]` → paket beli multi-emiten siap Stockbit dari snapshot `/scan` (fungsi `/rencana` saat ini).
  - Validasi argumen ketat: input tidak valid membalas contoh perintah, bukan diam-diam di-clamp ke `top=5`.
- [ ] **Selaraskan bahasa label dengan bukti empiris**
  - Ganti atau perjelas istilah `BELI` / `JUAL` di `/sinyal` menjadi `Peringkat Atas` / `Peringkat Bawah` (atau tambahkan catatan IC live di header) untuk mencegah salah paham sebagai sinyal pasti untung.
- [ ] **`P12` — Retry `NetworkError` saat kirim pesan/chart Telegram**
  - Bungkus `reply_text`, `reply_photo`, dan `send_message` dengan helper retry backoff ringan untuk `telegram.error.NetworkError`/`TimedOut`.
- [ ] **`P14` — Perintah `/cek <KODE>`**
  - Checklist terpadu 1 layar (teknikal, fundamental, sentimen berita, bandar/asing, agenda dividen/RUPS) + ringkasan faktual `X/Y syarat terpenuhi`.
- [ ] **`P17` — Perintah `/portofolio` (tracking posisi riil)**
  - Catat kepemilikan riil manual user (`/beli` atau `/porto tambah`), tampilkan PnL berjalan, konsentrasi per sektor, dan total risiko portofolio jika semua SL tersentuh.
- [ ] **`P15`, `P16`, `P18` (bertahap)**
  - `P15`: `/sinyal beli bagus` (saringan kualitas + likuiditas di atas `/sinyal`).
  - `P16`: Ringkasan harian & alert zona beli untuk watchlist setelah `16:15 WIB`.
  - `P18`: `/sektor` (ringkasan rotasi & kekuatan relatif per sektor BEI).

---

## Fase 4 — Refactor Arsitektur, CI & Operasional (Berjalan Terus)

### 4.1 Modularisasi [`bot.py`](file:///d:/Coding/SahamBot/bot.py) (3.085 baris)
- [ ] **Satukan 5 formatter rupiah duplikat** (`fmt_rp`, `_rp`, `_money`, `saran.fmt`, `analysis.money_fmt`) ke [`core/textutil.py`](file:///d:/Coding/SahamBot/core/textutil.py).
- [ ] **Pecah `bot.py` per PR kecil (1 domain per PR):**
  ```text
  bot/
    __init__.py
    app.py            # inisialisasi Application, registrasi handler, error handler
    formatters.py     # esc(), formatter laporan & tabel HTML
    guards.py         # cooldown, PENDING + TTL, rate limit
    jobs.py           # scheduler alert, full_market_scan_job, heartbeat
    handlers/
      analisis.py     # /analisis, /skor, /banding, /cek
      market.py       # /scan, /sinyal, /paket, /rencana, /bandar, /sektor
      portfolio.py    # /beli, /jual, /modal, /saran, /portofolio, /watch, /alert
      info.py         # /start, /help, /menu, /belajar, /validasi, /status, /id
  ```
  *(Pertahankan entrypoint `bot.py` sebagai wrapper tipis agar [`Dockerfile`](file:///d:/Coding/SahamBot/Dockerfile), [`run.sh`](file:///d:/Coding/SahamBot/run.sh), dan test import tetap kompatibel.)*
- [ ] **Non-blocking SQLite access:** bungkus pemanggilan `store.*` dari handler async dengan `asyncio.to_thread` (terutama saat scan menulis ratusan baris ke `market_scan`).

### 4.2 Kualitas Kode, Testing & CI
- [ ] **Lengkapi lingkungan dev lokal & CI** — [`pyproject.toml`](file:///d:/Coding/SahamBot/pyproject.toml), [`.github/workflows/ci.yml`](file:///d:/Coding/SahamBot/.github/workflows/ci.yml), [`requirements-dev.txt`](file:///d:/Coding/SahamBot/requirements-dev.txt)
  - Pastikan `ruff` terinstal di `.venv`, tambah `.pre-commit-config.yaml`, aktifkan coverage threshold di CI, dan mulai pasang `mypy` bertahap pada `core/`.
- [ ] **Fixture data terrekam (replay tests):** simpan sampel respons `yfinance` & `idx.co.id` (anonymized/sanitized) di `tests/fixtures/` agar perubahan skema API upstream terdeteksi test.
- [ ] **Scheduled research CI:** jalankan evaluasi rolling OOS mingguan di GitHub Actions dan beri peringatan otomatis jika rolling IC 6 bulan turun `< 0`.

### 4.3 Operasional Produksi
- [ ] **Backup SQLite otomatis:** cron/script harian memakai `sqlite3 store.db ".backup ..."` (aman dengan WAL mode) + rotasi 7 hari.
- [ ] **Alert admin:** kirim notifikasi ke `ADMIN_CHAT_ID` (opsional di `config.py`) jika `full_market_scan_job` gagal atau BEI terblokir Cloudflare berulang.
- [ ] **Observability & Lisensi:** logging terstruktur (JSON atau field konsisten untuk `/status`) dan tambahkan file `LICENSE` di root repo agar selaras dengan [`README.md`](file:///d:/Coding/SahamBot/README.md).
