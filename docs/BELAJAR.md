# BELAJAR.md — Rencana belajar 4 minggu untuk SahamBot

Disusun 12 November 2026 dari empat catatan di `temp/belajar/`: `kuant.md`, `data.md`,
`engineering.md`, `repo.md`. Tidak ada kode repo yang diubah selain berkas ini. Nomor baris
diperiksa ulang di sesi ini; angka yang berasal dari artefak disebut sumbernya.

## 0. Baseline yang saya ukur

| Yang diukur | Nilai | Bukti |
|---|---|---|
| Suite tes | **330 tes, exit 0** | `.\.venv\Scripts\python.exe -m pytest -q` → 330 titik (72×4+42), exit 0; `--collect-only` total 330 |
| IC skor teknikal | mean **0,0115**; t **2,68**; 2.231 tanggal | `data/validation.json` → `score.ic` (`meta.generated` 2026-11-06) |
| IC model 12 faktor OOS | 0,047 / 0,0482 / 0,0251 / −0,0256 / 0,0626 → rata-rata **0,031** | idem → `walk_forward` |
| Turnover portofolio | **73,6%** per rebalance | idem → `portfolio.avg_turnover_pct` |
| Alpha net | **+0,076%**/periode; median **−0,05%**; **9/20** fase positif | idem → `portfolio` |
| Biaya pulang-pergi | **0,5%** di artefak vs **0,40%** di kode | `data/validation.json` vs `research/portfolio.py:48` |
| Universe | **944** kode; field `board,code,name` saja | `data/idx_universe.json`, dibaca dengan Python |

Yang **tidak** saya jalankan di sesi ini: coverage ulang, `research/evaluate.py` penuh, dan
`research/portfolio.py` penuh. Angka IC/turnover di atas adalah isi artefak 2026-11-06, bukan
hasil hitung ulang hari ini.

## 1. Lima celah kemampuan paling penting

### Celah 1 — Harga bisa berubah diam-diam, dan tidak ada yang mencatatnya
`core/data.py:158-159` memakai `yf.download(..., auto_adjust=True)`. Setiap split/dividen baru
mengubah seluruh deret historis, tetapi cache disk (`core/data.py:102-126`) tidak menyimpan
`adjusted`, `provider`, atau `fetched_at`; nama berkasnya tetap `BBCA.csv`. `_validate`
(`core/data.py:132-142`) hanya membuang `high < low`, `close <= 0`, dan `volume < 0` — tidak ada
celah sesi, bar basi, volume nol beruntun, lompatan split, atau cakupan pendek. Karena IC,
backtest, dan `/saran` membaca deret yang sama, satu split yang tidak tercatat bisa mengubah
semua angka tanpa satu pun peringatan.

### Celah 2 — Klaim riset belum tahan uji
Empat masalah menumpuk di satu pipeline:
- `research/evaluate.py:122-131` menghitung `t = mean/std * sqrt(n)` — t-test naif untuk IC
  20-hari yang saling overlap 19 hari.
- `research/evaluate.py:229-238` menguji 12 faktor + `score` sekaligus tanpa koreksi multiple
  testing.
- `research/evaluate.py:262-276` hanya memberi embargo `horizon * 1.6` **hari kalender** di batas
  tahun; label yang overlap di dalam train tidak dipurge dan hanya ada satu lintasan per tahun.
- `research/evaluate.py:432` memakai `saran.LQ45`, daftar komposisi ±2026 (`core/saran.py:23-31`),
  untuk periode 2017–2026 → survivorship + look-ahead sekaligus.

Ditambah `research/evaluate.py:69` menghitung return close-to-close, padahal entry `/saran` di open
berikutnya. IC 0,031 dan t 2,68 belum bisa disebut "layak" sebelum kelima hal ini diukur.

