"""Monitoring kolektora i łańcucha archiwizacji.

W architekturze, w której dane są kasowane z VPS, cicha awaria jest droższa
niż głośna. Sprawdzamy cztery rzeczy naraz, bo każda z nich osobno prowadzi
do tej samej konsekwencji - nieodwracalnej luki w obserwacjach:

1. czy kolektor w ogóle zapisuje (rozdz. 7.1: luk nie da się nadrobić),
2. czy staging nie rośnie, czyli czy wysyłka na Drive działa,
3. czy jest wolne miejsce - pełny dysk zatrzyma bazę i kolektor naraz,
4. czy zadanie nocne domknęło się w ciągu doby.

Ping do usługi monitorującej leci TYLKO gdy wszystkie warunki są spełnione.
Brak pingu jest sygnałem - to odwrócenie odpowiedzialności, dzięki któremu
awaria samego healthchecka też zostanie zauważona.
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
    """Najbliższy istniejący przodek - disk_usage wymaga istniejącej ścieżki."""
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
