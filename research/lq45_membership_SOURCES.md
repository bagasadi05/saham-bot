# Provenance — Keanggotaan LQ45 (point-in-time), Feb 2016 – Nov 2026

Pendamping `lq45_membership_2016-2026.json` (Track B langkah 1, bot saham).
Disusun 17 Nov 2026 dari 5 riset slice + verifikasi silang rantai penuh.

## Cara baca file JSON

- Satu baris = satu interval keanggotaan: `{"ticker", "start", "end"}`.
- Interval **tertutup** `[start, end]`: `end` = **hari terakhir** menjadi anggota
  (= tanggal efektif event keluar dikurangi 1 hari). Contoh: keluar efektif
  2022-02-02 berarti `end` = 2022-02-01. Ini mengikuti contoh di
  `research/bias.py` (`"start": "2020-01-01", "end": "2024-12-31"`).
- `end: null` = masih anggota pada akhir jendela (periode berjalan P6,
  18 Nov 2026 – 29 Jan 2027).
- Anggota baseline (daftar Feb 2016) memakai `start` = 2016-02-01.
  Untuk MYRX/HMSP/ANTM itu tanggal masuk yang sebenarnya (event Feb 2016);
  untuk 42 ticker baseline lainnya itu **left-censored** — mereka mungkin sudah
  anggota jauh sebelum Feb 2016, tanggal masuk persisnya di luar jendela riset.

## ⚠️ Konflik format dengan `research/bias.py` (perlu diperbaiki sebelum dipakai)

Loader `research/bias.py` saat ini **menolak `end: null`**
(`REQUIRED = ("ticker", "start", "end")`, `row.get(k)` harus truthy).
File ini memakai `end: null` sesuai spesifikasi brief Track B.
Sebelum file dipakai, perbaiki loader satu baris (izinkan `end` null dan
perlakukan sebagai 'masih anggota'), ATAU sepakati sentinel lain.
File ini TIDAK diubah-ubah agar lolos loader yang sekarang — datanya yang benar,
loadernya yang perlu menyesuaikan. (Jangan commit perbaikan loader tanpa izin.)

## Ringkasan cakupan

- 44 tinjauan/event dalam rantai: **28 LENGKAP**, **1 LENGKAP-DEDUKSI** (Sep 2021, lihat bawah), **14 NO-CHANGE**, **1 MISSING**.
- 128 interval keanggotaan, 98 ticker unik.
- Rantai terverifikasi penuh: dirangkai **mundur** dari anchor P1
  (Nov 2025–1 Feb 2026, 45 ticker, dari delta Feb 2026 terverifikasi IMQ21/Antara)
  sampai baseline Feb 2016, lalu **maju** lagi ke P6 — tiap langkah tepat 45
  ticker, tanpa selisih yang tak terjelaskan.

## Peristiwa per periode

