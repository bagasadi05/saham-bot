#!/usr/bin/env python3
"""Unduh model sentimen indo-roBERTa finansial (HuggingFace) → folder lokal.
Dipakai saat build Docker. Tahan jaringan resek: resume per-chunk + retry."""
import os
import sys
import time

BASE = "https://huggingface.co/will702/indo-roBERTa-financial-sentiment-v2/resolve/main"
DEST = os.environ.get("SAHAMBOT_LOCAL_MODEL", "/app/models/indo-fin-roberta")
CHUNK = int(os.environ.get("DOWNLOAD_CHUNK", "4")) * 1024 * 1024
FILES = ["config.json", "tokenizer_config.json", "special_tokens_map.json",
         "tokenizer.json", "vocab.json", "merges.txt", "model.safetensors"]
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 | saham-bot-deploy"


def fetch(url: str, out: str, tries: int = 60):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    part = out + ".part"
    # ukuran total
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    total = 0
    for t in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                total = int(r.headers.get("Content-Length", 0))
            break
        except Exception as e:
            print(f"  HEAD gagal ({e}) coba {t+1}", flush=True)
            time.sleep(3)
    if not total:
        raise SystemExit(f"gagal ukur {url}")
    failures = 0
    while failures < tries:
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if have >= total:
            os.replace(part, out)
            print(f"  ✓ {os.path.basename(out)} ({total:,} B)", flush=True)
            return
        end = min(have + CHUNK, total) - 1
        rreq = urllib.request.Request(
            url, headers={"User-Agent": UA, "Range": f"bytes={have}-{end}"})
        try:
            wrote = 0
            with urllib.request.urlopen(rreq, timeout=120) as r, open(part, "ab") as f:
                while True:
                    b = r.read(1024 * 256)
                    if not b:
                        break
                    f.write(b)
                    wrote += len(b)
            if wrote == 0:
                failures += 1
                time.sleep(2)
            else:
                failures = 0
        except Exception as e:
            failures += 1
            print(f"  chunk putus di {os.path.getsize(part) if os.path.exists(part) else 0:,} ({e})", flush=True)
            time.sleep(2)
    # resume terakhir: kalau sudah >= total, finalkan
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if have >= total:
        os.replace(part, out)
        print(f"  ✓ {os.path.basename(out)}", flush=True)
    else:
        raise SystemExit(f"unduhan tidak tuntas: {os.path.basename(out)} {have:,}/{total:,}")


def main():
    # arg: file tunggal (dipakai juga untuk resume manual)
    targets = [(f"{BASE}/{f}", os.path.join(DEST, f)) for f in FILES]
    if len(sys.argv) == 3:
        targets = [(sys.argv[1], sys.argv[2])]
    for url, out in targets:
        if os.path.exists(out) and os.path.getsize(out) > 0:
            print(f"  = {os.path.basename(out)} sudah ada", flush=True)
            continue
        print(f"unduh {os.path.basename(out)} ...", flush=True)
        fetch(url, out)
    print("model siap di", DEST, flush=True)


if __name__ == "__main__":
    main()
