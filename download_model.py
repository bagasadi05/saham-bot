#!/usr/bin/env python3
"""Unduh model sentimen indo-roBERTa finansial (HuggingFace) → folder lokal.
Dipakai saat build Docker. Tahan jaringan resek: resume per-chunk + retry."""
import hashlib
import os
import sys
import time

MODEL_REVISION = os.environ.get("SAHAMBOT_MODEL_REVISION", "").strip() or "main"
BASE = f"https://huggingface.co/will702/indo-roBERTa-financial-sentiment-v2/resolve/{MODEL_REVISION}"
_DEFAULT_DEST = (
    "/app/models/indo-fin-roberta"
    if os.path.isdir("/app")
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "indo-fin-roberta")
)
DEST = os.environ.get("SAHAMBOT_LOCAL_MODEL", _DEFAULT_DEST)
CHUNK = int(os.environ.get("DOWNLOAD_CHUNK", "4")) * 1024 * 1024
FILES = ["config.json", "tokenizer_config.json", "special_tokens_map.json",
         "tokenizer.json", "vocab.json", "merges.txt", "model.safetensors"]

EXPECTED_SHA256: dict[str, str] = {
    "config.json": "47578f5ebb8ca5140b1440172411e21fa8ff3a9c96306c0ec6e5fd86a3bc108b",
    "merges.txt": "8557587ff36ebe40c0bafea1e7f6afe4e9033b87e6eff7da54c91acb7fa8f45a",
    "model.safetensors": "00e94809dd0f1288bbe0c66c887b1769bb7028f612d98fbe8957167811636f4c",
    "special_tokens_map.json": "8c785abebea9ae3257b61681b4e6fd8365ceafde980c21970d001e834cf10835",
    "tokenizer.json": "ec8faafdb01f96fca946efbfa82a71f5c2c803b5413e8ef8f4690cbd8626176f",
    "tokenizer_config.json": "e243d25a6773518bf621b7b6da0a94441efb3a4a92b8c1d4e71c9eebb624f549",
    "vocab.json": "7f0d797638915eaae6540a8141e247921b130ead34b03aa0b646a8fc17750972",
}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_sha256(path: str, expected: str | None = None) -> bool:
    exp = expected if expected is not None else EXPECTED_SHA256.get(os.path.basename(path))
    if not exp:
        return True
    return sha256_file(path) == exp

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 | saham-bot-deploy"


def _finalize_file(part: str, out: str, expected_sha256: str | None = None) -> None:
    os.replace(part, out)
    if expected_sha256 and not verify_sha256(out, expected_sha256):
        try:
            os.remove(out)
        except OSError:
            pass
        raise SystemExit(
            f"checksum SHA-256 tidak cocok untuk {os.path.basename(out)} — file korup dihapus"
        )


def fetch(url: str, out: str, tries: int = 60, *, expected_sha256: str | None = None):
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
            _finalize_file(part, out, expected_sha256)
            print(f"  ✓ {os.path.basename(out)} ({total:,} B)", flush=True)
            return
        end = min(have + CHUNK, total) - 1
        rreq = urllib.request.Request(
            url, headers={"User-Agent": UA, "Range": f"bytes={have}-{end}"})
        try:
            wrote = 0
            with urllib.request.urlopen(rreq, timeout=120) as r:
                status = getattr(r, "status", None)
                if status is not None and status != 206:
                    # Server mengabaikan header Range dan mengirim body penuh
                    # dari offset 0. Mode "ab" akan menggandakan byte -> model
                    # korup. Mulai ulang dari nol.
                    print(f"  server mengabaikan Range (HTTP {status}) — "
                          f"mulai ulang {os.path.basename(out)}", flush=True)
                    mode = "wb"
                else:
                    mode = "ab"
                with open(part, mode) as f:
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
        _finalize_file(part, out, expected_sha256)
        print(f"  ✓ {os.path.basename(out)}", flush=True)
    else:
        raise SystemExit(f"unduhan tidak tuntas: {os.path.basename(out)} {have:,}/{total:,}")


def main():
    # arg: file tunggal (dipakai juga untuk resume manual)
    targets = [(f"{BASE}/{f}", os.path.join(DEST, f)) for f in FILES]
    if len(sys.argv) == 3:
        targets = [(sys.argv[1], sys.argv[2])]
    for url, out in targets:
        exp_hash = EXPECTED_SHA256.get(os.path.basename(out))
        if os.path.exists(out) and os.path.getsize(out) > 0:
            if verify_sha256(out, exp_hash):
                print(f"  = {os.path.basename(out)} sudah ada (SHA-256 valid)", flush=True)
                continue
            print(f"  ! {os.path.basename(out)} gagal verifikasi SHA-256 — unduh ulang", flush=True)
            os.remove(out)
        print(f"unduh {os.path.basename(out)} ...", flush=True)
        fetch(url, out, expected_sha256=exp_hash)
    print("model siap di", DEST, flush=True)


if __name__ == "__main__":
    main()