| Periode | Efektif | Masuk | Keluar | Status |
|---|---|---|---|---|
| Feb 2016 | 2016-02-01 | ANTM, HMSP, MYRX | EXCL, ITMG, WTON | LENGKAP |
| Agu 2016 | 2016-08-01 | ELSA | TBIG | LENGKAP |
| Nov 2016 | 2016-11-01 | — | — | NO-CHANGE |
| Feb 2017 | 2017-02-01 | BUMI, EXCL, PPRO | BMTR, MPPA, SILO | LENGKAP |
| Mei 2017 | 2017-05-01 | — | — | NO-CHANGE |
| Agu 2017 | 2017-08-01 | BJBR, BMTR, BRPT | ASRI, CPIN, ELSA | LENGKAP |
| Feb 2018 | 2018-02-01 | INDY, TPIA, TRAM, WSBP | AALI, LSIP, PPRO, SMRA | LENGKAP |
| Mei 2018 | 2018-05-01 | — | — | NO-CHANGE |
| Agu 2018 | 2018-08-01 | BKSL, ELSA, INKP, ITMG, MEDC | BMTR, BUMI, MYRX, PWON, TRAM | LENGKAP |
| Nov 2018 | 2018-11-01 | — | — | NO-CHANGE |
| Feb 2019 | 2019-02-01 | CPIN, ERAA, PWON, TKIM | BJBR, BKSL, LPKR, SSMS | LENGKAP |
| Mei 2019 | 2019-05-01 | — | — | MISSING |
| Agu 2019 | 2019-08-01 | BTPS, CTRA, JPFA | ADHI, ELSA, WSBP | LENGKAP |
| Nov 2019 | 2019-11-01 | — | — | NO-CHANGE |
| Feb 2020 | 2020-02-03 | ACES, TBIG, TOWR | INDY, MEDC, TPIA | LENGKAP |
| Mei 2020 | 2020-05-01 | — | — | NO-CHANGE |
| Agu 2020 | 2020-08-03 | MDKA, MIKA, SMRA | BRPT, LPPF, WSKT | LENGKAP |
| Nov 2020 | 2020-11-01 | — | — | NO-CHANGE |
| Feb 2021 | 2021-02-01 | MEDC, TPIA | SCMA, SRIL | LENGKAP |
| Mei 2021 | 2021-05-01 | — | — | NO-CHANGE |
| Agu 2021 | 2021-08-02 | BRPT, TINS | BTPS, CTRA | LENGKAP |
| Sep 2021 fast-entry | 2021-09-29 | BUKA | SMRA | LENGKAP-DEDUKSI |
| Nov 2021 | 2021-11-01 | — | — | NO-CHANGE |
| Feb 2022 | 2022-02-02 | AMRT, BFIN, EMTK, HRUM, WSKT | ACES, AKRA, BSDE, JSMR, PWON | LENGKAP |
| Mei 2022 | 2022-05-01 | — | — | NO-CHANGE |
| Agu 2022 | 2022-08-01 | ARTO, BRIS, INDY | GGRM, PTPP, TKIM | LENGKAP |
| Nov 2022 | 2022-11-01 | — | — | NO-CHANGE |
| Feb 2023 | 2023-02-01 | ACES, AKRA, ESSA, GOTO, SCMA, SIDO, SRTG | BFIN, ERAA, HMSP, MIKA, MNCN, WIKA, WSKT | LENGKAP |
| Mei 2023 | 2023-05-01 | — | — | NO-CHANGE |
| Agu 2023 | 2023-08-01 | GGRM, MAPI | JPFA, TINS | LENGKAP |
| Nov 2023 | 2023-11-01 | — | — | NO-CHANGE |
| Feb 2024 | 2024-02-01 | MBMA, MTEL, PGEO, PTMP | INDY, SCMA, TBIG, TPIA | LENGKAP |
| Mei 2024 | 2024-05-02 | AMMN, ISAT | EMTK, PTMP | LENGKAP |
| Agu 2024 | 2024-08-01 | JSMR | SRTG | LENGKAP |
| Nov 2024 | 2024-11-01 | ADMR, SMRA | GGRM, HRUM | LENGKAP |
| Feb 2025 | 2025-02-03 | CTRA, JPFA, MAPA | BUKA, INTP, MTEL | LENGKAP |
| Mei 2025 | 2025-05-02 | — | — | NO-CHANGE |
| Agu 2025 | 2025-08-01 | AADI, SCMA | ESSA, SIDO | LENGKAP |
| Nov 2025 | 2025-11-03 | BUMI, DSSA, EMTK, HEAL, NCKL | ARTO, BRIS, JSMR, MAPA, SMRA | LENGKAP |
| Feb 2026 | 2026-02-02 | BREN | ACES | LENGKAP |
| Mei 2026 | 2026-05-04 | CUAN, DEWA, ESSA, HRTA, WIFI | BREN, CTRA, DSSA, HEAL, NCKL | LENGKAP |
| Agu 2026 | 2026-08-03 | INDY, NCKL | SMGR, TOWR | LENGKAP |
| Nov 2026 P5 | 2026-11-02 | ELSA, SMGR | CUAN, SCMA | LENGKAP |
| Nov 2026 P6 | 2026-11-18 | ERAA, GGRM, PWON | MAPI, NCKL, PGEO | LENGKAP |

## Sumber per periode

### Feb 2016 (efektif 2016-02-01) — LENGKAP

- Indonesia Investments (MYRX/HMSP/ANTM masuk, EXCL/ITMG/WTON keluar); sahamu.com, lampiran Peng-00021/BEI.OPP/01-2016 (25 Jan 2016)

### Agu 2016 (efektif 2016-08-01) — LENGKAP

- sahamu.com, Peng-00671/BEI.OPP/07-2016 (26 Jul 2016); daftar resmi IDX Agu 2016-Jan 2017

### Nov 2016 (efektif 2016-11-01) — NO-CHANGE

- Struktural: pergantian konstituen hanya tiap 6 bulan (Feb & Agu); periode tunggal IDX Agu 2016-Jan 2017 tanpa jeda November

