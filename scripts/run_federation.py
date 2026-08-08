\
\
\
\
\
\
\
\

import argparse
import json
from pathlib import Path

import numpy as np
from flwr.server.strategy import FedAvg, FedMedian, FedProx, FedTrimmedAvg
from loguru import logger

from gtfs_olap.features import FEATURES
from gtfs_olap.federation import Client, run_federation, train_local
from gtfs_olap.model import alarm_threshold, reconstruction_errors

STRATEGIES = {
    "fedavg": FedAvg,
    "fedprox": FedProx,

    "fedmedian": FedMedian,
    "fedtrimmed": FedTrimmedAvg,
}

def load_clients(directory: Path, val_share: float = 0.2) -> list[Client]:
    clients = []
    for path in sorted(directory.glob("client=*")):
        X = np.load(path / "X_train.npy")
        if len(X) < 10:
            logger.warning(f"{path.name}: tylko {len(X)} sekwencji, pomijam")
            continue

        boundary = int(len(X) * (1 - val_share))
        clients.append(Client(path.name.split("=", 1)[1], X[:boundary], X[boundary:]))
    return clients

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", type=Path, default=Path("cechy"))
    ap.add_argument("--out", type=Path, default=Path("wyniki"))
    ap.add_argument("--strategy", choices=list(STRATEGIES), default="fedavg")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--local-epochs", type=int, default=3)
    ap.add_argument("--hidden", type=int, default=64)
    ap.add_argument("--mu", type=float, default=0.1, help="tylko dla fedprox")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    clients = load_clients(args.features)
    if not clients:
        logger.error("Brak klientów z wystarczającą liczbą sekwencji")
        return 1
    n_features = clients[0].X_train.shape[2]
    assert n_features == len(FEATURES), \
        f"{n_features} cech w danych, {len(FEATURES)} w module"

    logger.info(f"{len(clients)} klientów, "
                f"{sum(len(c.X_train) for c in clients)} sekwencji treningowych, "
                f"{n_features} cech")

    kwargs = {
        "min_available_clients": len(clients),
        "min_fit_clients": len(clients),
        "fit_metrics_aggregation_fn": lambda metrics: {
            "loss": float(np.mean([m["loss"] for _, m in metrics]))},
    }
    if args.strategy == "fedprox":
        kwargs["proximal_mu"] = args.mu
    strategy = STRATEGIES[args.strategy](**kwargs)
    mu = args.mu if args.strategy == "fedprox" else 0.0

    global_model, history = run_federation(
        clients, strategy, args.rounds, n_features, args.hidden,
        args.local_epochs, mu, args.seed)

    local_epochs = args.rounds * args.local_epochs
    logger.info(f"Modele czysto lokalne, {local_epochs} epok "
                f"(tyle samo co federacja) - odniesienie dla E1...")

    print(f"\n{'klient':<26} {'sekw':>6} {'federacja':>11} "
          f"{'lokalny':>11} {'zysk':>8} {'próg 99,5':>11}")
    comparison = {}
    for client in clients:
        e_fed = reconstruction_errors(global_model, client.X_val)
        local_model = train_local(client, n_features, args.hidden,
                                  epochs=local_epochs, seed=args.seed)
        e_local = reconstruction_errors(local_model, client.X_val)
        gain = 100 * (e_local.mean() - e_fed.mean()) / e_local.mean()
        comparison[client.name] = {
            "federated": float(e_fed.mean()),
            "local": float(e_local.mean()),
            "gain_pct": float(gain),
            "threshold": alarm_threshold(e_fed),
        }
        print(f"{client.name:<26} {len(client.X_train):>6} {e_fed.mean():>11.5f} "
              f"{e_local.mean():>11.5f} {gain:>7.1f}% "
              f"{alarm_threshold(e_fed):>11.5f}")

    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{args.strategy}.json"
    path.write_text(json.dumps({
        "strategy": args.strategy, "rounds": args.rounds, "mu": mu,
        "seed": args.seed, "parameters": global_model.parameter_count(),
        "mb_per_client_per_round": history[-1].mb_per_client,
        "history": [{"round": h.round_no, "validation": h.validation,
                     "seconds": h.seconds} for h in history],
        "comparison": comparison,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nparametrów modelu: {global_model.parameter_count():,}")
    print(f"narzut komunikacyjny: {history[-1].mb_per_client:.2f} "
          f"MB na klienta na rundę")
    logger.success(f"Zapisano {path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
