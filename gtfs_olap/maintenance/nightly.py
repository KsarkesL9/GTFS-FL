"""Zadanie nocne: odśwież agregaty → wyeksportuj → wyślij → zweryfikuj →
dopiero wtedy skasuj surowe fakty.

KOLEJNOŚĆ JEST ISTOTNA I NIE WOLNO JEJ ZMIENIAĆ. Kasowanie jest ostatnie
i warunkowe. Każdy wcześniejszy krok, który się nie powiedzie, przerywa
zadanie z niezerowym kodem i zostawia dane nietknięte - dysk urośnie,
a healthcheck to zgłosi. Odwrotna kolejność raz w miesiącu kosztowałaby
dobę obserwacji, której rozdz. 7.1 specyfikacji nie pozwala odtworzyć.
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime, time as dtime, timedelta

import psycopg
from loguru import logger

from gtfs_olap.config import (
    DB_URL, EXPORT_DIR, FAKTY_RETENCJA_H, STATE_DIR, TZ,
)
from gtfs_olap.maintenance import export
from gtfs_olap.maintenance.rclone import dostepny, wyslij_i_zweryfikuj

ZNACZNIK = "nightly_ok"


def _odswiez_agregaty(dzien) -> None:
    """Materializuje agregaty za eksportowaną dobę, zanim surowe fakty znikną.

    Wymaga autocommit - TimescaleDB nie pozwala wywołać
    refresh_continuous_aggregate wewnątrz jawnej transakcji.

    Okno celowo wyprowadzone z eksportowanej doby, a nie stałe: musi mieścić
    się w oknie retencji surowych faktów. Odświeżenie zakresu, z którego
    surowe chunki już usunięto, przeliczyłoby kubełki z pustki i SKASOWAŁO
    poprawne wiersze agregatu."""
    od = datetime.combine(dzien, dtime.min, tzinfo=TZ) - timedelta(hours=1)
    do = datetime.now(TZ) - timedelta(minutes=10)
    with psycopg.connect(DB_URL, autocommit=True) as conn, conn.cursor() as cur:
        for widok in ("ca_opoznienia_15min", "ca_opoznienia_15min_przystanek"):
            cur.execute(
                f"CALL refresh_continuous_aggregate('{widok}', %s, %s)", (od, do)
            )
            logger.info(f"odświeżono {widok}: {od:%Y-%m-%d %H:%M} → {do:%m-%d %H:%M}")


def _kasuj_stare_fakty() -> int:
    """Jawne drop_chunks zamiast polityki retencji.

    Polityka kasuje według zegara, niezależnie od tego, czy dane dotarły na
    Drive. Tutaj kasowanie następuje wyłącznie po zweryfikowanym uploadzie."""
    with psycopg.connect(DB_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT drop_chunks('fakt_opoznienia', older_than => %s::interval)",
            (f"{FAKTY_RETENCJA_H} hours",),
        )
        usuniete = [r[0] for r in cur.fetchall()]
    logger.info(f"drop_chunks: usunięto {len(usuniete)} chunków "
                f"starszych niż {FAKTY_RETENCJA_H}h")
    return len(usuniete)


def _sprzataj_eksport(dzien) -> None:
    """Usuwa lokalne kopie wyeksportowanych dób po potwierdzonej wysyłce.

    Katalog wymiary/ zostaje - jest mały i nadpisywany co noc."""
    for rodzina in ("fakty", "ca_15min", "etl_run"):
        d = EXPORT_DIR / rodzina / f"dt={dzien:%Y-%m-%d}"
        if d.exists():
            shutil.rmtree(d)


def _zapisz_znacznik() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / ZNACZNIK).write_text(
        datetime.now(TZ).isoformat(), encoding="utf-8"
    )


def main() -> int:
    dzien = (datetime.now(TZ) - timedelta(days=1)).date()
    logger.info(f"=== zadanie nocne, doba {dzien} ===")

    # Bramka przed jakąkolwiek pracą: jeśli Drive jest nieosiągalny, nie ma
    # sensu eksportować, a tym bardziej kasować.
    if not dostepny():
        logger.error("rclone niedostępny - przerywam przed kasowaniem czegokolwiek")
        return 1

    _odswiez_agregaty(dzien)

    plik_faktow = export.eksport_faktow(dzien)
    export.eksport_agregatu(dzien)
    export.eksport_etl_run(dzien)
    export.eksport_wymiarow()

    if plik_faktow is None:
        logger.error(f"Eksport faktów za {dzien} nie dał pliku - NIE kasuję niczego")
        return 1

    if not wyslij_i_zweryfikuj(EXPORT_DIR, "export"):
        logger.error("Wysyłka eksportu nieudana - NIE kasuję niczego")
        return 1

    # Od tego miejsca dane są potwierdzone na Drive.
    _sprzataj_eksport(dzien)
    _kasuj_stare_fakty()
    _zapisz_znacznik()

    logger.success(f"=== zadanie nocne zakończone, doba {dzien} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
