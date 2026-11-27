# REVIEW-IMPROVE.md — Ringkasan Review & Rencana Perbaikan

**Tanggal:** Senin, 16 November 2026 · **Repo:** SahamBot · **Disusun oleh:** agen penyusun ringkasan (workflow)

**Sumber data:** berkas tugas `.zwork/runs/6d493c62/task-flow-penyusun-review-7361e831b4.md` — berisi hasil pull 11 commit, hasil tes pipeline, dan 8 temuan review lengkap dengan catatan konfirmasi (status verifikasi per temuan) dari pipeline review.
Selain itu, penyusun laporan ini **membaca ulang langsung** baris-baris kunci yang dikutip (bot.py, download_model.py, core/store.py, core/reports.py, requirements.txt, CHANGELOG.md, Dockerfile) dan **menjalankan ulang baseline tes** pada sesi ini (16 Nov 2026) — semuanya cocok; rincian perintah ada di bagian 1. Tidak ada file lain yang diedit selama penyusunan.

---

## 1. Ringkasan pull + hasil tes

### Pull: 11 commit baru

| # | Commit | Pesan |
|---|--------|-------|
| 1 | `2e4afe5` | docs: sinkronkan AGENTS, CHANGELOG, PLAN, dan README ke baseline 764 passed (86.05% cov) |
| 2 | `5d283fa` | fix(handlers): pulihkan tombol analisis rencana, CTA modal kecil, dan guard skor None |
| 3 | `f35ff0f` | feat(menu): mode pemula/expert, panduan bertahap 1-5 & UX tindak lanjut |
| 4 | `760be0f` | feat(handlers): sambungkan alur rekomendasi, cek, dan modal kecil |
| 5 | `513f1d5` | feat(handlers): lengkapi tombol tindak lanjut & guard portofolio, jurnal, analisis |
| 6 | `9377856` | feat(sinyal): tambah info modal, biaya 1 lot, tombol kandidat, dan menu utama di nav |
| 7 | `96bec44` | feat(store): tambah persistensi user_mode & onboard_state di chat_settings |
| 8 | `7286619` | feat(menu): sederhanakan menu utama & tambah panduan interaktif pemula |
| 9 | `9459f8a` | fix(menu): perbaiki sg:scan, edit pesan foto/media, dan audit seluruh tombol menu |
| 10 | `2d00917` | fix(deploy): perbaiki batas chunk loop download_model untuk model >240MB |
| 11 | `099cf01` | fix(data): cooldown & logging respons premium/limit Alpha Vantage |

Catatan: commit sinkronisasi dokumen `2e4afe5` hanya menyentuh AGENTS.md, CHANGELOG.md, PLAN.md, README.md (per `git show --stat 2e4afe5` pada catatan konfirmasi temuan T7) — requirements.txt terlewat, lihat T7.

### Hasil tes

| Cek | Sumber | Hasil |
|---|---|---|
| `pytest` (suite penuh, offline) | data pull | exit 0; ekor output `… [100%]` |
| `ruff` | data pull | exit 0 |
| `pytest -q` (suite penuh, offline) | **dijalankan ulang penyusun** — `rtk .venv\Scripts\python.exe -m pytest -q` | **exit 0**, progres penuh sampai `[100%]`, tanpa kegagalan (baris ringkasan jumlah tidak tampil di output yang ditangkap, sehingga jumlah dikonfirmasi terpisah via collect-only di bawah) |
| jumlah tes | **dijalankan ulang penyusun** — `rtk .venv\Scripts\python.exe -m pytest --collect-only -q` (dijumlahkan per berkas) | **SUM=764** — konsisten baseline AGENTS.md (764 passed, 0 skipped, coverage target CI 86,05%) |
| `ruff check .` | **dijalankan ulang penyusun** — `rtk .venv\Scripts\python.exe -m ruff check .` | **exit 0** — `All checks passed!` |

Kesimpulan: pull sehat — baseline tes dan lint hijau, baik menurut data pull maupun eksekusi ulang di sesi penyusunan.

### Ikhtisar temuan: 8 item, semuanya berstatus **terverifikasi**

