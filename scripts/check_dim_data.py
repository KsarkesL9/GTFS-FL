"""Diagnostyka szkód w dim_data.typ_dnia.

Wcześniejsze wersje static ETL nadpisywały typ_dnia wartością 'brak rozkładu'
dla dat przeszłych przy każdym uruchomieniu. typ_dnia wchodzi wprost do cechy
r(t), więc warto sprawdzić skalę przed użyciem danych historycznych.

Skrypt niczego nie zmienia.

    python scripts/check_dim_data.py
"""

import psycopg
from loguru import logger

from gtfs_olap.config import DB_URL


def main() -> int:
    with psycopg.connect(DB_URL) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT typ_dnia, count(*), min(data), max(data)
            FROM dim_data GROUP BY 1 ORDER BY 2 DESC
        """)
        print("\n=== Rozkład typ_dnia w dim_data ===")
        for typ, ile, od, do in cur.fetchall():
            print(f"  {str(typ):<24} {ile:>6}   {od} → {do}")

        cur.execute("""
            SELECT count(*) FILTER (WHERE typ_dnia = 'brak rozkładu'),
                   count(*)
            FROM dim_data
            WHERE data < CURRENT_DATE
        """)
        zepsute, wszystkie = cur.fetchone()
        print(f"\n=== Daty przeszłe ===")
        print(f"  'brak rozkładu': {zepsute} z {wszystkie}")

        # Czy to w ogóle boli - czyli czy dla tych dat są jakieś obserwacje.
        cur.execute("""
            SELECT count(DISTINCT f.data_kursu)
            FROM fakt_opoznienia f
            JOIN dim_data d ON d.data = f.data_kursu
            WHERE d.typ_dnia = 'brak rozkładu'
        """)
        (dotkniete,) = cur.fetchone()
        print(f"  z tego dni z obserwacjami w fakt_opoznienia: {dotkniete}")

        if zepsute == 0:
            logger.success("dim_data wygląda zdrowo - nic do naprawy.")
            return 0

        udzial = 100 * zepsute / wszystkie if wszystkie else 0
        if dotkniete > 0:
            logger.error(
                f"{zepsute} dat przeszłych ({udzial:.0f}%) ma typ_dnia "
                f"'brak rozkładu', w tym {dotkniete} dni z realnymi obserwacjami. "
                f"Cecha r(t) z rozdz. 8 byłaby liczona na wadliwym typie dnia."
            )
        else:
            logger.warning(
                f"{zepsute} dat przeszłych ({udzial:.0f}%) ma typ_dnia "
                f"'brak rozkładu', ale żadna nie ma obserwacji - "
                f"wpływ na cechy zerowy."
            )

        print(
            "\nNaprawa: typ_dnia dla dat przeszłych trzeba odtworzyć z paczek\n"
            "GTFS obowiązujących w tamtym okresie (archiwum /data/raw/static),\n"
            "albo - dla dni bez świąt - wyprowadzić z dzien_tygodnia.\n"
            "Sam kod jest już poprawiony: _upsert_df nie nadpisze wartości\n"
            "poprawnej wartością 'brak rozkładu'.\n"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
