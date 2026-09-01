import argparse
from datetime import date, datetime, timedelta

import psycopg
from loguru import logger

from gtfs_olap.config import DB_URL, EXPORT_DIR
from gtfs_olap.maintenance import export
from gtfs_olap.maintenance.rclone import available, upload_and_verify

def _zakres_w_bazie() -> tuple[date, date] | None:
    with psycopg.connect(DB_URL) as conn, conn.cursor() as cur:
        cur.execute("SELECT min(ts)::date, max(ts)::date FROM fakt_opoznienia")
        od, do = cur.fetchone()
    return (od, do) if od else None

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--od", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date())
    ap.add_argument("--do", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date())
    ap.add_argument("--bez-wysylki", action="store_true",
                    help="tylko zapis Parquetów lokalnie, bez rclone")
    args = ap.parse_args()

    zakres = _zakres_w_bazie()
    if zakres is None:
        logger.error("fakt_opoznienia jest pusta - nie ma czego eksportować")
        return 1
    logger.info(f"Zakres dostępny w bazie: {zakres[0]} → {zakres[1]}")

    od = args.od or zakres[0]
    do = args.do or zakres[1]
    if od > do:
        logger.error("--od jest późniejsze niż --do")
        return 1

    if not args.bez_wysylki and not available():
        return 1

    dzien = od
    puste = []
    while dzien <= do:
        logger.info(f"--- {dzien} ---")
        if export.export_facts(dzien) is None:
            puste.append(dzien)
        export.export_aggregate(dzien)
        export.export_etl_run(dzien)
        dzien += timedelta(days=1)

    export.export_dimensions()

    if puste:
        logger.warning(f"Dni bez faktów ({len(puste)}): "
                       f"{', '.join(str(d) for d in puste[:10])}"
                       f"{' ...' if len(puste) > 10 else ''}")
        logger.warning("To luki w zbieraniu - sprawdź scripts/inventory_gaps.py")

    if args.bez_wysylki:
        logger.info(f"Pominięto wysyłkę. Pliki w {EXPORT_DIR}")
        return 0

    if not upload_and_verify(EXPORT_DIR, "export"):
        logger.error("Wysyłka nieudana - NIE przycinaj bazy")
        return 1

    logger.success("Backfill wysłany i zweryfikowany. Dopiero teraz można "
                   "przycinać bazę (drop_chunks).")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
