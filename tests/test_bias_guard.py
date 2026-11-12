"""research/bias.py harus gagal keras tanpa keanggotaan universe."""
from __future__ import annotations

import json

import pytest

from research import bias


def test_dry_run_without_membership_fails(capsys):
    assert bias.main(["--dry-run"]) == 2
    err = capsys.readouterr().err.lower()
    assert "membership" in err


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        bias.load_membership("file-yang-tidak-ada.json")


def test_valid_membership_is_loaded(tmp_path, capsys):
    path = tmp_path / "membership.json"
    path.write_text(json.dumps({
        "membership": [
            {"ticker": "bbca.jk", "start": "2020-01-01", "end": "2024-12-31"},
        ]
    }), encoding="utf-8")

    rows = bias.load_membership(str(path))
    assert rows[0]["ticker"] == "BBCA"

    assert bias.main(["--membership", str(path), "--dry-run"]) == 0
    assert "1 baris" in capsys.readouterr().out


def test_invalid_shape_is_rejected(tmp_path):
    path = tmp_path / "salah.json"
    path.write_text(json.dumps({"foo": 1}), encoding="utf-8")
    with pytest.raises(ValueError):
        bias.load_membership(str(path))


def test_row_without_dates_is_rejected(tmp_path):
    path = tmp_path / "kurang.json"
    path.write_text(json.dumps([{"ticker": "BBCA"}]), encoding="utf-8")
    with pytest.raises(ValueError):
        bias.load_membership(str(path))