- 2 temuan prioritas **sedang**: reliabilitas notifikasi dividen (T1) dan integritas unduhan model (T2).
- 6 temuan prioritas **rendah**: 3 tentang perilaku pengguna (T3–T5), 3 kebersihan kode & dokumen (T6–T8).
- Tidak ada temuan tanpa verifikasi (lihat bagian 3). Estimasi total usaha perbaikan seluruh temuan ≈ **4,5–5 jam**.

---

## 2. Temuan terverifikasi (urut prioritas)

Penomoran T1–T8. Nomor baris sudah dikoreksi mengikuti catatan konfirmasi pipeline; kecuali diberi label "per catatan konfirmasi", baris-baris kunci juga dibaca langsung penyusun pada sesi ini (16 Nov 2026) dan cocok dengan temuan.

### 🔴 Prioritas sedang

#### T1 — Pengingat dividen bisa hilang permanen bila pengiriman gagal

- **Berkas:** bot.py — fungsi `check_dividend_reminders` (domain: bot-dan-ci)
- **Masalah:** Tanda dedup ditulis **sebelum** kirim: `ok_rem = await asyncio.to_thread(store.mark_div_reminder, cid, sym, ex_iso, days_left)` (bot.py:1233-1234) dengan guard `if not ok_rem: continue` (bot.py:1235-1236). Kiriman memakai `app.bot.send_message` **polos tanpa send_with_retry** (bot.py:1245); bila kirim gagal karena NetworkError/TimedOut — persis kelas gangguan yang P12/`send_with_retry` cegah — exception ditelan `except Exception:` (bot.py:1248-1249) sehingga hanya `log.exception`, tetapi tanda dedup sudah tersimpan. Karena `mark_div_reminder` = INSERT OR IGNORE yang mengembalikan True hanya pertama kali (core/store.py:555-565; PK menyertakan `days`), pengingat **H-3 (days=3) hilang permanen** untuk event itu, dan H-1 hanya terkirim bila kirimnya sendiri sukses.
- **Bukti:** bot.py:1233-1236, 1245, 1248-1249 dan core/store.py:555-565 (dibaca langsung); pembanding `notify_admin` memakai `send_with_retry(...)` (bot.py:1592-1595, dibaca langsung); `send_with_retry` menangani `(NetworkError, TimedOut)` (bot.py:163, 166-190 — per catatan konfirmasi); job harian memanggil fungsi ini (handlers/jobs.py:184 — per catatan konfirmasi); tes yang ada hanya jalur sukses+dedup: `test_p10_dividend_exdate_reminder_deduped` (tests/test_bot_handlers.py:318-340 — per catatan konfirmasi; cek pipeline: `rtk .venv\Scripts\python.exe -m pytest -q "tests/test_bot_handlers.py::test_p10_dividend_exdate_reminder_deduped"` → 1 passed, exit 0; grep tests/ tidak menemukan tes kegagalan kirim).
- **Saran:** Balik urutan — kirim dulu via `send_with_retry(lambda: app.bot.send_message(int(cid), msg_txt, parse_mode=ParseMode.HTML))`, **baru** panggil `store.mark_div_reminder`; atau tetap tandai dulu tetapi hapus tandanya saat kirim gagal. Tambah tes: monkeypatch `send_message` melempar `NetworkError` → asersi pengingat terkirim pada putaran job berikutnya.
- **Catatan nuansa (per konfirmasi):** BadRequest (chat diblokir) tetap re-raise dan akan mencoba kirim ulang tiap putaran maks ~2 hari per event (filter `days_left in (3, 1)`) — dampak minor, tidak menggugurkan saran. Severity "sedang" wajar: notifikasi P10 bisa hilang senyap saat blip jaringan, tanpa korupsi data.
- **Severity:** sedang · **Usaha:** <2 jam

#### T2 — Korupsi senyap `model.safetensors` bila server mengabaikan header Range

