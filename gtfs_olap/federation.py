"""Symulacja uczenia federacyjnego (Moduł C, rozdz. 7.3).

Agregacja jest wykonywana przez prawdziwe obiekty strategii Flower - FedAvg,
FedProx, FedMedian, FedTrimmedAvg - więc logika uśredniania jest tą samą,
która działałaby we wdrożeniu rozproszonym.

Klienci są uruchamiani sekwencyjnie w jednym procesie, bez silnika Ray.
Rozdz. 13 wprost wymienia sekwencyjną symulację klientów jako sposób na
ograniczoną moc obliczeniową, a przy dziewięciu klientach równoległość nie
daje nic poza dodatkowym trybem awarii.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import torch
from flwr.common import (
    Code, FitRes, Status, ndarrays_to_parameters, parameters_to_ndarrays,
)
from flwr.server.strategy import Strategy
from loguru import logger

from gtfs_olap.model import (
    GRUAutoencoder, arrays_to_weights, reconstruction_errors, train,
    weights_to_arrays,
)


@dataclass
class Client:
    name: str
    X_train: np.ndarray
    X_val: np.ndarray


@dataclass
class RoundResult:
    round_no: int
    losses: dict[str, float] = field(default_factory=dict)
    validation: dict[str, float] = field(default_factory=dict)
    mb_per_client: float = 0.0
    seconds: float = 0.0


def _size_mb(arrays: list[np.ndarray]) -> float:
    return sum(a.nbytes for a in arrays) / 1_048_576


def _seed(value: int) -> None:
    torch.manual_seed(value)
    np.random.seed(value)


def run_federation(clients: list[Client], strategy: Strategy, rounds: int,
                   n_features: int, hidden: int = 64, local_epochs: int = 3,
                   mu: float = 0.0, seed: int = 0
                   ) -> tuple[GRUAutoencoder, list[RoundResult]]:
    """Pełny obieg federacyjny. Zwraca model globalny i przebieg rund."""
    _seed(seed)

    global_model = GRUAutoencoder(n_features, hidden)
    parameters = ndarrays_to_parameters(weights_to_arrays(global_model))
    history: list[RoundResult] = []

    for round_no in range(1, rounds + 1):
        started = time.monotonic()
        global_weights = parameters_to_ndarrays(parameters)
        result = RoundResult(round_no=round_no)
        fit_results = []

        for client in clients:
            local = GRUAutoencoder(n_features, hidden)
            arrays_to_weights(local, global_weights)
            loss = train(local, client.X_train, epochs=local_epochs, mu=mu,
                         global_weights=global_weights)
            update = weights_to_arrays(local)
            result.losses[client.name] = loss
            result.mb_per_client = _size_mb(update)
            fit_results.append((None, FitRes(
                status=Status(Code.OK, ""),
                parameters=ndarrays_to_parameters(update),
                num_examples=len(client.X_train),
                metrics={"loss": loss},
            )))

        aggregated, _ = strategy.aggregate_fit(round_no, fit_results, [])
        if aggregated is None:
            raise RuntimeError(f"Strategia nie zwróciła parametrów w rundzie {round_no}")
        parameters = aggregated

        arrays_to_weights(global_model, parameters_to_ndarrays(parameters))
        for client in clients:
            errors = reconstruction_errors(global_model, client.X_val)
            result.validation[client.name] = (
                float(errors.mean()) if len(errors) else float("nan"))

        result.seconds = time.monotonic() - started
        history.append(result)
        mean_val = np.nanmean(list(result.validation.values()))
        logger.info(f"runda {round_no:>2}/{rounds}  walidacja(śr.)={mean_val:.5f}  "
                    f"{result.mb_per_client:.2f} MB/klienta  {result.seconds:.1f}s")

    return global_model, history


def train_local(client: Client, n_features: int, hidden: int = 64,
                epochs: int = 30, seed: int = 0) -> GRUAutoencoder:
    """Model czysto lokalny - punkt odniesienia dla eksperymentu E1."""
    _seed(seed)
    model = GRUAutoencoder(n_features, hidden)
    train(model, client.X_train, epochs=epochs)
    return model
