# Prompt untuk Agent — Perbaikan SahamBuddy

> Salin seluruh isi file ini ke agent kamu. Kalau mau dikerjakan sebagian,
> hapus item yang tidak diinginkan dan biarkan sisanya — prioritas sudah diurutkan.

---

## 1. Konteks

Repo: `bagasadi05/saham-bot` (branch `main`, Python 3.12, `python-telegram-bot`
v21, `yfinance`, `pandas`, `pandas-ta-classic`, `backtesting.py`).
Bot Telegram: **@claudepencaribot**, live di sandbox (bukan VPS sungguhan):
2 CPU, ~8 GB RAM, hanya `/home/hatch` yang persisten, semua egress lewat proxy.
Repo lokal: `~/workspace/saham-bot` (venv `.venv`, starter `./run.sh`).

Sebelum mulai, baca dulu sampai paham:
- `README.md` (fitur + kejujuran backtest — sinyal BUY berdiri sendiri BELUM
  profitable, win-rate 29–32%),
- `KENDALA.md` (daftar kendala + status perbaikannya),
- `bot.py` dan `core/` (alur perintah, `build_report`, `fetch_and_analyze`).

**Status terakhir (6 Nov 2026 siang, setelah P1–P7 ditulis):** sebagian
pekerjaan sudah dikerjakan langsung di repo lokal dan BELUM semuanya
di-commit — sebelum mulai, jalankan `git pull` lalu `git status`, dan JANGAN
menimpa perubahan lokal yang belum di-commit:
- Commit `af9212e` ("Menu interaktif v2 + label RSI/volume yang jujur"):
  menu v2 (picker di semua perintah tanpa ticker, perintah `/menu`, tombol
  🔄 Perbarui, watchlist berbintang) + perbaikan label RSI/volume di
  `core/analysis.py` (RSI "lemah" bukan "ekstrem"; volume faktual + catatan
  intraday).
- Belum di-commit: fitur tanya beli/jual di `bot.py` (`/beli`, `/jual`,
  tombol 🛒 Beli / 💰 Jual, parsing bahasa natural `parse_tanya`,
  `build_verdict`) + `run-supervised.sh` (loop restart otomatis: bot mati →
  nyala lagi 5 detik, backoff sampai 60 detik; JANGAN ditulis ulang).
- `KENDALA.md` dan file prompt ini sendiri sengaja untracked (tidak masuk
  repo).

## 2. Tujuan

Implementasikan perbaikan di bawah ini, sesuai urutan prioritas. Pemilik bot
adalah pembelajar saham murni (bukan trader) — bot ini alat belajar, bukan
mesin beli otomatis. Semua hasil harus tetap jujur soal keterbatasan data.

### P1 — Mode belajar di laporan (prioritas tertinggi)

**Masalah:** `/analisis` memberi skor dan daftar sinyal (`MA20 < MA50`,
`STRONG SELL`) tanpa menjelaskan artinya. Pembelajar tidak jadi paham.

**Yang dibangun:**
- Setiap baris sinyal di laporan `/analisis` (bull/bear + indikator pro)
  ditambah penjelasan 1 baris berbahasa Indonesia sederhana, mis.
  `MA20 < MA50` → "tren jangka pendek sudah di bawah tren menengah — harga
  sedang downtrend".
- Tambah perintah `/belajar <topik>` (mis. `/belajar RSI`, `/belajar PBV`,
  `/belajar MACD`) yang menjelaskan satu indikator/istilah + cara bacanya
  + jebakan umumnya. Minimal 10 topik: RSI, MACD, MA20/50/200, Bollinger,
  ATR, OBV, StochRSI, PER, PBV, ROE, dividend yield, DER.
- Penjelasan harus benar secara konsep (cek ulang definisinya, jangan karang).

**Kriteria selesai:**
- `/analisis BBCA` menampilkan penjelasan untuk setiap sinyal yang muncul.
- `/belajar RSI` dan `/belajar PBV` menjawab dengan benar dan singkat.
- Format pesan tetap valid (lihat aturan escape di bawah).

