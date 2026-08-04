"""Monitoring kolektora i łańcucha archiwizacji.

Sprawdza: czy kolektor zapisuje, czy staging nie rośnie, czy jest wolne
miejsce, czy zadanie nocne domknęło się w ciągu doby.

Ping leci TYLKO przy spełnieniu wszystkich warunków - brak pingu jest
sygnałem, dzięki czemu wykrywalna jest też awaria samego healthchecka.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
import psycopg
from loguru import logger

from gtfs_olap.config import (
    DB_URL, HEALTHCHECK_URL, MAX_ETL_CISZA_MIN, MAX_NIGHTLY_H, MAX_STAGING_H,
    MIN_FREE_GB, RAW_DIR, STATE_DIR, TZ,
)
from gtfs_olap.maintenance.nightly import ZNACZNIK


def _istniejacy_katalog(p: Path) -> Path:
    """disk_usage wymaga istniejącej ścieżki."""
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def _sprawdz_kolektor() -> str | None:
    try:
        with psycopg.connect(DB_URL, connect_timeout=10) as conn, conn.cursor() as cur:
            cur.execute("SELECT EXTRACT(EPOCH FROM (now() - max(started_at))) "
                        "FROM fakt_etl_run")
            row = cur.fetchone()
    except Exception as e:
        return f"baza nieosiągalna: {e}"
    if row is None or row[0] is None:
        return "fakt_etl_run pusty - kolektor nigdy nie zapisał przebiegu"
    cisza_min = float(row[0]) / 60
    if cisza_min > MAX_ETL_CISZA_MIN:
        return f"kolektor milczy od {cisza_min:.1f} min (próg {MAX_ETL_CISZA_MIN})"
    return None


def _wiek_systemu_h() -> float | None:
    """Ile godzin zbiera system - odróżnia 'nocne padło' od 'jeszcze
    niewymagalne'."""
    try:
        with psycopg.connect(DB_URL, connect_timeout=10) as conn, conn.cursor() as cur:
            cur.execute("SELECT EXTRACT(EPOCH FROM (now() - min(started_at))) "
                        "FROM fakt_etl_run")
            row = cur.fetchone()
        if row is None or row[0] is None:
            return None
        return float(row[0]) / 3600
    except Exception:
        return None


def _sprawdz_staging() -> str | None:
    if not RAW_DIR.exists():
        return None
    najstarszy = None
    for dirpath, _dirnames, filenames in os.walk(RAW_DIR):
        for f in filenames:
            m = (Path(dirpath) / f).stat().st_mtime
            if najstarszy is None or m < najstarszy:
                najstarszy = m
    if najstarszy is None:
        return None
    wiek_h = (time.time() - najstarszy) / 3600
    if wiek_h > MAX_STAGING_H:
        return (f"najstarszy plik w stagingu ma {wiek_h:.1f} h "
                f"(próg {MAX_STAGING_H}) - wysyłka na Drive nie działa")
    return None


def _sprawdz_dysk() -> str | None:
    uzycie = shutil.disk_usage(_istniejacy_katalog(RAW_DIR))
    wolne_gb = uzycie.free / 1e9
    if wolne_gb < MIN_FREE_GB:
        return f"wolne miejsce {wolne_gb:.1f} GB poniżej progu {MIN_FREE_GB} GB"
    return None


def _sprawdz_zadanie_nocne() -> str | None:
    znacznik = STATE_DIR / ZNACZNIK
    if not znacznik.exists():
        # Świeże wdrożenie: nocne chodzi o 03:30, więc do pierwszej nocy
        # znacznika nie ma i alarm byłby fałszywy.
        wiek = _wiek_systemu_h()
        if wiek is not None and wiek < MAX_NIGHTLY_H:
            logger.info(f"Zadanie nocne jeszcze niewymagalne "
                        f"(system zbiera {wiek:.1f} h z {MAX_NIGHTLY_H})")
            return None
        return "brak znacznika zadania nocnego - nie wykonało się ani razu"
    wiek_h = (time.time() - znacznik.stat().st_mtime) / 3600
    if wiek_h > MAX_NIGHTLY_H:
        return f"zadanie nocne ostatnio udane {wiek_h:.1f} h temu (próg {MAX_NIGHTLY_H})"
    return None


def main() -> int:
    problemy = [
        p for p in (
            _sprawdz_kolektor(),
            _sprawdz_staging(),
            _sprawdz_dysk(),
            _sprawdz_zadanie_nocne(),
        ) if p
    ]

    if problemy:
        for p in problemy:
            logger.error(f"HEALTHCHECK: {p}")
        return 1

    logger.info(f"healthcheck OK ({datetime.now(TZ):%Y-%m-%d %H:%M:%S})")
    if HEALTHCHECK_URL:
        try:
            httpx.get(HEALTHCHECK_URL, timeout=10.0)
        except Exception as e:
            logger.warning(f"Ping do usługi monitorującej nieudany: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
