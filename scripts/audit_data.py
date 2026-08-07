"""Audyt jakości danych: co łapiemy, co gubimy, czy trafia do właściwych kolumn.

Pobiera świeżą migawkę i liczy odrzuty (_process_feed po cichu pomija
zatrzymania bez dopasowania w rozkładzie), potem sprawdza rozkłady wartości
w bazie i dobę operacyjną.

    python scripts/audit_data.py
"""

from __future__ import annotations

import httpx
import psycopg
from google.transit import gtfs_realtime_pb2

from gtfs_olap.config import DB_URL, RT_TIMEOUT_S, RT_URL

TRIP_CANCELED = gtfs_realtime_pb2.TripDescriptor.CANCELED
STOP_SKIPPED = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.SKIPPED


def naglowek(t: str) -> None:
    print(f"\n{'=' * 70}\n {t}\n{'=' * 70}")


def audyt_feedu(conn) -> None:
    naglowek("1. POKRYCIE ŻYWEGO STRUMIENIA")

    raw = httpx.get(RT_URL, timeout=RT_TIMEOUT_S).content
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw)

    with conn.cursor() as cur:
        cur.execute("SELECT wersja_id FROM dim_wersja_rozkladu "
                    "ORDER BY zaladowano DESC LIMIT 1")
        wersja = cur.fetchone()[0]

    encje = len(feed.entity)
    tu = anulowane = stu_razem = 0
    bez_seq = pominiete = bez_arrival = bez_delay = kandydaci = 0
    pary_trip, pary_seq = [], []
    tripy = set()

    for e in feed.entity:
        if not e.HasField("trip_update"):
            continue
        tu += 1
        t = e.trip_update
        if not t.trip.trip_id:
            continue
        tripy.add(t.trip.trip_id)
        if t.trip.schedule_relationship == TRIP_CANCELED:
            anulowane += 1
            continue
        for s in t.stop_time_update:
            stu_razem += 1
            if not s.HasField("stop_sequence"):
                bez_seq += 1
                continue
            pary_trip.append(t.trip.trip_id)
            pary_seq.append(s.stop_sequence)
            if s.schedule_relationship == STOP_SKIPPED:
                pominiete += 1
                continue
            if not s.HasField("arrival"):
                bez_arrival += 1
                continue
            if not s.arrival.HasField("delay"):
                bez_delay += 1
                continue
            kandydaci += 1

    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM unnest(%s::text[]) AS t(trip_id) "
            "WHERE EXISTS (SELECT 1 FROM lookup_schedule l "
            "              WHERE l.trip_id = t.trip_id AND l.wersja_id = %s)",
            (list(tripy), wersja))
        tripy_znane = cur.fetchone()[0]

        cur.execute(
            "SELECT count(*) FROM unnest(%s::text[], %s::int[]) AS f(trip_id, seq) "
            "JOIN lookup_schedule l ON l.trip_id = f.trip_id "
            "                      AND l.stop_sequence = f.seq "
            "                      AND l.wersja_id = %s",
            (pary_trip, pary_seq, wersja))
        pary_dopasowane = cur.fetchone()[0]

    print(f"  encje w migawce            : {encje:,}")
    print(f"  z tego TripUpdate          : {tu:,}")
    print(f"  kursów unikalnych          : {len(tripy):,}")
    print(f"  kursów znanych rozkładowi  : {tripy_znane:,} "
          f"({100 * tripy_znane / max(len(tripy), 1):.1f}%)")
    print(f"  kursów ANULOWANYCH         : {anulowane:,}")
    print()
    print(f"  zatrzymań (stop_time_update): {stu_razem:,}")
    print(f"    bez stop_sequence         : {bez_seq:,}  <- nie do dopasowania")
    print(f"    dopasowanych do rozkładu  : {pary_dopasowane:,} "
          f"({100 * pary_dopasowane / max(len(pary_trip), 1):.1f}% tych z sekwencją)")
    print(f"    oznaczonych POMINIETY     : {pominiete:,}")
    print(f"    bez pola arrival          : {bez_arrival:,}")
    print(f"    z arrival, ale bez delay  : {bez_delay:,}")
    print(f"    kandydatów na OBSERWACJĘ  : {kandydaci:,}")

    gubione = len(pary_trip) - pary_dopasowane
    print()
    print(f"  ODRZUT z braku dopasowania : {gubione:,} "
          f"({100 * gubione / max(stu_razem, 1):.1f}% wszystkich zatrzymań)")


