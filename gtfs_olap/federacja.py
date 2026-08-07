"""Symulacja uczenia federacyjnego (Moduł C, rozdz. 7.3).

Agregacja jest wykonywana przez prawdziwe obiekty strategii Flower - FedAvg,
FedProx, FedMedian, FedTrimmedAvg - więc logika uśredniania jest tą samą,
która działałaby we wdrożeniu rozproszonym.

Klienci są uruchamiani SEKWENCYJNIE w jednym procesie, bez silnika Ray.
Rozdz. 13 wprost wymienia sekwencyjną symulację klientów jako sposób na
ograniczoną moc obliczeniową, a przy dziewięciu klientach równoległość nie
daje nic poza dodatkowym trybem awarii.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from flwr.common import Code, FitRes, Status, ndarrays_to_parameters, parameters_to_ndarrays
from flwr.server.strategy import Strategy
from loguru import logger

from gtfs_olap.model import (
    AutoenkoderGRU, bledy_rekonstrukcji, tablice_do_wag, trenuj, wagi_do_tablic,
)


@dataclass
class Klient:
    nazwa: str
    X_tren: np.ndarray
    X_wal: np.ndarray


@dataclass
class PrzebiegRundy:
    runda: int
    straty: dict[str, float] = field(default_factory=dict)
    walidacja: dict[str, float] = field(default_factory=dict)
    mb_na_klienta: float = 0.0
    czas_s: float = 0.0


def _rozmiar_mb(tablice: list[np.ndarray]) -> float:
    return sum(t.nbytes for t in tablice) / 1_048_576


def uruchom_federacje(klienci: list[Klient], strategia: Strategy, rundy: int,
                      n_cech: int, ukryte: int = 64, epoki_lokalne: int = 3,
                      mu: float = 0.0, ziarno: int = 0
                      ) -> tuple[AutoenkoderGRU, list[PrzebiegRundy]]:
    """Pełny obieg federacyjny. Zwraca model globalny i przebieg rund."""
    import time

    import torch

    torch.manual_seed(ziarno)
    np.random.seed(ziarno)

    globalny = AutoenkoderGRU(n_cech, ukryte)
    parametry = ndarrays_to_parameters(wagi_do_tablic(globalny))
    historia: list[PrzebiegRundy] = []

    for runda in range(1, rundy + 1):
        t0 = time.monotonic()
        wagi_globalne = parameters_to_ndarrays(parametry)
        przebieg = PrzebiegRundy(runda=runda)
        wyniki = []

        for k in klienci:
            lokalny = AutoenkoderGRU(n_cech, ukryte)
            tablice_do_wag(lokalny, wagi_globalne)
            strata = trenuj(lokalny, k.X_tren, epoki=epoki_lokalne, mu=mu,
                            globalne=wagi_globalne)
            aktualizacja = wagi_do_tablic(lokalny)
            przebieg.straty[k.nazwa] = strata
            przebieg.mb_na_klienta = _rozmiar_mb(aktualizacja)
            wyniki.append((None, FitRes(
                status=Status(Code.OK, ""),
                parameters=ndarrays_to_parameters(aktualizacja),
                num_examples=len(k.X_tren),
                metrics={"strata": strata},
            )))

        nowe, _ = strategia.aggregate_fit(runda, wyniki, [])
        if nowe is None:
            raise RuntimeError(f"Strategia nie zwróciła parametrów w rundzie {runda}")
        parametry = nowe

        tablice_do_wag(globalny, parameters_to_ndarrays(parametry))
        for k in klienci:
            bledy = bledy_rekonstrukcji(globalny, k.X_wal)
            przebieg.walidacja[k.nazwa] = float(bledy.mean()) if len(bledy) else float("nan")

        przebieg.czas_s = time.monotonic() - t0
        historia.append(przebieg)
        sr = np.nanmean(list(przebieg.walidacja.values()))
        logger.info(f"runda {runda:>2}/{rundy}  walidacja(śr.)={sr:.5f}  "
                    f"{przebieg.mb_na_klienta:.2f} MB/klienta  {przebieg.czas_s:.1f}s")

    return globalny, historia


def trenuj_lokalnie(klient: Klient, n_cech: int, ukryte: int = 64,
                    epoki: int = 30, ziarno: int = 0) -> AutoenkoderGRU:
    """Model czysto lokalny - punkt odniesienia dla eksperymentu E1."""
    import torch

    torch.manual_seed(ziarno)
    np.random.seed(ziarno)
    model = AutoenkoderGRU(n_cech, ukryte)
    trenuj(model, klient.X_tren, epoki=epoki)
    return model
