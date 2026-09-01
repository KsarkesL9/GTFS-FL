from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

from gtfs_olap.federation import Client

MIN_SEQUENCES = 10

def load_clients(directory: Path, val_share: float = 0.2) -> list[Client]:

    clients = []
    for path in sorted(directory.glob("client=*")):
        X = np.load(path / "X_train.npy")
        if len(X) < MIN_SEQUENCES:
            logger.warning(f"{path.name}: tylko {len(X)} sekwencji, pomijam")
            continue
        boundary = int(len(X) * (1 - val_share))
        clients.append(Client(path.name.split("=", 1)[1], X[:boundary], X[boundary:]))
    return clients

def load_events(directory: Path, name: str) -> tuple[np.ndarray, pd.DataFrame]:
    path = directory / f"client={name}"
    return np.load(path / "X_test.npy"), pd.read_parquet(path / "test_labels.parquet")

def kind_subset(labels: pd.DataFrame, errors: np.ndarray,
                kind: str) -> tuple[np.ndarray, pd.DataFrame]:

    keep = (labels["event_id"] < 0) | (labels["event_kind"] == kind)
    return errors[keep.to_numpy()], labels[keep].reset_index(drop=True)
