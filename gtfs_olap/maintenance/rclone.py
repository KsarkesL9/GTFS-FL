"""Cienka warstwa nad rclone.

Jedyny sposób, w jaki reszta modułu dowiaduje się, że dane są bezpieczne na
Drive - i tym samym jedyna bramka przed kasowaniem czegokolwiek lokalnie.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from loguru import logger

from gtfs_olap.config import RCLONE_BIN, RCLONE_REMOTE

# Ustawienia dobrane pod Google Drive: chunk 64M ogranicza liczbę żądań,
# low-level-retries łagodzi typowe dla Drive'a błędy 403 rate limit.
_OPCJE_COPY = [
    "--transfers=4",
    "--checkers=8",
    "--drive-chunk-size=64M",
    "--low-level-retries=10",
    "--retries=3",
]


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [RCLONE_BIN, *args], capture_output=True, text=True, timeout=3600
    )


def wyslij_i_zweryfikuj(lokalny: Path, podkatalog: str) -> bool:
    """Wysyła katalog na Drive i potwierdza zgodność sumami kontrolnymi.

    Zwraca True dopiero po udanym `rclone check --checksum`. Kod wyjścia
    samego `copy` NIE wystarcza jako podstawa do kasowania: copy potrafi
    zakończyć się zerem przy częściowo przesłanych plikach, a to jest
    dokładnie ta decyzja, której nie wolno pomylić.

    --one-way, bo zdalny katalog może zawierać więcej niż lokalny (np. po
    wcześniejszym, częściowo skasowanym cyklu) - interesuje nas wyłącznie,
    czy wszystko co lokalne ma odpowiednik zdalny.
    """
    cel = f"{RCLONE_REMOTE}/{podkatalog}"

    cp = _run(["copy", str(lokalny), cel, *_OPCJE_COPY])
    if cp.returncode != 0:
        logger.error(f"rclone copy {lokalny} → {cel} nieudane "
                     f"(rc={cp.returncode}): {cp.stderr.strip()[:400]}")
        return False

    ck = _run(["check", str(lokalny), cel, "--checksum", "--one-way"])
    if ck.returncode != 0:
        logger.error(f"rclone check {lokalny} → {cel} NIEZGODNE "
                     f"(rc={ck.returncode}): {ck.stderr.strip()[:400]}")
        return False

    logger.success(f"{lokalny} → {cel}: wysłane i zweryfikowane")
    return True


def dostepny() -> bool:
    """Sprawdza, czy rclone w ogóle działa i zna skonfigurowany remote."""
    r = _run(["listremotes"])
    if r.returncode != 0:
        logger.error(f"rclone niedostępny: {r.stderr.strip()[:200]}")
        return False
    remote = RCLONE_REMOTE.split(":", 1)[0] + ":"
    if remote not in r.stdout:
        logger.error(f"Remote {remote} nie jest skonfigurowany. "
                     f"Dostępne: {r.stdout.split() or 'brak'}")
        return False
    return True