- **Berkas:** download_model.py — fungsi `fetch` (domain: lainnya)
- **Masalah:** Unduhan per-chunk mengirim header `Range: bytes={have}-{end}` (download_model.py:42) tetapi **tidak pernah memverifikasi** status 206 / header Content-Range; body ditulis via `open(part, "ab")` (download_model.py:45). Bila proxy/CDN mengabaikan Range dan membalas 200 dengan body penuh, body dari offset 0 ditulis di atas `.part` yang sudah berisi `have` byte → file membengkak melewati `total`, iterasi berikutnya langsung finalisasi `have >= total` → `os.replace(part, out)` (download_model.py:36-37; jalur duplikat di 62-64) → `model.safetensors` korup tersimpan **tanpa warning**. `main()` melewatinya selamanya bila file ada dan ukurannya > 0 (download_model.py:75-77); korupsi baru terasa saat model dimuat di runtime, bukan saat build.
- **Bukti:** download_model.py:36-37, 42, 45, 62-64, 75-77 (dibaca langsung; grep `206|Content-Range|\.status|sha256|hash|etag` di file = tidak ada — per catatan konfirmasi). Reproduksi dinamis oleh pipeline review: fake `urlopen` yang mengabaikan Range dan membalas 200 full-body, `.part` pra-isi 100 byte → output `ukuran akhir: 10340 | seharusnya: 10240 / KORUP: True` dan fetch tetap mencetak `✓ model.safetensors (10,240 B)` tanpa warning. Script aktif dipakai: Dockerfile:18 (`COPY requirements.txt requirements-ml.txt download_model.py ./`) dan Dockerfile:30 (`python download_model.py`, tanpa cek integritas setelahnya) — dibaca langsung; juga README.md:88 (per catatan konfirmasi); sentuhan terakhir commit `2d00917` (ada di daftar pull).
- **Saran:** Sebelum menulis chunk, periksa `getattr(r, "status", None) != 206` atau absennya `Content-Range`: bila 200, potong `.part` ke 0 dan mulai ulang dari offset 0. Tambahan murah: simpan ukuran total/ETag di samping `.part` agar resume lintas versi file ditolak; opsional bandingkan hash akhir dengan metadata HF.
- **Catatan nuansa (per konfirmasi):** korupsi terjadi saat resume dengan `have > 0`; bila `have = 0`, body-200 penuh kebetulan menghasilkan file benar — konsisten dengan rumusan temuan. Pola tes offline-nya sudah ada (tests/test_phase2_data_infra.py:411-440 — fake urlopen yang menghormati Range), jadi menambah fake "balas 200 tanpa Range" mudah. Severity "sedang" sesuai: butuh proxy/CDN yang melanggar Range, tapi dampaknya korupsi senyap model ~500 MB.
- **Severity:** sedang · **Usaha:** <1 jam

### 🟡 Prioritas rendah — perilaku pengguna

#### T3 — Pengingat dividen hanya melayani 15 watchlist pertama dari maksimum 30

- **Berkas:** bot.py (domain: bot-dan-ci)
- **Masalah:** `check_dividend_reminders` memotong watchlist dengan `[:15]` (bot.py:1220-1221) — magic number tanpa konstanta/komentar — padahal `MAX_WATCH = 30` (bot.py:102) ditegakkan di handlers/watchlist.py:83-85 ("Watchlist maksimal 30 emiten"). Entri ke-16..30 tidak pernah mendapat pengingat H-3/H-1 dan batasnya **tidak diumumkan** di teks `/auto on` (handlers/watchlist.py:302-306) maupun README.md:59 — melanggar semangat prinsip produk "jujur terhadap data". Pola `[:15]` identik juga di handlers/jobs.py:195-196 (auto-scan pagi), jadi saran konstanta bernama relevan untuk keduanya.
- **Bukti:** bot.py:102 dan bot.py:1220-1221 (dibaca langsung); handlers/watchlist.py:83-85, 302-306, handlers/jobs.py:195-196, README.md:59 (per catatan konfirmasi).
- **Saran:** Naikkan ke `MAX_WATCH` dan/atau perlihatkan batasnya secara jujur (mis. di `/auto on`: "pengingat dividen hanya untuk 30 emiten pertama watchlist"). Kalau cap 15 sengaja demi biaya fetch kalender harian, jadikan konstanta bernama dengan komentar alasan + sebutkan di README/AGENTS agar perilakunya terdokumentasi.
- **Catatan nuansa (per konfirmasi):** tes existing (`test_p10_dividend_exdate_reminder_deduped`, tests/test_bot_handlers.py:319-340) hanya pakai ≤2 entri watchlist sehingga tidak mengunci cap-15; `cal_cache` dibagi lintas chat per run sehingga kenaikan biaya fetch moderat. Kaitan literal ke prinsip "jujur terhadap data" (AGENTS.md prinsip 1) adalah interpretasi reviewer; basis faktanya benar.
- **Severity:** rendah · **Usaha:** <1 jam

