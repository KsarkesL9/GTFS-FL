"""Sprawdza niezmienniki potoku cech i agregacji federacyjnej.

Uruchamiać po każdej zmianie w features.py, model.py lub federation.py.

    python scripts/verify_pipeline.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from flwr.common import (
    Code, FitRes, Status, ndarrays_to_parameters, parameters_to_ndarrays,
)
from flwr.server.strategy import FedAvg

from gtfs_olap.clients import CLIENTS, OPERATOR_TO_CLIENT
from gtfs_olap.features import (
    FEATURES, build_features, daily_profile, load_facts,
)
from gtfs_olap.model import GRUAutoencoder, weights_to_arrays

FAILURES: list[str] = []


def check(condition: bool, description: str, detail: str = "") -> None:
    if condition:
        print(f"  OK   {description}")
    else:
        print(f"  BLAD {description}  {detail}")
        FAILURES.append(description)


def client_split(facts: pd.DataFrame) -> None:
    print("\n[1] Podzial na klientow federacji")
    operators = [o for group in CLIENTS.values() for o in group]
    check(len(operators) == len(set(operators)),
          "zaden operator nie nalezy do dwoch klientow")
    check(6 <= len(CLIENTS) <= 10,
          f"liczba klientow w zakresie 6-10 (jest {len(CLIENTS)})")
    missing = set(facts.operator_id.unique()) - set(operators)
    check(not missing, "kazdy operator z danych ma przypisanego klienta",
          f"brakuje: {missing}")
    check(facts.operator_id.map(OPERATOR_TO_CLIENT).notna().all(),
          "zadna obserwacja nie gubi sie przy mapowaniu")


def feature_formulas(df: pd.DataFrame) -> None:
    print("\n[2] Poprawnosc wyliczenia cech")
    print("    (na surowych wartosciach, przed standaryzacja)")
    full = df[df.complete]
    check(np.allclose(full.d, full.S / full.n), "d(t) = S(t)/n(t)")
    check(np.allclose(full.w, full.q / full.n), "w(t) = q(t)/n(t)")
    check(np.allclose(full.p, full.u / full.n), "p(t) = u(t)/n(t)")
    check(df[~df.complete].d.isna().all(), "puste okna nie maja wyliczonego d(t)")
    check(df.groupby("client").window.apply(
        lambda s: s.diff().dropna().eq(pd.Timedelta("15min")).all()).all(),
        "siatka czasu jest ciagla co 15 minut")


def no_leakage(df: pd.DataFrame) -> None:
    print("\n[3] Brak przecieku danych testowych do cech")
    train = df[df.split == "train"]
    test = df[df.split == "test"]
    check(train.window.max() < test.window.min(),
          "podzial jest czasowy - caly trening przed calym testem")

    # Profil liczony z calosci roznilby sie od tego z samego treningu.
    keys = ["client", "hour", "workday"]
    from_train = daily_profile(train).set_index(keys)["m"]
    from_all = daily_profile(df).set_index(keys)["m"]
    common = from_train.index.intersection(from_all.index)
    check(not np.allclose(from_train[common], from_all[common]),
          "profil dobowy policzony z treningu, nie z calosci")

    varying = [f for f in FEATURES if train[f].std() > 1e-9]
    max_mean = np.abs(train[varying].mean()).max()
    check(max_mean < 0.05,
          f"standaryzacja wyzerowala srednia na TRENINGU (max |sr|={max_mean:.4f})")


def sequence_integrity(directory: Path, df: pd.DataFrame) -> None:
    print("\n[4] Sekwencje wejsciowe modelu")
    total, shape_ok, nan_ok = 0, True, True
    for path in sorted(directory.glob("client=*")):
        for name in ("X_train.npy", "X_test.npy"):
            X = np.load(path / name)
            total += len(X)
            if len(X) and (X.shape[1] != 8 or X.shape[2] != len(FEATURES)):
                shape_ok = False
            if len(X) and np.isnan(X).any():
                nan_ok = False
    check(shape_ok, f"kazda sekwencja ma ksztalt (8, {len(FEATURES)})")
    check(nan_ok, "zadna sekwencja nie zawiera NaN")
    check(total > 0, f"zbudowano sekwencje (razem {total})")

    # Sekwencje moga powstac tylko z osmiu kolejnych KOMPLETNYCH okien.
    possible = 0
    for _, group in df.sort_values("window").groupby("client"):
        flags = group.complete.to_numpy()
        possible += sum(flags[i - 7:i + 1].all() for i in range(7, len(flags)))
    check(total <= possible,
          f"liczba sekwencji nie przekracza liczby ciaglych okien "
          f"({total} <= {possible})")


def flower_aggregation() -> None:
    print("\n[5] Agregacja federacyjna (Flower FedAvg)")
    torch.manual_seed(0)
    models = [GRUAutoencoder(len(FEATURES), 16) for _ in range(3)]
    sizes = [100, 200, 700]
    results = [(None, FitRes(status=Status(Code.OK, ""),
                             parameters=ndarrays_to_parameters(weights_to_arrays(m)),
                             num_examples=n, metrics={}))
               for m, n in zip(models, sizes)]

    strategy = FedAvg(min_available_clients=3, min_fit_clients=3)
    aggregated, _ = strategy.aggregate_fit(1, results, [])
    actual = parameters_to_ndarrays(aggregated)

    total = sum(sizes)
    expected = [sum(w * n for w, n in zip(layer, sizes)) / total
                for layer in zip(*[weights_to_arrays(m) for m in models])]
    check(all(np.allclose(a, b, atol=1e-6) for a, b in zip(actual, expected)),
          "wynik agregacji rowny sredniej wazonej liczebnoscia klientow")
    check(not np.allclose(actual[0], weights_to_arrays(models[0])[0]),
          "model globalny rozni sie od modelu pojedynczego klienta")


def autoencoder_shape() -> None:
    print("\n[6] Autoenkoder")
    model = GRUAutoencoder(len(FEATURES), 64)
    x = torch.randn(5, 8, len(FEATURES))
    check(model(x).shape == x.shape, "wyjscie ma ksztalt wejscia")
    check(10_000 < model.parameter_count() < 1_000_000,
          f"rozmiar modelu w widelkach z rozdz. 13 ({model.parameter_count():,})")


def main() -> int:
    data, features_dir = Path("dane"), Path("cechy")
    facts = load_facts(data / "fakty")
    raw = build_features(data / "fakty", data / "lookup",
                         pd.read_parquet(data / "wymiary" / "dim_data.parquet"))
    processed = pd.read_parquet(features_dir / "features.parquet")

    client_split(facts)
    feature_formulas(raw)
    no_leakage(processed)
    sequence_integrity(features_dir, processed)
    flower_aggregation()
    autoencoder_shape()

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"NIEPOWODZENIE: {len(FAILURES)} sprawdzen nie przeszlo")
        for failure in FAILURES:
            print("  -", failure)
        return 1
    print("WSZYSTKIE SPRAWDZENIA PRZESZLY")
    return 0


if __name__ == "__main__":
    sys.exit(main())
