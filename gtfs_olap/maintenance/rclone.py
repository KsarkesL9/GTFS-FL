from __future__ import annotations

import subprocess
from pathlib import Path

from loguru import logger

from gtfs_olap.config import RCLONE_BIN, RCLONE_REMOTE

_COPY_OPTS = [
    "--transfers=4",
    "--checkers=8",
    "--drive-chunk-size=64M",
    "--low-level-retries=10",
    "--retries=3",
]

def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [RCLONE_BIN, *args], capture_output=True, text=True, timeout=3600)

def upload_and_verify(local: Path, subpath: str) -> bool:
\

    target = f"{RCLONE_REMOTE}/{subpath}"

    copied = _run(["copy", str(local), target, *_COPY_OPTS])
    if copied.returncode != 0:
        logger.error(f"rclone copy {local} → {target} nieudane "
                     f"(rc={copied.returncode}): {copied.stderr.strip()[:400]}")
        return False

    checked = _run(["check", str(local), target, "--checksum", "--one-way"])
    if checked.returncode != 0:
        logger.error(f"rclone check {local} → {target} NIEZGODNE "
                     f"(rc={checked.returncode}): {checked.stderr.strip()[:400]}")
        return False

    logger.success(f"{local} → {target}: wysłane i zweryfikowane")
    return True

def available() -> bool:
    result = _run(["listremotes"])
    if result.returncode != 0:
        logger.error(f"rclone niedostępny: {result.stderr.strip()[:200]}")
        return False
    remote = RCLONE_REMOTE.split(":", 1)[0] + ":"
    if remote not in result.stdout:
        logger.error(f"Remote {remote} nie jest skonfigurowany. "
                     f"Dostępne: {result.stdout.split() or 'brak'}")
        return False
    return True
