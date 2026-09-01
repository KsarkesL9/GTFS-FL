import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from flwr.server.strategy import FedAvg
from loguru import logger

from gtfs_olap.detection import evaluate
from gtfs_olap.experiments import load_clients, load_events
from gtfs_olap.features import FEATURES
from gtfs_olap.federation import Client, run_federation
from gtfs_olap.model import alarm_threshold, reconstruction_errors

GROUPS = {
    "opóźnienie": ["d", "dmax", "delta_d"],
    "odchylenie od profilu": ["r"],
    "punktualność": ["w"],
    "natężenie": ["n"],
    "pominięcia": ["p"],
    "nieregularność odstępów": ["h"],
    "kontekst czasowy": ["sin_hour", "cos_hour", "workday"],
}

def _blank(X: np.ndarray, columns: list[int]) -> np.ndarray:
    out = X.copy()
    out[:, :, columns] = 0.0
    return out

def _run(clients: list[Client], events: Path, percentile: float, rounds: int,
         local_epochs: int, hidden: int, seed: int,
         blank: list[int] | None = None) -> dict:
    if blank:
        clients = [Client(c.name, _blank(c.X_train, blank), _blank(c.X_val, blank))
                   for c in clients]
    n_features = clients[0].X_train.shape[2]
    model, history = run_federation(
        clients, FedAvg(min_available_clients=len(clients),
                        min_fit_clients=len(clients)),
        rounds, n_features, hidden, local_epochs, 0.0, seed)

    rows = []
    for client in clients:
        X, labels = load_events(events, client.name)
        if blank:
            X = _blank(X, blank)
        threshold = alarm_threshold(
            reconstruction_errors(
                model, np.concatenate([client.X_train, client.X_val])), percentile)
        rows.append(evaluate(reconstruction_errors(model, X), labels,
                             threshold).as_dict())
    mean = pd.DataFrame(rows).mean()
    return {"f1_event": float(mean.f1_event),
            "recall_event": float(mean.recall_event),
            "pr_auc_window": float(mean.pr_auc_window),
            "false_alarms_per_day": float(mean.false_alarms_per_day),
            "mb_per_round": float(history[-1].mb_per_client),
            "seconds_per_round": float(np.mean([h.seconds for h in history]))}

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("wyniki_e4"))
    ap.add_argument("--percentile", type=float, default=90.0)
    ap.add_argument("--hidden", type=int, default=64)
    ap.add_argument("--budget", type=int, default=15, help="epok łącznie")
    ap.add_argument("--events-suffix", default="",
                    help="przyrostek katalogów ze zdarzeniami, np. _s1")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    results: dict[str, dict] = {}

    logger.info("Oś 1: długość sekwencji")
    lengths = {}
    sfx = args.events_suffix
    for length, features, events in ((4, "cechy_L4", f"cechy_zdarzenia_L4{sfx}"),
                                     (8, "cechy", f"cechy_zdarzenia{sfx}"),
                                     (12, "cechy_L12", f"cechy_zdarzenia_L12{sfx}")):
        if not Path(features).exists():
            logger.warning(f"brak {features}, pomijam długość {length}")
            continue
        lengths[length] = _run(load_clients(Path(features)), Path(events),
                               args.percentile, 5, 3, args.hidden, args.seed)
    results["dlugosc_sekwencji"] = lengths

    clients = load_clients(Path("cechy"))
    events = Path(f"cechy_zdarzenia{sfx}")

    logger.info("Oś 2: zestaw cech (ablacja grup)")
    baseline = _run(clients, events, args.percentile, 5, 3, args.hidden, args.seed)
    ablation = {"pełny zestaw": baseline}
    for name, members in GROUPS.items():
        columns = [FEATURES.index(f) for f in members if f in FEATURES]
        ablation[f"bez: {name}"] = _run(clients, events, args.percentile, 5, 3,
                                        args.hidden, args.seed, blank=columns)
    results["ablacja_cech"] = ablation

    logger.info("Oś 3: percentyl progu")
    model, _ = run_federation(
        clients, FedAvg(min_available_clients=len(clients),
                        min_fit_clients=len(clients)),
        5, clients[0].X_train.shape[2], args.hidden, 3, 0.0, args.seed)
    percentiles = {}
    for q in (80, 90, 95, 97.5, 99, 99.5):
        rows = []
        for client in clients:
            X, labels = load_events(events, client.name)
            threshold = alarm_threshold(
                reconstruction_errors(
                    model, np.concatenate([client.X_train, client.X_val])), q)
            rows.append(evaluate(reconstruction_errors(model, X), labels,
                                 threshold).as_dict())
        mean = pd.DataFrame(rows).mean()
        percentiles[q] = {"f1_event": float(mean.f1_event),
                          "recall_event": float(mean.recall_event),
                          "false_alarms_per_day": float(mean.false_alarms_per_day),
                          "delay_median_min": float(mean.delay_median_min)}
    results["percentyl_progu"] = percentiles

    logger.info("Oś 4: częstość rund przy stałym budżecie epok")
    rounds = {}
    for r in (1, 3, 5, 15):
        if args.budget % r:
            continue
        rounds[r] = _run(clients, events, args.percentile, r,
                         args.budget // r, args.hidden, args.seed)
    results["czestosc_rund"] = rounds

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "e4.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n=== oś 1: długość sekwencji ===")
    print(f"{'okien':>7}{'F1 zdarzeń':>12}{'czułość':>10}{'AP okna':>10}{'fałsz/dobę':>12}")
    for length, r in lengths.items():
        print(f"{length:>7}{r['f1_event']:>12.3f}{r['recall_event']:>10.3f}"
              f"{r['pr_auc_window']:>10.3f}{r['false_alarms_per_day']:>12.2f}")

    print("\n=== oś 2: ablacja grup cech (P1) ===")
    print(f"{'wariant':<28}{'F1 zdarzeń':>12}{'zmiana':>10}{'AP okna':>10}")
    base_f1 = baseline["f1_event"]
    for name, r in ablation.items():
        delta = "" if name == "pełny zestaw" else f"{r['f1_event'] - base_f1:+.3f}"
        print(f"{name:<28}{r['f1_event']:>12.3f}{delta:>10}{r['pr_auc_window']:>10.3f}")

    print("\n=== oś 3: percentyl progu (P2) ===")
    print(f"{'percentyl':>10}{'F1 zdarzeń':>12}{'czułość':>10}"
          f"{'fałsz/dobę':>12}{'opóźn.min':>11}")
    for q, r in percentiles.items():
        print(f"{q:>10}{r['f1_event']:>12.3f}{r['recall_event']:>10.3f}"
              f"{r['false_alarms_per_day']:>12.2f}{r['delay_median_min']:>11.1f}")

    print("\n=== oś 4: rundy federacji przy 15 epokach (P3) ===")
    print(f"{'rund':>6}{'epok/rundę':>12}{'F1 zdarzeń':>12}{'MB/rundę':>10}"
          f"{'MB łącznie':>12}{'s/rundę':>9}")
    for r, v in rounds.items():
        print(f"{r:>6}{args.budget // r:>12}{v['f1_event']:>12.3f}"
              f"{v['mb_per_round']:>10.2f}{v['mb_per_round'] * r:>12.2f}"
              f"{v['seconds_per_round']:>9.1f}")

    logger.success(f"Zapisano {args.out}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
