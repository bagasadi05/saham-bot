# Referensi Regulasi BEI (IDX), Gerbang Kualitas, & Metodologi Valuasi

Dokumen referensi ini memuat aturan pasar modal Indonesia beserta nomor regulasi dan tanggal berlakunya, pemisahan gerbang kualitas (Piotroski 9 poin vs Bank 6 poin), konversi kurs laporan keuangan, pajak dividen, kalkulasi Right Issue (TERP), model valuasi (termasuk Reverse DCF BEI), serta aturan eksekusi risiko.

## 1. Regulasi Perdagangan BEI (Wajib Cek Ulang Sebelum Analisis)

> **Perintah Wajib**: Aturan BEI bersifat dinamis dan dievaluasi berkala. Selalu verifikasi ulang **Peraturan BEI Nomor II-A**, **Peraturan I-X / II-X**, dan pengumuman terbaru di `idx.co.id` sebelum menyusun level harga. Gunakan `scripts/idx_calc.py` untuk memastikan pembulatan fraksi harga, ARA/ARB, valuasi, TERP, dan *position sizing* 100% presisi.
> **Peringatan Data Pengujian Akhir Pekan BEI**: Situs `idx.co.id` berkala melakukan pemeliharaan/pengujian di akhir pekan (mis. 20-21 Nov 2026) yang menampilkan data simulasi (*dummy*). Jangan mengambil angka harga atau laporan dari halaman pengujian; selalu *cross-check* ke 2 sumber pasar yang valid.

### A. Batas Auto Rejection (ARA / ARB Asimetris) & Fraksi Harga (Tick Size)
*Sumber ARA/ARB: SK Direksi BEI No. Kep-00158/BEI/10-2026 (menggantikan Kep-00157/BEI/10-2026), berlaku efektif **20 Oktober 2026 s.d. 31 Desember 2026** untuk Papan Utama, Papan Pengembangan, dan Papan Ekonomi Baru. Sumber Fraksi Harga: Peraturan BEI Nomor II-A:*

| Rentang Harga Acuan (ARA/ARB) | Batas Kenaikan (ARA) | Batas Penurunan (ARB) | Kelompok Pita Fraksi Harga | Fraksi Harga (Tick) |
| :--- | :--- | :--- | :--- | :--- |
| Rp1 - Rp10 | +Rp1 | -Rp1 | < Rp200 | Rp1 |
| Rp11 - Rp14 | +35% | -Rp1 | < Rp200 | Rp1 |
| Rp15 - Rp200 | +35% | -7% | < Rp200 | Rp1 |
| > Rp200 - Rp5.000 | +25% | -7% | Rp200 s.d. < Rp500 | Rp2 |
| > Rp200 - Rp5.000 | +25% | -7% | Rp500 s.d. < Rp2.000 | Rp5 |
| > Rp200 - Rp5.000 | +25% | -7% | Rp2.000 s.d. < Rp5.000 | Rp10 |
| > Rp5.000 | +20% | -7% | >= Rp5.000 | Rp25 |

*Catatan Batas Pita Fraksi*: Harga tepat di batas atas (Rp200, Rp500, Rp2.000, Rp5.000) memakai fraksi kelompok di bawahnya saat bergerak turun dan fraksi kelompok di atasnya saat bergerak naik. 1 lot = 100 lembar saham; *settlement* T+2.

### B. Papan Pemantauan Khusus (FCA - 7 Kriteria Fundamental), Notasi Khusus, Free Float 15%, HSC, & Indeks Global
- **Papan Pemantauan Khusus / Full Call Auction (FCA)** (*SK Direksi BEI No. Kep-00139/BEI/09-2026 tentang Perubahan Peraturan I-X dan Peraturan II-X, efektif 28 September 2026*):
  - Batas ARA dan ARB sebesar **10%** dengan mekanisme lelang berkala (*Full Call Auction*) dan *Non-Cancellation Period*.
  - Penempatan di FCA (Notasi Khusus **X**) kini **murni ditentukan oleh 7 kriteria fundamental** (opini audit *disclaimer*, tidak ada pendapatan/perubahan pendapatan, tambang minerba belum punya pendapatan inti hingga tahun buku ke-4, ekuitas negatif, PKPU/pailit pada emiten atau anak usaha material, atau perintah OJK).
