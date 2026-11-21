# AGENTS.md — panduan untuk agent AI berikutnya

Berkas ini untuk **kamu yang membuka repo ini dengan agent AI** (Claude Code,
Cursor, Copilot, AutoClaw, dll). Isinya: cara kerja repo, aturan yang tidak
boleh dilanggar, peta kode, dan status terakhir. Riwayat perubahan ada di
[`CHANGELOG.md`](CHANGELOG.md); rencana & status per fase ada di
[`PLAN.md`](PLAN.md).

Terakhir diperbarui: **2026-11-21** (gelombang W0–W4: housekeeping skill
`.agents/skills/alphaear-*`, audit & perbaikan sentimen berita `CAMPURAN`/negasi/frasa +
dataset emas `news_sentiment_gold.json` & `research/evaluate_sentiment.py`, laporan
kondisi pasar `/pasar` (`/market`) + `fetch_market_news` + job penutupan 16:15 WIB &
ringkasan di `morning_scan`, pelacak tesis posisi `core/tesis.py`
(`GUGUR`/`MELEMAH`/`MENGUAT`/`TETAP`) di `/portofolio` & alert exit hanya saat status
berubah, plus VaR historis/ES, batas sektor, alpha jurnal vs IHSG, `job_guard`, dan
tes wiring `main()`; lihat `git log`).

---

## 1. Mulai dari sini (5 menit)

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows; Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q                         # harus 838 passed (0 skipped, coverage 87% / 87.37%, gerbang CI 86%), offline
ruff check .                      # harus bersih
python research/evaluate_sentiment.py  # audit offline akurasi & macro-F1 sentimen berita
python tools/check_sync.py        # audit sinkronisasi riset, LQ45, umur data & backup DB
python tools/record_fixtures.py --verify  # audit struktur fixture offline
python bot.py                     # butuh BOT_TOKEN di .env
```

Tes **wajib offline**: tidak boleh memanggil Yahoo/Google News/BEI/Telegram.
Kalau butuh data, pakai fixture di `tests/fixtures/` atau data sintetis dari
`tests/conftest.py`.

## 2. Prinsip produk (jangan dilanggar)

1. **Jujur terhadap data.** Bagian yang datanya tidak tersedia ditulis
   "tidak tersedia" — tidak diisi nol, tidak dikarang. Contoh: `core/cek.py`
   memisahkan status `na`, dan `/sektor` menolak menampilkan indeks non-sektor.
2. **Tidak ada janji untung.** Label sinyal = peringkat relatif (IC OOS
   ±0,03; `t_hac` skor 0,85), bukan probabilitas. JUAL bukan instruksi short.
3. **Tanpa auto-trading.** Bot tidak pernah login ke Stockbit, tidak menyimpan
   kredensial, tidak menaruh order.
4. **Tes tidak boleh melemah** hanya agar hijau. Kalau perilaku berubah,
   ubah tes + jelaskan di commit message.
5. **Angka riset hanya dari perintah yang dijalankan.** Kalau mengubah
   pipeline riset, regenerasi `data/validation.json` dan sebutkan di changelog.

## 3. Peta arsitektur

```
bot.py                  entrypoint + menu/aksi + markup + helper aplikasi (1.865 baris)
core/                   logika domain (tanpa Telegram), 35 modul, bisa diuji offline
  alerts.py             deteksi kondisi alert harga/indikator & zona beli watchlist
  analysis.py           indikator teknikal, skor momentum, trend_label & snapshot
  bandar.py             ringkasan saham/indeks/asing BEI (idx.co.id via cloudscraper)
  banding.py            perbandingan emiten se-sektor & benchmark IHSG (20h/1th)
  btcache.py            cache hasil simulasi backtest 5 tahun di disk
  cek.py                aturan ceklist 6 bagian + verdict LAYAK/TUNGGU/HINDARI + Pro/Risiko
  chart.py              render chart 4 panel matplotlib
  data.py               fetch Yahoo (auto_adjust+repair) + fallback opt-in + cache per-period
  data_quality.py       audit kualitas bar OHLCV & format provenance WIB
  fundamental.py        valuasi, tren 4Q, normalize_roe (roe_unit="pct"), forensik arus kas
  guards.py             cooldown per user + state input PENDING
  jurnal.py             jurnal eksekusi manual Stockbit, slippage, MAE/MFE & R-multiple
  kualitas.py           saringan kualitas fundamental (/sinyal beli bagus) + cache harian v2
  learn.py              kamus edukasi indikator & rasio (/belajar)
  logsetup.py           logging teks/JSON terstruktur (LOG_JSON=1)
  marketcal.py          kalender bursa XIDX (exchange_calendars) & zona waktu WIB
  news.py               berita emiten & pasar (Google News RSS) + sentimen lexicon/CAMPURAN/cakupan mesin
  news_ml.py            inferensi sentimen ML lokal indo-roBERTa (fallback lexicon)
  pasar.py              laporan kondisi pasar BEI (IHSG/rezim, breadth, sektor, asing, berita)
  portofolio.py         ringkasan PnL/risiko portofolio asli (SL 1×ATR / eksekusi + exit)
  powerscore.py         kombinasi 3 pin (teknikal, fundamental + penalti forensik, berita)
  pricing.py            fraksi tick BEI, batas ARA/ARB 2026 (Kep-00136 Rp1 & Kep-00158 7%)
  regime.py             deteksi rezim kausal IHSG (^JKSE SMA50 vs SMA200)
  relatif.py            skor relatif 12 faktor (bobot & kuintil OOS sinkron validation.json)
  rencana.py            level harga, paket & rencana anggaran (/rencana, /paket)
  reports.py            formatter mata uang (IDR vs non-IDR) & teks laporan (/analisis dll.)
  risk.py               vol/drawdown/VaR/beta/korelasi/inv-vol + batas risiko portofolio
  saran.py              kandidat BUY, sizing, paper trading & panduan exit (Chandelier/TP1/time-stop)
  scorecard.py          evaluasi sinyal live (/jejak, BELI vs JUAL terpisah, live_ic t_hac)
  screener.py           scan seluruh pasar BEI → filter kualitas/staleness → snapshot SQLite
  sektor.py             parser indeks sektor BEI (defensif)
  store.py              SQLite (SCHEMA_VERSION = 6): watchlist, alert, paper, scan_history,
                        real_positions/real_trades, zone_state, signal_context (intent),
                        real_executions, chat_settings (batas risiko, user_mode, onboard_state,
                        thesis_state:<TICKER>)
  tesis.py              pelacak tesis posisi (GUGUR/MELEMAH/MENGUAT/TETAP) & aturan transisi alert
  textutil.py           formatter rupiah & parser nominal (parse_rupiah)
  universe.py           pemuat daftar 944 emiten BEI (data/idx_universe.json) & papan pencatatan
