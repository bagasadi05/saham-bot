# AGENTS.md — panduan untuk agent AI berikutnya

Berkas ini untuk **kamu yang membuka repo ini dengan agent AI** (Claude Code,
Cursor, Copilot, AutoClaw, dll). Isinya: cara kerja repo, aturan yang tidak
boleh dilanggar, peta kode, dan status terakhir. Riwayat perubahan ada di
[`CHANGELOG.md`](CHANGELOG.md); rencana & status per fase ada di
[`PLAN.md`](PLAN.md).

Terakhir diperbarui: **2026-11-14** (P23/P24 risiko portofolio + P28 jurnal
eksekusi manual; lihat `git log`).

---

## 1. Mulai dari sini (5 menit)

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows; Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q                         # harus 586 passed, ~72 detik, offline
ruff check .                      # harus bersih
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
bot.py                  entrypoint + menu/aksi + markup + helper aplikasi
core/                   logika domain (tanpa Telegram), bisa diuji offline
  analysis.py           indikator, skor teknikal, snapshot
  data.py               fetch Yahoo + cache + provenance
  screener.py           scan seluruh pasar → snapshot SQLite
  relatif.py            skor relatif 12 faktor (bobot beku)
  saran.py              kandidat BUY, sizing, paper trading
  rencana.py            level harga, paket & rencana anggaran
  cek.py                aturan ceklist + verdict
  kualitas.py           kualitas fundamental (PER/PBV/ROE) + cache harian
  portofolio.py         ringkasan PnL/risiko portofolio asli
  jurnal.py             jurnal eksekusi manual Stockbit, slippage & realisasi
  risk.py               vol/drawdown/VaR/beta/korelasi/inv-vol + batas portofolio
  sektor.py             parser indeks sektor BEI (defensif)
  store.py              SQLite: watchlist, alert, paper, scan_history,
                        real_positions/real_trades, zone_state,
                        signal_context, real_executions, chat_settings (v6)
  guards.py             cooldown + PENDING
  reports.py            teks laporan (/analisis dll.)
  logsetup.py           logging teks/JSON (LOG_JSON=1)
handlers/               handler Telegram, pola `deps`
  analisis, cek, info, jobs, jurnal, market, menu, portfolio, sektor, sinyal,
  trading, watchlist
research/               harness riset (evaluate, portfolio, publish, horizons)
tests/                  586 tes offline + fixtures/
tools/                  skrip manual (build_universe, record_fixtures)
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

## 5. Status saat ini (2026-11-14)

| Area | Status |
|---|---|
| Fase 1 (bug P0) | selesai |
| Fase 2.1–2.2 (scan_history, turnover, CI) | selesai |
| Fase 2.3 (survivorship, faktor non-teknikal, end-to-end) | ⛔ di luar cakupan (butuh data historis) |
| Fase 3 (P12–P18) | selesai, termasuk `/sektor` |
| Fase 4 (modularisasi, CI, backup, log, LICENSE) | selesai |
| Uji chat di host | ⛔ diblokir akses token (lihat `deploy.md`) |
| P23/P24 (risiko portofolio) | selesai; batas kustom (`/portofolio batas`) + bobot *inv-vol* |
| P28 (jurnal eksekusi Stockbit & slippage) | selesai (tanpa auto-trading / kredensial) |
| Audit forensik kas + Pro/Risiko (`/cek`, `/fundamental`) | selesai (`.agents/skills/idx-equity-research/`) |

Angka riset terbaru (`data/validation.json`): turnover 50,6%; alpha neto
+0,264% [CI +0,148; +0,390]; model 0,0304 vs bobot-sama −0,0061; DSR 0,881;
IC skor `t_hac` 0,85 (tidak signifikan).

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
2. Rekam fixture asli sekali: `python tools/record_fixtures.py --ticker BBCA`.
3. Bila ingin mengerjakan Fase 2.3, pilih sumber data dulu (IDX Stock List
   manual / EODHD / Sectors) lalu buat `research/bias.py` yang gagal keras
   tanpa tabel keanggotaan.
