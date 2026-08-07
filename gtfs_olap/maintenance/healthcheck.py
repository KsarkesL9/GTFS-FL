"""Monitoring kolektora i łańcucha archiwizacji.

Ping leci TYLKO gdy wszystko gra. Brak pingu jest sygnałem - dzięki temu
wykrywalny jest też pad samego healthchecka.
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
    DB_URL, HEALTHCHECK_URL, MAX_ETL_SILENCE_MIN, MAX_NIGHTLY_H, MAX_STAGING_H,
    MIN_FREE_GB, RAW_DIR, STATE_DIR, TZ,
)
from gtfs_olap.maintenance.nightly import MARKER


def _existing_dir(path: Path) -> Path:
    # disk_usage wywala się na nieistniejącej ścieżce.
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def _check_collector() -> str | None:
    try:
        with psycopg.connect(DB_URL, connect_timeout=10) as conn, conn.cursor() as cur:
            cur.execute("SELECT EXTRACT(EPOCH FROM (now() - max(started_at))) "
                        "FROM fakt_etl_run")
            row = cur.fetchone()
    except Exception as exc:
        return f"baza nieosiągalna: {exc}"
    if row is None or row[0] is None:
        return "fakt_etl_run pusty - kolektor nigdy nie zapisał przebiegu"
    silence_min = float(row[0]) / 60
    if silence_min > MAX_ETL_SILENCE_MIN:
        return f"kolektor milczy od {silence_min:.1f} min (próg {MAX_ETL_SILENCE_MIN})"
    return None


def _system_age_h() -> float | None:
    """Odróżnia "nocne padło" od "jeszcze niewymagalne"."""
    try:
        with psycopg.connect(DB_URL, connect_timeout=10) as conn, conn.cursor() as cur:
            cur.execute("SELECT EXTRACT(EPOCH FROM (now() - min(started_at))) "
                        "FROM fakt_etl_run")
            row = cur.fetchone()
        return float(row[0]) / 3600 if row and row[0] is not None else None
    except Exception:
        return None


def _check_staging() -> str | None:
    if not RAW_DIR.exists():
        return None
    oldest = None
    for dirpath, _dirnames, filenames in os.walk(RAW_DIR):
        for name in filenames:
            mtime = (Path(dirpath) / name).stat().st_mtime
            if oldest is None or mtime < oldest:
                oldest = mtime
    if oldest is None:
        return None
    age_h = (time.time() - oldest) / 3600
    if age_h > MAX_STAGING_H:
        return (f"najstarszy plik w stagingu ma {age_h:.1f} h "
                f"(próg {MAX_STAGING_H}) - wysyłka na Drive nie działa")
    return None


def _check_disk() -> str | None:
    free_gb = shutil.disk_usage(_existing_dir(RAW_DIR)).free / 1e9
    if free_gb < MIN_FREE_GB:
        return f"wolne miejsce {free_gb:.1f} GB poniżej progu {MIN_FREE_GB} GB"
    return None


def _check_nightly() -> str | None:
    marker = STATE_DIR / MARKER
    if not marker.exists():
        # Świeże wdrożenie: nocne chodzi o 03:30, więc do pierwszej nocy
        # znacznika nie ma i alarm byłby fałszywy.
        age = _system_age_h()
        if age is not None and age < MAX_NIGHTLY_H:
            logger.info(f"Zadanie nocne jeszcze niewymagalne "
                        f"(system zbiera {age:.1f} h z {MAX_NIGHTLY_H})")
            return None
        return "brak znacznika zadania nocnego - nie wykonało się ani razu"
    age_h = (time.time() - marker.stat().st_mtime) / 3600
    if age_h > MAX_NIGHTLY_H:
        return f"zadanie nocne ostatnio udane {age_h:.1f} h temu (próg {MAX_NIGHTLY_H})"
    return None


def main() -> int:
    problems = [p for p in (_check_collector(), _check_staging(),
                            _check_disk(), _check_nightly()) if p]

    if problems:
        for problem in problems:
            logger.error(f"HEALTHCHECK: {problem}")
        return 1

    logger.info(f"healthcheck OK ({datetime.now(TZ):%Y-%m-%d %H:%M:%S})")
    if HEALTHCHECK_URL:
        try:
            httpx.get(HEALTHCHECK_URL, timeout=10.0)
        except Exception as exc:
            logger.warning(f"Ping do usługi monitorującej nieudany: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