### Feb 2017 (efektif 2017-02-01) — LENGKAP

- belajarcuan.com 29 Mar 2018 (daftar lengkap 45 + 3 keluar/3 baru); diff daftar resmi IDX Agu 2016 vs Feb 2017 identik; Bareksa 8 Feb 2018 (MPPA & SILO Grup Lippo terlempar)

### Mei 2017 (efektif 2017-05-01) — NO-CHANGE

- Struktural: pergantian hanya Feb & Agu; periode tunggal IDX Feb-Jul 2017; ringkasan JII Jun 2017 masih menandai EXCL & MYRX anggota LQ45

### Agu 2017 (efektif 2017-08-01) — LENGKAP

- belajarcuan.com 29 Mar 2018 (daftar lengkap 45 + 3 keluar/3 baru); daftar resmi IDX Agu 2017-Jan 2018 (idx.co.id/media/2463/20170927_idx-lq45-august-2017.pdf)

### Feb 2018 (efektif 2018-02-01) — LENGKAP

- BisnisPro 26 Jan 2018; BelajarCuan 28 Mar 2018; Kiwoom Monthly Report Feb 2018 hlm.16 (tabel Inclusion/Deletion) + kalender hlm.22 (efektif 1 Feb 2018); Bareksa 5 Mar 2018 (TPIA anggota baru). Dua riset independen (slice 2016-2017 & 2018-2019) sepakat persis.

### Mei 2018 (efektif 2018-05-01) — NO-CHANGE

- Metodologi era itu 6-bulanan (Feb & Agu)

### Agu 2018 (efektif 2018-08-01) — LENGKAP

- Pengumuman BEI Peng-00696/BEI.OPP/07-2018 (25 Jul 2018) via Sosial79 + daftar lengkap Sinarmas Sekuritas

### Nov 2018 (efektif 2018-11-01) — NO-CHANGE

- Metodologi 6-bulanan. Peng-00893/BEI.OPP/11-2018 (23 Nov 2018) = perubahan metodologi bobot free float, BUKAN pergantian konstituen

### Feb 2019 (efektif 2019-02-01) — LENGKAP

- Perbandingan kolom FEB 2019-JULI 2019 vs AGS 2018-JAN 2019, tabel Scribd 'LQ45 Constituents and Stock Splits 2023'; dikuatkan daftar BEI Nov 2019

### Mei 2019 (efektif 2019-05-01) — MISSING

- 3x pencarian tanpa hasil. INFERENSI (bukan fakta): selisih Feb 2019 -> Agu 2019 persis sama dengan yang Bareksa atribusikan ke evaluasi Agu 2019, jadi kemungkinan besar NO-CHANGE. Rantai memperlakukannya sebagai no-change; bila ada perubahan Mei 2019 yang berbalik sebelum Agu 2019, rantai tidak bisa mendeteksinya.

### Agu 2019 (efektif 2019-08-01) — LENGKAP

- Bareksa 26 Jul 2019 (mengutip BEI via CNBC Indonesia); Ajaib (BTPS masuk 1 Agu 2019)

### Nov 2019 (efektif 2019-11-01) — NO-CHANGE

- Daftar BEI Peng-00513/BEI.POP/10-2019 (25 Okt 2019) via lampiran tesis UNAS: ke-45 saham 'Tetap'

### Feb 2020 (efektif 2020-02-03) — LENGKAP

- Blog Innibaksa 27 Apr 2020 (daftar lengkap 45 Feb-Jul 2020); BigAlpha (TPIA didepak Jan 2020); lampiran skripsi Undiksha

### Mei 2020 (efektif 2020-05-01) — NO-CHANGE

- Rezim pra-Apr 2024 (mayor hanya Jan/Jul, efektif Feb/Agu; minor hanya bobot) + validasi rantai (daftar lengkap per periode cocok tanpa selisih)

### Agu 2020 (efektif 2020-08-03) — LENGKAP

- BigAlpha Okt 2020 (MDKA/MIKA/SMRA gantikan BRPT/LPPF/WSKT)

### Nov 2020 (efektif 2020-11-01) — NO-CHANGE

- Rezim pra-Apr 2024 + validasi rantai

### Feb 2021 (efektif 2021-02-01) — LENGKAP

- BigAlpha 26 Jan 2021 (pengumuman BEI Senin 25 Jan 2021)

### Mei 2021 (efektif 2021-05-01) — NO-CHANGE

- Rezim pra-Apr 2024 + validasi rantai

### Agu 2021 (efektif 2021-08-02) — LENGKAP