- **Kamus Kode Notasi Khusus BEI** (*Sumber: idx.co.id/perusahaan-tercatat/notasi-khusus*):
  - **C**: Perkara hukum material terhadap emiten/anak usaha/direksi/komisaris.
  - **Q**: Pembatasan kegiatan usaha oleh regulator.
  - **Y**: Belum menyelenggarakan RUPST hingga 6 bulan setelah tahun buku berakhir.
  - **F / G / V**: Sanksi administratif OJK kategori pelanggaran **Ringan (F) / Sedang (G) / Berat (V)**.
  - **N / K**: Menerapkan Saham Dengan Hak Suara Multipel (SDHSM) di luar/di dalam Papan Ekonomi Baru.
  - **X / P**: Efek dalam Pemantauan Khusus (**X**) atau Notasi Khusus tambahan (**P**, *SE BEI 3 Agt 2026*). Selalu cek juga status **Suspensi / Unsuspend** (Peraturan I-L).
- **Batas Minimum Free Float 15%** (*Peraturan BEI No. I-A, efektif 31 Maret 2026*): Menaikkan syarat minimum saham publik dari 7,5% menjadi **15%** secara bertahap (emiten berkapitalisasi >= Rp5 triliun wajib memenuhi 12,5% paling lambat 31 Maret 2027 dan 15% paling lambat 31 Maret 2028).
- **High Shareholding Concentration / HSC & Rebalancing Indeks (Domestik & Global)** (*Siaran Pers BEI No. 110/BEI.SPR/11-2026 & Pengumuman BEI/KSEI 11-13 November 2026, data per 30 Oktober 2026*):
  - Saham dengan akumulasi 10 pemegang saham terbesar (*scripless*) + warkat >= **92,5%** masuk daftar **383 saham HSC** (penurunan kepemilikan akibat *repo* atau yang belum dilaporkan distribusinya tetap ditahan di HSC).
  - **Indeks Domestik (LQ45 & IDX80, efektif 18 Nov 2026 - 29 Jan 2027)**: **MAPI, NCKL, PGEO, TKIM** dikeluarkan karena HSC; digantikan oleh **ERAA, GGRM, PWON** (LQ45) serta **DKFT, IMPC, NICL, RMKE** (IDX80).
  - **Indeks Global (MSCI & FTSE Russell, update 17-23 Nov 2026)**: Review MSCI (efektif penutupan **30 November 2026**) berdampak pada **BRPT dan BRMS**; sementara konsultasi MSCI mengenai usulan reklasifikasi Indonesia ke *Standalone Market* berlangsung **hingga 30 April 2027** dengan **keputusan akhir paling lambat 30 Juni 2027** (selama konsultasi, MSCI membekukan kenaikan *Foreign Inclusion Factor* / penambahan saham baru). **FTSE Russell** mempertahankan Indonesia di *Secondary Emerging Market* tetapi menunda penambahan bobot hingga **Maret 2027**. Waspadai potensi *outflow* pasif **US$8-11 miliar** apabila reklasifikasi benar-benar terjadi.
  - **Penegakan Hukum & Pengawasan Manipulasi Saham/Waran (Update 21-23 Nov 2026)**: OJK dan BEI memperketat pengawasan saham terindikasi manipulasi harga (termasuk sanksi denda Rp2,1 triliun pada kasus perdagangan DSSA, BREN, CUAN, pelimpahan kasus manipulasi waran oleh OJK per 21 Nov 2026, serta **kolaborasi BEI menggandeng Nasdaq** per 23 Nov 2026 untuk menurunkan tim khusus deteksi dugaan manipulasi perdagangan) dan pembekuan rekening efek terkait. Tandai saham dengan lonjakan harga/waran tanpa dasar fundamental sebagai *High Regulatory Risk*.