handlers/               13 modul handler Telegram, pola `deps`
  analisis, cek, info, jobs, jurnal, market, menu, pasar, portfolio, sektor,
  sinyal, trading, watchlist
research/               harness riset (evaluate, evaluate_sentiment, experiments, portfolio,
                        publish, horizons, evaluate_kronos, bias)
tests/                  838 tes offline (0 skipped, coverage 87% / 87.37%, gerbang CI 86%) + fixtures/
tools/                  skrip utilitas (backup_db, build_universe, check_sync, record_fixtures)
```

### Pola `deps` (penting)

Handler **tidak mengimpor `bot`** (menghindari impor melingkar). Setiap
handler menerima `deps` = modul `bot`:

```python
async def do_sesuatu(update, deps, ...):
    await deps.reply_long(update.effective_message, "...")
    await deps.cooldown_ok(update, "nama")
```

Di `bot.py` ada wrapper tipis dengan nama lama (`cmd_*`, `_do_*`) yang
meneruskan `sys.modules[__name__]`. Ini menjaga
`monkeypatch.setattr(bot, "...")` di tes tetap bekerja. **Kalau memindahkan
handler baru, pertahankan pola ini** dan sisakan wrapper + alias nama lama.

### Akses SQLite

Semua `store.*` di jalur async harus lewat `asyncio.to_thread`:

```python
rows = await asyncio.to_thread(store.load_market_scan)
```

Satu pengecualian: `handlers/market._snapshot_row` sinkron dan **selalu**
dipanggil via `to_thread`.

## 4. Konvensi commit & PR

- Satu commit = satu domain/perubahan logis. Jangan big-bang refactor.
- Pesan commit bahasa Indonesia, format `tipe(scope): ringkasan`
  (`feat`, `fix`, `refactor`, `perf`, `docs`, `test`, `chore`).
- Sebelum commit: `pytest -q` **dan** `ruff check .`.
- Jangan commit `.env`, `store.db`, `temp/`, log, atau token.
- Setelah mengubah perilaku pengguna, perbarui `README.md`, `PLAN.md`, dan
  `CHANGELOG.md`.

## 5. Status saat ini (2026-11-21)

| Area | Status |
|---|---|
| Fase 1 (bug P0) | selesai |
| Fase 2.1–2.2 (scan_history, turnover, CI) | selesai |
| Fase 2.3 (survivorship, faktor non-teknikal, end-to-end) | ⛔ di luar cakupan (butuh data historis) |
| Fase 3 (P12–P18) | selesai, termasuk `/sektor` |
| Fase 4 (modularisasi, CI, backup, log, LICENSE) | selesai |
| Uji chat di host | ⛔ diblokir akses token (lihat `deploy.md`) |
| P23/P24 (risiko portofolio) | selesai; batas kustom (`/portofolio batas`), bobot *inv-vol* dua lapis (posisi+sektor), VaR historis + ES |
| P28 (jurnal eksekusi Stockbit & slippage) | selesai (tanpa auto-trading / kredensial; + MAE/MFE, R-multiple & alpha vs IHSG) |
| Audit forensik kas + Pro/Risiko (`/cek`, `/fundamental`) | selesai (`.agents/skills/idx-equity-research/`, penalti skor & mata uang non-IDR) |
| Mode Pemula vs Expert, panduan linear 5-tahap & tombol tindak lanjut lintas handler | selesai (`user_mode` & `onboard_state` di `chat_settings`, `/mode`, `onboard:s1..s5`, tindak lanjut semua handler) |
| Gelombang W0–W4 (skill housekeeping, sentimen berita gold/`CAMPURAN`, `/pasar` + job 16:15 WIB & brief pagi, pelacak tesis posisi `core/tesis.py`) | selesai (**838 passed** offline, `0 skipped`, `0 failed`, coverage CI **87.37%** ≥ 86%, `ruff check .` bersih, `tools/check_sync.py` PASS) |

Angka riset terbaru (`data/validation.json`, konfigurasi `--hold 0.6 --min-hold 2 --regime sma50_200_half --offline`, biaya BEI `0,40%` pulang-pergi):
turnover satu arah **21,7%** (dua arah **43,4%**); eksposur **79,8%**; MDD **−20,0%**;
alpha bruto aktif **+0,534%** (total **+0,416%**); alpha neto **+0,33%** (+0,330%,
median +0,385%, **16/20** fase positif, CI 95% **[+0,243%; +0,522%]**);
walk-forward model **0,0304** vs bobot-sama **−0,0061** (menang 4/5 tahun, kuintil OOS
Q5 **+0,400%** vs Q1 **−0,204%**); **DSR 0,86** (`best_sr=0,279`, `sr0=0,142`,
`n_trials=20` fase; `320` trial pada grid ablasi `research/experiments.py`);
IC skor `t_hac` **0,85** (tidak signifikan).

## 6. Jangan dilakukan

1. Menambah bobot faktor karena `t` naif 2,68 — yang bertahan hanya `t_hac`
   0,85.
2. Mengklaim angka survivorship tanpa `universe_membership`/`delistings`.
3. Menambah scraper IDX/Stockbit (ToS melarang; lihat `KENDALA.md`).
4. Mengganti label sinyal menjadi janji probabilitas.
5. Menghapus/melemahkan tes atau `cooldown_ok` saat menambah rate limiter.
6. Refactor `bot.py` sekaligus — satu domain per commit, wrapper dipertahankan.
7. Menjalankan `python bot.py` di mesin yang sama dengan host produksi
   (bentrok polling Telegram).
8. Menambahkan dependensi skill pihak ketiga (`.agents/skills/alphaear-*`) ke
   `requirements*.txt` atau memasukkannya ke commit; skill resmi repo hanya
   `.agents/skills/idx-equity-research/` & `.agents/skills/idx-quant-research/`
   (sementara `alphaear-*` diabaikan oleh `.gitignore`, `ruff`, dan `pytest`).

## 7. Kalau ada yang aneh

| Gejala | Cek |
|---|---|
| `/sektor` "tidak bisa dibaca" / "diblokir Cloudflare" | idx.co.id memblokir IP; bukan bug bot |
| Yahoo `401 Invalid Crumb` / data kosong | rate-limit/jaringan; cache `temp/cache` dipakai |
| Token invalid | `.env` di mesin dev sering kosong; host punya token sendiri |
| `/scan` lambat | 944 emiten, 3–6 menit; cek `_screener_running` |
| Error handler | `docker compose logs -f`; `LOG_JSON=1` untuk `jq` |

## 8. Langkah berikutnya yang disarankan

1. Jalankan checklist chat di host (`/status`, `/sektor`, `/cek BBCA`,
   `/sinyal beli bagus`) dan tempel hasilnya ke `deploy.md`.
2. Rekam fixture asli sekali di mesin berjaringan:
   `python tools/record_fixtures.py --ticker BBCA --ohlcv-rows 120 --rows 5`
   lalu verifikasi dengan `python tools/record_fixtures.py --verify` dan
   `python tools/check_sync.py`.
3. Bila ingin mengerjakan Fase 2.3, pilih sumber data dulu (IDX Stock List
   manual / EODHD / Sectors) lalu buat `research/bias.py` yang gagal keras
   tanpa tabel keanggotaan.