- Stockbit Snips 27 Jul 2021 (berlaku 2 Agu 2021-Jan 2022)

### Sep 2021 fast-entry (efektif 2021-09-29) — LENGKAP-DEDUKSI

- BUKA MASUK 29 Sep 2021 terkonfirmasi: Trenasia 30 Sep 2021 (mengutip keterbukaan BEI 30 Sep 2021, 'berlaku efektif 29 Sep 2021-Jan 2022'). SMRA KELUAR 29 Sep 2021 = DEDUKSI ELIMINASI (bukan berita eksplisit): lampiran Peng-00023/BEI.POP/01-2022 menandai 6 'Baru' (AMRT/BFIN/BUKA/EMTK/HRUM/WSKT) tapi tabel 'Keluar' hanya 5 (ACES/AKRA/BSDE/JSMR/PWON) — SMRA satu-satunya nama dari daftar Agu 2021 yang hilang tanpa tercantum di tabel keluar; didukung Kontan 'pergantian lima saham' untuk review Feb 2022. Bila pengumuman fast-entry BEI (Peng-.../09-2021) ditemukan kelak, verifikasi pasangan ini.

### Nov 2021 (efektif 2021-11-01) — NO-CHANGE

- Minor; periode Agu 2021-Jan 2022 berlaku utuh (rantai: daftar Feb 2022 cocok dengan Agu 2021 + fast-entry + delta Feb 2022)

### Feb 2022 (efektif 2022-02-02) — LENGKAP

- BEI Peng-00023/BEI.POP/01-2022 (25 Jan 2022) via bloglq45.my.id 19 Feb 2022 — lampiran: 6 'Baru' vs 5 'Keluar' (BUKA sudah masuk Sep 2021, lihat atas); Kontan via duniafintech 9 Feb 2022 ('pergantian lima saham'); Stockbit Snips 15 Feb 2022 (daftar lengkap 45); lampiran skripsi Undiksha. TGL EFEKTIF DIKOREKSI: 2 Feb 2022, bukan 1 Feb (1 Feb 2022 = Imlek, BEI libur; gambar lampiran menulis '2 Februari' — tahun '2021' di gambar itu typo dokumen).

### Mei 2022 (efektif 2022-05-01) — NO-CHANGE

- Siaran Pers BEI 28 Mar 2024 (rezim pra-Apr 2024: mayor hanya Jan & Jul); Stockbit Snips 26 Jan 2023 (minor hanya menyesuaikan bobot); PDF 'Daftar Saham LQ45 Mei 2022' (vintra.co.id): daftar diterbitkan per semester, isi = periode Feb-Jul 2022

### Agu 2022 (efektif 2022-08-01) — LENGKAP

- Stockbit Snips 26 Jul 2022; Vibiznews 27 Jul 2022; QMFinancial 2 Agu 2022. CATATAN: lampiran skripsi Undiksha SALAH di periode ini (mempertahankan GGRM/TKIM, menghilangkan TLKM & WSKT) — dibantah 3 berita + daftar Finplan Feb 2023 (TLKM ada). Delta berita yang dipakai.

### Nov 2022 (efektif 2022-11-01) — NO-CHANGE

- Rezim pra-Apr 2024; daftar Agu 2022-Jan 2023 berlaku utuh

### Feb 2023 (efektif 2023-02-01) — LENGKAP

- Kalderanews 1 Feb 2023 (daftar lengkap 45); Finplan 27 Jan 2023 (daftar lengkap 45, identik); Stockbit Snips 26 Jan 2023. DISKREPANSI: ringkasan berita menghitung 6/6 tanpa GOTO/WSKT — versi 7/7 dari dua daftar lengkap identik + konfirmasi Agu 2023 (EMTK & GOTO 'Tetap', BFIN tidak ada) yang dipakai. CATATAN: lampiran Undiksha SALAH di Feb 2023/Agu 2023/Feb 2024 (mempertahankan BFIN, menghilangkan EMTK).

### Mei 2023 (efektif 2023-05-01) — NO-CHANGE

- Rezim pra-Apr 2024; daftar Feb-Jul 2023 berlaku utuh

### Agu 2023 (efektif 2023-08-01) — LENGKAP

- investor.id 26 Jul 2023 (Peng-00190/BEI.POP/07-2023, 25 Jul 2023); Kalderanews 31 Jul 2023 (daftar lengkap 45)

### Nov 2023 (efektif 2023-11-01) — NO-CHANGE

