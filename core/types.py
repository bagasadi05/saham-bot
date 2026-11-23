"""Kontrak tipe data inti (TypedDict) untuk modul SahamBuddy.

Dipakai sebagai referensi skema payload antar-modul (`analysis`, `saran`,
`store`, `news_ml`, `tesis`) agar pemeriksaan statis dan refactor lebih aman.
"""
from __future__ import annotations

from typing import TypedDict


class IndicatorSnapshot(TypedDict, total=False):
    ticker: str
    date: str
    close: float
    prev_close: float
    chg_pct: float
    volume: float
    vol_ratio: float
    rsi: float | None
    macd: float | None
    macd_sig: float | None
    macd_hist: float | None
    ma20: float | None
    ma50: float | None
    ma200: float | None
    atr: float | None
    score: int
    score_pct: float
    rating: str


class SaranCandidate(TypedDict, total=False):
    ticker: str
    price: float
    entry: float
    sl: float
    tp1: float
    tp2: float
    lots: int
    shares: int
    cost: float
    rr: float
    rel_pct: float | None
    score_pct: float
    signal: str


class RealPosition(TypedDict, total=False):
    ticker: str
    lots: int
    avg_price: float
    updated_at: str


class ThesisState(TypedDict, total=False):
    ticker: str
    status: str
    score: int
    reasons: list[str]
    updated_at: str


class NewsSentimentResult(TypedDict):
    label: str
    score: float
    probs: dict[str, float]


class DailySnapshot(TypedDict, total=False):
    trade_date: str
    ticker: str
    close: float | None
    volume: float | None
    foreign_buy: float | None
    foreign_sell: float | None
    foreign_net: float | None
    pe: float | None
    pbv: float | None
    roe: float | None
    der: float | None
    div_yield: float | None
    saved_at: str