def audyt_kolumn(conn) -> None:
    naglowek("2. ZAWARTOŚĆ KOLUMN (ostatnia godzina)")
    zapytania = [
        ("status - rozkład", """
            SELECT status, count(*),
                   count(*) FILTER (WHERE opoznienie_s IS NULL) AS bez_opoznienia
            FROM fakt_opoznienia WHERE ts > now() - INTERVAL '1 hour'
            GROUP BY status ORDER BY 2 DESC"""),
        ("NULL-e w kolumnach kluczowych", """
            SELECT count(*) FILTER (WHERE linia_id IS NULL)     AS bez_linii,
                   count(*) FILTER (WHERE operator_id IS NULL)  AS bez_operatora,
                   count(*) FILTER (WHERE kierunek IS NULL)     AS bez_kierunku,
                   count(*) FILTER (WHERE data_kursu IS NULL)   AS bez_daty,
                   count(*) FILTER (WHERE wersja_id IS NULL)    AS bez_wersji
            FROM fakt_opoznienia WHERE ts > now() - INTERVAL '1 hour'"""),
        ("kierunek - wartości", """
            SELECT kierunek, count(*) FROM fakt_opoznienia
            WHERE ts > now() - INTERVAL '1 hour' GROUP BY 1 ORDER BY 2 DESC"""),
        ("opóźnienie - rozkład (s)", """
            SELECT min(opoznienie_s) AS min,
                   percentile_disc(0.01) WITHIN GROUP (ORDER BY opoznienie_s) AS p01,
                   percentile_disc(0.25) WITHIN GROUP (ORDER BY opoznienie_s) AS p25,
                   percentile_disc(0.50) WITHIN GROUP (ORDER BY opoznienie_s) AS mediana,
                   percentile_disc(0.75) WITHIN GROUP (ORDER BY opoznienie_s) AS p75,
                   percentile_disc(0.99) WITHIN GROUP (ORDER BY opoznienie_s) AS p99,
                   max(opoznienie_s) AS max
            FROM fakt_opoznienia
            WHERE status = 'OBSERWACJA' AND ts > now() - INTERVAL '1 hour'"""),
        ("wartości absurdalne", """
            SELECT count(*) FILTER (WHERE opoznienie_s < -3600) AS wczesniej_niz_godzina,
                   count(*) FILTER (WHERE opoznienie_s > 7200)  AS pozniej_niz_2h,
                   count(*) FILTER (WHERE stop_sequence < 0)    AS ujemna_sekwencja
            FROM fakt_opoznienia WHERE ts > now() - INTERVAL '1 hour'"""),
        ("doba operacyjna: data_kursu vs data ts", """
            SELECT (ts AT TIME ZONE 'Europe/Warsaw')::date - data_kursu AS przesuniecie_dni,
                   count(*)
            FROM fakt_opoznienia WHERE ts > now() - INTERVAL '1 hour'
            GROUP BY 1 ORDER BY 1"""),
    ]
    for tytul, sql in zapytania:
        print(f"\n--- {tytul} ---")
        with conn.cursor() as cur:
            cur.execute(sql)
            kols = [d.name for d in cur.description]
            wiersze = cur.fetchall()
        print("  " + " | ".join(kols))
        for w in wiersze:
            print("  " + " | ".join("NULL" if v is None else str(v) for v in w))


def audyt_pokrycia(conn) -> None:
    naglowek("3. POKRYCIE WYMIARÓW")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT (SELECT count(*) FROM dim_linia)     AS linii_w_wymiarze,
                   (SELECT count(DISTINCT linia_id) FROM fakt_opoznienia) AS linii_w_faktach,
                   (SELECT count(*) FROM dim_operator)  AS operatorow_w_wymiarze,
                   (SELECT count(DISTINCT operator_id) FROM fakt_opoznienia) AS operatorow_w_faktach,
                   (SELECT count(*) FROM dim_przystanek) AS przystankow_w_wymiarze,
                   (SELECT count(DISTINCT przystanek_id) FROM fakt_opoznienia) AS przystankow_w_faktach
        """)
        kols = [d.name for d in cur.description]
        for k, v in zip(kols, cur.fetchone()):
            print(f"  {k:<26} {v:,}")

        print("\n--- typ dnia w dim_data dla dni z obserwacjami ---")
        cur.execute("""
            SELECT d.typ_dnia, count(DISTINCT f.data_kursu) AS dni
            FROM fakt_opoznienia f JOIN dim_data d ON d.data = f.data_kursu
            GROUP BY 1 ORDER BY 2 DESC""")
        for typ, dni in cur.fetchall():
            print(f"  {str(typ):<24} {dni}")


def main() -> int:
    with psycopg.connect(DB_URL) as conn:
        audyt_feedu(conn)
        audyt_kolumn(conn)
        audyt_pokrycia(conn)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
