"""Wysyłka archiwum surowego na Drive i kasowanie po weryfikacji."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from loguru import logger

from gtfs_olap.config import RAW_DIR, UPLOAD_QUIET_MIN
from gtfs_olap.maintenance.rclone import dostepny, wyslij_i_zweryfikuj


def _katalogi_z_plikami(root: Path):
    """os.walk zamiast rglob - nie chcemy stat() na każdym z tysięcy plików."""
    for dirpath, _dirnames, filenames in os.walk(root):
        if filenames:
            yield Path(dirpath), filenames


def _zamkniety(d: Path, pliki: list[str], cisza_s: float) -> bool:
    """Gotowy do wysyłki, gdy nic nie przybyło od cisza_s. Jedna reguła dla
    wszystkich rodzajów archiwum - bieżąca godzina wyklucza się sama."""
    najnowszy = max((d / f).stat().st_mtime for f in pliki)
    return (time.time() - najnowszy) > cisza_s


def _usun_puste_rodzice(d: Path, root: Path) -> None:
    """Sprząta katalogi dt=/hh= po wysłaniu zawartości."""
    p = d
    while p != root and p.is_relative_to(root):
        try:
            p.rmdir()
        except OSError:
            return
        p = p.parent


def main() -> int:
    if not RAW_DIR.exists():
        logger.warning(f"{RAW_DIR} nie istnieje - nic do wysłania")
        return 0
    if not dostepny():
        return 1

    cisza_s = UPLOAD_QUIET_MIN * 60
    wyslane = pominiete = bledy = 0

    for d, pliki in _katalogi_z_plikami(RAW_DIR):
        # .tmp = zapis w toku. Wysyłka dałaby podstawę do skasowania
        # niekompletnego pliku.
        if any(f.endswith(".tmp") for f in pliki):
            logger.info(f"{d}: trwa zapis (.tmp) - pomijam w tym cyklu")
            pominiete += 1
            continue
        if not _zamkniety(d, pliki, cisza_s):
            pominiete += 1
            continue

        rel = d.relative_to(RAW_DIR).as_posix()
        if wyslij_i_zweryfikuj(d, f"raw/{rel}"):
            for f in pliki:
                (d / f).unlink()
            _usun_puste_rodzice(d, RAW_DIR)
            wyslane += 1
        else:
            # Świadomie NIE kasujemy. Dane zostają, alarm pójdzie z
            # healthcheck.py po przekroczeniu MAX_STAGING_H.
            bledy += 1

    logger.info(f"upload_raw: wysłane={wyslane} pominięte={pominiete} błędy={bledy}")
    return 1 if bledy else 0


if __name__ == "__main__":
    sys.exit(main())