- Rezim pra-Apr 2024; daftar Agu 2023-Jan 2024 berlaku utuh

### Feb 2024 (efektif 2024-02-01) — LENGKAP

- Stockbit Snips 26 Jan 2024; Bloomberg Technoz 26 Jan 2024

### Mei 2024 (efektif 2024-05-02) — LENGKAP

- Stockbit Snips 25 Apr 2024; Bloomberg Technoz 29 Apr 2024. Mulai rezim triwulanan (Apr 2024).

### Agu 2024 (efektif 2024-08-01) — LENGKAP

- Kalderanews 31 Jul 2024 (daftar lengkap 45)

### Nov 2024 (efektif 2024-11-01) — LENGKAP

- Kontan via TradingView 30 Okt 2024; Indovestory 6 Nov 2024. TGL EFEKTIF BELUM PASTI: sumber hanya 'November 2024'; 1 Nov 2024 = Jumat/hari bursa pertama (inferensi) — perlu konfirmasi pengumuman BEI.

### Feb 2025 (efektif 2025-02-03) — LENGKAP

- Kontan via TradingView 23 Jan 2025; Brights

### Mei 2025 (efektif 2025-05-02) — NO-CHANGE

- Ragamutama 1 Mei 2025; Kontan via TradingView ('tidak terdapat perubahan', pengumuman BEI 24 Apr 2025)

### Agu 2025 (efektif 2025-08-01) — LENGKAP

- Kontan via TradingView (pengumuman BEI 25 Jul 2025); Trenasia 28 Jul 2025

### Nov 2025 (efektif 2025-11-03) — LENGKAP

- KabarBursa (pengumuman BEI 27 Okt 2025, Peng-00601/BEI.POP/10-2025); Kontan via TradingView; Heyyoyo 28 Okt 2025

### Feb 2026 (efektif 2026-02-02) — LENGKAP

- IMQ21/Antara (daftar lengkap 45)

### Mei 2026 (efektif 2026-05-04) — LENGKAP

- Bareksa/Ajaib/theeconopost (5 masuk 5 keluar, tabel lengkap)

### Agu 2026 (efektif 2026-08-03) — LENGKAP

- Stockbit Snips 28 Jul 2026; Fortune Indonesia; CIMB Niaga Weekly Review 3 Agu 2026

### Nov 2026 P5 (efektif 2026-11-02) — LENGKAP

- BEI Peng-00205/BEI.POP/10-2026 (27 Okt 2026, REVISI atas Peng-00204) + Bloomberg Technoz + Stockbit Snips + RCTI+. Pengumuman awal 26 Okt (Peng-00204) menampilkan CUAN bertahan tanpa ELSA — yang dipakai adalah hasil REVISI.

### Nov 2026 P6 (efektif 2026-11-18) — LENGKAP

- BEI Peng-00218/BEI.POP/11-2026 (11 Nov 2026) via StockWatch (daftar lengkap 45). Evaluasi insidental (HSC). Periode berjalan s.d. 29 Jan 2027.

## Catatan & caveat (baca sebelum memakai)

1. **Mei 2019 MISSING.** Tidak ditemukan sumber apa pun (3x pencarian). Rantai
   memperlakukannya sebagai no-change karena selisih Feb 2019 → Agu 2019 persis
   sama dengan delta event Agu 2019 (Bareksa). Bila ada perubahan Mei 2019 yang
   berbalik sebelum Agu 2019, rantai tidak bisa mendeteksinya.
2. **Nov 2024 — tanggal efektif belum pasti.** Sumber hanya menyebut 'November 2024';
   dipakai 2024-11-01 (Jumat, hari bursa pertama — inferensi). Perlu konfirmasi
   pengumuman BEI bila presisi tanggal penting.
3. **Sep 2021 fast-entry BUKA/SMRA — SMRA deduksi eliminasi.** BUKA masuk 29 Sep 2021
   terkonfirmasi (Trenasia 30 Sep 2021, mengutip keterbukaan BEI). SMRA sebagai yang
   keluar pada tanggal itu dideduksi dari lampiran Peng-00023/BEI.POP/01-2022:
   kolom 'Baru' ada 6 (AMRT/BFIN/BUKA/EMTK/HRUM/WSKT) tapi tabel 'Keluar' hanya 5
   (ACES/AKRA/BSDE/JSMR/PWON); SMRA satu-satunya nama dari daftar Agu 2021 yang
   hilang tanpa tercantum di tabel keluar. Didukung Kontan ('pergantian lima saham'
   untuk review Feb 2022). Bila pengumuman fast-entry BEI (Peng-.../09-2021)
   ditemukan kelak, verifikasi pasangan ini.
