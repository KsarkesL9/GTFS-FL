"""Inwentaryzacja luk w zbieraniu danych.

Luki wykrywa się przez nieciągłość started_at, a NIE przez status = 'ERROR':
gdy proces nie żyje, nie ma kto zapisać wiersza o błędzie, więc luka objawia
się brakiem wierszy.

    python scripts/inventory_gaps.py                 # próg 2 minuty
    python scripts/inventory_gaps.py --prog-min 5
"""

import argparse
from pathlib import Path

import pandas as pd
import psycopg
from loguru import logger

from gtfs_olap.config import DB_URL, EXPORT_DIR

SQL = """
WITH kolejne AS (
    SELECT started_at,
           LEAD(started_at) OVER (ORDER BY started_at) AS nastepny
    FROM fakt_etl_run
)
SELECT started_at AS luka_od,
       nastepny   AS luka_do,
       EXTRACT(EPOCH FROM (nastepny - started_at)) / 60.0 AS czas_min
FROM kolejne
WHERE nastepny IS NOT NULL
  AND nastepny - started_at > make_interval(mins => %s)
ORDER BY started_at
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prog-min", type=float, default=2.0,
                    help="przerwa uznawana za lukę (domyślnie 2 min = 6 migawek)")
    args = ap.parse_args()

    with psycopg.connect(DB_URL) as conn, conn.cursor() as cur:
        cur.execute("SELECT min(started_at), max(started_at), count(*) FROM fakt_etl_run")
        od, do, ile = cur.fetchone()
        if od is None:
            logger.error("fakt_etl_run jest pusty - brak czegokolwiek do inwentaryzacji")
            return 1

        cur.execute(SQL, (args.prog_min,))
        kolumny = [d.name for d in cur.description]
        luki = pd.DataFrame(cur.fetchall(), columns=kolumny)

    okno_min = (do - od).total_seconds() / 60
    utracone_min = float(luki["czas_min"].sum()) if not luki.empty else 0.0
    pokrycie = 100 * (1 - utracone_min / okno_min) if okno_min else 0

    print(f"\n=== Pokrycie czasowe ===")
    print(f"  okno        : {od:%Y-%m-%d %H:%M} → {do:%Y-%m-%d %H:%M}")
    print(f"  przebiegów  : {ile:,}")
    print(f"  luk (>{args.prog_min:g} min): {len(luki)}")
    print(f"  czas utracony: {utracone_min / 60:.1f} h")
    print(f"  pokrycie     : {pokrycie:.2f}%")

    if not luki.empty:
        print(f"\n=== 10 największych luk ===")
        for _, r in luki.nlargest(10, "czas_min").iterrows():
            print(f"  {r.luka_od:%Y-%m-%d %H:%M} → {r.luka_do:%Y-%m-%d %H:%M}  "
                  f"({r.czas_min / 60:.2f} h)")

        out = EXPORT_DIR / "luki" / "inwentaryzacja_luk.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        luki.to_parquet(out, index=False, compression="zstd")
        logger.success(f"Zapisano {len(luki)} luk → {out}")
    else:
        logger.success("Brak luk powyżej progu.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