### Celah 3 — Eksekusi dan biaya belum realistis
`research/portfolio.py:87-88` menghitung turnover dari pergantian **nama**
(`1 - irisan/nama_baru`), bukan perubahan bobot, lalu membebankan satu `ROUND_TRIP` 0,40% untuk
semua emiten (`research/portfolio.py:48`). `tick_size()` sudah ada di `core/pricing.py:20` tetapi
tidak dipakai; ARA/ARB tidak dimodelkan; `value_avg20` sudah dihitung (`core/screener.py:209-216`)
tetapi tidak menjadi gate likuiditas. Hasilnya: turnover 73,6%, alpha net +0,076%, median −0,05%,
hanya 9/20 fase positif — edge yang lebih kecil daripada ketidakpastian modelnya.

### Celah 4 — Kegagalan senyap dan event loop yang mudah terkunci
`core/saran.py:146-151` membuang kandidat dengan `except Exception: continue` tanpa log dan tanpa
hitungan. `core/analysis.py:63-74` jatuh ke ATR kasar `(high-low).rolling(14).mean()` saat
`pandas_ta_classic` tidak ada, dan BB/OBV/StochRSI ditelan `except Exception: pass`
(`core/analysis.py:84-100`). Di sisi lain, `store.*` yang sinkron dipanggil langsung dari handler
async (`bot.py:684`, `:2916`, `:2936`) sementara `core/store.py:151-160` membuka koneksi baru
dengan `busy_timeout=15000`; `bot.py:3114` membangun `Application` tanpa `concurrent_updates`.
Satu `/scan` 2–3 menit menahan semua chat, dan laporan bisa tampak lengkap padahal isinya fallback.

### Celah 5 — Operasional tanpa jaring
Tidak ada backup SQLite, `PRAGMA user_version`, atau migrasi bernomor di `core/*.py`, `bot.py`,
`research/*.py`, `tools/*.py` (grep `VACUUM INTO|user_version|\.backup\(` → tidak ada hasil).
`bot.py:65` menyimpan `STATS` di memori saja. `data/validation.json` masih memakai biaya 0,5%
sementara kode sudah 0,40% (`research/portfolio.py:48`), jadi `/validasi` menampilkan angka yang
tidak konsisten dengan model biaya sekarang. Satu volume Docker hilang = watchlist, alert, paper
trading, dan `scan_history` hilang.

## 2. Rencana 4 minggu

Aturan kerja: **satu PR = satu aksi**, jalankan baseline sebelum mengubah apa pun, dan sertakan
perintah + output di PR. Jangan menghapus atau melemahkan tes agar hijau. Kalau target angka tidak
tercapai, laporkan apa adanya — jangan memilih konfigurasi terbaik lalu menyembunyikan yang lain.

### Minggu 1 — Data yang bisa diaudit

**Fokus:** sebelum menambah analisis, pastikan deret harga dan universe tidak berbohong.

| Aksi | Modul yang disentuh | Bukti keberhasilan |
|---|---|---|
| A1. Tambah `df.attrs` (`provider`, `adjusted`, `fetched_at`, `interval`); simpan sidecar JSON per cache; `_load_cache` menolak cache yang `adjusted`-nya tidak cocok | `core/data.py:102-126`, `:145-180` | Tes baru: cache `adjusted=False` tidak dipakai untuk permintaan `adjusted=True`; `pytest -q` 330 → **≥338** |
| A2. Pindahkan `_validate` ke `core/data_quality.py`: flag `session_gap` (pakai `is_trading_day`), `stale_days`, `zero_volume_streak`, `unexplained_jump` >25% tanpa aksi korporasi, `short_history`, OHLC konsisten. Simpan `df.attrs["quality_flags"]` dan log jumlahnya per simbol | `core/data.py:132-142`; `core/marketcal.py:68,92` | Fixture dengan 5 cacat menghasilkan ≥5 flag berbeda; tidak ada bar yang dibuang selain OHLC mustahil; laporan scan menampilkan jumlah flag |
| A3. Tambah `listed_at`/`delisted_at` di payload universe; buat tabel `universe_membership`/`delistings`; set `PRAGMA user_version=1`; isi dari unduhan **manual** halaman IDX Stock List (tombol Download), bukan scraper baru | `core/universe.py:32`; `tools/build_universe.py:93-96`; `core/store.py:124-130` | 944 vs 962 direkonsiliasi dan selisih 18 dijelaskan; `listed_at` terisi untuk sampel; `PRAGMA user_version` = 1 dan `table_info` menunjukkan kolom baru |
| A4. Catat batasan Yahoo/personal use, lisensi dataset NC, dan larangan Stockbit di dokumen; tambah tes offline untuk A1–A3 | `README.md`, `KENDALA.md`, `tests/conftest.py:1-5` | `pytest -q` ≥338; tidak ada panggilan jaringan baru di tes |

