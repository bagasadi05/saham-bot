"""Audit konsistensi offline riset, universe LQ45, umur data, dan backup SQLite.

Cek yang dijalankan (100% offline, aman dijalankan di CI tanpa BOT_TOKEN/jaringan):
  a) Keselarasan WEIGHTS & OOS_QUINTILES di `core/relatif.py` vs `data/validation.json`
  b) Kelengkapan seluruh emiten `core.saran.LQ45` di `data/idx_universe.json`
  c) Umur `data/idx_universe.json` dan `data/validation.json` (warning jika stale)
  d) Round-trip backup -> restore SQLite pada direktori temporer memakai `tools/backup_db.py`

Pakai:
    python tools/check_sync.py
    python tools/check_sync.py --max-universe-days 30 --max-validation-days 60
    python tools/check_sync.py --json
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import marketcal, relatif, saran, store  # noqa: E402
from tools import backup_db  # noqa: E402

DEFAULT_VALIDATION = ROOT / "data" / "validation.json"
DEFAULT_UNIVERSE = ROOT / "data" / "idx_universe.json"


def _norm_ticker(code: str) -> str:
    return str(code or "").strip().upper().removesuffix(".JK")


def _parse_iso_dt(raw: object) -> dt.datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        try:
            d = dt.date.fromisoformat(text[:10])
            return dt.datetime(d.year, d.month, d.day, tzinfo=marketcal.WIB)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=marketcal.WIB)
    return parsed.astimezone(marketcal.WIB)


def check_weights_and_quintiles(
    validation_path: Path | str = DEFAULT_VALIDATION,
) -> dict:
    """(a) Verifikasi keselarasan WEIGHTS & OOS_QUINTILES vs validation.json."""
    path = Path(validation_path)
    errors: list[str] = []
    warnings: list[str] = []
    if not path.exists():
        return {
            "name": "relatif_sync",
            "ok": False,
            "errors": [f"file tidak ditemukan: {path}"],
            "warnings": [],
        }
    try:
        val = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {
            "name": "relatif_sync",
            "ok": False,
            "errors": [f"gagal membaca {path}: {exc}"],
            "warnings": [],
        }

    val_weights_raw = val.get("weights") or {}
    if not val_weights_raw:
        errors.append("bagian `weights` di validation.json kosong")
    else:
        # validation.json memakai prefiks `f_` (dari dataset riset), core/relatif.py tanpa `f_`
        val_weights = {str(k).removeprefix("f_"): float(v) for k, v in val_weights_raw.items()}
        val_keys = set(val_weights)
        code_keys = set(relatif.WEIGHTS)
        if val_keys != code_keys:
            errors.append(
                f"kunci WEIGHTS berbeda: validation-only={sorted(val_keys - code_keys)}, "
                f"code-only={sorted(code_keys - val_keys)}"
            )
        for k in sorted(val_keys & code_keys):
            v_json = val_weights[k]
            v_code = float(relatif.WEIGHTS[k])
            if abs(v_json - v_code) > 1e-9:
                errors.append(
                    f"bobot {k} tidak selaras: core/relatif.py={v_code} vs json={v_json}"
                )

    val_quints = {
        int(row["quintile"]): row
        for row in (val.get("quintiles") or [])
        if "quintile" in row
    }
    if set(val_quints) != {1, 2, 3, 4, 5}:
        errors.append(f"quintiles validation.json tidak lengkap 1..5: {sorted(val_quints)}")
    if set(relatif.OOS_QUINTILES) != {1, 2, 3, 4, 5}:
        errors.append(
            f"OOS_QUINTILES di core/relatif.py tidak lengkap 1..5: "
            f"{sorted(relatif.OOS_QUINTILES)}"
        )

    loaded_quints = relatif.load_oos_quintiles(str(path))
    for q in (1, 2, 3, 4, 5):
        jq = val_quints.get(q)
        cq = relatif.OOS_QUINTILES.get(q)
        lq = loaded_quints.get(q)
        if jq and cq:
            for field in ("n", "excess_pct", "win_rate"):
                jv = jq.get(field)
                cv = cq.get(field)
                if jv is None or cv is None or abs(float(jv) - float(cv)) > 1e-6:
                    errors.append(
                        f"Q{q}.{field} tidak selaras: core/relatif.py={cv} vs json={jv}"
                    )
            for opt in ("median_pct", "p25_pct", "p75_pct", "iqr_pct"):
                if opt in jq and jq[opt] is not None and (
                    not lq or lq.get(opt) is None or abs(float(jq[opt]) - float(lq[opt])) > 1e-6
                ):
                    errors.append(f"Q{q}.{opt} gagal dimuat load_oos_quintiles")

    has_iqr = all(
        all(val_quints.get(q, {}).get(f) is not None for f in ("p25_pct", "p75_pct", "iqr_pct"))
        for q in (1, 2, 3, 4, 5)
    )
    if not has_iqr:
        warnings.append(
            "pita IQR P25-P75 Q1..Q5 belum ada di data/validation.json "
            "(akan terisi otomatis saat run research/evaluate.py + publish.py berikutnya)"
        )

    return {
        "name": "relatif_sync",
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "details": {
            "n_weights": len(relatif.WEIGHTS),
            "n_quintiles": len(val_quints),
            "has_iqr_bands": has_iqr,
        },
    }


def check_lq45_in_universe(
    universe_path: Path | str = DEFAULT_UNIVERSE,
) -> dict:
    """(b) Pastikan seluruh konstituen core.saran.LQ45 ada di data/idx_universe.json."""
    path = Path(universe_path)
    errors: list[str] = []
    warnings: list[str] = []
    if not path.exists():
        return {
            "name": "lq45_universe",
            "ok": False,
            "errors": [f"file universe tidak ditemukan: {path}"],
            "warnings": [],
        }
    try:
        uni = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {
            "name": "lq45_universe",
            "ok": False,
            "errors": [f"gagal membaca {path}: {exc}"],
            "warnings": [],
        }

    stocks = uni.get("stocks") or []
    if not stocks:
        errors.append("daftar `stocks` di idx_universe.json kosong")

    universe_codes = {
        _norm_ticker(item.get("code") if isinstance(item, dict) else str(item))
        for item in stocks
    }
    universe_codes.discard("")
    lq45_codes = [_norm_ticker(t) for t in saran.LQ45]
    missing = [t for t in lq45_codes if t not in universe_codes]
    if missing:
        errors.append(f"emiten LQ45 tidak ada di idx_universe.json: {missing}")

    declared_count = uni.get("count")
    if isinstance(declared_count, int) and declared_count != len(stocks):
        warnings.append(
            f"idx_universe.json count={declared_count} berbeda dengan len(stocks)={len(stocks)}"
        )

    return {
        "name": "lq45_universe",
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "details": {
            "lq45_count": len(lq45_codes),
            "universe_count": len(universe_codes),
            "missing": missing,
        },
    }


def check_data_freshness(
    universe_path: Path | str = DEFAULT_UNIVERSE,
    validation_path: Path | str = DEFAULT_VALIDATION,
    *,
    max_universe_days: int = 30,
    max_validation_days: int = 60,
    strict_age: bool = False,
    now: dt.datetime | None = None,
) -> dict:
    """(c) Audit umur data/idx_universe.json dan data/validation.json."""
    ref_now = now or marketcal.now_wib()
    if ref_now.tzinfo is None:
        ref_now = ref_now.replace(tzinfo=marketcal.WIB)
    else:
        ref_now = ref_now.astimezone(marketcal.WIB)

    errors: list[str] = []
    warnings: list[str] = []
    ages: dict[str, float | None] = {}

    targets = (
        ("idx_universe", Path(universe_path), ("fetched_at",), max_universe_days),
        ("validation", Path(validation_path), ("meta.generated", "meta.published_at"),
         max_validation_days),
    )

    for label, path, fields, max_days in targets:
        if not path.exists():
            errors.append(f"file {path} tidak ada")
            ages[label] = None
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"gagal parse {path}: {exc}")
            ages[label] = None
            continue

        ts_raw = None
        for fpath in fields:
            cur = payload
            for part in fpath.split("."):
                cur = cur.get(part) if isinstance(cur, dict) else None
            if cur:
                ts_raw = cur
                break
        parsed = _parse_iso_dt(ts_raw)
        if parsed is None:
            msg = f"timestamp {label} ({fields}) tidak tersedia atau tidak valid: {ts_raw!r}"
            if strict_age:
                errors.append(msg)
            else:
                warnings.append(msg)
            ages[label] = None
            continue

        age_days = (ref_now - parsed).total_seconds() / 86400.0
        ages[label] = round(age_days, 1)
        if age_days > max_days:
            msg = (
                f"{label} berumur {age_days:.1f} hari (> ambang {max_days} hari; "
                f"timestamp={parsed.isoformat(timespec='seconds')})"
            )
            if strict_age:
                errors.append(msg)
            else:
                warnings.append(msg)
        elif age_days < -1.0:
            warnings.append(
                f"timestamp {label} ({parsed.isoformat(timespec='seconds')}) "
                f"lebih baru {-age_days:.1f} hari dari jam sistem saat ini"
            )

    return {
        "name": "data_freshness",
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "details": {
            "age_days": ages,
            "max_universe_days": max_universe_days,
            "max_validation_days": max_validation_days,
            "strict_age": strict_age,
        },
    }


def check_backup_restore_roundtrip() -> dict:
    """(d) Uji round-trip backup -> restore SQLite di direktori temporer."""
    errors: list[str] = []
    warnings: list[str] = []
    details: dict[str, object] = {}

    orig_db = store.STORE_DB
    orig_file = store.STORE_FILE
    orig_ready = store._ready_for
    try:
        with tempfile.TemporaryDirectory(prefix="sahambot_sync_db_") as tmp:
            root = Path(tmp)
            src_db = root / "source.db"
            src_json = root / "source.json"
            backup_dir = root / "backups"
            restored_db = root / "restored.db"
            restored_json = root / "restored.json"

            store.STORE_DB = str(src_db)
            store.STORE_FILE = str(src_json)
            store._ready_for = None

            store.watchlist_add(9001, "BBCA")
            store.watchlist_add(9001, "BMRI")
            store.set_modal(9001, 25_000_000.0)

            with contextlib.redirect_stdout(io.StringIO()):
                rc = backup_db.main([str(backup_dir), "--keep", "3"])
            if rc != 0:
                errors.append(f"tools.backup_db.main mengembalikan kode {rc}")
                return {
                    "name": "backup_restore_roundtrip",
                    "ok": False,
                    "errors": errors,
                    "warnings": warnings,
                    "details": details,
                }

            backups = sorted(backup_dir.glob("store-*.db"))
            if not backups:
                errors.append("backup_db tidak menghasilkan file store-*.db")
                return {
                    "name": "backup_restore_roundtrip",
                    "ok": False,
                    "errors": errors,
                    "warnings": warnings,
                    "details": details,
                }

            bak = backups[-1]
            with contextlib.closing(sqlite3.connect(bak)) as conn:
                integrity = conn.execute("PRAGMA integrity_check").fetchone()
                version = conn.execute("PRAGMA user_version").fetchone()
            if not integrity or integrity[0] != "ok":
                errors.append(f"PRAGMA integrity_check backup gagal: {integrity}")
            if not version or int(version[0]) != store.SCHEMA_VERSION:
                errors.append(
                    f"user_version backup {version} != SCHEMA_VERSION {store.SCHEMA_VERSION}"
                )

            shutil.copy2(bak, restored_db)
            store.STORE_DB = str(restored_db)
            store.STORE_FILE = str(restored_json)
            store._ready_for = None

            wl = store.get_watchlist(9001)
            modal = store.get_modal(9001)
            if sorted(wl) != ["BBCA", "BMRI"]:
                errors.append(f"watchlist hasil restore tidak cocok: {wl}")
            if abs(float(modal) - 25_000_000.0) > 1e-6:
                errors.append(f"modal hasil restore tidak cocok: {modal}")

            details = {
                "backup_bytes": int(os.path.getsize(bak)),
                "schema_version": store.SCHEMA_VERSION,
                "restored_watchlist": wl,
                "restored_modal": modal,
            }
    except Exception as exc:  # noqa: BLE001
        errors.append(f"exception saat round-trip backup/restore: {exc}")
    finally:
        store.STORE_DB = orig_db
        store.STORE_FILE = orig_file
        store._ready_for = orig_ready

    return {
        "name": "backup_restore_roundtrip",
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "details": details,
    }


def run_all(
    *,
    validation_path: Path | str = DEFAULT_VALIDATION,
    universe_path: Path | str = DEFAULT_UNIVERSE,
    max_universe_days: int = 30,
    max_validation_days: int = 60,
    strict_age: bool = False,
    now: dt.datetime | None = None,
) -> dict:
    """Jalankan keempat audit sinkronisasi offline dan kembalikan ringkasan terstruktur."""
    checks = [
        check_weights_and_quintiles(validation_path),
        check_lq45_in_universe(universe_path),
        check_data_freshness(
            universe_path,
            validation_path,
            max_universe_days=max_universe_days,
            max_validation_days=max_validation_days,
            strict_age=strict_age,
            now=now,
        ),
        check_backup_restore_roundtrip(),
    ]
    return {
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--validation", default=str(DEFAULT_VALIDATION))
    ap.add_argument("--universe", default=str(DEFAULT_UNIVERSE))
    ap.add_argument("--max-universe-days", type=int, default=30)
    ap.add_argument("--max-validation-days", type=int, default=60)
    ap.add_argument("--strict-age", action="store_true",
                    help="gagalkan exit code bila data stale melewati ambang")
    ap.add_argument("--json", action="store_true", help="cetak hasil sebagai JSON")
    args = ap.parse_args(argv)

    res = run_all(
        validation_path=args.validation,
        universe_path=args.universe,
        max_universe_days=args.max_universe_days,
        max_validation_days=args.max_validation_days,
        strict_age=args.strict_age,
    )
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        for c in res["checks"]:
            tag = "OK" if c["ok"] else "FAIL"
            print(f"[{tag}] {c['name']}")
            for w in c.get("warnings") or []:
                print(f"  WARN: {w}")
            for e in c.get("errors") or []:
                print(f"  ERR : {e}")
        print(f"Ringkasan check_sync: {'PASS' if res['ok'] else 'FAIL'}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