#### T4 — Fallback teks-polos `reply_long` menampilkan entity HTML literal + log menyesatkan

- **Berkas:** bot.py — `reply_long` (domain: bot-dan-ci)
- **Masalah:** Fallback plain-text hanya membuka tag `re.sub(r"<[^>]+>", "", p)` (bot.py:214) **tanpa `html.unescape`**, padahal seluruh teks dinamis lolos `esc()` = `html.escape` (core/reports.py:14-22, dibaca langsung; `esc()` dipakai masif di reports.py) → pesan fallback menampilkan entity literal seperti `&amp;`, `&lt;`, `&#x27;` ke user. `import html` belum ada di bot.py. Selain itu `except Exception` (bot.py:209) + log "bagian pesan ditolak parser HTML" (bot.py:210) juga menangkap `NetworkError` yang dilempar `send_with_retry` setelah retry habis (bot.py:185-186, `raise last` — per catatan konfirmasi) — label log menyesatkan untuk debugging.
- **Bukti:** bot.py:209-215 dan core/reports.py:14-22 (dibaca langsung); bot.py:185-186, komentar `BadRequest` subclass `NetworkError` (bot.py:157-165), `on_error` terdaftar (bot.py:1255, bot.py:1752), tidak ada tes yang mengunci jalur fallback (tests/test_send_retry.py:66-69; tests/test_menu_beginner.py:216/230) — per catatan konfirmasi.
- **Saran:** Ubah menjadi `html.unescape(re.sub(r"<[^>]+>", "", p))` dan pisahkan penanganan: `BadRequest` → fallback plain-text (log seperti sekarang); `NetworkError` setelah retry habis → log "jaringan gagal" dan lempar ulang agar `on_error` yang menangani. Perhatikan urutan: `BadRequest` subclass `NetworkError`, jadi `except BadRequest` harus didahulukan.
- **Catatan nuansa (per konfirmasi):** perubahan aman dari sisi tes existing, tapi sesuai AGENTS.md perlu tes baru + update README/PLAN/CHANGELOG setelah perilaku berubah.
- **Severity:** rendah · **Usaha:** <30 menit

#### T5 — Statistik `/status` tidak menghitung trafik tombol (callback & teks)

- **Berkas:** bot.py + handlers/info.py (domain: bot-dan-ci)
- **Masalah:** `timed()` (bot.py:218-242) hanya membungkus CommandHandler lewat `reg()` (bot.py:1692-1693); `menu_callback` (CallbackQueryHandler) dan `menu_text_input` (MessageHandler TEXT) didaftarkan **tanpa** `timed` (bot.py:1726-1727) — padahal sejak menu pemula & tombol tindak lanjut (commit `7286619`/`f35ff0f`/`513f1d5`) mayoritas aksi user lewat tombol. Akibatnya `STATS['commands']` dan rata-rata ms yang ditampilkan `/status` (handlers/info.py:134 `avg = STATS['ms']/STATS['commands']` dan :150-151) bias ke perintah slash saja dan tidak mencerminkan beban nyata bot.
- **Bukti:** bot.py:218-242, 1692-1693, 1726-1727 (dibaca langsung); handlers/info.py:134, 150-151 dan referensi commit (per catatan konfirmasi, cocok dengan daftar pull).
- **Saran:** Bungkus keduanya juga: `app.add_handler(CallbackQueryHandler(timed(menu_callback)))` dan `app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, timed(menu_text_input)))` — `timed` membaca `args[0]=update`, valid untuk kedua tipe handler PTB.
- **Catatan nuansa (per konfirmasi):** `timed` hanya mutasi STATS + log dan meneruskan args/kwargs apa adanya (perilaku tak berubah); setelah dibungkus, label "perintah" di /status ikut menghitung tekanan tombol — itu memang tujuan temuan.
- **Severity:** rendah · **Usaha:** <30 menit

### 🟢 Prioritas rendah — kebersihan kode & dokumen

#### T6 — Re-sort Python redundan untuk Top BELI

