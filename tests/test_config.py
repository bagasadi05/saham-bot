"""Test pemuatan .env di config.py."""
from __future__ import annotations

import os

import config


def test_load_dotenv_mengisi_variabel_baru(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# komentar\n"
        "SAHAM_TEST_BARU=nilai-baru\n"
        'SAHAM_TEST_KUTIP="nilai berkutip"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("SAHAM_TEST_BARU", raising=False)
    monkeypatch.delenv("SAHAM_TEST_KUTIP", raising=False)

    config.load_dotenv(str(env))

    assert os.environ["SAHAM_TEST_BARU"] == "nilai-baru"
    assert os.environ["SAHAM_TEST_KUTIP"] == "nilai berkutip"


def test_load_dotenv_tidak_menimpa_env_yang_sudah_ada(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("SAHAM_TEST_TETAP=dari-file\n", encoding="utf-8")
    monkeypatch.setenv("SAHAM_TEST_TETAP", "dari-env")

    config.load_dotenv(str(env))

    assert os.environ["SAHAM_TEST_TETAP"] == "dari-env"


def test_load_dotenv_file_tidak_ada_tidak_crash(tmp_path):
    config.load_dotenv(str(tmp_path / "tidak-ada.env"))


def test_load_dotenv_tahan_file_rusak(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes(b"\xff\xfe\x00rusak")
    config.load_dotenv(str(env))  # tidak boleh melempar


def test_placeholder_token_dianggap_belum_diset():
    assert config.clean_token("tempel-token-dari-botfather") == ""
    assert config.clean_token("  123456:ABC-DEF  ") == "123456:ABC-DEF"
    assert config.clean_token(None) == ""
