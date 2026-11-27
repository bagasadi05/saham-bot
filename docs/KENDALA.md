# Kendala SahamBuddy — rangkuman untuk diperbaiki

> **Batasan data & lisensi (ditambahkan 2026-11-12):** yfinance/Yahoo untuk
> penggunaan pribadi; data BEI tidak untuk komersial tanpa izin dan ToS BEI
> melarang scraping/crawling; jangan menambah scraper Stockbit/IDX. Rincian
> ada di README bagian "Batasan data & lisensi".

> **STATUS PERBAIKAN — 2026-11-06 ~09:35 WIB (commit `fa71f24` "Hardening
> produksi", sudah di-pull dan diverifikasi di mesin ini):**
> - ✅ 1. Yahoo 429 — **sudah bisa lagi**: fetch BBCA live 240 baris, `stale: False`,
>   cache `temp/cache/BBCA.JK.csv` tertulis. Skor BBCA: -70 (STRONG SELL, -77,8%).
> - ✅ 3. Crash httpx `NO_PROXY` — `run.sh` resmi sudah berisi fix yang sama.
> - ✅ 4. Guard `None` — `analyze(None)` kini raise `ValueError` ramah, teruji.
> - ✅ 5. Allowlist — ada (`ALLOWED_USER_IDS`, kosong = siapa pun; warning tercatat).
> - ✅ 6. Redaksi token — logger `httpx`/`httpcore` di-`WARNING`, URL token tidak
>   lagi muncul di `bot.log` (terverifikasi).
> - ✅ 9. Format log — `%(message)s` diperbaiki, log bersih, 0 "Logging error".
> - ⏳ 2. IDX 403 — belum diperbaiki (tetap fallback `/analisis`).
> - ⏳ 7. Model ML sentimen — belum dipasang (masih fallback lexicon).
> - ⏳ 8. Persistensi proses — bot masih proses latar biasa, mati saat VM restart.
> - Bot di-restart ke kode baru (PID 5924), polling Telegram 200 OK.
> Retest Yahoo cron ~15:51 WIB tetap jalan sebagai cek kedua.
> - ✅ 11. Bug HTML Telegram (ditemukan 09:45 WIB saat arif tes live, BUKAN dari
>   daftar awal) — teks "MA20 < MA50" di laporan bikin Telegram menolak pesan
>   ("Can't parse entities: unsupported start tag"), /analisis BBCA crash
>   lewat perintah maupun tombol menu. Diperbaiki dengan `esc()` (html.escape)
>   di build_report/berita/fundamental/bandar/alert + `_safe_edit` yang
>   mengabaikan "Message is not modified" (double-tap) + on_error kini log
>   `ctx.error` asli. Dites offline end-to-end dengan network asli (laporan
>   + chart + menu, semua HTML bersih); bot restart ~09:50 WIB (PID 2055).

Repo: `bagasadi05/saham-bot` (commit `72ce373`, 1 commit, dibuat 2026-11-06).
Lokasi lokal: `~/workspace/saham-bot` — venv `.venv`, config `.env` (mode 600,
gitignored), starter `run.sh` (untracked).
Bot Telegram: **@claudepencaribot** ("analisisahambot", id 8966250359) — **LIVE**
sejak 2026-11-06 ~09:20 WIB, proses `.venv/bin/python bot.py` (PID 3797 saat
ditulis), polling `getUpdates` 200 OK.

## Konteks lingkungan (penting — banyak kendala berasal dari sini)

- Mesin ini **sandbox, bukan VPS**: container 2 CPU, ~8 GB RAM, hanya `/home/hatch`
  yang persisten. Proses mati saat VM restart; file unit systemd ikut terhapus.
- Semua egress lewat proxy (`HTTP(S)_PROXY` + `ALL_PROXY` ke egress proxy).
  `NO_PROXY` bawaan berisi literal IPv6 kurung-siku (`[::1]`, `[fd8b:...]`).
- RAM sempat mepet saat install (~800 MB available) — lihat kendala 7.

## 1. [KRITIS] Yahoo Finance 429 — sumber data harga diblokir

- **Gejala:** `data.fetch_history('BBCA')` mengembalikan `None`; semua fitur inti
  (`/analisis`, `/skor`, `/backtest`, `/saran`, alert) tidak bisa jalan.
- **Bukti:** `curl query1.finance.yahoo.com/.../BBCA.JK` → `429` (0,5 dtk);
  `yfinance` timeout 10 dtk / 30 dtk lalu `Failed download: Timeout`.
  README repo sendiri sudah mengingatkan YF rate-limit dari luar Indonesia.
- **Dampak:** bot online tapi tidak bisa analisis harga — fungsi utamanya mati.
- **Opsi perbaikan:**
  - Tunggu/pulih sendiri (rate-limit IP egress bersama kadang reda).
  - Buat yfinance memakai session proxy-aware secara eksplisit.
  - Ganti/tambah sumber data harga lain (mis. Stooq, IDX, provider lain).
  - Deploy ke VPS sungguhan (lihat `deploy.md`) dengan IP yang tidak di-rate-limit.
- **Status:** retest satu kali sudah dijadwalkan cron `saham-bot-yahoo-retest`
  ~15:51 WIB 2026-11-06 (curl + fetch BBCA + uji analisis, lapor ke chat).

## 2. idx.co.id 403 (Cloudflare) — `/bandar` mati

- **Gejala:** `curl https://www.idx.co.id/primary/TradingSummary/GetStockSummary`
  → `403`.
- **Dampak:** `/bandar` (data resmi BEI: IHSG, sektor, top gainer/loser, aliran
  asing) tidak jalan dari IP datacenter. README sudah mengakui ini dan bot
  memberi pesan fallback ke `/analisis`.
- **Opsi perbaikan:** terima sebagai keterbatasan + pastikan fallback-nya bagus,
  atau pakai toolkit `idx-bei` yang disebut di README.

## 3. Crash startup: httpx tidak bisa parsing `NO_PROXY` bawaan

- **Gejala:** saat pertama dinyalakan, bot crash sebelum polling:
  `httpx.InvalidURL: Invalid port: ':1]'` di
  `Application.builder().token(...).build()` → `HTTPXRequest` →
  `httpx.AsyncClient()` membaca env proxy.
- **Penyebab:** entri IPv6 kurung-siku di `NO_PROXY` (`[::1]`, `[fd8b:...]`).
- **Workaround aktif (sudah diterapkan):** `run.sh` menimpa
  `NO_PROXY`/`no_proxy` menjadi `localhost,127.0.0.1,::1` sebelum start.
- **Perbaikan permanen:** set proxy/no-proxy eksplisit di kode (mis. bangun
  `HTTPXRequest` dengan proxy sendiri) atau sanitasi env di `bot.py`, supaya
  bot tidak bergantung pada `run.sh`.

## 4. `analyze()` crash kalau data gagal diambil (tidak ada guard `None`)

- **Gejala:** `analysis.analyze('BBCA', None)` →
  `AttributeError: 'NoneType' object has no attribute 'copy'` di
  `compute_indicators` (`core/analysis.py:44`, dipanggil dari `:230`).
- **Dampak:** begitu sumber data gagal, handler bot crash dengan traceback
  alih-alih membalas pesan ramah ke user.
- **Perbaikan:** guard `None` di `analyze()` dan di semua handler
  (`/analisis`, `/skor`, `/backtest`, `/saran`, scanner alert) — balas
  "data gagal diambil, coba lagi nanti" + log penyebabnya.

## 5. Tanpa allowlist — siapa pun bisa memakai bot

- **Gejala:** tidak ada filter user di `bot.py`; siapa pun yang menemukan
  username bot bisa menjalankan semua command dan menambah alert/watchlist.
- **Perbaikan:** tambah `ALLOWED_USER_IDS` (env) + filter di tiap handler
  (atau `filters.User(...)` di `python-telegram-bot`).

## 6. Token bocor ke log (dan pernah terkirim lewat chat)

- **Gejala:** `httpx` me-log URL lengkap termasuk token
  (`POST https://api.telegram.org/bot<TOKEN>/getUpdates ... 200 OK`) di `bot.log`.
  File sudah `chmod 600` dan `*.log` di-`.gitignore`, tapi isinya tetap token
  mentah. Token juga pernah ditempel langsung di chat (bukan lewat form aman).
- **Perbaikan:** matikan/redact logging URL httpx (mis. set level `WARNING`
  untuk logger `httpx`/`httpcore`, atau filter kustom); pertimbangkan
  `/revoke` di @BotFather lalu isi token baru ke `.env` dan restart bot.

## 7. Model ML sentimen belum terpasang — sentimen jatuh ke lexicon

- **Gejala:** `torch` + `transformers` + model `will702/indo-roBERTa-financial-
  sentiment-v2` (~500 MB) sengaja tidak di-install; `core/news_ml.py`
  fallback ke lexicon. Hugging Face terjangkau dari sini (307 ke CDN).
- **Penyebab penundaan:** RAM available sempat ~800 MB saat install.
- **Perbaikan:** install `torch` CPU + `transformers` + jalankan
  `download_model.py` saat RAM longgar, lalu verifikasi `/berita` memakai ML.

## 8. Bot mati saat VM restart (tidak ada persistensi proses)

- **Gejala:** bot jalan sebagai proses latar biasa (`./run.sh`), bukan service.
  VM replacement mematikan proses; tidak ada watchdog (sengaja tidak dibuat).
- **Perbaikan:** systemd service dengan `ExecStart` absolut + `Restart=always`
  (ingat fix `NO_PROXY` kendala 3 dan token dari `.env`), atau deploy ke VPS
  sungguhan sesuai `deploy.md` (Docker + volume `data/`).

## 9. Format string logging salah — semua log record gagal diformat

- **Gejala:** `bot.py:34` —
  `logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)", ...)`
  — `%(message)` kehilangan `s`, seharusnya `%(message)s`.
- **Bukti:** `bot.log` berisi 9+ blok `--- Logging error ---` + traceback
  `logging/__init__.py: formatMessage`, dan pesan log asli hanya terlihat di
  dalam dump traceback.
- **Dampak:** kosmetik (bot tetap jalan), tapi log hampir tidak terbaca dan
  error sungguhan sulit ditemukan.
- **Perbaikan:** satu karakter — ubah ke `%(message)s`.

## 10. Catatan (bukan bug): sinyal BUY belum profitable sendiri

- README repo jujur: backtest 5 tahun BBCA/TLKM/ANTM — sinyal BUY berdiri
  sendiri belum profitable setelah biaya (win-rate 29–32%, PF 0,48–0,64).
  Pemakaian sehat: alat belajar + filter/pelindung + alarm, bukan mesin beli
  otomatis. Jangan "memperbaiki" ini dengan overfitting; kalau mau ditingkatkan,
  lakukan via riset strategi, bukan ubah angka backtest.

## Yang sudah diverifikasi jalan

- Clone GitHub + `git pull` (publik, tanpa auth). Push butuh auth GitHub pemilik.
- Semua import: `telegram`, `yfinance`, `pandas`, `matplotlib`,
  `pandas-ta-classic`, `backtesting` — OK.
- Google News RSS → `200` (`/berita` + sentimen lexicon bisa jalan).
- Token valid (`getMe` OK) dan polling Telegram `200 OK`.
- `.env` + `store.json` + `data/` + `temp/` + `*.log` + `.venv/` di-`.gitignore`.

## Prioritas yang disarankan

1. Kendala 1 (Yahoo 429) — penentu apakah bot berguna di mesin ini.
2. Kendala 4 (guard `None`) + 9 (format log) — perbaikan kecil, dampak besar
   buat ketahanan dan keterbacaan.
3. Kendala 5 (allowlist) + 6 (redact token di log) — keamanan sebelum bot
   dipakai luas.
4. Kendala 3 (fix permanen proxy) + 8 (service/VPS) — ketahanan jalan.
5. Kendala 7 (model ML) + 2 (IDX) — peningkatan fitur.

## Catatan 2026-11-06 ~10:23 WIB — bot mati karena NetworkError, tanpa auto-restart
- Bot (PID 2055, jalan sejak 09:50) mati jam ~10:23 WIB: `NetworkError:
  httpx.RemoteProtocolError: Server disconnected without sending a response`
  saat polling `get_updates` ke api.telegram.org (kemungkinan hiccup proxy).
  Error tercatat lewat `on_error`, lalu proses berhenti — tidak ada
  restart otomatis, bot diam ~20 menit sampai di-restart manual ~10:45 WIB
  (PID 2419, log bersih).
- Ini bukti hidup kendala 8 (no process persistence): satu network blip saja
  cukup mematikan bot. Perbaikan yang disarankan: supervisor/restart loop
  (mis. `while true; do ./run.sh; sleep 5; done` di wrapper, atau systemd
  dengan `Restart=always`) — tapi ingat unit systemd bisa hilang saat VM
  restart, jadi wrapper loop di `run.sh`/skrip terpisah lebih tahan.

## Catatan 2026-11-06 16:17–17:07 WIB — VM restart matikan bot, supervisor ikut mati
- VM restart ~16:17 WIB mematikan bot + supervisor. Seorang background worker
  sempat menyalakan lagi supervisor, tapi bot gagal konek ke Telegram
  (`httpx.ProxyError: 403 Forbidden` dari proxy egress, tiap percobaan mati
  dalam 3–4 detik, supervisor retry tiap 60 detik) dan supervisor-nya
  sendiri ditemukan mati ~17:05 WIB (penyebab pasti tidak diketahui —
  kemungkinan ikut ter-reap saat sesi worker yang menjalankannya berakhir;
  supervisor adalah `while true` loop, tidak bisa mati sendiri).
- Dicek 17:06 WIB: proxy 403 ternyata transient — tes httpx langsung ke
  api.telegram.org (dengan NO_PROXY yang sudah disanitasi run.sh) dapat 401
  normal (token palsu), dan curl juga tembus. Bukan blokir permanen.
- Supervisor dinyalakan ulang 17:07 WIB (10:07 UTC) dengan
  `setsid nohup ./run-supervised.sh >> bot.log 2>&1 < /dev/null &` supaya
  terlepas dari sesi shell dan tidak ikut mati. Bot PID 5876, supervisor PID
  5872, log bersih ("Bot jalan", scheduler + scan_alerts OK).
- Pelajaran: (1) supervisor HARUS dijalankan dengan setsid/nohup (atau
  setara) — start biasa dari sesi agent bisa ikut mati saat sesi berakhir.
  (2) Setelah VM restart, jam container jadi UTC — timestamp bot.log
  pasca-restart dalam UTC, bukan WIB. (3) Proxy 403 untuk Telegram bisa
  transient; jangan langsung menyimpulkan blokir permanen, tes ulang dulu
  dengan httpx + NO_PROXY tersanitasi.

## Catatan 2026-11-07 ~06:28–07:13 WIB — VM restart kedua, pola yang sama
- VM restart lagi ~06:28 WIB (uptime 43 menit saat dicek 07:12). Bot + supervisor
  mati. Background worker menyalakan lagi supervisor ~07:00 (PID 4976), tapi bot
  gagal konek lagi (proxy 403 ke Telegram, tiap percobaan mati 3–6 detik, retry
  tiap 60 detik, total 138× "403 Forbidden" di log) dan supervisor ditemukan
  mati lagi ~07:11 — lagi-lagi saat fase sleep 60 detik, persis seperti 6 Nov.
- Pola yang makin jelas: supervisor yang dinyalakan background worker mati
  dalam ~10 menit (kemungkinan ikut ter-reap saat sesi worker berakhir),
  sedangkan supervisor yang dinyalakan 6 Nov 17:07 via `setsid nohup`
  bertahan 13+ jam sampai VM restart. Jadi: SELALU pakai setsid + nohup +
  disown saat menyalakan supervisor, dan verifikasi PID masih hidup >60 detik
  kemudian (satu siklus retry).
- Dinyalakan ulang 07:13 WIB via setsid nohup (supervisor PID 6265, bot PID
  6270, "Bot jalan", scheduler + scan_alerts OK). Proxy 403 lagi-lagi transient
  — tes httpx langsung ke Telegram dapat 401 normal di saat yang sama.

## Catatan 2026-11-07 ~07:36–08:07 WIB — VM restart KETIGA <24 jam, supervisor mati 3x
- VM restart ketiga: 16:17 WIB 6 Nov, ~06:28 WIB 7 Nov, ~07:36 WIB 7 Nov
  (uptime 30 menit saat dicek 08:06). Bot yang dinyalakan 07:13 (PID 6270)
  hanya hidup ~23 menit.
- Supervisor dinyalakan ulang ~07:58 (PID 4649, diklaim via setsid nohup),
  retry tiap 60 detik; bot sempat "Bot jalan" 2x (08:03, 08:04) lalu mati
  2–6 detik kemudian — 403 Forbidden menghantam saat polling get_updates,
  padahal get_me/inisialisasi sempat lolos. Jadi 403 proxy itu intermittent
  PER-REQUEST, bukan blokir total: inisialisasi bisa lolos, polling bisa kena.
- Supervisor ditemukan mati lagi ~08:05 (log berhenti 01:04:09 UTC
  "restart dalam 60s", tidak ada proses saat dicek 08:06). Ini kematian
  supervisor ke-3 saat fase sleep — dan kali ini diklaim sudah pakai setsid.
  Dugaan baru: supervisor mungkin tidak pernah bertahan lama dari sesi agent
  mana pun; bot-nya sendiri (sebagai orphan) yang bertahan 13 jam 6–7 Nov.
  Perlu diselidiki serius (item P11 di prompt): systemd user + lingering,
  atau cron penjaga yang memeriksa tiap beberapa menit.
- Dinyalakan ulang 08:07 WIB via setsid nohup (supervisor PID 5658, bot PID
  5663, "Bot jalan", scan_alerts OK, hidup >20 detik saat dicek).
- Git status 08:06 WIB: M bot.py (fitur tanya beli/jual belum di-commit),
  untracked KENDALA.md, PROMPT-PERBAIKAN-BOT.md, run-supervised.sh.
  Branch 2 commit di depan origin/main, belum push.

## Catatan 2026-11-07 ~09:31–10:48 WIB — VM restart keempat DAN kelima, 403 terpanjang
- VM restart keempat ~09:31 WIB: bot (hidup 1 jam 24 mnt sejak 08:07) mati.
  Supervisor dinyalakan ~09:58 (PID 5302), retry tiap 60 detik; bot sempat
  "Bot jalan" berkali-kali 09:59–10:07 tapi mati 2–5 detik kemudian karena
  proxy 403 saat polling. Total 403 di log naik ke 264.
- VM restart KELIMA ~10:25 WIB (uptime 22 menit saat dicek 10:47): semuanya
  mati lagi; log membeku 03:07:42 UTC (10:07:42 WIB); tidak ada proses saat
  dicek 10:47. Lima restart dalam ~18 jam (16:17 6 Nov; 06:28, 07:36, 09:31,
  10:25 7 Nov) — frekuensi tidak normal untuk sandbox ini.
- Dinyalakan ulang 10:48 WIB via setsid nohup (supervisor PID 3477, bot PID
  3482, "Bot jalan", scan_alerts OK, hidup >25 detik). Tes httpx langsung ke
  Telegram saat itu dapat 401 normal — proxy 403 sudah terangkat.

## Catatan 2026-11-07 ~16:47–17:09 WIB — VM restart KEENAM, supervisor mati ke-4
- VM restart keenam ~16:47 WIB (uptime 22 menit saat dicek 17:08). Enam
  restart dalam ~24 jam (16:17 6 Nov; 06:28, 07:36, 09:31, 10:25, 16:47
  7 Nov).
- Supervisor dinyalakan ~16:58 (PID 1828, diklaim via setsid) — bot sempat
  "Bot jalan" 3x (17:01–17:03) lalu mati 2–4 detik kemudian karena proxy
  403; supervisor ditemukan mati lagi ~17:04 saat fase sleep. Ini kematian
  supervisor ke-4 dalam sehari. Polanya konsisten: supervisor yang
  dinyalakan background worker mati dalam ~6–11 menit. Satu-satunya
  supervisor yang bot-nya bertahan lama adalah yang dinyalakan 6 Nov 17:07
  dari sesi main agent (bot hidup 13 jam) — tapi supervisor-nya sendiri
  tidak pernah diverifikasi hidup setelah menit-menit awal; bot mungkin
  bertahan sebagai orphan.
- Dinyalakan ulang 17:09 WIB via setsid nohup (supervisor PID 2925, bot PID
  2929, "Bot jalan", scan_alerts OK). Tes httpx ke Telegram saat itu 401
  normal — 403 sudah terangkat.
- Yang masih terbuka: mekanisme awet untuk supervisor (systemd user +
  lingering, atau cron penjaga) — P11 di prompt agent. Jangan tawarkan
  watchdog (arif sudah menolak).

## Catatan 2026-11-08 ~07:54–08:11 WIB — VM restart KETUJUH, supervisor mati ke-5
- VM restart ketujuh ~07:54 WIB (uptime 16 menit saat dicek 08:10). Tujuh
  restart dalam ~40 jam (16:17 6 Nov; 06:28, 07:36, 09:31, 10:25, 16:47
  7 Nov; 07:54 8 Nov).
- Supervisor dinyalakan ~07:59 (PID 2158) — mati lagi; dinyalakan lagi 08:07
  (PID 2976) — mati lagi ~08:10 saat fase sleep. Ini kematian supervisor
  ke-5. Bot sempat "Bot jalan" 08:08, 08:09, 08:10 lalu mati 2 detik
  kemudian karena proxy 403 saat polling.
- Dinyalakan ulang 08:11 WIB via setsid nohup (supervisor PID 3360, bot PID
  3391, "Bot jalan", scan_alerts OK). Tes httpx ke Telegram 401 normal —
  403 sudah terangkat saat itu.
- Refleksi: semua supervisor (siapa pun yang menyalakan) hanya pernah
  diverifikasi hidup <60 detik setelah start; bot-nya yang bertahan lama
  sebagai orphan. Jadi "setsid tahan, worker mati" mungkin salah baca —
  kemungkinan SEMUA supervisor mati dalam hitungan menit, dan bot-lah yang
  awet. Yang benar-benar dibutuhkan: penjaga yang tidak bergantung pada
  proses supervisor yang hidup terus — mis. cron tiap 5 menit yang memeriksa
  pgrep bot.py dan menyalakan ulang bila mati (cron runtime tahan VM
  restart), atau bot yang retry sendiri saat polling 403 tanpa mati.
  Perlu keputusan arif sebelum memasang cron penjaga.

## Catatan 2026-11-08 ~09:52–10:49 WIB — VM restart KEDELAPAN, supervisor mati ke-6
- VM restart kedelapan ~09:52 WIB (uptime 9 menit saat dicek 10:01).
  Delapan restart dalam ~42 jam.
- Supervisor dinyalakan ~10:02 (PID 1924) — bot tidak pernah nyala (tiap
  percobaan mati 2–6 detik karena proxy 403); supervisor ditemukan mati
  lagi ~10:20 saat fase sleep (kematian supervisor ke-6). Tidak ada yang
  me-retry bot saat itu.
- Dinyalakan ulang 10:49 WIB via setsid nohup (supervisor PID 2965, bot PID
  2969, "Bot jalan", scan_alerts OK, hidup >75 detik saat dicek). 403 sudah
  terangkat saat itu.
