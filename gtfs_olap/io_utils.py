from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


def write_atomic(path: Path, write: Callable[[Path], None]) -> bool:
    """Zapis przez plik tymczasowy i rename. Uploader nie może zobaczyć pliku
    w połowie i skasować na tej podstawie oryginału.

    False, gdy `write` nic nie utworzyło - tak eksport strumieniowy sygnalizuje
    brak danych, bo z góry nie wie, czy cokolwiek zapisze.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    write(tmp)
    if not tmp.exists():
        return False
    tmp.replace(path)
    return True
