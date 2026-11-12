# 🚀 Panduan Deploy SahamBuddy ke VPS (5 menit)

Bot jalan 24/7 di Docker: otomatis restart bila crash/reboot, data posisi
latihan & watchlist bertahan di volume.

## Yang diperlukan

- VPS Ubuntu/Debian/Alma, **2 GB RAM** (kencang untuk fitur ML) atau 1 GB
  (pakai build `SKIP_MODEL=1`, sentimen jatuh ke lexicon)
- Docker + docker compose plugin:
  ```bash
  curl -fsSL https://get.docker.com | sh        # atau dari package manager distromu
  sudo usermod -aG docker $USER && newgrp docker
  ```
- Token bot dari [@BotFather](https://t.me/BotFather): `/newbot` → salin token
- (Disarankan) ID Telegram-mu — kirim `/id` langsung ke bot-mu saat start awal
  (atau tanyakan ke [@userinfobot](https://t.me/userinfobot)), lalu isi
  `ALLOWED_USER_IDS` (dan opsional `REQUIRE_ALLOWLIST=1`) supaya bot privat

## Langkah (Docker — cara utama)

```bash
git clone https://github.com/bagasadi05/saham-bot.git
cd saham-bot
cp .env.example .env
nano .env                # isi BOT_TOKEN=..., ALLOWED_USER_IDS=idmu, simpan
docker compose up -d --build
```

Build pertama memakan 5–15 menit (unduh python image + torch + model ML 500 MB).
Cek log:

```bash
docker compose logs -f
# muncul: "Bot jalan. Tekan Ctrl+C untuk berhenti." → tekan Ctrl+C (log saja, bot tetap jalan)
```

Test ke Telegram: kirim `/start` ke bot-mu → jawaban menu. Selesai — bot hidup 24/7.

## Uji live setelah deploy

Jalankan di chat bot (bukan di mesin dev), urut dari yang paling murah:

| # | Perintah | Yang harus terlihat | Kalau gagal |
|---|---|---|---|
| 1 | `/status` | uptime, jumlah perintah, cache, status bursa | cek `docker compose logs -f` |
| 2 | `/sektor` | daftar sektor BEI + terkuat/terlemah, **atau** pesan "diblokir Cloudflare" | idx.co.id memblokir IP VPS — bukan bug bot |
| 3 | `/cek BBCA` | 6 bagian + kesimpulan LAYAK/TUNGGU/HINDARI; bagian tanpa data ditulis "tidak tersedia" | kalau semua bagian kosong: cek koneksi Yahoo |
| 4 | `/sinyal beli bagus` | penanda ✅/⚠️/❔ + jumlah kandidat dinilai | butuh snapshot `/scan` dulu |
| 5 | `/portofolio` | "Belum ada posisi" atau daftar posisi + PnL | — |
| 6 | `/rencana BBCA 5000000` | lot, biaya, % modal, rugi maks, RR | — |

Catat tempel output `/cek` dan `/sektor` ke chat; kalau ada yang aneh, kirim
barisnya — log JSON (`LOG_JSON=1`) memudahkan mencari perintah yang gagal:

```bash
docker compose logs -f | jq 'select(.level=="ERROR")'
```

### Hasil uji data (2026-11-12, mesin dev)

Checklist dijalankan dengan data Yahoo/BEI sungguhan, tanpa polling Telegram
(mesin ini tidak punya `BOT_TOKEN`; `getMe` → `InvalidToken`). Hasil:

| Perintah | Hasil |
|---|---|
| `/status` | OK — uptime, cache, store, histori sinyal, status bursa tampil |
| `/sektor` | Jujur gagal: payload BEI tidak memuat kode sektor yang dikenal (idx.co.id memblokir IP ini) |
| `/cek BBCA` | OK — 6 bagian; ROE tidak tersedia ditulis terbuka; verdict TUNGGU |
| `/sinyal beli bagus` | OK — "tidak ada kandidat BELI" (12 emiten sampel semuanya TAHAN oleh gate tren pasar) |

Uji tingkat chat (pesan benar-benar terkirim ke Telegram) tetap harus
dijalankan di host yang punya token; langkahnya ada di tabel di atas.

## Perintah sehari-hari

| Tujuan | Perintah |
|---|---|
| Lihat log | `docker compose logs -f --tail=100` |
| Update tanpa hilang data | `git pull && docker compose up -d --build` |
| Matikan/aktifkan | `docker compose down` / `docker compose up -d` |
| Cek status | `docker compose ps` (lihat kolom STATUS: `healthy`/`unhealthy`) |
| Cek isi bot | kirim `/status` di Telegram (uptime, error, isi store, cache) |

Data `store.db` (watchlist/alert/paper trading, SQLite) disimpan di folder
`data/` di dalam VPS — kebal terhadap rebuild container. Backup cukup
menyalin folder itu:

```bash
tar czf saham-data-$(date +%F).tgz data/     # saat container berhenti:
docker compose stop && tar czf ... && docker compose start
```

`store.json` versi lama (kalau ada) dimigrasikan otomatis sekali saat start,
lalu berganti nama jadi `store.json.migrated` — jangan dihapus sebelum yakin.

**Healthcheck.** Container menulis `data/heartbeat.txt` tiap 5 menit; Docker
menandai container `unhealthy` kalau file itu basi >30 menit. Lihat dengan
`docker inspect --format '{{.State.Health.Status}}' saham-bot`.

## Varian slim (RAM kecil / HDD kecil)

```bash
SKIP_MODEL=1 docker compose up -d --build
```

Build ini tidak memasang `torch`/`transformers` dan tidak mengunduh model —
semua fitur tetap jalan kecuali sentimen ML, yang otomatis jatuh ke lexicon.

## Alternatif tanpa Docker (systemd)

```bash
sudo apt install -y python3-pip python3-venv
sudo mkdir -p /opt/saham-bot && sudo chown $USER /opt/saham-bot
git clone https://github.com/bagasadi05/saham-bot.git /opt/saham-bot
cd /opt/saham-bot
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt          # inti; tambah requirements-ml.txt untuk model ML
cp .env.example .env && nano .env

# servis systemd:
sudo tee /etc/systemd/system/saham-bot.service > /dev/null <<'EOF'
[Unit]
Description=SahamBuddy Telegram bot
After=network-online.target

[Service]
User=KAMU
WorkingDirectory=/opt/saham-bot
EnvironmentFile=/opt/saham-bot/.env
Environment=NO_PROXY=localhost,127.0.0.1,::1
Environment=HEARTBEAT_FILE=/opt/saham-bot/heartbeat.txt
ExecStart=/opt/saham-bot/run.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now saham-bot
journalctl -u saham-bot -f        # lihat log
```

## Anti-jebakan

- 🔒 **BOT_TOKEN tidak pernah masuk git** (`.gitignore` mengunci `.env`).
  Kalau takut bocor: @BotFather → `/revoke` → buat token baru → update `.env`
- 📵 Jaringan VPS kadang punyai limit unduhan — jika build freeze di layer
  model, jalankan ulang `docker compose build` (unduhan resume otomatis,
  `.dockerignore` & chunk 4 MB anti-putus)
- 📊 Data BEI (`/bandar`) kadang diblokir Cloudflare saat bot ditempatkan di
  IP datacenter — itu normal, bot kasih pesan jujur + alternatif `/analisis`
- 🔄 **Restart cepat setelah VM reboot (mode supervisor):** bila menjalankan tanpa
  Docker/systemd, hidupkan ulang dengan satu perintah:
  `nohup ./run-supervised.sh >> bot.log 2>&1 &`
- 🗓️ Kalender bursa memakai paket `exchange_calendars` (XIDX). Kalau SKB cuti
  bersama tahun berjalan belum masuk paket, isi `MARKET_HOLIDAYS` di `.env`
  supaya job alert & autoscan tidak jalan di hari libur
- ⚠️ Bukan saran investasi — DYOR senantiasa