**Gerbang minggu 1:** 330 → ≥338 tes. Laporkan IC skor sebelum/sesudah (baseline 0,0115; t 2,68); setiap pergeseran harus bisa dijelaskan bar per bar — kalau tidak, ada bar yang diam-diam dibuang atau ditambah.

### Minggu 2 — Angka riset punya error bar dan universe yang jujur

**Fokus:** mengubah "IC 0,031" dari klaim menjadi hasil yang bisa diaudit.

| Aksi | Modul yang disentuh | Bukti keberhasilan |
|---|---|---|
| B1. Tambah `t_hac` di `ic_summary` (Newey-West, `maxlags=horizon-1=19`); simpan `t` naif dan `t_hac` berdampingan | `research/evaluate.py:122-131` | Sebelum t=2,68; sesudah `t_hac` dilaporkan berdampingan — jangan diasumsikan lebih kecil, laporkan apa adanya. Jika `t_hac` <3,0, faktor dilabeli "kandidat" — bukan bobotnya dinaikkan |
| B2. Hitung p dua sisi lalu koreksi Benjamini–Hochberg untuk 12 `f_*` + `score`; tulis `p_adj` di `evaluation.md`/JSON | `research/evaluate.py:229-238` | Tabel memuat `p` dan `p_adj`; jumlah faktor dengan `p_adj<0,05` dilaporkan; sebelum: 12 uji tanpa koreksi |
| B3. Purge baris train yang label 20 harinya overlap dengan test; ganti embargo `horizon*1.6` hari kalender dengan `trading_days_between` + buffer 5 sesi; laporkan sebaran IC per fold. Sekaligus ukur `fwd` open-to-open vs close-to-close | `research/evaluate.py:69,262-276`; `core/marketcal.py:92` | Tes: `max(train.date)+horizon < test_start` untuk semua tahun; IC 2022–2026 dilaporkan ulang; selisih IC open vs close dicatat |
| B4. Tulis `research/bias.py` (A: universe tetap vs point-in-time; B: hitung yang hilang; C: asumsi delisting −100/−50/0%). Gagal keras jika `universe_membership`/`delistings` kosong; tandai `universe=LQ45_2026` di payload | `research/evaluate.py:432`; `core/saran.py:23-31`; `research/publish.py` | `python research/bias.py` exit ≠0 tanpa data (bukan angka karangan); jika data ada, laporkan delta IC/Sharpe dan asumsi yang dipakai |
| B5. Regenerasi artefak: smoke test `python research/evaluate.py --limit 12`, lalu `python research/evaluate.py` dan `python research/publish.py` | `research/publish.py:18-21`; `data/validation.json` | `cost_per_round_trip_pct` **0,5 → 0,40**; `meta.generated` berubah dari 2026-11-06; alpha net dihitung ulang; `pytest -q` ≥344 |

**Gerbang minggu 2:** t naif dan `t_hac` tampil berdampingan; `p_adj` ada; biaya artefak = 0,40;
tidak ada klaim bias delisting sebelum B4 benar-benar punya data keanggotaan.

### Minggu 3 — Turnover, biaya, dan aturan eksekusi BEI

**Fokus:** alpha net 0,076% masih di bawah noise; biaya dan aturan eksekusi harus realistis.

