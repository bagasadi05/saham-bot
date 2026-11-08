#!/usr/bin/env bash
# Supervisor SahamBuddy: jalankan bot, nyalakan lagi otomatis kalau mati/crash.
#
# Dipakai:  ./run-supervised.sh                     (foreground)
#           nohup ./run-supervised.sh >> bot.log 2>&1 &   (background)
# Berhenti: pkill -f run-supervised.sh  (lalu pkill -f "bot.py")
#
# Yang ditangani: crash (mis. putusnya koneksi Telegram — 2x dalam 1 jam
# pada 2026-11-06). Yang TIDAK ditangani: VM restart — setelah VM restart,
# jalankan lagi secara manual. run.sh tidak diubah (tetap milik repo).
set -u
cd "$(dirname "$0")"

delay=5
max_delay=60

while true; do
    start=$(date +%s)
    echo "$(date '+%Y-%m-%d %H:%M:%S') supervisor: menjalankan bot (./run.sh)" >> bot.log
    ./run.sh >> bot.log 2>&1
    code=$?
    uptime=$(( $(date +%s) - start ))
    echo "$(date '+%Y-%m-%d %H:%M:%S') supervisor: bot berhenti (exit=$code, uptime=${uptime}s), restart dalam ${delay}s" >> bot.log
    sleep "$delay"
    if [ "$uptime" -lt 60 ]; then
        # Crash cepat beruntun (mis. token salah / kode rusak): naikkan jeda
        # supaya tidak spam restart — 5 -> 10 -> 20 -> 40 -> 60 detik (cap).
        delay=$(( delay * 2 ))
        [ "$delay" -gt "$max_delay" ] && delay=$max_delay
    else
        delay=5
    fi
done
