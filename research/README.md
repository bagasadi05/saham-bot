# Riset — kenapa skor diubah

Folder ini **tidak dipakai bot saat berjalan**. Isinya harness untuk menguji
apakah skor benar-benar punya daya prediksi, plus hasilnya.

Semua angka yang ditampilkan `/validasi` berasal dari sini (`research/publish.py`
meringkas `temp/research/evaluation.json`, `portfolio.json`, `experiments.json`,
dan `kronos_eval.json` ke [`data/validation.json`](../data/validation.json)).

## Cara menjalankan

```powershell
# 1) Evaluasi skor (IC, desil, kuintil P25-P75 IQR, per rating, walk-forward CV)
#    Menulis temp/research/evaluation.md, evaluation.json, oos_predictions.pkl
python research/evaluate.py

# 2) Diagnostik horizon 5/10/20/60 hari
python research/horizons.py

# 2b) Simulasi portofolio setelah biaya IDX 0,40% (+ hysteresis, min_hold, rezim IHSG)
python research/portfolio.py --hold 0.6 --min-hold 2 --regime sma50_200_half --offline

# 2c) Uji ablasi 16 konfigurasi (320 trial) + perbandingan horizon 20/40/60 hari (offline)
python research/experiments.py --offline --horizons 20 40 60

# 3) Uji Kronos (butuh torch + model HF ±100 MB) — ±25 menit
python research/evaluate_kronos.py --tickers 20 --every 42

# 4) Ringkas ke data/validation.json (ikut di-commit, dipakai /validasi)
python research/publish.py

# 5) Audit konsistensi offline (weights/quintile core/relatif.py, LQ45, umur data, backup DB)
python tools/check_sync.py
```

## Hasil empiris (`data/validation.json`, 45 emiten `LQ45_2026`, 2017-09-07 s/d 2026-10-09, horizon 20 hari, 91.395 observasi)

### 1) Evaluasi sinyal & walk-forward (`temp/research/evaluation.md`)

| Yang diuji | Hasil | Arti |
|---|---|---|
| IC skor antar-emiten | **+0,0115** (`t=2,68` naif, **`t_hac=0,85`**) | praktis nol & tidak signifikan setelah koreksi Newey-West |
| IC skor dalam satu emiten | **−0,0207** (`t=−2,33`, 44 emiten) | sedikit berlawanan arah |
| Excess return STRONG BUY | **+0,461%** / 20 hari (menang `46,2%`) | tipis, di bawah biaya tanpa seleksi faktor |
| Excess return STRONG SELL | **−0,008%** / 20 hari (`n=30.101`) | label ekstrem bawah tidak informatif |
| Walk-forward 12 faktor (2022–2026) | **+0,0492 / +0,0490 / +0,0206 / −0,0250 / +0,0584** | rata-rata **+0,0304** vs bobot-sama **−0,0061** vs skor **+0,0107** (4/5 tahun unggul) |
| Kuintil OOS Q5 vs Q1 | **+0,400%** (menang `47,2%`) vs **−0,204%** (menang `44,0%`) | spread relatif **+0,604%** / 20 hari, bukan jaminan return; pita P25–P75 IQR mengisi tiap run baru |

### 2) Simulasi portofolio (`--hold 0.6 --min-hold 2 --regime sma50_200_half`, biaya IDX `0,40%` RT)

| Metrik | Hasil | Catatan |
|---|---|---|
| Turnover rata-rata | **21,7%** satu arah (**43,4%** dua arah) | ditekan oleh `hold=0.6` & `min_hold=2` |
| Eksposur & Max Drawdown | **79,8%** eksposur · MDD rata-rata **−20,0%** | rezim kausal `sma50_200_half` pada `^JKSE` |
| Alpha bruto → neto antar-fase | **+0,416%** (aktif **+0,534%**) → **+0,330%** neto | median neto **+0,385%**, **16/20** fase positif |
| CI 95% alpha neto (block=4) | **[+0,243%, +0,522%]** / periode | moving-block bootstrap jendela 20 fase |
| Excess vs IHSG (`^JKSE`) | **+1,190%** / periode (**20/20** fase positif) | pembanding pasar; bukan jaminan performa masa depan |
| Deflated Sharpe & PSR | **DSR 0,86** (`best_sr=0,279`, `sr0=0,142`, `n_trials=20`) | belum melewati gerbang `0,95`; **PSR** (`SR*=0`) & total **320 trial** ablasi (16×20) kini dicatat eksplisit |

Kesimpulan yang dipakai di kode:

1. Skor teknikal lama **tidak** dipakai untuk memilih saham — hanya ditampilkan
   sebagai bacaan tren (`bot.build_report` menyatakannya eksplisit).
2. Pemilihan `/saran` memakai **skor relatif** 12 faktor (`core/relatif.py`)
   dengan `WEIGHTS` dan `OOS_QUINTILES` **disinkronkan** tepat ke `data/validation.json`
   (dibekukan, tidak di-tune ulang dengan bobot liar).
3. Angka tetap tipis dan bervariasi lebar antar saham/fase: biaya transaksi IDX
   **0,40%** pulang-pergi (`0,15%` beli + `0,25%` jual) wajib diperhitungkan, dan
   DSR `0,86` pada 20 fase (serta 320 trial di `research/experiments.py`) menjadi
   alasan sistem ini hanya alat bantu disiplin, bukan janji profit.

## Jebakan yang sudah terhindar

- **Sampel kecil menipu.** Dengan 10 emiten, IC momentum tampak −0,06
  ("semua tren salah arah"). Dengan 45 emiten nilainya ~0,00. Jangan ambil
  kesimpulan dari 1–10 ticker.
- **Return mentah vs excess.** Perbandingan per rating tanpa di-demean per
  tanggal membuat SELL terlihat bagus karena label itu menumpuk di pasar
  bearish yang lalu rebound bersama-sama.
- **Baris tumpang tindih.** Horizon 20 hari pada data harian membuat observasi
  berkorelasi; karena itu CI memakai moving-block bootstrap per fase dan IC
  memakai `t_hac` Newey-West (`nlags=20`).
- **Look-ahead.** Fitur hari i hanya memakai data ≤ i; target i+20; CV ridge
  dan filter rezim `^JKSE` bersifat kausal tanpa mengintip masa depan.
- **Multiple-testing DSR & PSR.** Laporan memisahkan PSR fase individual
  (Bailey & López de Prado, 2012), DSR 20 fase konfigurasi terpilih, dan total
  320 trial di `research/experiments.py` agar selektivitas tidak tersamarkan.
