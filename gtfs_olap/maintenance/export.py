"""Eksport z TimescaleDB do Parquetu.

Warunek skasowania surowych faktów z VPS, a przy okazji interfejs do części
uczeniowej - ta pracuje wyłącznie na plikach, bez dostępu do bazy.
"""

from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import pandas as pd
import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
from loguru import logger

from gtfs_olap.config import DB_URL, EXPORT_DIR, TZ
from gtfs_olap.io_utils import write_atomic

# Schemat jawny - partia z kolumną w całości NULL zepsułaby wnioskowanie typu.
FACTS_SCHEMA = pa.schema([
    ("ts", pa.timestamp("us", tz="UTC")),
    ("wersja_id", pa.int32()),
    ("trip_id", pa.string()),
    ("przystanek_id", pa.string()),
    ("stop_sequence", pa.int32()),
    ("linia_id", pa.string()),
    ("operator_id", pa.string()),
    ("kierunek", pa.string()),
    ("data_kursu", pa.date32()),
    ("opoznienie_s", pa.int32()),
    ("status", pa.string()),
])

_BATCH = 100_000


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    """Doba lokalna, nie UTC - inaczej rozjazd z time_bucket w agregatach."""
    start = datetime.combine(day, dtime.min, tzinfo=TZ)
    return start, start + timedelta(days=1)


def export_facts(day: date) -> Path | None:
    """Doba faktów do jednego Parquetu, partiami przez kursor serwerowy -
    3,5 mln wierszy, a obok chodzi kolektor z 650 MB cache'u."""
    start, end = _day_bounds(day)
    out = (EXPORT_DIR / "fakty" / f"dt={day:%Y-%m-%d}"
           / f"fakt_opoznienia_{day:%Y%m%d}.parquet")
    rows = 0

    def dump(target: Path) -> None:
        nonlocal rows
        writer = None
        try:
            with psycopg.connect(DB_URL) as conn:
                with conn.cursor(name="export_facts") as cur:
                    cur.itersize = _BATCH
                    cur.execute(
                        "SELECT ts, wersja_id, trip_id, przystanek_id, stop_sequence, "
                        "       linia_id, operator_id, kierunek, data_kursu, "
                        "       opoznienie_s, status "
                        "FROM fakt_opoznienia "
                        "WHERE ts >= %s AND ts < %s ORDER BY ts",
                        (start, end))
                    while True:
                        batch = cur.fetchmany(_BATCH)
                        if not batch:
                            break
                        columns = list(zip(*batch))
                        table = pa.Table.from_arrays(
                            [pa.array(c, type=f.type)
                             for c, f in zip(columns, FACTS_SCHEMA)],
                            schema=FACTS_SCHEMA)
                        if writer is None:
                            writer = pq.ParquetWriter(target, FACTS_SCHEMA,
                                                      compression="zstd")
                        writer.write_table(table)
                        rows += len(batch)
        finally:
            if writer is not None:
                writer.close()

    if not write_atomic(out, dump) or rows == 0:
        logger.warning(f"Brak faktów dla {day} - nic nie eksportuję")
        return None

    logger.success(f"fakty {day}: {rows:,} wierszy → {out.name} "
                   f"({out.stat().st_size / 1e6:.1f} MB)")
    return out


def _export_small(sql: str, params: tuple, out: Path, label: str) -> Path | None:
    """Tylko dla małych tabel - fakty idą przez export_facts."""
    with psycopg.connect(DB_URL) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        columns = [d.name for d in cur.description]
        rows = cur.fetchall()

    if not rows:
        logger.warning(f"Brak danych: {label}")
        return None

    df = pd.DataFrame(rows, columns=columns)
    write_atomic(out, lambda target: df.to_parquet(
        target, index=False, compression="zstd"))
    logger.success(f"{label}: {len(df):,} wierszy → {out.name}")
    return out


def export_aggregate(day: date) -> Path | None:
    """Bezpośrednie źródło wektora cech."""
    start, end = _day_bounds(day)
    return _export_small(
        "SELECT * FROM ca_opoznienia_15min "
        "WHERE kwadrans >= %s AND kwadrans < %s ORDER BY kwadrans",
        (start, end),
        EXPORT_DIR / "ca_15min" / f"dt={day:%Y-%m-%d}"
        / f"ca_opoznienia_15min_{day:%Y%m%d}.parquet",
        f"ca_15min {day}")


def export_etl_run(day: date) -> Path | None:
    """Jedyne źródło inwentaryzacji luk. Luka to BRAK wierszy, nie ERROR -
    gdy proces nie żyje, nie ma kto zapisać błędu."""
    start, end = _day_bounds(day)
    return _export_small(
        "SELECT * FROM fakt_etl_run "
        "WHERE started_at >= %s AND started_at < %s ORDER BY started_at",
        (start, end),
        EXPORT_DIR / "etl_run" / f"dt={day:%Y-%m-%d}"
        / f"fakt_etl_run_{day:%Y%m%d}.parquet",
        f"etl_run {day}")


def export_dimensions() -> list[Path]:
    """Nadpisywane co noc."""
    out = []
    for table in ("dim_linia", "dim_przystanek", "dim_operator",
                  "dim_data", "dim_wersja_rozkladu"):
        path = _export_small(f"SELECT * FROM {table}", (),
                             EXPORT_DIR / "wymiary" / f"{table}.parquet", table)
        if path is not None:
            out.append(path)
    return out