4. **Feb 2022 efektif 2022-02-02, bukan 1 Feb.** 1 Feb 2022 = Imlek (BEI libur);
   gambar lampiran Peng-00023 menulis '2 Februari' (tahun '2021' di situ typo dokumen).
5. **NO-CHANGE minor pra-Apr 2024** = inferensi rezim, bukan artikel per kuartal:
   Siaran Pers BEI 28 Mar 2024 menyatakan evaluasi mayor hanya Jan & Jul (efektif
   Feb & Agu) dan sisanya minor; Stockbit Snips menegaskan minor 'tidak mengubah
   konstituen, hanya menyesuaikan bobot'. Diperkuat validasi rantai (daftar lengkap
   per semester cocok tanpa selisih di tiap langkah). Pengecualian: Mei 2025 NO-CHANGE
   terkonfirmasi artikel ('tidak terdapat perubahan', pengumuman BEI 24 Apr 2025);
   Nov 2019 NO-CHANGE terkonfirmasi daftar BEI Peng-00513 (ke-45 'Tetap').
6. **Lampiran skripsi Undiksha (daftar Agu 2021–Jul 2024, sumber idx.co.id) mengandung
   kesalahan transkripsi — JANGAN dipakai mentah-mentah:** daftar Agu 2022
   mempertahankan GGRM/TKIM dan menghilangkan TLKM & WSKT (dibantah Stockbit Snips +
   Vibiznews + QMFinancial + Finplan Feb 2023); daftar Feb 2023/Agu 2023/Feb 2024
   mempertahankan BFIN dan menghilangkan EMTK (dibantah Finplan + Kalderanews, dua
   daftar lengkap identik). Rantai ini memakai **delta berita** sebagai patokan;
   lampiran hanya dipakai sebagai silang di periode yang cocok (Agu 2021, Feb 2022,
   Feb 2024-diff). Feb 2023 memakai versi 7/7 dari daftar lengkap (GOTO masuk,
   WSKT keluar) — ringkasan berita 6/6 tidak menyebut keduanya.
7. **Revisi Nov 2026:** pengumuman awal 26 Okt 2026 (Peng-00204) menampilkan CUAN
   bertahan tanpa ELSA; REVISI 27 Okt 2026 (Peng-00205) menukar CUAN keluar / ELSA
   masuk. Yang dipakai = hasil revisi (berlaku efektif). Artikel Kontan (26 Okt) &
   InvestasiKu menampilkan daftar pra-revisi — jangan dipakai.
8. **Wikipedia LQ45 tidak dipakai:** tabel Agu–Okt 2026 hanya 44 baris, tanpa NCKL,
   referensi dari 2024.
9. **Rezim evaluasi berubah sepanjang jendela:** 2016–Mar 2024 = mayor 2x/tahun
   (efektif Feb & Agu), minor hanya bobot; sejak Apr 2024 = triwulanan (efektif
   Feb/Mei/Agu/Nov); Nov 2026 ada 2 evaluasi insidental (P5 2 Nov & P6 18 Nov,
   keduanya dipicu daftar HSC).

## Validasi yang dijalankan (script perakit, 17 Nov 2026)

- Rantai mundur anchor P1 → baseline Feb 2016: tiap langkah tepat 45 ticker;
  tiap IN ada di state, tiap OUT tidak ada di state (asersi keras).
- Cek silang independen: Feb 2016 memuat ANTM/HMSP/MYRX (tanpa EXCL/ITMG/WTON);
  Agu 2016 memuat ELSA/MYRX/HMSP/ANTM/WIKA/SILO/SSMS (tanpa TBIG/EXCL/ITMG/WTON);
  Feb 2022 rantai == daftar lengkap lampiran Undiksha (persis); Agu 2022 rantai
  memuat TLKM/EMTK/WSKT (tanpa GGRM/PTPP/TKIM); Feb 2023 rantai memuat
  EMTK/GOTO/TLKM (tanpa BFIN/WSKT); diff Agu 2023 → Feb 2024 persis delta berita;
  diff Feb → Agu 2019 persis event Agu 2019 (dukung asumsi Mei 2019 no-change).
- Jalan maju baseline → P6: anggota akhir == daftar P6 Nov 2026 (persis).
- Interval: tanpa overlap per ticker; tiap tanggal efektif event tepat 45 anggota.
