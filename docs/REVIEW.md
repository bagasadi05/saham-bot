# Review & Perbaikan — SahamBuddy

Review kode menyeluruh memakai skill **code-reviewer** (aturan universal + Python),
ditambah test suite offline. Tanggal: 6 November 2026.

**Hasil:** verdict *Approve* — rata-rata kualitas **85,3 (B) → 90,2 (A)**,
81 test lulus. Angka dari `scripts/code_quality_checker.py` (tanpa `.venv`).

---

## Bug yang diperbaiki

| # | Lokasi | Masalah | Perbaikan |
|---|---|---|---|
| 1 | `bot.py` `cmd_analisis` | `build_report()` dipanggil tanpa `mkt` → baris **"Konteks pasar (IHSG)" selalu berbunyi "tidak tersedia"** meski data sudah diambil | argumen `mkt` diteruskan |
| 2 | `backtest.py` `summarize` | baris **"Baseline (tanpa filter)"** dibangun tapi dibuang (`return "\n".join(lines)`) | `out + lines` |
| 3 | `core/data.py` `fetch_quote_name` | `UnboundLocalError` bila `normalize_ticker()` gagal (`symbol` belum terisi) — mematikan `/berita` untuk ticker salah | normalisasi dipisah, fallback aman |
| 4 | `core/banding.py` | kegagalan Yahoo dicache **6 jam** (komentar mengaku "sebentar") — satu hiccup mematikan konteks IHSG sepanjang hari | TTL gagal 5 menit |
| 5 | `core/saran.py` `position_size` | nilai posisi bisa **melebihi modal** saat ATR kecil; `shares` (471) tidak konsisten dengan lot (4) | cap `max_lots`, `shares = lots × 100` |
| 6 | `bot.py` | judul berita, nama perusahaan, sektor **tidak di-escape** → Telegram gagal parse saat ada `&`/`<` | `esc()` di semua teks dinamis |
| 7 | `bot.py` `cmd_alert` | default alert tidak memuat `breakdown` & `vol_spike` padahal didokumentasikan di README | default = `alerts.ALL_KINDS` |
| 8 | `bot.py` chart | error chart setelah laporan terkirim memicu pesan "kesalahan internal"; file `temp/` bisa tertinggal | blok `try/except/finally` terisolasi |
| 9 | `core/data.py` | percobaan terakhir tetap `sleep(20)` sebelum menyerah | tidur hanya antar-percobaan |
| 10 | `config.py` | `.env` tidak terbaca saat `python bot.py` langsung (padahal README menjanjikan) | `load_dotenv()` tanpa dependensi; env eksplisit tetap menang |

## Peningkatan ketahanan

- **Chart thread-safe**: `core/chart.py` memakai `matplotlib.figure.Figure` (bukan
  `pyplot`) — bot memanggil `asyncio.to_thread` per permintaan, dan state global
  pyplot bisa saling menimpa.
- **Store**: folder induk `store.json` dibuat otomatis (aman untuk
  `STORE_FILE=/app/data/store.json`); kelas mati `StoreWarning` dihapus.
- Typo teks pengguna `"Skema cais"` → `"Skema entry"`.
- `/skor` diberi jeda antar-emiten agar tidak memicu rate-limit Yahoo.

## Test suite (baru)

`tests/` — 81 test, ~13 detik, **sepenuhnya offline** (data sintetis, jaringan
di-mock, model ML di-bypass):

```
tests/test_analysis.py     indikator, skor, snapshot, S/R
tests/test_alerts.py       pemicu jump/drop/vol_spike, jam bursa
tests/test_backtest.py     tabel & ringkasan backtest
tests/test_banding.py      pembanding, konteks IHSG + cache gagal
tests/test_bot_text.py     escaping HTML, konteks IHSG di laporan
tests/test_chart.py        render PNG + akses 4 thread bersamaan
tests/test_config.py       pemuatan .env
tests/test_data.py         normalisasi ticker, fallback cache 429
tests/test_news.py         lexicon, negasi, agregat sentimen
tests/test_powerscore.py   bobot 50/30/20, ambang rating, trap momentum
tests/test_saran.py        sizing risiko 1%, cap modal, filter jebakan
tests/test_store.py        watchlist/alert/paper/modal, file rusak
```

Jalankan: `pip install -r requirements-dev.txt && python -m pytest`.

## Sisa utang teknis (belum dikerjakan)

1. **`bot.py` 1.116 baris / 41 fungsi** — skor tool 0/100 karena ukuran; pecah
   per perintah (`handlers/`) bila akan tumbuh lagi.
2. **Belum ada CI** — tambahkan GitHub Actions (`pip install -r requirements-dev.txt
   && pytest`) agar regresi tertangkap.
3. **`store.json` hanya aman untuk satu proses** — lock masih `threading`, bukan
   antar-proses. Pindah ke SQLite bila bot dijalankan lebih dari satu instance.
