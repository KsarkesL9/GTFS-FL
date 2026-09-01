import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from flwr.server.strategy import FedAvg, FedProx
from loguru import logger

from gtfs_olap.detection import evaluate
from gtfs_olap.experiments import kind_subset, load_clients, load_events
from gtfs_olap.features import FEATURES
from gtfs_olap.federation import (
    Client, run_federation, train_centralized, train_local,
)
from gtfs_olap.model import (
    DEFAULT_PERCENTILE, alarm_threshold, reconstruction_errors,
)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", type=Path, default=Path("cechy"))
    ap.add_argument("--events", nargs="+", type=Path,
                    default=[Path("cechy_zdarzenia")],
                    help="strumienie raportowane; wynik to średnia i odchylenie")
    ap.add_argument("--tune-events", type=Path, default=None,
                    help="osobny strumień do wyboru percentyla progu")
    ap.add_argument("--out", type=Path, default=Path("wyniki_e1"))
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--local-epochs", type=int, default=3)
    ap.add_argument("--hidden", type=int, default=64)
    ap.add_argument("--mu", type=float, default=0.1)
    ap.add_argument("--percentile", type=float, default=DEFAULT_PERCENTILE)
    ap.add_argument("--percentile-grid", nargs="*", type=float,
                    default=[90, 95, 97.5, 99, 99.5])
    ap.add_argument("--drop-features", nargs="*", default=[],
                    help="cechy usuwane z wektora wejściowego, np. d dmax delta_d")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    clients = load_clients(args.features)
    keep = None
    if args.drop_features:

        keep = [i for i, f in enumerate(FEATURES) if f not in args.drop_features]
        clients = [Client(c.name, c.X_train[:, :, keep], c.X_val[:, :, keep])
                   for c in clients]
        logger.info(f"Usunięto {args.drop_features}; zostaje "
                    f"{len(keep)} cech: {[FEATURES[i] for i in keep]}")
    n_features = clients[0].X_train.shape[2]
    epochs = args.rounds * args.local_epochs
    logger.info(f"{len(clients)} klientów, {n_features} cech, "
                f"budżet {epochs} epok na wariant")

    kwargs = {"min_available_clients": len(clients),
              "min_fit_clients": len(clients)}
    logger.info("Wariant FedAvg...")
    fedavg, _ = run_federation(clients, FedAvg(**kwargs), args.rounds, n_features,
                               args.hidden, args.local_epochs, 0.0, args.seed)
    logger.info("Wariant FedProx...")
    fedprox, _ = run_federation(clients, FedProx(proximal_mu=args.mu, **kwargs),
                                args.rounds, n_features, args.hidden,
                                args.local_epochs, args.mu, args.seed)
    logger.info("Wariant scentralizowany...")
    central = train_centralized(clients, n_features, args.hidden, epochs, args.seed)

    variants: dict[str, object] = {"fedavg": fedavg, "fedprox": fedprox,
                                   "scentralizowany": central}

    locals_ = {c.name: train_local(c, n_features, args.hidden, epochs, args.seed)
               for c in clients}
    logger.info("Modele lokalne gotowe")

    percentile = args.percentile
    if args.tune_events:

        scores_by_q = {}
        for q in args.percentile_grid:
            f1 = []
            for client in clients:
                X, labels = load_events(args.tune_events, client.name)
                if keep is not None:
                    X = X[:, :, keep]
                for variant, model in {"lokalny": locals_[client.name],
                                       **variants}.items():
                    cal = np.concatenate([client.X_train, client.X_val])
                    t = alarm_threshold(reconstruction_errors(model, cal), q)
                    f1.append(evaluate(reconstruction_errors(model, X),
                                       labels, t).f1_event)
            scores_by_q[q] = float(np.mean(f1))
        percentile = max(scores_by_q, key=scores_by_q.get)
        logger.info(f"Percentyl progu wybrany na strumieniu strojącym: "
                    f"P{percentile} " + ", ".join(
                        f"P{q}={v:.3f}" for q, v in scores_by_q.items()))

    rows = []
    for stream in args.events:
      for client in clients:
        X_test, labels = load_events(stream, client.name)
        if keep is not None:
            X_test = X_test[:, :, keep]
        local = locals_[client.name]

        for variant, model in {"lokalny": local, **variants}.items():

            calibration = np.concatenate([client.X_train, client.X_val])
            threshold = alarm_threshold(
                reconstruction_errors(model, calibration), percentile)
            errors = reconstruction_errors(model, X_test)

            scores = evaluate(errors, labels, threshold)
            rows.append({"wariant": variant, "klient": client.name,
                         "strumien": stream.name, "typ": "wszystkie",
                         "prog": threshold, **scores.as_dict()})
            for kind in sorted(labels.loc[labels["event_id"] >= 0,
                                          "event_kind"].dropna().unique()):
                e, l = kind_subset(labels, errors, kind)
                rows.append({"wariant": variant, "klient": client.name,
                             "strumien": stream.name, "typ": kind,
                             "prog": threshold,
                             **evaluate(e, l, threshold).as_dict()})

    frame = pd.DataFrame(rows)
    args.out.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(args.out / "e1.parquet", index=False)

    overall = frame[frame["typ"] == "wszystkie"].groupby("wariant")
    summary = overall[["f1_window", "pr_auc_window", "f1_event", "pr_auc_event",
                       "recall_event", "delay_median_min",
                       "false_alarms_per_day"]].mean()
    order = ["lokalny", "fedavg", "fedprox", "scentralizowany"]
    summary = summary.reindex([v for v in order if v in summary.index])

    spread = (frame[frame["typ"] == "wszystkie"]
              .groupby(["wariant", "strumien"])["f1_event"].mean()
              .groupby("wariant").std())

    print(f"\n=== E1: {len(clients)} klientów, {len(args.events)} strumieni, "
          f"próg P{percentile} ===")
    print(f"{'wariant':<17}{'F1 okna':>9}{'AP okna':>9}{'F1 zdarz':>10}"
          f"{'AP zdarz':>10}{'czułość':>9}{'opóźn.min':>11}{'fałsz/dobę':>12}")
    for variant, r in summary.iterrows():
        print(f"{variant:<17}{r.f1_window:>9.3f}{r.pr_auc_window:>9.3f}"
              f"{r.f1_event:>10.3f}{r.pr_auc_event:>10.3f}{r.recall_event:>9.3f}"
              f"{r.delay_median_min:>11.1f}{r.false_alarms_per_day:>12.2f}")

    print("\n=== czułość na poziomie zdarzeń, wg typu ===")
    per_kind = (frame[frame["typ"] != "wszystkie"]
                .pivot_table(index="wariant", columns="typ",
                             values="recall_event", aggfunc="mean")
                .reindex([v for v in order if v in summary.index]))
    print(per_kind.round(3).to_string())

    payload = {"percentile": percentile,
               "f1_event_sd_between_streams": spread.round(4).to_dict(),
               "streams": [s.name for s in args.events], "rounds": args.rounds,
               "epochs": epochs, "seed": args.seed,
               "summary": summary.round(4).to_dict(),
               "recall_by_kind": per_kind.round(4).to_dict()}
    (args.out / "e1.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.success(f"Zapisano {args.out}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