- **Kuotasi Liquidity Provider Saham BEI** (*Implementasi mulai 20 April 2026*): Mencakup saham **GGRM, TKIM, TPMA, TUGU, dan WINS**. Pisahkan kuotasi dua arah (*bid-offer*) broker *Liquidity Provider* saat membaca *broker summary*.

### C. Biaya Transaksi Ritel, Pajak Dividen Bebas PPh 0%, & Filter Saham Syariah
- **Biaya Transaksi Standar Ritel BEI**: Fee Beli **~0,15%**, Fee Jual (termasuk PPh Final 0,1% sesuai *PP No. 14 Tahun 1997*) **~0,25%**, Total Pulang-Pergi **~0,40%**. Jika broker mengenakan *minimum fee* harian, beri peringatan pada pembelian bernilai kecil (<Rp500.000).
- **Pajak Dividen Bebas PPh 0%** (*UU HPP, PP No. 55 Tahun 2022, & PMK No. 18/PMK.03/2021*): Dividen saham dalam negeri (BEI) yang diterima Wajib Pajak Orang Pribadi **bebas PPh Final (0%)** (tidak dipotong 10% oleh emiten) **dengan syarat**: (1) diinvestasikan kembali ke instrumen yang diperbolehkan di dalam negeri (seperti saham/RDN/SBN/reksadana) minimal selama **3 tahun pajak**, dan (2) dilaporkan pada **Laporan Realisasi Investasi di SPT Tahunan** paling lambat akhir Maret tahun berikutnya. Jika tidak diinvestasikan kembali, dikenai PPh Final **10%** (setor sendiri).
- **Filter Saham Syariah (Opsional — DES OJK / ISSI / JII)**: Jika pengguna meminta saham syariah, pastikan emiten masuk **Daftar Efek Syariah (DES) OJK** (kegiatan usaha halal, total utang berbasis bunga terhadap total aset <= **45%**, dan total pendapatan non-halal <= **10%**).

## 2. Screener Dua Keranjang & Gerbang Kualitas Terpisah (Non-Bank vs Bank)

### A. Screener Awal Dua Keranjang (Hindari Bias Value Trap)
- **Keranjang 1 (Value / Cyclical)**: `PER < 10, PBV < 1,2, DER < 1,0, OCF Positif`.
- **Keranjang 2 (Quality Compounder & Big Bank)**: `ROE > 15%, PBV di bawah rata-rata historis 5 tahunnya (-1 SD), pertumbuhan laba bersih YoY > 10%`.

### B. Jalur A — Emiten Non-Keuangan: Piotroski F-Score (9 Poin)
1. **Profitabilitas (4 poin)**: Laba bersih > 0 (+1); Arus Kas Operasi / OCF > 0 (+1); ROA naik YoY (+1); OCF > Laba bersih (+1).
2. **Leverage, Likuiditas, & Dilusi (3 poin)**: Rasio utang jangka panjang turun (+1); *Current Ratio* naik (+1); Tidak ada penerbitan saham baru/dilusi (+1).
3. **Efisiensi Operasional (2 poin)**: *Gross Margin* naik (+1); *Asset Turnover* naik (+1).
- *Interpretasi*: **Skor 7-9 (Kuat)**, **Skor 5-6 (Sedang)**, **Skor 0-4 (Lemah — dilarang vonis beli)**. Saringan tambahan Beneish M-Score (> -1,78) dan Altman Z'' (< 1,1) sebagai sinyal investigasi lanjutan.