4. **Rate limit per user** belum ada — bot publik bisa dibanjiri `/analisis`
   (tiap request memanggil Yahoo 2–4 kali).
5. **Sumber data tunggal** — Yahoo (`yfinance`) + idx.co.id (Cloudflare); belum
   ada penyedia cadangan.
6. **Pin dependensi** — repo belum diuji di CI; pandas 3.x / yfinance 1.x baru
   diverifikasi manual di mesin ini.
7. **`LICENSE` belum ada** di repo (README menyebut MIT untuk model, bukan kode).
8. **Type hints longgar** - banyak `dict` tanpa `TypedDict`/`dataclass`.

---

# Ronde 2 — validasi statistik & skor relatif (6 Nov 2026)

Review pertama menilai **kualitas kode**. Ronde ini menilai **kualitas
analisisnya**: apakah skor benar-benar memprediksi? Harness ada di `research/`.

## Yang diukur

`research/evaluate.py` — 45 emiten LQ45, 91.395 observasi, 2017–2026, horizon
20 hari bursa:

- IC cross-sectional per tanggal (Spearman), dengan t-stat.
- Monotonisitas desil (return di-demean per tanggal).
- Excess return per rating + bootstrap CI **per tanggal** (bukan per baris —
  baris pada hari yang sama saling berkorelasi).
- IC time-series per emiten (lensa "beli sekarang?").
- Kekuatan tiap fitur + walk-forward ridge (uji 2022–2026, embargo 32 hari).
- Kalibrasi kuintil **out-of-sample**.

## Hasil yang mengubah produk

| Temuan | Angka | Konsekuensi |
|---|---|---|
| IC skor antar-emiten | +0,0115 (t=2,68) | praktis nol → tidak dipakai memilih |
| IC skor time-series | −0,0207 (t=−2,33) | label BUY justru sedikit berlawanan |
| Excess STRONG SELL | −0,01% (n=30.101) | label ekstrem bawah tak informatif |
| Walk-forward model | +0,047/+0,048/+0,025/−0,026/+0,063 | dasar skor relatif |
| Kuintil OOS Q5−Q1 | +0,42% − (−0,24%) | tipis tapi terukur |

Perubahan:

1. `core/relatif.py` — skor relatif 12 faktor dengan bobot **dibekukan** dari
   rata-rata walk-forward; z-score cross-sectional, clip ±3, NaN = netral.
2. `core/saran.py:pick` — mode `relatif` bila universe ≥ 8 emiten; fallback
   `powerscore` (dan mengatakannya) bila terlalu kecil.
3. `bot.py` — `/validasi` menampilkan angka uji; `/analisis` menyatakan skor
   teknikal = bacaan tren; `/saran` menampilkan peringkat relatif + kuintil.
4. `research/` — harness reproducible (`evaluate.py`, `horizons.py`,
   `evaluate_kronos.py`, `publish.py`) + `data/validation.json` di-commit.
5. Test: 82 → **91** (relatif, mode pick, format /validasi).

## Jebakan yang ditemukan (dan kenapa penting)

- **10 emiten vs 45 emiten.** Uji awal 10 emiten memberi IC momentum −0,06
  ("semua tren salah arah"). Di 45 emiten nilainya ~0,00. Kesimpulan apa pun
  dari sampel kecil adalah noise — harness sekarang menolak tanggal dengan < 8
  emiten dan melaporkan jumlah tanggal, bukan cuma jumlah baris.
- **Return mentah menyesatkan.** Tanpa demean per tanggal, label SELL tampak
  menguntungkan karena menumpuk di pasar bearish yang rebound bersama-sama.
- **Bug z-score walk-forward.** Tanggal dengan satu emiten menghasilkan std=NaN
  yang merambat ke seluruh regresi → semua prediksi NaN (kolom IC model kosong).
  Diperbaiki dengan `fillna(0)` (netral terhadap rata-rata pasar).

## Sisa utang teknis (ronde 2)

1. **Kalibrasi kuintil belum monoton sempurna** (Q2 < Q1). Jangan tampilkan
   "ekspektasi imbal hasil" per saham sampai bentuknya monoton; sekarang hanya
   kuintil + angka historis agregat.
2. **Belum ada biaya transaksi di evaluasi IC.** IC mengukur ranking, bukan PnL.
   Langkah berikutnya: backtest portofolio long-only Q5 dengan biaya 0,15%
   beli + 0,25% jual + pajak 0,1%.
3. **Bobot masih linier & global.** Belum ada interaksi sektor; bank vs
   komoditas diperlakukan sama.
4. **Universum = LQ45 2026 yang dipakai mundur** → ada survivorship bias ringan
   (emiten yang keluar index tidak masuk sampel).
5. **Horizon 20 hari saja** untuk keputusan; `horizons.py` sudah ada untuk
   memeriksa 5/10/60 hari, hasilnya belum dimasukkan ke produk.
