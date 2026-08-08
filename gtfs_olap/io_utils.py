from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

def write_atomic(path: Path, write: Callable[[Path], None]) -> bool:
\
\
\
\
\

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    write(tmp)
    if not tmp.exists():
        return False
    tmp.replace(path)
    return True