### C. Jalur B — Sektor Perbankan & Multifinance: Gerbang Kualitas Bank (6 Poin)
1. **ROE > 12%** (+1, ideal >15% untuk KBMI 4)
2. **NIM > 4,0%** (+1)
3. **NPL Gross < 3,0%** dan tren stabil/membaik (+1; rata-rata industri BI Sept 2026: **2,07% gross / 0,82% net**)
4. **Loan Loss Coverage Ratio (CKPN / NPL) > 150%** (+1)
5. **Capital Adequacy Ratio (CAR) > 15%** (+1; rata-rata industri Sept 2026: **24,30%**)
6. **Rasio Dana Murah (CASA) > 50%** atau *Cost of Fund (CoF)* terkendali (+1)
- *Interpretasi*: **Skor 5-6 (Kuat)**, **Skor 4 (Sedang)**, **Skor 0-3 (Lemah — dilarang vonis beli)**.
- **Katalis & Sensitivitas Likuiditas Bank (RDG BI 18 Nov 2026 & Update Pasar 23 Nov 2026)**: **BI-Rate 6,00%** (Deposit Facility 5,25%, Lending Facility 6,75%; kurs JISDOR **Rp18.223/USD** & spot **Rp18.203/USD** per 23 Nov 2026), diimbangi penguatan **KLM BI hingga 5% DPK** mulai 1 Desember 2026 dan fasilitas *Cross Currency Repo* (CCR).

### D. Aturan Konversi Kurs (USD -> IDR) & Kalkulasi Aksi Korporasi (Right Issue / Buyback / Stock Split)
- **Konversi Wajib USD ke IDR**: Ambil **kurs tengah BI / JISDOR terbaru** (acuan JISDOR per 23 Nov 2026: **Rp18.223/USD**; selalu cek pembaruan terakhir dan cantumkan angkanya), lalu konversikan EPS, BVPS, OCF, dan Kas Bersih ke Rupiah sebelum menghitung rasio valuasi.
- **Harga Teoritis Right Issue (TERP) & Efek Dilusi**: Jika emiten menggelar *Right Issue* (HMETD), hitung menggunakan `scripts/idx_calc.py terp`:
  - `TERP = (Rasio_Lama x Harga_Cum + Rasio_Baru x Harga_Pelaksanaan) / (Rasio_Lama + Rasio_Baru)`.
  - Periksa siapa pembeli siaga (*standby buyer*), apakah pengendali mengalihkan HMETD sehingga terjadi perubahan pengendali, dan berapa persen dilusi jika investor ritel tidak menebus haknya.
- **Buyback & Stock Split**: Untuk emiten dengan program *buyback* aktif (mis. BBRI, EXCL, CDIA), selalu cek **batas harga maksimal pembelian (*price cap*)** karena buyback tidak menopang harga jika harga pasar berada di atas batas tersebut. Untuk aksi *stock split* (mis. PRDA 1:5), sesuaikan seluruh perbandingan EPS, BVPS, dan harga historis secara proporsional.
- **Cek Siklus Dividen (*Dividend Trap*)**: Larang rekomendasi *swing buy* dalam **1-5 hari bursa menjelang Cum-Date** pada saham ber-*yield* jumbo (>7%).

## 3. Model Valuasi BEI, Reverse DCF, & Matriks Rating Tanpa Celah

### A. Reverse DCF, Hurdle Rate BEI, & Metode Sektoral
- **Hurdle Rate (Discount Rate / CoE) Tetap BEI**: **12%** untuk emiten berkualitas tinggi / Big-4 Banks, atau **14%** untuk emiten berisiko / siklikal / lapis kedua, dengan **Terminal Growth 4% - 5%** (acuan **BI-Rate 6,00%**, **Yield SUN 10 thn 7,73%** per 23 Nov 2026, PDB RI Q3 2026 **5,47% YoY**, inflasi **3,23% YoY**, ERP Indonesia 7%-8%, CRP ~2%-3%).
- **Reverse DCF & Kisaran Sensitivitas**: Gunakan `scripts/idx_calc.py dcf` untuk menghitung pertumbuhan FCF tersirat dibanding histori 3-5 tahun dan kisaran nilai wajar (±200 bps).
- **Metode Sektoral**: *Justified PBV* (`scripts/idx_calc.py pbv`, `CoE = 12%/14%`, `g = 5%-6%`) + Pita SD PBV 5 Thn untuk Perbankan; *EV/EBITDA* (Telko/Menara/RS); *Ex-Cash P/E* & *SOTP* (Komoditas/Holding, diskon 15%-20%); *Diskon RNAV* (Properti); *Forward P/E* & *SSSG* (Konsumer/Ritel).

