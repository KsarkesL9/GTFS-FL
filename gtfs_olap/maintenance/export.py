"""Eksport danych z TimescaleDB do plików Parquet.

Pełni dwie role naraz:
1. Jest warunkiem bezpiecznego skasowania surowych faktów z VPS.
2. Jest interfejsem do części uczeniowej (decyzja D3 specyfikacji - Flower,
   PyTorch i River pracują wyłącznie na plikach, bez zależności od bazy).
"""

from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
from loguru import logger

from gtfs_olap.config import DB_URL, EXPORT_DIR, TZ

# Schemat jawny, nie wnioskowany. Przy zapisie strumieniowym każda partia
# musi mieć identyczny schemat, a wnioskowanie z partii, w której jakaś
# kolumna jest w całości NULL-em, dałoby niezgodny typ i wywrócenie zapisu.
FAKTY_SCHEMA = pa.schema([
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

_PARTIA = 100_000


def _granice_doby(dzien: date) -> tuple[datetime, datetime]:
    """Doba kalendarzowa w czasie lokalnym, nie w UTC.

    Kolumna ts jest w UTC, ale doba operacyjna projektu jest warszawska -
    granice liczone w UTC rozjechałyby się z agregatami, które używają
    time_bucket(..., 'Europe/Warsaw')."""
    od = datetime.combine(dzien, dtime.min, tzinfo=TZ)
    return od, od + timedelta(days=1)


def eksport_faktow(dzien: date) -> Path | None:
    """Zrzuca dobę surowych faktów do jednego pliku Parquet.

    Czyta partiami przez kursor serwerowy: doba to ~4,3 mln wierszy, a proces
    działa na VPS obok kolektora, który sam trzyma ~1 GB cache'u rozkładu.
    Wczytanie całości do pamięci wywróciłoby maszynę."""
    od, do = _granice_doby(dzien)
    out_dir = EXPORT_DIR / "fakty" / f"dt={dzien:%Y-%m-%d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"fakt_opoznienia_{dzien:%Y%m%d}.parquet"
    tmp = out_dir / (out.name + ".tmp")

    writer = None
    razem = 0
    try:
        with psycopg.connect(DB_URL) as conn:
            with conn.cursor(name="eksport_faktow") as cur:
                cur.itersize = _PARTIA
                cur.execute(
                    "SELECT ts, wersja_id, trip_id, przystanek_id, stop_sequence, "
                    "       linia_id, operator_id, kierunek, data_kursu, "
                    "       opoznienie_s, status "
                    "FROM fakt_opoznienia "
                    "WHERE ts >= %s AND ts < %s "
                    "ORDER BY ts",
                    (od, do),
                )
                while True:
                    partia = cur.fetchmany(_PARTIA)
                    if not partia:
                        break
                    kolumny = list(zip(*partia))
                    tabela = pa.Table.from_arrays(
                        [pa.array(k, type=f.type)
                         for k, f in zip(kolumny, FAKTY_SCHEMA)],
                        schema=FAKTY_SCHEMA,
                    )
                    if writer is None:
                        writer = pq.ParquetWriter(tmp, FAKTY_SCHEMA,
                                                  compression="zstd")
                    writer.write_table(tabela)
                    razem += len(partia)
    finally:
        if writer is not None:
            writer.close()

    if razem == 0:
        tmp.unlink(missing_ok=True)
        logger.warning(f"Brak faktów dla {dzien} - nic nie eksportuję")
        return None

    tmp.replace(out)
    logger.success(f"fakty {dzien}: {razem:,} wierszy → {out.name} "
                   f"({out.stat().st_size / 1e6:.1f} MB)")
    return out


def _eksport_maly(sql: str, params: tuple, out: Path, opis: str) -> Path | None:
    """Eksport małej tabeli w całości. Tylko dla agregatów i logów.

    Świadomie NIE używane dla fakt_opoznienia - patrz eksport_faktow."""
    import pandas as pd

    with psycopg.connect(DB_URL) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        kolumny = [d.name for d in cur.description]
        wiersze = cur.fetchall()

    if not wiersze:
        logger.warning(f"Brak danych: {opis}")
        return None

    df = pd.DataFrame(wiersze, columns=kolumny)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.parent / (out.name + ".tmp")
    df.to_parquet(tmp, index=False, compression="zstd")
    tmp.replace(out)
    logger.success(f"{opis}: {len(df):,} wierszy → {out.name}")
    return out


def eksport_agregatu(dzien: date) -> Path | None:
    """Agregat 15-minutowy za dobę - bezpośrednie źródło wektora cech (rozdz. 8).

    Zostaje też w bazie na cały projekt; kopia na Drive jest zabezpieczeniem
    i wejściem dla części uczeniowej działającej poza VPS-em."""
    od, do = _granice_doby(dzien)
    return _eksport_maly(
        "SELECT * FROM ca_opoznienia_15min "
        "WHERE kwadrans >= %s AND kwadrans < %s ORDER BY kwadrans",
        (od, do),
        EXPORT_DIR / "ca_15min" / f"dt={dzien:%Y-%m-%d}" /
        f"ca_opoznienia_15min_{dzien:%Y%m%d}.parquet",
        f"ca_15min {dzien}",
    )


def eksport_etl_run(dzien: date) -> Path | None:
    """Rejestr przebiegów kolektora - jedyne źródło inwentaryzacji luk.

    Luka objawia się BRAKIEM wierszy, a nie wierszem ze statusem ERROR
    (gdy proces nie żyje, nie ma kto zapisać błędu), więc ciągłość tego
    strumienia jest sama w sobie daną badawczą - rozdz. 4.2."""
    od, do = _granice_doby(dzien)
    return _eksport_maly(
        "SELECT * FROM fakt_etl_run "
        "WHERE started_at >= %s AND started_at < %s ORDER BY started_at",
        (od, do),
        EXPORT_DIR / "etl_run" / f"dt={dzien:%Y-%m-%d}" /
        f"fakt_etl_run_{dzien:%Y%m%d}.parquet",
        f"etl_run {dzien}",
    )


def eksport_wymiarow() -> list[Path]:
    """Wymiary w całości. Małe, zmieniają się rzadko, nadpisujemy co noc."""
    out = []
    for tabela in ("dim_linia", "dim_przystanek", "dim_operator",
                   "dim_data", "dim_wersja_rozkladu"):
        p = _eksport_maly(
            f"SELECT * FROM {tabela}", (),
            EXPORT_DIR / "wymiary" / f"{tabela}.parquet",
            tabela,
        )
        if p is not None:
            out.append(p)
    return out
