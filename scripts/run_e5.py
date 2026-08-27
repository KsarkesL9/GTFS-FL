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
import pandas as pd
from flwr.server.strategy import FedAvg, FedMedian, FedTrimmedAvg
from loguru import logger

from gtfs_olap.detection import evaluate
from gtfs_olap.experiments import kind_subset, load_clients, load_events
from gtfs_olap.features import FEATURES
from gtfs_olap.federation import Client, run_federation
from gtfs_olap.model import alarm_threshold, reconstruction_errors

POISON_SIGMA = 1.0
UPDATE_SCALE = 10.0

AGGREGATIONS = {
    "uśredniająca": lambda n: FedAvg(min_available_clients=n, min_fit_clients=n),
    "medianowa": lambda n: FedMedian(min_available_clients=n, min_fit_clients=n),
    "przycinana": lambda n: FedTrimmedAvg(min_available_clients=n, min_fit_clients=n),
}

def poison_delays(X: np.ndarray) -> np.ndarray:
\
\
\
\

    out = X.copy()
    for name, shift in (("d", -POISON_SIGMA), ("dmax", -POISON_SIGMA),
                        ("r", -POISON_SIGMA), ("delta_d", -POISON_SIGMA),
                        ("w", +POISON_SIGMA)):
        if name in FEATURES:
            out[:, :, FEATURES.index(name)] += shift
    return out

def scaling_attack(name: str):
    def hook(client: str, update: list[np.ndarray],
             global_weights: list[np.ndarray]) -> list[np.ndarray]:
        if client != name:
            return update

        return [g + UPDATE_SCALE * (u - g) for u, g in zip(update, global_weights)]
    return hook

def measure(model, clients: list[Client], events: Path, attacker: str,
            percentile: float, kind: str | None = None) -> dict:
    rows = []
    for client in clients:
        if client.name == attacker:
            continue
        X, labels = load_events(events, client.name)
        threshold = alarm_threshold(
            reconstruction_errors(
                model, np.concatenate([client.X_train, client.X_val])), percentile)
        errors = reconstruction_errors(model, X)
        if kind:
            errors, labels = kind_subset(labels, errors, kind)
        rows.append(evaluate(errors, labels, threshold).as_dict())
    mean = pd.DataFrame(rows).mean()
    return {"f1_event": float(mean.f1_event),
            "recall_event": float(mean.recall_event),
            "pr_auc_window": float(mean.pr_auc_window),
            "false_alarms_per_day": float(mean.false_alarms_per_day)}

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", type=Path, default=Path("cechy"))
    ap.add_argument("--events", type=Path, default=Path("cechy_zdarzenia"))
    ap.add_argument("--a1-train", type=Path, default=Path("cechy_a1_trening"))
    ap.add_argument("--out", type=Path, default=Path("wyniki_e5"))
    ap.add_argument("--attacker", default="pkm_katowice")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--local-epochs", type=int, default=3)
    ap.add_argument("--hidden", type=int, default=64)
    ap.add_argument("--percentile", type=float, default=90.0)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    clients = load_clients(args.features)
    n_features = clients[0].X_train.shape[2]
    assert any(c.name == args.attacker for c in clients), "brak takiego klienta"
    logger.info(f"{len(clients)} klientów, złośliwy: {args.attacker}, "
                f"pomiar u {len(clients) - 1} uczciwych")

    def federate(cs, aggregation, hook=None):
        model, _ = run_federation(
            cs, AGGREGATIONS[aggregation](len(cs)), args.rounds, n_features,
            args.hidden, args.local_epochs, 0.0, args.seed, update_hook=hook)
        return model

    poisoned = [Client(c.name, poison_delays(c.X_train), poison_delays(c.X_val))
                if c.name == args.attacker else c for c in clients]

    dirty_a1 = np.load(args.a1_train / f"client={args.attacker}" / "X_train.npy")
    split = int(len(dirty_a1) * 0.8)
    targeted = [Client(c.name, dirty_a1[:split], dirty_a1[split:])
                if c.name == args.attacker else c for c in clients]

    rows = []
    for aggregation in AGGREGATIONS:
        logger.info(f"T1/T2/T3/T4 przy agregacji {aggregation}...")
        base = measure(federate(clients, aggregation), clients, args.events,
                       args.attacker, args.percentile)
        rows.append({"test": "T1 bez ataku", "agregacja": aggregation, **base})

        label = "T2 zatrucie danych" if aggregation == "uśredniająca" \
            else "T3 zatrucie danych"
        rows.append({"test": label, "agregacja": aggregation,
                     **measure(federate(poisoned, aggregation), clients,
                               args.events, args.attacker, args.percentile)})

        rows.append({"test": "T4 skalowanie x10", "agregacja": aggregation,
                     **measure(federate(clients, aggregation,
                                        scaling_attack(args.attacker)),
                               clients, args.events, args.attacker,
                               args.percentile)})

        logger.info(f"T5 atak ukierunkowany na A1 przy agregacji {aggregation}...")
        base_a1 = measure(federate(clients, aggregation), clients, args.events,
                          args.attacker, args.percentile, kind="A1")
        rows.append({"test": "T5 odniesienie (A1)", "agregacja": aggregation,
                     **base_a1})
        rows.append({"test": "T5 atak ukierunkowany (A1)", "agregacja": aggregation,
                     **measure(federate(targeted, aggregation), clients,
                               args.events, args.attacker, args.percentile,
                               kind="A1")})

    frame = pd.DataFrame(rows)
    args.out.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(args.out / "e5.parquet", index=False)

    pivot = frame.pivot_table(index="test", columns="agregacja",
                              values="f1_event", sort=False)
    print(f"\n=== E5: F1 zdarzeń u {len(clients) - 1} uczciwych klientów ===")
    print(pivot.round(3).to_string())

    verdicts = {}
    for test in ("T2 zatrucie danych", "T4 skalowanie x10"):
        source = "T3 zatrucie danych" if test.startswith("T2") else test
        base = pivot.loc["T1 bez ataku", "uśredniająca"]
        attacked = pivot.loc[test if test in pivot.index else source, "uśredniająca"]
        damage = base - attacked
        if abs(damage) < 1e-9:
            verdicts[test] = {"damage": 0.0, "note": "atak nie zaszkodził"}
            continue
        for defence in ("medianowa", "przycinana"):
            defended = pivot.loc[source, defence]
            recovered = (defended - attacked) / damage
            verdicts[f"{test} / {defence}"] = {
                "damage": float(damage), "recovered": float(recovered),
                "met": bool(recovered >= 0.5)}
    print("\n=== K3: udział zniwelowanego spadku ===")
    for key, v in verdicts.items():
        if "note" in v:
            print(f"{key:<40} {v['note']} (spadek {v['damage']:+.3f})")
        else:
            print(f"{key:<40} spadek {v['damage']:+.3f}  "
                  f"zniwelowane {v['recovered']:+.1%}  "
                  f"{'SPEŁNIONE' if v['met'] else 'NIESPEŁNIONE'}")

    (args.out / "e5.json").write_text(json.dumps({
        "attacker": args.attacker, "percentile": args.percentile,
        "poison_sigma": POISON_SIGMA, "update_scale": UPDATE_SCALE,
        "f1_event": pivot.round(4).to_dict(), "k3": verdicts,
        "t6": "nie dotyczy: detekcja dryfu działa lokalnie u klienta, "
              "więc klient nie ma jak zawyżyć błędów widzianych przez innych",
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.success(f"Zapisano {args.out}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
