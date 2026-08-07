from __future__ import annotations

import subprocess
from pathlib import Path

from loguru import logger

from gtfs_olap.config import RCLONE_BIN, RCLONE_REMOTE

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
\
\
\
\

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