### P2 — Perbandingan emiten + konteks IHSG

**Masalah:** angka satu emiten tanpa pembanding tidak bisa dinilai. PR pemilik
yang belum terjawab: BBCA turun 8000→4800 dalam periode berapa vs IHSG?
PER/PBV BBCA vs BBRI/BMRI?

**Yang dibangun:**
- Perintah baru `/banding BBCA BBRI BMRI` (2–4 emiten): tabel perbandingan
  PER, PBV, ROE, dividend yield, return 1 tahun, return 20 hari, dan skor
  PowerScore masing-masing, plus baris penutup "paling murah/mahal menurut PBV
  adalah X" (faktual dari angka, bukan rekomendasi).
- Di laporan `/analisis`, tambahkan 1 baris konteks pasar: return IHSG
  (ticker `^JKSE` Yahoo) pada periode yang sama (20 hari dan 1 tahun) +
  kalimat "BBCA melemah X% vs IHSG Y%" (angka, bukan opini).
- Kalau data pembanding gagal diambil, tampilkan "data pembanding tidak
  tersedia" — jangan karang angka.

**Kriteria selesai:**
- `/banding BBCA BBRI BMRI` menampilkan tabel lengkap yang angkanya
  terverifikasi dari sumber yang sama dengan `/fundamental`.
- `/analisis BBCA` menampilkan return IHSG 20 hari dan 1 tahun.

### P3 — Ketahanan data: jangan bisu saat Yahoo ngadat

**Masalah:** harga + fundamental semuanya dari Yahoo Finance. Pernah 429
(rate-limit IP bersama) dan bot langsung tidak bisa menjawab. Fundamental
juga hanya snapshot (PER hari ini), tanpa tren.

**Yang dibangun:**
- Fundamental: tampilkan tren 4 kuartal terakhir untuk pendapatan dan laba
  bersih (dari `yfinance` quarterly financials) di `/fundamental`, plus
  tanggal data ("data per ...").
- Saat Yahoo gagal total dan tidak ada cache: handler membalas pesan ramah
  dengan jelas (bukan crash) — guard ini sudah ada untuk `analyze()`,
  pastikan berlaku di semua handler (`/skor`, `/backtest`, `/saran`,
  scanner alert).
- (Opsional, kalau ada sumber terbuka yang andal) tambah sumber harga cadangan
  dan pakai hanya saat Yahoo gagal; beri label sumber di laporan.

**Kriteria selesai:**
- `/fundamental BBCA` menampilkan tren laba/pendapatan 4 kuartal terakhir.
- Simulasi Yahoo gagal (matikan network / pakai ticker palsu) → bot membalas
  ramah di semua perintah, tidak ada traceback ke user.

### P4 — Aliran asing + aksi korporasi

**Masalah:** di BEI, aliran asing adalah penggerak besar; `/bandar` mati
karena idx.co.id diblokir Cloudflare dari IP server. Dividen, rights issue,
split, RUPS tidak pernah diingatkan.

**Yang dibangun:**
- `/bandar`: coba sumber alternatif yang legal/terbuka saat idx.co.id
  diblokir (mis. toolkit `idx-bei` yang disebut di README, atau sumber
  resmi lain). Kalau tetap tidak bisa, pertahankan pesan jujur + alternatif
  `/analisis` (jangan pura-pura menampilkan data).
- Perintah baru `/agenda BBCA`: jadwal dividen berikutnya (ex-date, besaran),
  dan aksi korporasi terbaru dari `yfinance` (`calendar`, `dividends`,
  `splits`). Kalau tidak ada data, katakan "tidak ada agenda tercatat".

**Kriteria selesai:**
- `/bandar` menampilkan data BEI atau pesan blokir yang jujur (tidak ada
  yang berubah diam-diam).
- `/agenda BBCA` menampilkan ex-date dividen terakhir/berikutnya bila datanya ada.

