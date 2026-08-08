\
\
\

from __future__ import annotations

import shutil
import sys
from datetime import date, datetime, time as dtime, timedelta

import psycopg
from loguru import logger

from gtfs_olap.config import DB_URL, EXPORT_DIR, FACTS_RETENTION_H, STATE_DIR, TZ
from gtfs_olap.maintenance import export
from gtfs_olap.maintenance.rclone import available, upload_and_verify

MARKER = "nightly_ok"

def _refresh_aggregates(day: date) -> None:
\
\
\
\

    start = datetime.combine(day, dtime.min, tzinfo=TZ) - timedelta(hours=1)
    end = datetime.now(TZ) - timedelta(minutes=10)
    with psycopg.connect(DB_URL, autocommit=True) as conn, conn.cursor() as cur:
        for view in ("ca_opoznienia_15min", "ca_opoznienia_15min_przystanek"):
            cur.execute(f"CALL refresh_continuous_aggregate('{view}', %s, %s)",
                        (start, end))
            logger.info(f"odświeżono {view}: {start:%Y-%m-%d %H:%M} → {end:%m-%d %H:%M}")

def _drop_old_facts() -> int:
\

    with psycopg.connect(DB_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT drop_chunks('fakt_opoznienia', older_than => %s::interval)",
            (f"{FACTS_RETENTION_H} hours",))
        dropped = [row[0] for row in cur.fetchall()]
    logger.info(f"drop_chunks: usunięto {len(dropped)} chunków "
                f"starszych niż {FACTS_RETENTION_H}h")
    return len(dropped)

def _clean_export(day: date) -> None:

    for family in ("fakty", "ca_15min", "etl_run"):
        directory = EXPORT_DIR / family / f"dt={day:%Y-%m-%d}"
        if directory.exists():
            shutil.rmtree(directory)

def _write_marker() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / MARKER).write_text(datetime.now(TZ).isoformat(), encoding="utf-8")

def main() -> int:
    day = (datetime.now(TZ) - timedelta(days=1)).date()
    logger.info(f"=== zadanie nocne, doba {day} ===")

    if not available():
        logger.error("rclone niedostępny - przerywam przed kasowaniem czegokolwiek")
        return 1

    _refresh_aggregates(day)

    facts_file = export.export_facts(day)
    export.export_aggregate(day)
    export.export_etl_run(day)
    export.export_dimensions()

    if facts_file is None:
        logger.error(f"Eksport faktów za {day} nie dał pliku - NIE kasuję niczego")
        return 1

    if not upload_and_verify(EXPORT_DIR, "export"):
        logger.error("Wysyłka eksportu nieudana - NIE kasuję niczego")
        return 1

    _clean_export(day)
    _drop_old_facts()
    _write_marker()

    logger.success(f"=== zadanie nocne zakończone, doba {day} ===")
    return 0

if __name__ == "__main__":
    sys.exit(main())