### B. Matriks Keputusan Rating Tanpa Celah Logika
Hitung **Margin of Safety (MoS)** = `((Nilai Wajar - Harga Pasar) / Nilai Wajar) x 100%`:
- **STRONG BUY**: `MoS >= 25%` DAN (`Piotroski >= 7` atau `Skor Bank >= 5/6`) DAN tesis utuh.
- **BUY**: (`MoS 15% - 24%` DAN `Piotroski >= 6` / `Skor Bank >= 5/6`) ATAU (`MoS >= 25%` dengan `Piotroski 5-6` / `Skor Bank 4/6` dan OCF/laba inti sehat).
- **HOLD**: `MoS -10% s.d. +14%` dengan kualitas fundamental minimal sedang (`Piotroski >= 5` atau `Skor Bank >= 4/6`). Jika belum punya barang = *Wait & See* di area *Support*.
- **SELL**: Harga melampaui nilai wajar (`MoS < -10%`) ATAU salah satu *kill criteria* terpenuhi.
- **AVOID**: `Piotroski <= 4` ATAU `Skor Bank <= 3` ATAU OCF negatif kronis ATAU masuk FCA/UMA/Suspensi/saham gorengan tidak likuid.

## 4. Manajemen Risiko, Audit Order Broker, & Mode Eksekusi (Swing vs Hold)

- **Audit Order / Screenshot Aplikasi Broker**: Periksa 6 hal: (1) *Order Type* (`Limit` vs `Market`), (2) *Leverage* (wajib `None`), (3) Posisi *Price* terhadap *Bid/Ask/Low* & fraksi BEI, (4) *Expiry* (`Day` vs `GTC`), (5) Porsi pemakaian *Trading Balance*, dan (6) Kesesuaian saklar *Stop Loss / Take Profit* dengan horizon waktu.
- **Mode Swing vs Mode Hold (6-12+ Bulan)**:
  - **Mode Swing Trading**: Wajib aktifkan **Stop Loss** otomatis dan terapkan **Trailing Stop** (geser SL ke titik impas +0,40% setelah TP1 tercapai).
  - **Mode Hold / Investasi Menengah-Panjang (6-12+ Bulan)**: *Auto Stop Loss* di aplikasi boleh dimatikan (*OFF*) agar posisi tidak terbuang fluktuasi harian, dengan syarat emiten lolos gerbang kualitas kuat dan pengguna memantau *Kill Criteria* tiap rilis laporan keuangan kuartalan serta mematuhi syarat reinvestasi dividen 3 tahun (PMK 18/2021) agar bebas pajak.
- **Position Sizing Deterministik (`scripts/idx_calc.py size`)**: Otomatis memilih **Scaling-In 40/60** (`>= 2 lot`) atau **Single-Bullet Support 2** (`1 lot`), memotong fee BEI (0,15% beli / 0,25% jual), dan memunculkan peringatan jika 1 lot melebihi batas risiko 1% atau terkena *minimum fee* broker.

## 5. Pembaruan Regulasi, Indeks Global, & Makroekonomi (Terverifikasi 24 November 2026)