### P5 — Position sizing di paper trading

**Masalah:** `/saran` memberi entry/SL/TP tapi tanpa ukuran posisi. Position
sizing adalah separuh manajemen risiko.

**Yang dibangun:**
- Modal latihan default Rp10.000.000 (bisa diubah lewat `/modal <jumlah>` —
  tersimpan per chat di `store.json`).
- Setiap kandidat `/saran` menampilkan ukuran posisi dari risiko 1% modal
  per trade: `risiko per saham = entry − SL`, `jumlah saham = 1% modal /
  risiko per saham` (dibulatkan ke bawah per lot 100), plus `nilai posisi`.
- `/saran list` menampilkan nilai posisi tiap posisi latihan yang terbuka.

**Kriteria selesai:**
- `/saran` menampilkan lot + nilai posisi untuk tiap kandidat, konsisten
  dengan entry/SL yang sama.
- `/modal 5000000` mengubah modal dan tercermin di `/saran` berikutnya.

### P6 — Auto-scan tiap pagi

**Masalah:** `/saran` harus dipencet manual setiap hari.

**Yang dibangun:**
- Bot mengirim ringkasan `/saran` + rekap watchlist secara otomatis tiap hari
  bursa sekitar 08:30 WIB (sebelum bursa buka) ke chat yang mengaktifkannya.
- Perintah `/auto on` / `/auto off` per chat (default off), tersimpan di
  `store.json`. Hanya kirim di hari bursa (Senin–Jumat, bukan libur — cukup
  cek weekday; libur bursa boleh tetap terkirim dengan label tanggal).
- Pakai `job_queue` yang sudah ada (lihat `scan_alerts` sebagai pola).

**Kriteria selesai:**
- `/auto on` lalu scan pagi berikutnya terkirim tanpa perintah manual.
- `/auto off` menghentikan pengiriman.

### P7 — Universe lebih luas

**Masalah:** `/saran` hanya memindai maksimal 12 emiten (watchlist + default).

**Yang dibangun:**
- Perluas `DEFAULT_UNIVERSE` ke saham LQ45 (45 emiten) dengan pemindaian
  bertahap + cache (jangan sampai satu `/saran` memakan waktu >5 menit atau
  kena rate-limit Yahoo).
- Tambah `/saran lq45` untuk memindai seluruh LQ45 (boleh lebih lambat,
  beri progres ke user).

**Kriteria selesai:**
- `/saran` memindai lebih dari 12 emiten tanpa timeout.
- `/saran lq45` selesai dan menampilkan kandidat dari universe LQ45.

### P8 — Verdict beli/jual yang tahu rekam jejaknya (backtest-aware)

**Masalah:** `/beli` dan `/jual` (serta tanya bahasa natural) memakai ambang
PowerScore tetap. Padahal bot punya `/backtest` — dan tiap emiten beda:
sinyal BUY di satu emiten bisa win-rate 45%, di emiten lain 25%. Verdict
tanpa rekam jejak itu skor kosong.

**Yang dibangun:**
- Verdict `/beli`/`/jual`/tanya menampilkan 1 baris rekam jejak per emiten
  dari backtest 5 tahun yang sudah ada (`backtest.py` + `backtest_real.py`):
  win-rate, profit factor, return/tahun vs buy & hold, jumlah transaksi.
- Backtest lambat (1–2 menit) — JANGAN dihitung inline di dalam jawaban
  verdict. Pakai cache per emiten (file di `temp/`, maks. 1 hari); kalau
  cache belum ada atau kedaluwarsa, verdict tetap terkirim tanpa baris
  backtest + catatan "backtest belum tersedia — jalankan /backtest BBCA
  dulu". `/backtest` yang dijalankan user ikut mengisi cache. (Opsional: job
  harian yang me-refresh cache untuk watchlist.)
- Baris backtest harus jujur soal keterbatasan: angka itu hasil simulasi
  historis, bukan janji.

