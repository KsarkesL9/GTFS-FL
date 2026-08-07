"""Wspólne operacje na plikach archiwum."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


def write_atomic(path: Path, write: Callable[[Path], None]) -> bool:
    """Zapisuje przez plik tymczasowy i podmienia go pod docelową nazwę.

    Restart w trakcie zapisu nie zostawi obciętego pliku, a uploader nigdy
    nie zobaczy pliku w połowie i nie skasuje na jego podstawie oryginału.

    Zwraca False, gdy `write` nie utworzyło pliku - tak sygnalizuje brak
    danych eksport strumieniowy, który nie wie z góry, czy coś zapisze."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    write(tmp)
    if not tmp.exists():
        return False
    tmp.replace(path)
    return True
