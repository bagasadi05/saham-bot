# Changelog — SahamBuddy

Semua perubahan penting pada repo ini. Format mengikuti [Keep a Changelog](https://keepachangelog.com/id/1.1.0/);
repo belum memakai nomor rilis, jadi entri dikelompokkan per tanggal kerja.

Baseline sebelum rangkaian ini: commit `b11fcd2` (PLAN.md awal, 277 tes).
Rangkaian di bawah: **25 commit**, `e93e2d0` … `202ec2b` (2026-11-12).

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
  bila semua SL (2×ATR di harga sekarang) tersentuh. Skema store v3.
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

## Catatan kompatibilitas

- Skema SQLite naik ke **v4** (v3: `real_positions`/`real_trades`; v4:
  `zone_state`). Migrasi berjalan otomatis saat start; backup harian tetap
  memakai API backup SQLite.
- `bot.py` tetap menjadi entrypoint (`python bot.py`); Docker/run.sh tidak
  berubah. Nama-nama lama (`cmd_*`, `_do_*`, `_money`, `_sinyal_row`, dst.)
  masih tersedia sebagai wrapper/alias untuk tes dan pemanggil lama.
- Dependensi baru: `tenacity==9.2.1` + extra `[rate-limiter]` untuk
  `AIORateLimiter` (dipin di `requirements.txt`).
