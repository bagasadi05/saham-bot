"""Job gagal harus berteriak ke admin dan tercatat untuk /status."""
from __future__ import annotations

import asyncio

import bot
from handlers import info


class _App:
    pass


def test_job_guard_alerts_admin_and_records_failure(monkeypatch):
    sent: list[str] = []

    async def fake_notify(app, text):
        sent.append(text)
        return True

    monkeypatch.setattr(bot, "notify_admin", fake_notify)
    bot.JOB_STATUS.clear()

    @bot.job_guard("demo-gagal")
    async def boom(app):
        raise RuntimeError("disk penuh")

    assert asyncio.run(boom(_App())) is None

    st = bot.JOB_STATUS["demo-gagal"]
    assert st["fails"] == 1
    assert "disk penuh" in st["last_error"]
    assert sent and "demo-gagal" in sent[0] and "disk penuh" in sent[0]


def test_job_guard_success_marks_last_ok(monkeypatch):
    bot.JOB_STATUS.clear()

    @bot.job_guard("demo-ok")
    async def ok(app):
        return 42

    assert asyncio.run(ok(_App())) == 42

    st = bot.JOB_STATUS["demo-ok"]
    assert st["runs"] == 1 and st["fails"] == 0
    assert st["last_ok"] and not st["last_error"]


def test_jobs_terjadwal_dibungkus_guard():
    for fn in (bot.morning_scan, bot.fill_open, bot.scan_alerts,
               bot.backup_job, bot.full_market_scan_job,
               bot.weekly_journal_report):
        assert hasattr(fn, "__wrapped__"), fn.__name__


def test_jobs_status_line_jujur():
    assert info.jobs_status_line({}) == ""
    assert "tidak ada kegagalan" in info.jobs_status_line(
        {"a": {"fails": 0, "last_error": ""}})
    line = info.jobs_status_line({"a": {"fails": 2, "last_error": "NetworkError"}})
    assert "Job bermasalah" in line and "2x" in line and "NetworkError" in line