| Aksi | Modul yang disentuh | Bukti keberhasilan |
|---|---|---|
| C1. Ganti turnover biner dengan berbasis bobot: `sum(max(Δw,0))*BUY_COST + sum(max(-Δw,0))*SELL_COST`; laporkan one-way `0,5*sum(abs(Δw))` | `research/portfolio.py:87-88` | Bobot identik → biaya 0; swap 100% → biaya 0,40%; angka baru dibandingkan dengan 73,6% |
| C2. Hysteresis: nama lama bertahan jika peringkat ≥ persentil 40; nama baru masuk hanya jika ≤20% teratas. Uji band 40/20, 50/30, 60/40 dan laporkan semuanya | `research/portfolio.py:69-98` | Turnover **73,6% → target ≤55%**; alpha net, median, dan 9/20 fase positif dilaporkan untuk tiap band; kalau alpha rusak, band ditolak |
| C3. Pakai `tick_size`/`ceil_tick` di harga eksekusi; gate ARA/ARB per pita harga; tandai rezim 19 Okt–31 Des 2026 | `core/pricing.py:20,40`; `research/portfolio.py` | Tidak ada entri di bar yang high/low-nya mentok ARA/ARB; harga selalu kelipatan tick; % observasi dibuang dilaporkan; periode sebelum/sesudah rezim dipisah |
| C4. Filter ILLIQ sebelum membentuk kuintil: `abs(ret1d)/(close*volume)` rolling 20, buang 20% tertinggi; winsorize 1% ekor; syaratkan minimal 15 hari bervolume | `core/screener.py:209-216`; `research/portfolio.py` | Jumlah nama yang dibuang dilaporkan; alpha net dengan/tanpa filter dibandingkan; hari volume 0 tidak membuat ILLIQ meledak |

**Gerbang minggu 3:** 330 → ≥350 tes; satu tabel sebelum-sesudah berisi turnover 73,6%, biaya
0,40%, alpha net 0,076%, median −0,05%, dan 9/20.

### Minggu 4 — Operasional yang tidak menghilangkan data

**Fokus:** bot boleh salah, tapi tidak boleh kehilangan riwayat atau membeku saat satu perintah berat.

| Aksi | Modul yang disentuh | Bukti keberhasilan |
|---|---|---|
| D1. Migrasi bernomor dari `user_version` (mulai 2); `store.backup_to(dest)` memakai `sqlite3.Connection.backup`; job harian lewat `asyncio.to_thread`; rotasi 7 berkas | `core/store.py:124-130,151-160`; `bot.py:3165-3178` | Backup terbuka, `PRAGMA integrity_check` = `ok`, jumlah baris `chats`/`saran` sama; restore diuji, bukan hanya file dibuat |
| D2. `concurrent_updates(4)` + `AIORateLimiter(max_retries=2)` + `connection_pool_size`; bungkus `store.*` di handler dengan `asyncio.to_thread` (mulai `bot.py:684`, `:2916`, `:2936`) | `bot.py:3114`, `:684`, `:2916`, `:2936` | `/analisis` di satu chat tetap jalan saat `/scan` di chat lain; tidak ada `PoolTimeout` baru; durasi `/analisis` sebelum/sesudah diukur dengan `timed()` (`bot.py:154`) |
| D3. structlog: JSON saat `LOG_JSON=1`, bind `cmd`, `chat_id`, `duration_ms`, `ok`; CI simpan `coverage.xml` + matrix 3.12/3.13 | `bot.py:65,154-165,2488-2504`; `.github/workflows/ci.yml:23-26` | `docker logs` lalu `jq` menghitung error rate per perintah; artefak `coverage.xml` terunggah; gerbang `--cov-fail-under=75` tetap lulus; `pytest -q` ≥354 |

**Gerbang minggu 4:** backup + restore terbukti; dua perintah berat jalan bersamaan; coverage tidak
turun dari gerbang 75%.

## 3. Sumber per domain

Semua tautan di bawah berasal dari empat catatan; tidak ada sumber baru yang saya tambahkan.
Kolom "cara pakai" sengaja menyebut modul, bukan sekadar "baca".

### 3.1 Data pasar, universe, dan kepatuhan

