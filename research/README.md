# Riset — kenapa skor diubah

Folder ini **tidak dipakai bot saat berjalan**. Isinya harness untuk menguji
apakah skor benar-benar punya daya prediksi, plus hasilnya.

Semua angka yang ditampilkan `/validasi` berasal dari sini (`research/publish.py`
meringkasnya ke `data/validation.json`).

## Cara menjalankan

```powershell
# 1) Evaluasi skor (IC, desil, per rating, walk-forward) — ±6 menit, data di-cache
.\.venv\Scripts\python.exe research\evaluate.py

# 2) Diagnostik horizon 5/10/20/60 hari
.\.venv\Scripts\python.exe research\horizons.py

# 2b) Simulasi portofolio setelah biaya IDX (+ hysteresis, min_hold, rezim IHSG)
.\.venv\Scripts\python.exe research\portfolio.py --hold 0.6 --min-hold 2 --regime sma50_200_half --offline

# 2c) Uji ablasi + perbandingan walk-forward horizon 20/40/60 hari (offline)
.\.venv\Scripts\python.exe research\experiments.py --offline --horizons 20 40 60

# 3) Uji Kronos (butuh torch + model HF ±100 MB) — ±25 menit
.\.venv\Scripts\python.exe research\evaluate_kronos.py --tickers 20 --every 42

# 4) Ringkas ke data/validation.json (ikut di-commit, dipakai /validasi)
.\.venv\Scripts\python.exe research\publish.py
```

## Hasil (45 emiten LQ45, 2017–2026, horizon 20 hari, 91.395 observasi)

| Yang diuji | Hasil | Arti |
|---|---|---|
| IC skor antar-emiten | **+0,0115** (t=2,68) | praktis nol |
| IC skor dalam satu emiten | **−0,0207** (t=−2,33) | sedikit berlawanan arah |
| Excess return STRONG BUY | +0,46% / 20 hari | tipis, menang 46% |
| Excess return STRONG SELL | −0,01% / 20 hari | label ekstrem bawah tidak informatif |
| Walk-forward model 12 faktor | **+0,047 / +0,048 / +0,025 / −0,026 / +0,063** | 4 dari 5 tahun positif |
| Kuintil OOS teratas vs terbawah | +0,42% vs −0,24% | selisih 0,67% / 20 hari |

Kesimpulan yang dipakai di kode:

1. Skor teknikal lama **tidak** dipakai untuk memilih saham — hanya ditampilkan
   sebagai bacaan tren (`bot.build_report` menyatakannya eksplisit).
2. Pemilihan `/saran` memakai **skor relatif** 12 faktor (`core/relatif.py`)
   dengan bobot **dibekukan** dari walk-forward, bukan PowerScore.
3. Angka tetap tipis: IC 0,03 bukan mesin uang. Biaya transaksi IDX
   (±0,5% pulang-pergi) memakan sebagian besar selisih kuintil.

## Jebakan yang sudah terhindar

- **Sampel kecil menipu.** Dengan 10 emiten, IC momentum tampak −0,06
  ("semua tren salah arah"). Dengan 45 emiten nilainya ~0,00. Jangan ambil
  kesimpulan dari 1–10 ticker.
- **Return mentah vs excess.** Perbandingan per rating tanpa di-demean per
  tanggal membuat SELL terlihat bagus karena label itu menumpuk di pasar
  bearish yang lalu rebound bersama-sama.
- **Baris tumpang tindih.** Horizon 20 hari pada data harian membuat observasi
  berkorelasi; karena itu CI dihitung per tanggal (bootstrap), bukan per baris.
- **Look-ahead.** Fitur hari i hanya memakai data ≤ i; target i+20; ada embargo
  ±32 hari antara train dan test di walk-forward.
