"""Paket modular UI tab & widget untuk SahamBot Desktop."""
from __future__ import annotations

from desktop_ui.tab_alerts import AlertsTabMixin
from desktop_ui.tab_cek import CekTabMixin
from desktop_ui.tab_jurnal import JurnalTabMixin
from desktop_ui.tab_paket import PaketTabMixin
from desktop_ui.tab_pasar import PasarTabMixin
from desktop_ui.tab_portofolio import PortofolioTabMixin
from desktop_ui.tab_sinyal import SinyalTabMixin
from desktop_ui.tab_watchlist import WatchlistTabMixin

__all__ = [
    "AlertsTabMixin",
    "CekTabMixin",
    "JurnalTabMixin",
    "PaketTabMixin",
    "PasarTabMixin",
    "PortofolioTabMixin",
    "SinyalTabMixin",
    "WatchlistTabMixin",
]