**Kriteria selesai:**
- `/beli BBCA` menampilkan "Backtest 5 thn: win-rate X%, PF Y, return Z%/thn
  vs buy&hold W% (data 6 Nov)" saat cache ada.
- `/beli BBCA` tetap menjawab cepat (<10 detik) saat cache belum ada,
  dengan catatan yang jelas.
- Ambang verdict dan skor tidak berubah.

### P9 — Multi-timeframe + kekuatan relatif

**Masalah:** analisis hanya timeframe daily 6 bulan. Pantulan harian di dalam
downtrend mingguan terlihat seperti sinyal beli; dan tanpa pembanding, tidak
ketahuan apakah penurunan itu masalah sahamnya atau pasarnya.

**Yang dibangun:**
- `/analisis` menampilkan konfirmasi tren mingguan: ambil candle mingguan
  (Yahoo `interval=1wk`), tampilkan "Tren mingguan: naik/turun" (harga vs
  MA20 mingguan) + peringatan kalau daily dan weekly berlawanan arah
  ("daily membaik tapi weekly masih downtrend — pantulan, bukan pembalikan").
- Perbandingan relatif: return emiten vs IHSG (`^JKSE`) dan vs rata-rata
  sektornya (atau vs 2 emiten se-sektor dari watchlist/default) untuk 20 hari
  dan 1 tahun. Catatan: bagian IHSG di P2 tetap — jangan duplikasi,
  gabungkan jadi satu baris konteks pasar.
- Kalau data mingguan gagal diambil, tampilkan analisis daily seperti biasa
  + catatan "data mingguan tidak tersedia" (jangan karang).

**Kriteria selesai:**
- `/analisis BBCA` menampilkan tren mingguan + "vs IHSG (20d/1th)".
- Sinyal daily yang berlawanan dengan weekly diberi peringatan eksplisit.

### P10 — Bot proaktif: alert perubahan skor + pengingat dividen

**Masalah:** bot hanya menjawab kalau ditanya (kecuali alert harga).
Perubahan kondisi (skor watchlist berbalik) dan jadwal dividen lewat tanpa
kabar. (P6 sudah menangani scan pagi; item ini soal peristiwa, bukan jadwal.)

**Yang dibangun:**
- Alert perubahan skor: saat scan 30 menit (`scan_alerts`) mendeteksi rating
  PowerScore sebuah emiten watchlist PINDAH band (mis. NETRAL → BUY,
  BUY → SELL), kirim 1 pesan ke chat pemilik watchlist itu. Simpan band
  terakhir per emiten di `store.json`; hanya kirim saat band BERUBAH, bukan
  tiap scan.
- Pengingat dividen: cek harian (pagi, ikut job P6) jadwal dividen watchlist
  dari `yfinance`; kirim pengingat 3 hari dan 1 hari sebelum ex-date
  ("BBCA ex-dividen RpX pada <tanggal>"). Kalau tidak ada data, diam
  (jangan spam "tidak ada dividen").
- Kedua fitur ini ikut saklar `/auto on`/`/off` yang sama dengan P6 — jangan
  bikin saklar baru yang membingungkan.

**Kriteria selesai:**
- Simulasi band BBCA NETRAL → BUY memicu tepat 1 pesan; scan berikutnya
  tanpa perubahan band tidak mengirim apa-apa.
- Dividen watchlist dengan ex-date 3 hari lagi memicu pengingat sekali.

### P11 — Tahan banting: survive VM restart + allowlist

**Masalah:** bot jalan sebagai proses background di sandbox + supervisor loop
(`run-supervised.sh`, menangani crash — JANGAN ditulis ulang). Tapi kalau VM
restart, supervisor ikut mati dan bot diam sampai dijalankan manual.
Allowlist juga masih kosong — siapa pun bisa memakai bot.