- **Eksklusi Saham HSC di 12 Indeks BEI (30 Nov 2026) & FTSE Russell (21 Des 2026)**:
  - Mulai **30 November 2026**, BEI mengeluarkan saham berstatus **HSC** dari **12 indeks bursa** ([Heygotrade, 24 Nov 2026](https://www.heygotrade.com/id/news/bursa-efek-indonesia-saham-hsc-12-indeks/)).
  - **FTSE Russell** (pengumuman 13 Nov 2026, berlaku efektif penutupan Jumat 18 Des / **Senin, 21 Desember 2026**) menghapus **14 saham HSC Indonesia** dari indeks globalnya, termasuk **BRIS** (*Large Cap*), **MTEL & TBIG** (*Mid Cap*), **MAPI & TKIM** (*Small Cap*), serta 9 saham *Micro Cap* ([Kompas, 16 Nov 2026](https://money.kompas.com/read/2026/11/16/145157126/ftse-keluarkan-14-saham-ri-waspadai-potensi-aksi-jual-hingga-desember)). Waspadai tekanan jual dana pasif hingga pertengahan Desember 2026.
- **Klarifikasi Konsultasi MSCI *Standalone Market* (24 Nov 2026)**: MSCI menegaskan bahwa usulan reklasifikasi Indonesia ke *Standalone Market* (diumumkan 15 Okt 2026) **masih berjalan dan belum dibatalkan** ([Bareksa, 24 Nov 2026](https://www.bareksa.com/berita/saham/2026-11-24/msci-bantah-batal-turunkan-status-ri-konsultasi-tetap-berjalan)).
- **Risiko *Voluntary Delisting* Sektor Perbankan Akibat Aturan *Free Float* 15%**: Per 23 Nov 2026 masih terdapat **27 emiten bank** dengan *free float* di bawah 15%; **PT Bank SMBC Indonesia Tbk (BTPN)** memilih jalur *voluntary delisting* dan disuspensi sejak 23 Nov 2026 ([Kontan, 24 Nov 2026](https://keuangan.kontan.co.id/news/emiten-bank-sulit-memenuhi-ketentuan-free-float-15-ada-yang-memutuskan-delisting)). Wajib cek apakah bank ber-*free float* < 15% memilih *rights issue* (seperti BRIS) atau berisiko *go private*.
- **Snapshot Makro & Komoditas (24 Nov 2026)**:
  - **BI-Rate:** **6,00%** (18 Nov 2026) + insentif KLM hingga 5% DPK mulai 1 Des 2026.
  - **Kurs Spot USD/IDR:** Ditutup di **Rp18.230/USD** pada 24 Nov 2026 ([Kontan](https://investasi.kontan.co.id/news/rupiah-spot-ditutup-melemah-015-ke-rp-18230-per-dolar-as-pada-selasa-2411)).
  - **Komoditas Acuan:** HBA Batu Bara Nov 2026 **US$130,81/ton** ([Kompas](https://money.kompas.com/read/2026/11/17/155806226/harga-batu-bara-acuan-naik-di-november-2026-jadi-13081-dollar-as-per-ton)), Newcastle **US$145–150/ton** ([Trading Economics](https://id.tradingeconomics.com/commodity/coal)), dan Emas Antam di kisaran **Rp2,505 juta/gram** ([Kontan](https://pusatdata.kontan.co.id/news/ihsg-terjun-saham-saham-ini-justru-mencatat-net-buy-asing-pada-rabu-1811)).

### Pembaruan Tambahan 25 November 2026
- **3 Pilar Reformasi Struktural BEI (25 Nov 2026)**: BEI bersama OJK mempercepat reformasi di 3 area utama — transparansi data kepemilikan/HSC, peningkatan likuiditas (*free float* minimum 15%), serta publikasi indikasi awal manipulasi perdagangan — untuk memulihkan kepercayaan investor serta memenuhi kriteria MSCI, FTSE Russell, dan S&P Dow Jones ([Pasardana](https://pasardana.id/news/2026/11/25/reformasi-pasar-modal-dikebut-bei-bidik-kepercayaan-investor-dan-restu-msci), [Validnews](https://validnews.id/ekonomi/2026/11/25/usai-disorot-msci-bei-siapkan-3-reformasi-pasar-modal-162700563)).
- **Siklus Evaluasi Mayor DES OJK / ISSI / JII (Berlaku s.d. 30 Nov 2026)**: Konstituen indeks syariah periode evaluasi Mei 2026 berlaku hingga **30 November 2026** ([Syariahsaham.id](https://syariahsaham.id/rombak-besar-indeks-saham-syariah-2026-jii-issi-berubah-ini-yang-wajib-diketahui-investor/), [IDX](https://www.idx.co.id/id/idx-syariah/indeks-saham-syariah)). Mulai awal Desember 2026, wajib verifikasi ulang daftar hasil evaluasi November 2026 agar tidak memegang emiten yang keluar dari DES OJK/JII.
- **Kurs USD/IDR Rekor Baru (25 Nov 2026)**: Rupiah spot ditutup melemah ke **Rp18.310 – Rp18.332/USD** ([ANTARA](https://www.antaranews.com/berita/5877715/rupiah-rabu-sore-ditutup-melemah-jadi-rp18332-per-dolar-as), [Inilah](https://www.inilah.com/rupiah-ditutup-di-rp18310-per-dolar-as-cetak-rekor-terlemah-sepanjang-masa)). Perketat audit eksposur utang USD tanpa *hedging* & bahan baku impor.

### Pembaruan Tambahan 26 November 2026
- **Jadwal Efektif Eksklusi HSC pada Indeks BEI & Syariah**: Dari 12 indeks BEI yang mengeluarkan saham **HSC**, **3 indeks papan berlaku efektif 30 November 2026**, sedangkan **9 indeks lainnya termasuk ISSI dan JII berlaku efektif 1 Desember 2026** ([Maybank Trade](https://www.maybanktrade.co.id/berita/12-indeks-bei-yang-coret-saham-hsc-mulai-30-november-2026/)). Wajib cek apakah saham Syariah berstatus HSC terdampak *rebalancing* pasif ISSI/JII per 1 Des 2026.
- **Pelongsaran GWM ke 7% & Remunerasi GWM (Efektif 1 Des 2026)**: BI menurunkan GWM bank umum konvensional dari 8% menjadi **7% mulai 1 Desember 2026** (menambah likuiditas ~Rp90 triliun per 1 poin persentase) disertai **remunerasi GWM rata-rata maksimal 5%**, sementara batas insentif KLM disesuaikan dari 6% menjadi **5%** dengan fokus ke sektor padat karya/UMKM ([Pasardana, 25 Nov 2026](https://pasardana.id/news/2026/11/25/gwm-turun-bi-rate-naik-perbankan-hadapi-dilema-kredit-usaha-kecil-makin-tertinggal)).
- **Red Flag *Rights Issue* Tanpa Pembeli Siaga (*No Standby Buyer*)**: Pada **26 Nov 2026**, **BRIS** mengumumkan rencana penerbitan maksimal 6,8 miliar saham (dilusi maks **12,85%**) **tanpa pembeli siaga** ([Kontan](https://keuangan.kontan.co.id/news/bsi-bris-terbitkan-68-miliar-saham-tanpa-pembeli-siaga-dilusi-maksimal-1285)). Dalam audit aksi korporasi, *rights issue* tanpa *standby buyer* wajib ditandai berisiko *overhang* dan gagal serap penuh bila harga pasar mendekati/di bawah harga pelaksanaan.
- **Snapshot Makro & Komoditas (25–26 Nov 2026)**:
  - **Kurs Spot USD/IDR:** Ditutup melemah ke rekor baru **Rp18.364/USD** pada 26 Nov 2026 setelah sempat menyentuh **Rp18.400–Rp18.401/USD** *intraday* ([Kontan](https://investasi.kontan.co.id/news/rupiah-ditutup-melemah-ke-rp-18364-per-dolar-as-hari-ini-2611-terburuk-lagi), [Liputan6](https://www.liputan6.com/bisnis/read/8343117/rupiah-melemah-ke-18364-per-dolar-as-terseret-tarif-as)).
  - **Komoditas Acuan:** WTI **US$83,68/barel** & Brent **US$94,61/barel**, Batu Bara ICE Newcastle **US$147,40/ton**, CPO **MYR 4.229/ton**, Nikel LME **US$15.169/ton**, Timah LME **US$37.187/ton** (penutupan 25 Nov, [Kumparan](https://kumparan.com/kumparanbisnis/harga-komoditas-minyak-mentah-naik-2-persen-timah-anjlok-nyaris-6-persen-28e8235SRWY)), serta Emas Antam **Rp2.501.000/gram** (*buyback* **Rp2.111.000/gram**, 26 Nov 2026, [Kompas](https://money.kompas.com/read/2026/11/26/123944126/harga-emas-antam-hari-ini-26-november-2026-naik-tipis-cek-rinciannya), [TradingView/Kontan](https://www.tradingview.com/news/kontan:dc3ad7fa887ea:0/?mobileapp_new=true)).