| Sumber | Jenis | Kenapa penting untuk repo ini | Cara pakai |
|---|---|---|---|
| [Dokumentasi `yfinance.download`](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html) | docs | `auto_adjust=True` default di `core/data.py:158-159`; intraday hanya 60 hari terakhir | Tambah provenance + cache berversi; jangan rencanakan backtest intraday panjang |
| [README `ranaroussi/yfinance`](https://github.com/ranaroussi/yfinance) | repo | Menyatakan API Yahoo "intended for personal use only" — batas bot multi-pengguna | Tulis batasan di `README.md`; siapkan env var provider sebelum komersial |
| [Yahoo Terms of Service](https://guce.yahoo.com/terms?locale=en-US) | legal | Melarang pengumpulan data otomatis tanpa izin; alasan tidak menjadikan Yahoo satu-satunya sumber | Batasi retry 429 (dua kali lalu cache); jangan buat endpoint unduh harga mentah |
| [IDX Stock List](https://www.idx.co.id/en/market-data/stocks-data/stock-list) | data resmi | 962 emiten + `Listing Date`; snapshot repo 944 dan tanpa `listed_at` | Rekonsiliasi 944 vs 962; isi `listed_at` dari unduhan manual |
| [IDX Corporate Actions](https://www.idx.co.id/en/listed-companies/corporate-actions) | data resmi | Daftar split/bonus/rights/partial delisting — menjelaskan lompatan harga yang kini lolos `_validate` | Isi tabel `corporate_actions`; pakai untuk memutuskan `unexplained_jump` |
| [IDX Data Services](https://www.idx.co.id/en/products/idx-data-services) | data berbayar | Lisensi EoD resmi; opsi jika bot butuh data yang dapat dipertanggungjawabkan | Jangan berlangganan sekarang; simpan sebagai opsi di `PLAN.md` |
| [GoAPI — Stock Market IDX](https://goapi.io/hub) | API trial/berbayar | Kandidat penyedia cadangan termurah; kuota trial terbatas | Uji 60 bar vs yfinance; bungkus adapter jika median selisih <0,5% |
| [Sectors — Corporate Actions](https://docs.sectors.app/api-references/v2/indonesia/company/corporate-actions.md) | API berbayar | Aksi korporasi terstruktur; endpoint hariannya maks 90 hari | Cross-check split; bukan sumber backtest 10 tahun |
| [EODHD Exchanges API](https://eodhd.com/financial-apis/exchanges-api-list-of-tickers-and-trading-hours) | API berbayar | Satu-satunya sumber terverifikasi dengan `delisted=1`; prasyarat universe point-in-time | Unduh JK aktif + delisted; isi `universe_membership`/`delistings` |
| [`wildangunawan/Dataset-Saham-IDX`](https://github.com/wildangunawan/Dataset-Saham-IDX) | dataset NC | Sudah dipakai `tools/build_universe.py:31-33`; lisensi CC BY-NC | Catat lisensi di komentar + payload; jangan pakai untuk produk komersial |
| [Stockbit Terms of Use](https://stockbit.com/terms) | legal | Melarang robot/spider/extraction tanpa izin tertulis | Jangan tambah scraper Stockbit; catat alasannya di `KENDALA.md` |
| [arXiv 2603.19380 — survivorship bias](https://arxiv.org/abs/2603.19380) | paper | Memberi metode mengukur bias; angka 4,94 pp untuk NIFTY, bukan BEI | Tiru eksperimen A/B/C di `research/bias.py`; jangan kutip sebagai angka BEI |

### 3.2 Riset kuantitatif dan validasi statistik

| Sumber | Jenis | Kenapa penting untuk repo ini | Cara pakai |
|---|---|---|---|
| [statsmodels — `get_robustcov_results` HAC](https://www.statsmodels.org/stable/generated/statsmodels.regression.linear_model.RegressionResults.get_robustcov_results.html) | docs | `t = mean/std*sqrt(n)` di `research/evaluate.py:131` mengabaikan overlap 20 hari | Tambah `t_hac` (`maxlags=19`); simpan `t` dan `t_hac` di JSON |
| [Harvey, Liu & Zhu — …and the Cross-Section](https://people.duke.edu/~charvey/Research/Published_Papers/P118_and_the_cross.PDF) | paper | Ambang t>3,0 untuk faktor baru; `verdict()` di `bot.py:1763` hanya memakai abs(IC) | Faktor 2,0–3,0 dilabeli "kandidat"; jangan naikkan bobot `core/relatif.py:26` |
| [Bailey & López de Prado — Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) | paper | `research/portfolio.py:101` menjalankan 20 fase = 20 percobaan; SR terbaik bias | Hitung DSR dengan `n_trials`; tampilkan SR terdeflasi, bukan SR terbaik |
| [SciPy — `false_discovery_control`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.false_discovery_control.html) | docs | `signal_table` menguji 12 faktor + `score` tanpa koreksi | Hitung `p_adj` BH; tulis di `evaluation.md`; sebut metode di laporan |
| [skfolio — model selection / CPCV](https://skfolio.org/user_guide/model_selection.html) | docs | `walk_forward` satu lintasan per tahun; purge hanya di batas tahun | CPCV atau purge manual; laporkan sebaran IC + jumlah observasi per fold |
| [Novy-Marx & Velikov — buy/hold spread](https://www.nber.org/system/files/working_papers/w20721/w20721.pdf) | paper | Buy/hold spread adalah mitigasi biaya paling efektif; turnover 73,6% | Hysteresis 40/20; uji beberapa band dan laporkan semuanya |
| [Amihud — ILLIQ](https://www.cis.upenn.edu/~mkearns/finread/amihud.pdf) | paper | Tidak ada filter likuiditas sebelum kuintil; `value_avg20` sudah tersedia | Buang 20% ILLIQ tertinggi; laporkan nama yang hilang |
| [exchange_calendars](https://github.com/gerrymanoim/exchange_calendars) | repo | XIDX sudah dipakai `core/marketcal.py:61`; embargo masih hari kalender | Ganti embargo ke sesi bursa + buffer 5 sesi |
| [IDN Financials — batas ARA/ARB](https://www.idnfinancials.com/news/70466/idx-officially-adjusts-symmetric-arb-limit-to-7-effective-today) | artikel | ARA/ARB per pita harga; rezim 19 Okt–31 Des 2026 | Gate eksekusi; simpan tabel `(start,end,band,ara,arb)`, jangan hard-code |
| [alphalens-reloaded](https://github.com/stefan-jansen/alphalens-reloaded) | repo | Pembanding independen IC/kuantil/turnover untuk `research/evaluate.py:107` | Venv terpisah (pandas<3), tukar CSV; selisih >0,005 cari bug dulu |
| [arch — uji SPA](https://arch.readthedocs.io/en/latest/multiple-comparison/generated/arch.bootstrap.SPA.html) | docs | Uji multiple testing antar-model; belum dijadwalkan | Setelah HAC/FDR; `block_size=horizon`, `reps=1000` |
| [vectorbt — Portfolio](https://vectorbt.dev/api/portfolio/base/) | docs | Cross-check biaya/DSR; lisensi Apache-2.0 + Commons Clause | Riset internal saja; jangan jadikan dependensi produk |

### 3.3 Rekayasa bot dan pengujian

| Sumber | Jenis | Kenapa penting untuk repo ini | Cara pakai |
|---|---|---|---|
| [PTB `ApplicationBuilder`](https://docs.python-telegram-bot.org/en/v21.11.1/telegram.ext.applicationbuilder.html) | docs | `bot.py:3114` tanpa `concurrent_updates`; `/scan` 2–3 menit memblokir semua chat | `concurrent_updates(4)` + `connection_pool_size`; jangan `True` (256) |
| [`AIORateLimiter`](https://docs.python-telegram-bot.org/en/v21.11.1/telegram.ext.aioratelimiter.html) | docs | `cooldown_ok()` (`bot.py:179`) membatasi per user, bukan laju ke Bot API | `.rate_limiter(AIORateLimiter(max_retries=2))`; jangan hapus cooldown |
| [`asyncio.to_thread`](https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread) | docs | `store.*` sinkron dipanggil di `async def` (`bot.py:684`) | Bungkus akses store; tulis aturan "tidak ada I/O >50 ms di `async def`" |
| [tenacity](https://tenacity.readthedocs.io/en/latest/) | docs | Retry `core/data.py:156-166` tanpa jitter; tidak ada retry `NetworkError` Telegram | `wait_random_exponential`; `AsyncRetrying` untuk kirim pesan |
| [pytest-asyncio](https://pytest-asyncio.readthedocs.io/en/latest/) | docs | 55 pemakaian `asyncio.run` di `tests/test_bot_handlers.py` | Migrasi bertahap ke `async def test_`; satu tes lewat `Application.process_update` |
| [pytest-recording](https://github.com/kiwicom/pytest-recording) | repo | Tidak ada `tests/fixtures/`; tes sintetis tidak menangkap perubahan skema yfinance | Rekam `BBCA.JK` + satu respons IDX; jalankan `--block-network` di CI |
| [Pandera — DataFrameSchema](https://pandera.readthedocs.io/en/stable/dataframe_schemas.html) | docs | `_validate` hanya 3 aturan, bukan skema kolom/dtype | Definisikan `OHLCVSchema` dan panggil sebelum `_validate` |
| [aiosqlite](https://aiosqlite.omnilib.dev/) | docs | Alternatif async untuk `store` tanpa mengubah SQL | Hanya jika `to_thread` tidak cukup; jangan dua-duanya sekaligus |

### 3.4 Operasional, penyimpanan, dan pelaporan

| Sumber | Jenis | Kenapa penting untuk repo ini | Cara pakai |
|---|---|---|---|
| [SQLite WAL](https://sqlite.org/wal.html) | docs | WAL di `core/store.py:159`; `synchronous` belum di-set; WAL tidak untuk filesystem jaringan | `PRAGMA synchronous=NORMAL`; pastikan `STORE_DB` di volume lokal |
| [SQLite Online Backup API](https://sqlite.org/backup.html) | docs | Tidak ada backup; `cp store.db` bukan backup karena `-wal`/`-shm` bagian dari state | `store.backup_to()` + rotasi 7 + uji restore |
| [structlog](https://www.structlog.org/en/stable/) | docs | `bot.py:65` menyimpan `STATS` di memori; log teks bebas | JSON saat `LOG_JSON=1`; bind `cmd`, `chat_id`, `duration_ms` |
| [Docker Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/) | docs | `BOT_TOKEN` lewat environment (`config.py:6-29`); env bisa tercetak saat crash | Pindahkan token ke `secrets/bot_token.txt`; fallback env untuk Windows karena secrets Compose hanya Linux |
| [prometheus client](https://prometheus.github.io/client_python/) | docs | Tidak ada metrik yang bertahan setelah restart | Counter/Histogram; expose `127.0.0.1` saja |
| [GitHub Actions — build & test Python](https://docs.github.com/en/actions/tutorials/build-and-test-code/python) | docs | CI hanya `--cov-report=term-missing`, tidak menyimpan laporan | `--cov-report=xml` + `upload-artifact`; matrix 3.12/3.13 |
| [mypy](https://mypy.readthedocs.io/en/stable/) | docs | Tidak ada cek tipe; `core/store.py` mengembalikan `Row`/`dict`/`None` campur | Mulai `core/store.py` + `core/data.py`; `continue-on-error` dulu |
| [`uv pip compile`](https://docs.astral.sh/uv/pip/compile/) | docs | Pin `==` hanya untuk dependensi langsung; transitif tidak terkunci | `requirements.in` + `--generate-hashes`; CI cek drift |
| [QuantStats](https://github.com/ranaroussi/quantstats) | repo | Portofolio hanya alpha/turnover; belum ada Sharpe/CVaR/tearsheet | Notebook riset saja; jangan dipanggil dari handler bot |

## 4. Skill lokal yang bisa dipakai, dan kapan

| Skill | Kapan dipakai | Untuk apa di repo ini |
|---|---|---|
| `senior-data-scientist` | Minggu 2, sebelum mengubah `walk_forward` | Checklist leakage/CV: scaler dan ridge hanya di-fit di train; tanggal test tidak muncul di train; laporkan n per fold |
| `database-designer` | Minggu 1 dan 4, saat menambah `universe_membership`/`delistings` dan migrasi | Analisis skema, index, dan migrasi bernomor; `core/store.py` sekarang memakai `ALTER TABLE` ad hoc tanpa `user_version` |
| `observability-designer` | Minggu 4, setelah structlog | Kerangka golden signals untuk SLI per command (p95, error rate, stale rate); skrip Grafana berlebihan untuk bot satu proses — pakai checklist-nya |
| `tdd-guide` | Minggu 1 dan 3, sebelum menulis tes baru | Red-green-refactor: tes `quality_flags` dan tes turnover ditulis lebih dulu, bukan setelah implementasi |
| `code-reviewer` | Akhir setiap minggu, sebelum merge PR | Review diff satu PR terhadap risiko; pastikan 330+ tes tetap lulus dan tidak ada klaim tanpa output perintah |

## 5. Yang sebaiknya TIDAK dilakukan

1. **Jangan menambah faktor baru sebelum validasi diperbaiki.** `core/relatif.py:15` menyatakan bobot
   dibekukan dari walk-forward. Faktor dengan `t_hac` <3,0 atau `p_adj` tidak lolos adalah kandidat,
   bukan alasan menaikkan bobot.
2. **Jangan mengklaim angka survivorship bias sebelum `universe_membership` + `delistings` terisi.**
   Tanpa daftar delisting, yang diukur hanya bias cuplikan LQ45. Jangan kutip 4,94 pp dari arXiv
   sebagai angka BEI — itu angka NIFTY Smallcap 250.
3. **Jangan menambah scraper IDX atau Stockbit.** `tools/build_universe.py:93-96` sudah memakai
   `cloudscraper` ke endpoint internal; ToS IDX dan Stockbit melarang ekstraksi otomatis. Perbarui
   universe lewat unduhan manual halaman Stock List.
4. **Jangan menaruh alphalens/QuantStats di `requirements.txt` produksi.** Alphalens meminta
   `pandas<3.0`, repo memakai pandas 3.1.1; jalankan di venv riset terpisah dan tukar CSV.
5. **Jangan memakai Sectors daily untuk backtest 10 tahun.** Endpoint hariannya dibatasi 90 hari;
   ia alat cross-check, bukan sumber sejarah.
6. **Jangan menyalin `PLAN.md` apa adanya.** Biaya transaksi, `auto_adjust`, CI, dan `scan_history`
   sudah selesai; menyalinnya menghabiskan satu minggu untuk pekerjaan yang tidak ada.
7. **Jangan menaikkan `concurrent_updates` ke `True`** (artinya 256). Mulai dari 4; mesin ini juga
   menjalankan yfinance dan SQLite.
8. **Jangan menghapus `cooldown_ok()` saat menambah `AIORateLimiter`.** Keduanya mengatur hal
   berbeda: biaya CPU/jaringan vs laju ke Bot API.
9. **Jangan memakai `except Exception: pass/continue` sebagai "fallback".** Ganti dengan flag +
   hitungan; `core/saran.py:146-151` dan `core/analysis.py:84-100` adalah contoh yang harus diperbaiki.
10. **Jangan big-bang refactor `bot.py`.** Pecah per domain dan pertahankan `bot.py` sebagai
    entrypoint agar Docker dan tes tetap jalan.
11. **Jangan menganggap `/validasi` selalu segar.** Sampai B5 dijalankan, angkanya snapshot
    2026-11-06 dengan biaya 0,5% — sudah tidak cocok dengan kode 0,40%.
12. **Jangan menaikkan `synchronous`/tuning WAL sebelum backup ada.** Backup dulu, tuning kemudian;
    tuning tanpa backup adalah taruhan.
13. **Jangan memakai skill `senior-qa` untuk repo ini.** Isinya React/Next.js/Jest/Playwright;
    untuk Python pakai pytest + pytest-recording.
14. **Jangan menyentuh jaringan saat tes.** 330 tes sekarang offline (`tests/conftest.py:1-5`);
    cassette yang direkam harus disanitasi sebelum di-commit.

## 6. Definisi "selesai" yang saya pakai

Satu minggu selesai hanya jika: (a) `pytest` lulus dengan jumlah tes yang naik, (b) coverage tidak
turun dari gerbang CI 75%, (c) ada satu tabel sebelum-sesudah berisi angka nyata dari perintah yang
dijalankan, dan (d) tidak ada klaim yang tidak berasal dari output perintah. Kalau salah satu tidak
terpenuhi, minggu itu dilaporkan **belum selesai** — bukan diperpanjang diam-diam.