- **Berkas:** bot.py — `_scan_summary_text` (domain: bot-dan-ci)
- **Masalah:** Blok `buys_all.sort(key=...)` (bot.py:1527-1532, dibaca langsung) redundan: `store.load_market_scan("BELI")` sudah `ORDER BY rel_pct DESC, ticker` (core/store.py:708-710, dibaca langsung), dan hasil keduanya setara untuk kasus BELI. Sementara Top JUAL (bot.py:1534) tidak di-sort ulang — inkonsisten sekaligus kode ekstra yang harus dibaca-pahami setiap kali.
- **Bukti ekuivalensi (per konfirmasi, dieksekusi pipeline review):** Python + SQLite in-memory dengan replikasi persis key sort & SQL: EQUAL True untuk campuran realistis BELI (rel_pct ≥ 70 + NULL) dan semua-NULL; hanya berbeda bila ada rel_pct < −1, yang tak mungkin untuk BELI karena classify mensyaratkan `rel_pct >= BUY_PCT=70.0` (core/screener.py:43, 193-199) atau fallback teknikal dengan rel_pct=None (core/screener.py:205-210). Tidak ada tes yang mengunci urutan "Top BELI" (grep `Top BELI|buys_all` di tests/ kosong); `save_market_scan` hanya dipanggil core/screener.py:470.
- **Saran:** Hapus blok `buys_all.sort(...)` dan andalkan ORDER BY SQL untuk kedua daftar (atau, bila tujuannya menyatukan handling NULL, terapkan pada BELI dan JUAL sekaligus lewat helper kecil). Penyederhanaan murni, tanpa perubahan output.
- **Catatan nuansa (per konfirmasi):** "kode mati" lebih tepat "redundan" (sort tetap tereksekusi, hanya tak berefek); rationale "(NULL tetap di urutan terakhir)" hanya benar secara tidak langsung via batasan BUY_PCT — key Python memetakan NULL ke setara rel_pct −1,0, bukan posisi terakhir mutlak.
- **Severity:** rendah · **Usaha:** <15 menit

#### T7 — Komentar pin versi stale di requirements.txt

- **Berkas:** requirements.txt:14-17 (domain: lainnya)
- **Masalah:** Komentar masih mengklaim "ini diverifikasi `pytest` **670** lulus (coverage target CI **83%**, SCHEMA_VERSION = 6)" (requirements.txt:15, dibaca langsung) padahal baseline sekarang **764 passed / 86,05%**. Commit sinkronisasi docs `2e4afe5` hanya menyentuh AGENTS.md, CHANGELOG.md, PLAN.md, README.md (per `git show --stat 2e4afe5` pada catatan konfirmasi) dan melewatkan requirements.txt; sentuhan terakhir requirements.txt adalah `2d00917` (2026-11-14, fix(deploy) download_model). Jumlah 764 diverifikasi pipeline via `pytest --collect-only -q` dan dikonfirmasi ulang penyusun (SUM=764, bagian 1).
- **Saran:** Perbarui komentar ke "764 lulus (coverage target CI 86,05%)" + tanggal sinkron (±5 menit) agar klaim verifikasi dependensi tidak menyesatkan pembaca berikutnya.
- **Catatan nuansa (per konfirmasi):** "SCHEMA_VERSION = 6" di komentar masih akurat sesuai AGENTS.md; baris "naik dari 517 pada 2026-11-12" di blok yang sama ikut tertinggal (opsional diperbarui), tapi perbaikan komentar saja sudah memadai.
- **Severity:** rendah · **Usaha:** ±5 menit

#### T8 — Konjungsi ganda "serta dan" di CHANGELOG.md

- **Berkas:** CHANGELOG.md:27-28 (domain: lainnya)
- **Masalah:** Baris 27 diakhiri "…`manual:alert_add` & `manual:rencana`, **serta**" dan baris 28 dibuka "**dan** ketahanan `do_paket`/`do_scan`/`exec:*`)" (dibaca langsung) → terbaca "serta dan ketahanan", rancu di ringkasan rilis 14 Nov.
- **Saran:** Hapus salah satu konjungsi, mis. "…`manual:alert_add` & `manual:rencana`, serta ketahanan `do_paket`/`do_scan`/`exec:*`)". Alternatif sebaliknya (sisakan "dan", hapus "serta") juga memperbaiki.
- **Severity:** rendah · **Usaha:** ±5 menit

---

## 3. Temuan tidak-terkonfirmasi / tanpa verifikasi

