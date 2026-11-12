"""Tes daftar universe emiten BEI + skrip pembarunya (offline)."""
from __future__ import annotations

import json
import re

from core import universe
from tools import build_universe as bu


def test_universe_snapshot_is_valid():
    codes = universe.codes()
    assert len(codes) >= 800
    assert len(codes) == len(set(codes))
    assert all(re.fullmatch(r"[A-Z]{4}", c) for c in codes)
    assert {"BBCA", "BBRI", "TLKM", "ASII", "AADI"} <= set(codes)
    assert universe.name_of("BBCA")
    assert universe.board_of("BBCA")
    meta = universe.meta()
    assert meta["count"] == len(codes)
    assert meta["source"]


def test_unknown_ticker_is_empty_not_invented():
    assert universe.name_of("ZZZZ") == ""
    assert universe.board_of("ZZZZ") == ""


def test_parse_csv_and_clean_dedupes_and_rejects_bad_codes():
    rows = bu.parse_csv(
        "code,name,listingBoard\n"
        "BBCA,Bank Central Asia Tbk.,Utama\n"
        "bad,Kode Salah,Utama\n"
        "BBCA,Duplikat,Utama\n"
        "TLKM,Telkom Tbk.,Utama\n")
    cleaned = bu.clean(rows)
    assert cleaned == [
        {"code": "BBCA", "name": "Bank Central Asia Tbk.", "board": "Utama"},
        {"code": "TLKM", "name": "Telkom Tbk.", "board": "Utama"},
    ]


def test_parse_idx_json_accepts_official_field_names():
    rows = bu.parse_idx_json(json.dumps({"data": [
        {"KodeEmiten": "ANTM", "NamaEmiten": "Aneka Tambang Tbk.",
         "PapanPencatatan": "Utama"},
        {"Code": "BBCA", "Name": "Bank Central Asia Tbk.",
         "ListingBoard": "Utama"},
    ]}))
    assert rows == [
        {"code": "ANTM", "name": "Aneka Tambang Tbk.", "board": "Utama"},
        {"code": "BBCA", "name": "Bank Central Asia Tbk.", "board": "Utama"},
    ]


def test_build_refuses_to_overwrite_with_too_few_codes(tmp_path, monkeypatch):
    out = tmp_path / "idx_universe.json"
    out.write_text('{"count": 900}', encoding="utf-8")
    monkeypatch.setattr(bu, "fetch_id_official",
                        lambda: ([{"code": "BBCA", "name": "x",
                                   "board": "Utama"}], "uji"))
    assert bu.main(["--out", str(out)]) == 1
    assert out.read_text(encoding="utf-8") == '{"count": 900}'


def test_build_writes_metadata_and_sorted_codes(tmp_path, monkeypatch):
    out = tmp_path / "idx_universe.json"
    rows = [{"code": c, "name": c, "board": "Utama"}
            for c in ("TLKM", "BBCA", "ASII")]
    rows += [{"code": f"X{chr(65 + i // 676)}{chr(65 + (i // 26) % 26)}"
                      f"{chr(65 + i % 26)}", "name": "x", "board": "Utama"}
             for i in range(900)]
    monkeypatch.setattr(bu, "fetch_fallback", lambda: (rows, "uji"))
    monkeypatch.setattr(bu, "fetch_id_official", lambda: ([], ""))
    assert bu.main(["--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["count"] == len(data["stocks"])
    assert data["source"] == "uji"
    codes = [s["code"] for s in data["stocks"]]
    assert codes == sorted(codes)
