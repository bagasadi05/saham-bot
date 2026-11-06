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

## Langkah (Docker — cara utama)

```bash
git clone https://github.com/NAMA-KAMU/saham-bot.git
cd saham-bot
cp .env.example .env
nano .env                # isi BOT_TOKEN=123456:ABC..., simpan (Ctrl+O, Ctrl+X)
docker compose up -d --build
```

Build pertama memakan 5–15 menit (unduh python image + torch + model ML 500 MB).
Cek log:

```bash
docker compose logs -f
# muncul: "Bot jalan. Tekan Ctrl+C untuk berhenti." → tekan Ctrl+C (log saja, bot tetap jalan)
```

Test ke Telegram: kirim `/start` ke bot-mu → jawaban menu. Selesai — bot hidup 24/7.

## Perintah sehari-hari

| Tujuan | Perintah |
|---|---|
| Lihat log | `docker compose logs -f --tail=100` |
| Update tanpa hilang data | `git pull && docker compose up -d --build` |
| Matikan/aktifkan | `docker compose down` / `docker compose up -d` |
| Cek status | `docker compose ps` |

Data `store.json` (watchlist/alert/paper-trading) disimpan di folder `data/`
di dalam VPS — kebal terhadap rebuild container.

## Varian slim (RAM kecil / HDD kecil)

```bash
docker compose build --build-arg SKIP_MODEL=1 && docker compose up -d
```
Semua fitur tetap jalan kecuali sentimen ML → otomatis jatuh ke lexicon.

## Alternatif tanpa Docker (systemd)

```bash
sudo apt install -y python3-pip python3-venv
sudo mkdir -p /opt/saham-bot && sudo chown $USER /opt/saham-bot
git clone https://github.com/NAMA-KAMU/saham-bot.git /opt/saham-bot
cd /opt/saham-bot
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
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
ExecStart=/opt/saham-bot/.venv/bin/python bot.py
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
 .dockerignore & kali ini untuk chunk 4 MB anti-putus)
- 📊 Data BEI (`/bandar`) kadang diblokir Cloudflare saat bot ditempatkan di
  IP datacenter — itu normal, bot kasih pesan jujur + alternatif `/analisis`
- ⚠️ Bukan saran investasi — DYOR senantiasa