**Tidak ada.** Seluruh 8 temuan yang diterima dari pipeline review datang dengan status **"terverifikasi"** beserta catatan konfirmasi per temuan (bukti bacaan kode dan/atau reproduksi — lihat kolom Bukti & Catatan nuansa di bagian 2). Penyusun juga membaca ulang baris-baris kunci yang dikutip (bot.py, download_model.py, core/store.py, core/reports.py, requirements.txt, CHANGELOG.md, Dockerfile) dan semuanya cocok.

Penyesuaian kecil yang sudah diterapkan di laporan ini mengikuti catatan konfirmasi pipeline (tanpa mengubah substansi):
- T1: `except Exception` di bot.py:1248-1249 (temuan asli menulis 1246-1247 — selisih 2 baris).
- T4: `esc()` di core/reports.py:14-22 (temuan asli menulis :26).
- T5: rentang `timed()` 218-242 (temuan asli menulis 218-247).
- T6: Top JUAL di bot.py:1534 (temuan asli menulis 1533); istilah "kode mati" diperhalus menjadi "redundan".
- T1/T3: awal blok tes dedup tests/test_bot_handlers.py:318 (temuan asli menulis 322).

Kalau nanti muncul temuan baru **tanpa** verifikasi, tandai eksplisit "belum diverifikasi" dan jangan digabung ke daftar bagian 2 sebelum dicek.

---

## 4. Tiga langkah pertama yang disarankan minggu ini

1. **Perbaiki urutan kirim pengingat dividen (T1).** Kirim via `send_with_retry` dulu, baru `store.mark_div_reminder` (atau hapus tanda saat kirim gagal), plus tes kegagalan kirim (`NetworkError` → terkirim di putaran job berikutnya).
   *Kenapa penting:* satu-satunya jalur notifikasi P10 bisa gagal senyap dan hilang permanen saat blip jaringan — persis kelas gangguan yang `send_with_retry` dirancang cegah. **Usaha:** <2 jam.
2. **Hardening `download_model.py` (T2).** Validasi status 206 / `Content-Range` sebelum menulis chunk; bila 200, truncate `.part` dan mulai ulang dari offset 0; tambah tes fake "200 tanpa Range" (pola sudah ada di tests/test_phase2_data_infra.py:411-440).
   *Kenapa penting:* mencegah korupsi senyap model ~500 MB yang baru terasa saat runtime produksi, bukan saat build — dan script ini aktif dipakai di Dockerfile sejak `2d00917`. **Usaha:** <1 jam.
3. **Batch kecil kejujuran UX + dokumen (T3, T4, T7, T8).** Naikkan/dokumentasikan cap 15 watchlist pengingat dividen; `html.unescape` pada fallback `reply_long` (+ pisahkan penanganan BadRequest/NetworkError); perbarui komentar requirements.txt ke baseline 764/86,05%; perbaiki typo "serta dan" di CHANGELOG.md.
   *Kenapa penting:* menutup celah "tidak jujur terhadap data" (batas layanan yang tidak diumumkan, entity literal ke user) dan menjaga klaim dokumen tetap benar dengan usaha kecil. **Usaha:** ≈2 jam total.

(T5 dan T6 bisa menyusul setelahnya: masing-masing <30 menit dan <15 menit, tanpa risiko perilaku.)

---

## Lampiran: perintah yang dijalankan penyusun pada sesi ini

| Perintah | Hasil |
|---|---|
| `rtk .venv\Scripts\python.exe -m pytest -q` | exit 0; progres `[100%]`, tanpa kegagalan (suite penuh, offline) |
| `rtk .venv\Scripts\python.exe -m pytest --collect-only -q` (dijumlahkan per berkas) | `SUM=764` |
| `rtk .venv\Scripts\python.exe -m ruff check .` | exit 0; `All checks passed!` |
| Pembacaan langsung | bot.py:95-106, 200-249, 1210-1254, 1520-1539, 1588-1599, 1685-1732; download_model.py:1-83; core/store.py:550-569, 700-713; core/reports.py:10-29; requirements.txt:10-19; CHANGELOG.md:24-29; Dockerfile:1-35 |

Perintah verifikasi lain yang disebut di laporan (mis. `pytest` satu tes, `git show --stat 2e4afe5`, grep, simulasi korupsi unduhan) berasal dari catatan konfirmasi pipeline review pada berkas tugas, bukan dieksekusi ulang penyusun — disebut eksplisit dengan label "per catatan konfirmasi".
