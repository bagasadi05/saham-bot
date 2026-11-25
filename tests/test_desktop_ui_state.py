"""Tes offline untuk persistensi state sesi UI aplikasi desktop (`core/store_profile.desktop_ui_state`)."""
from __future__ import annotations

import json

import desktop  # noqa: F401  # import pertama: menarik rantai core.store dengan urutan benar
from core import store_profile


def test_clamp_font_scale_bounds_nan_and_invalid():
    assert store_profile.clamp_font_scale(None) == 1.0
    assert store_profile.clamp_font_scale("abc") == 1.0
    assert store_profile.clamp_font_scale(float("nan")) == 1.0
    assert store_profile.clamp_font_scale(0.1) == store_profile.FONT_SCALE_MIN
    assert store_profile.clamp_font_scale(99) == store_profile.FONT_SCALE_MAX
    assert store_profile.clamp_font_scale(1.25) == 1.25


def test_normalize_recent_tickers_upper_dedup_ascii_limit():
    raw = ["bbca.jk", " TLKM ", "BBCA", "?", "ASII", 123, None, " go-to "]
    out = store_profile.normalize_recent_tickers(raw)
    assert out == ["BBCA", "TLKM", "ASII", "123", "GOTO"]
    assert store_profile.normalize_recent_tickers("bukan-list") == []
    many = store_profile.normalize_recent_tickers([f"T{i}" for i in range(50)])
    assert len(many) == store_profile.RECENT_TICKERS_MAX


# ---------------------------------------------------------------------------
# store_profile: encode/decode ui state
# ---------------------------------------------------------------------------

def test_ui_state_roundtrip_sanitizes_values():
    state = {
        "geometry": "1200x800+50+50",
        "tab_index": 99,
        "refresh": "5m",
        "toast": False,
        "font_scale": 9,
        "recent_tickers": ["bbca", "bbca.jk", "tlkm"],
        "kunci_asing": "diabaikan",
    }
    dec = store_profile.decode_ui_state(store_profile.encode_ui_state(state))
    assert dec["geometry"] == "1200x800+50+50"
    assert dec["tab_index"] == store_profile.DESKTOP_TAB_MAX
    assert dec["refresh"] == "5m"
    assert dec["toast"] is False
    assert dec["font_scale"] == store_profile.FONT_SCALE_MAX
    assert dec["recent_tickers"] == ["BBCA", "TLKM"]
    assert "kunci_asing" not in dec


def test_decode_ui_state_aman_terhadap_input_rusak():
    default = store_profile.decode_ui_state(None)
    assert default["tab_index"] == 0 and default["refresh"] == "15m"
    assert store_profile.decode_ui_state("bukan json")["tab_index"] == 0
    assert store_profile.decode_ui_state(json.dumps([1, 2]))["refresh"] == "15m"
    assert store_profile.decode_ui_state(json.dumps({"geometry": "rusak"}))["geometry"] == ""
    assert store_profile.decode_ui_state(json.dumps({"refresh": "1h"}))["refresh"] == "15m"
    assert store_profile.decode_ui_state(json.dumps({"tab_index": "x"}))["tab_index"] == 0
    assert store_profile.decode_ui_state(json.dumps({"toast": 0}))["toast"] is False


def test_get_set_desktop_ui_state_merge_dan_clamp(tmp_store):
    cid = "desktop-test"
    awal = store_profile.get_desktop_ui_state(cid)
    assert awal["tab_index"] == 0 and awal["refresh"] == "15m" and awal["toast"] is True

    st = store_profile.set_desktop_ui_state(
        cid, tab_index=3, refresh="30m", toast=False, recent_tickers=["bbca.jk", "bbri"],
    )
    assert st["tab_index"] == 3 and st["refresh"] == "30m" and st["toast"] is False
    assert st["recent_tickers"] == ["BBCA", "BBRI"]

    # merge: field lama dipertahankan, pembacaan ulang dari DB konsisten
    st2 = store_profile.set_desktop_ui_state(cid, font_scale=1.2)
    assert st2["tab_index"] == 3 and st2["font_scale"] == 1.2
    assert store_profile.get_desktop_ui_state(cid) == st2

    # nilai invalid di-clamp saat set
    st3 = store_profile.set_desktop_ui_state(cid, tab_index=999, font_scale=-5)
    assert st3["tab_index"] == store_profile.DESKTOP_TAB_MAX
    assert st3["font_scale"] == store_profile.FONT_SCALE_MIN


