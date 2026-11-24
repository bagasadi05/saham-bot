"""Build skrip untuk memaketkan SahamBot Desktop menjadi aplikasi Windows (.exe).

Pakai:
    .venv\\Scripts\\python.exe tools\\build_desktop.py
Output:
    dist\\SahamBot\\SahamBot.exe  (ringan tanpa PyTorch/Transformers)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import desktop  # noqa: E402


def main() -> int:
    icon_dir = ROOT / "temp"
    icon_dir.mkdir(parents=True, exist_ok=True)
    icon_path = icon_dir / "sahambot.ico"
    img = desktop.create_tray_icon_image(256)
    img.save(icon_path, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)])

    excludes = [
        "torch",
        "transformers",
        "sentence_transformers",
        "tensorflow",
        "keras",
        "scipy",
        "lightgbm",
        "pytest",
        "ruff",
        "IPython",
        "jupyter",
        "notebook",
        "telegram",
    ]
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name",
        "SahamBot",
        "--icon",
        str(icon_path),
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(ROOT / "build"),
        "--specpath",
        str(ROOT / "temp"),
        "--add-data",
        f"{ROOT / 'data'};data",
        "--hidden-import",
        "pystray._win32",
    ]
    for ex in excludes:
        cmd.extend(["--exclude-module", ex])
    cmd.append(str(ROOT / "desktop.py"))

    print("Menjalankan PyInstaller:", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(ROOT))
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
