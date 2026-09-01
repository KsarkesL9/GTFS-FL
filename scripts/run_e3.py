import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from flwr.server.strategy import FedAvg
from loguru import logger

from gtfs_olap.detection import evaluate
from gtfs_olap.federation import Client, run_federation, train_local
from gtfs_olap.model import alarm_threshold, reconstruction_errors
from gtfs_olap.experiments import kind_subset, load_clients, load_events

def _federated(clients: list[Client], n_features: int, hidden: int,
               rounds: int, local_epochs: int, seed: int):
    strategy = FedAvg(min_available_clients=len(clients),
                      min_fit_clients=len(clients))
    model, _ = run_federation(clients, strategy, rounds, n_features, hidden,
                              local_epochs, 0.0, seed)
    return model

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", type=Path, default=Path("cechy"))
    ap.add_argument("--contaminated", type=Path, default=Path("cechy_a2_trening"))
    ap.add_argument("--events", type=Path, default=Path("cechy_zdarzenia"))
    ap.add_argument("--out", type=Path, default=Path("wyniki_e3"))
    ap.add_argument("--kind", default="A2")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--local-epochs", type=int, default=3)
    ap.add_argument("--hidden", type=int, default=64)
    ap.add_argument("--percentile", type=float, default=90.0)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    clean = load_clients(args.features)
    names = [c.name for c in clean]
    n_features = clean[0].X_train.shape[2]
    epochs = args.rounds * args.local_epochs
    logger.info(f"{len(clean)} klientów, biorcą jest po kolei każdy z nich")

    dirty = {}
    for name in names:
        X = np.load(args.contaminated / f"client={name}" / "X_train.npy")
        boundary = int(len(X) * 0.8)
        dirty[name] = (X[:boundary], X[boundary:])

    logger.info("Kontrola: federacja na samych czystych danych...")
    control = _federated(clean, n_features, args.hidden, args.rounds,
                         args.local_epochs, args.seed)

    rows = []
    for i, recipient in enumerate(names):
        donors = [names[(i + 1) % len(names)], names[(i + 2) % len(names)]]
        logger.info(f"Biorca {recipient}, dawcy {donors}")

        mixed = [Client(c.name, *dirty[c.name]) if c.name in donors else c
                 for c in clean]
        transfer = _federated(mixed, n_features, args.hidden, args.rounds,
                              args.local_epochs, args.seed)
        client = next(c for c in clean if c.name == recipient)
        local = train_local(client, n_features, args.hidden, epochs, args.seed)

        X_test, labels = load_events(args.events, recipient)
        calibration = np.concatenate([client.X_train, client.X_val])

        for variant, model in (("lokalny", local),
                               ("federacja (A2 u dawców)", transfer),
                               ("federacja (wszyscy czyści)", control)):
            threshold = alarm_threshold(
                reconstruction_errors(model, calibration), args.percentile)
            errors = reconstruction_errors(model, X_test)
            e, l = kind_subset(labels, errors, args.kind)
            rows.append({"biorca": recipient, "dawcy": ", ".join(donors),
                         "wariant": variant, **evaluate(e, l, threshold).as_dict()})

    frame = pd.DataFrame(rows)
    args.out.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(args.out / "e3.parquet", index=False)

    order = ["lokalny", "federacja (A2 u dawców)", "federacja (wszyscy czyści)"]
    summary = (frame.groupby("wariant")[["f1_event", "recall_event",
                                         "pr_auc_window", "false_alarms_per_day"]]
               .agg(["mean", "std"]).reindex(order))

    print(f"\n=== E3: wykrywanie {args.kind} u biorcy, {len(names)} układów ===")
    print(f"{'wariant':<28}{'F1 zdarzeń':>18}{'czułość':>16}{'AP okna':>16}")
    for variant in order:
        r = summary.loc[variant]
        print(f"{variant:<28}"
              f"{r[('f1_event','mean')]:>11.3f} ±{r[('f1_event','std')]:<5.3f}"
              f"{r[('recall_event','mean')]:>9.3f} ±{r[('recall_event','std')]:<5.3f}"
              f"{r[('pr_auc_window','mean')]:>9.3f} ±{r[('pr_auc_window','std')]:<5.3f}")

    pivot = frame.pivot_table(index="biorca", columns="wariant",
                              values="recall_event").reindex(columns=order)
    pivot["zysk transferu"] = (pivot["federacja (A2 u dawców)"] - pivot["lokalny"])
    print("\n=== czułość na A2 u poszczególnych biorców ===")
    print(pivot.round(3).to_string())

    wins = int((pivot["zysk transferu"] > 0).sum())
    print(f"\nfederacja z A2 u dawców lepsza od lokalnego u {wins} z {len(names)} biorców")

    payload = {"kind": args.kind, "percentile": args.percentile,
               "rounds": args.rounds, "seed": args.seed,
               "wins_over_local": wins, "n_recipients": len(names),
               "summary": {v: {k: float(summary.loc[v][(k, "mean")])
                               for k in ["f1_event", "recall_event",
                                         "pr_auc_window", "false_alarms_per_day"]}
                           for v in order}}
    (args.out / "e3.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.success(f"Zapisano {args.out}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