**Yang dibangun:**
- Selidiki dan implementasikan agar bot hidup kembali otomatis setelah VM
  restart di sandbox ini: cek apakah systemd user + lingering jalan; kalau
  tidak bisa, JANGAN pura-pura — dokumentasikan cara start ulang tercepat
  (satu perintah) di `README.md`/`deploy.md`, dan pastikan
  `run-supervised.sh` tetap satu-satunya entry point.
- Tambah perintah `/id` yang membalas numeric Telegram user ID pengirim
  (supaya pemilik bisa mengisi `ALLOWED_USER_IDS` tanpa @userinfobot), plus
  dokumentasi cara mengaktifkan allowlist di README.
- Jangan ubah perilaku supervisor yang sudah ada; uji dengan restart
  supervisor dan pastikan bot kembali.

**Kriteria selesai:**
- Setelah VM restart (atau simulasi: matikan semua proses bot + supervisor
  lalu jalankan prosedur yang didokumentasikan), bot kembali online.
- `/id` membalas ID numerik yang benar; README menjelaskan allowlist.
- `run-supervised.sh` tidak diubah isinya tanpa alasan.

## 3. Aturan main (wajib)

1. **Jangan ubah perilaku perintah yang sudah ada** (nama, argumen, format
   balasan) kecuali diminta di atas. Kompatibel mundur.
2. **Escape semua teks dinamis di pesan HTML.** Pelajaran dari bug 6 Nov 2026:
   string `MA20 < MA50` yang dimasukkan mentah ke pesan HTML membuat Telegram
   menolak pesan (`Can't parse entities: unsupported start tag`) dan handler
   crash. Pakai `bot.esc()` (sudah ada, `html.escape`) untuk judul berita,
   nama emiten, label, dan semua teks dari luar. Jangan pernah kirim `<`, `>`,
   `&` mentah dari data dinamis.
3. **Jangan janjikan keuntungan dan jangan hapus disclaimer DYOR.** Bot ini
   alat belajar. Rating (BUY/SELL) adalah deskripsi kondisi, bukan perintah.
4. **Jangan sentuh token dan `.env`.** Jangan commit `.env`, `store.json`,
   `bot.log`, atau file di `temp/`. Jangan tampilkan token di log/chat/kode.
5. **Jangan rusak fix yang sudah ada:** sanitasi `NO_PROXY` di `bot.py`/`run.sh`
   (httpx crash kalau ada IPv6 kurung-siku), retry+cache Yahoo di `core/data.py`,
   guard `None` di `analyze()`, allowlist `ALLOWED_USER_IDS`, redaksi token
   dari log, loop restart `run-supervised.sh`, menu v2 (picker, `/menu`,
   tombol Perbarui), tanya beli/jual (`parse_tanya`, `build_verdict`),
   label RSI/volume di `core/analysis.py`. `esc()` wajib untuk semua teks
   dinamis, termasuk di dalam `build_verdict`.
6. **Kerjakan satu item per commit**, pesan commit jelas (mis. `P1: mode
   belajar di laporan`). Jangan campur banyak item dalam satu commit besar.
7. **Tes sebelum selesai:** `python -m py_compile bot.py core/*.py`, lalu tes
   offline handler dengan mock (pola di `/tmp/test_menu.py` dan
   `/tmp/test_html_fix.py` di mesin dev — tiru polanya, jangan bergantung
   pada file itu). Untuk tiap item, tulis hasil tesnya di deskripsi commit.
8. **Kalau butuh keputusan pemilik** (mis. modal default, jam auto-scan,
   sumber data baru yang meragukan), berhenti di item itu, tulis opsinya, dan
   lanjutkan item lain yang tidak terblokir. Jangan menebak keputusan pemilik.

## 4. Definisi selesai

- Semua kriteria selesai pada item yang dikerjakan terpenuhi.
- `py_compile` lulus, bot restart bersih (log tanpa `ERROR`/`Traceback`
  baru), perintah lama tetap jalan (`/start`, `/analisis BBCA`, tombol menu).
- Ringkasan perubahan per item (apa yang berubah, file apa, cara tesnya)
  ditulis sebagai balasan terakhir.
