"""Wysyłka archiwum surowego na Drive, kasowanie po weryfikacji."""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path

from loguru import logger

from gtfs_olap.config import RAW_DIR, UPLOAD_QUIET_MIN
from gtfs_olap.maintenance.rclone import available, upload_and_verify


def _dirs_with_files(root: Path) -> Iterator[tuple[Path, list[str]]]:
    # os.walk, nie rglob - bez stat() na każdym z tysięcy plików.
    for dirpath, _dirnames, filenames in os.walk(root):
        if filenames:
            yield Path(dirpath), filenames


def _is_closed(directory: Path, files: list[str], quiet_s: float) -> bool:
    """Gotowy do wysyłki, gdy nic nie przybyło od quiet_s. Bieżąca godzina
    wyklucza się sama, bez logiki kalendarzowej."""
    newest = max((directory / f).stat().st_mtime for f in files)
    return (time.time() - newest) > quiet_s


def _remove_empty_parents(directory: Path, root: Path) -> None:
    current = directory
    while current != root and current.is_relative_to(root):
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def main() -> int:
    if not RAW_DIR.exists():
        logger.warning(f"{RAW_DIR} nie istnieje - nic do wysłania")
        return 0
    if not available():
        return 1

    quiet_s = UPLOAD_QUIET_MIN * 60
    sent = skipped = failed = 0

    for directory, files in _dirs_with_files(RAW_DIR):
        # .tmp = zapis w toku. Wysyłka dałaby podstawę do skasowania
        # niekompletnego pliku.
        if any(f.endswith(".tmp") for f in files):
            logger.info(f"{directory}: trwa zapis (.tmp) - pomijam w tym cyklu")
            skipped += 1
            continue
        if not _is_closed(directory, files, quiet_s):
            skipped += 1
            continue

        relative = directory.relative_to(RAW_DIR).as_posix()
        if upload_and_verify(directory, f"raw/{relative}"):
            for name in files:
                (directory / name).unlink()
            _remove_empty_parents(directory, RAW_DIR)
            sent += 1
        else:
            # Świadomie NIE kasujemy. Dane zostają, alarm pójdzie
            # z healthcheck.py po przekroczeniu MAX_STAGING_H.
            failed += 1

    logger.info(f"upload_raw: wysłane={sent} pominięte={skipped} błędy={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
