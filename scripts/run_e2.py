import argparse
import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
from flwr.server.strategy import FedAvg
from loguru import logger
from river.drift import ADWIN

from gtfs_olap.detection import evaluate
from gtfs_olap.experiments import load_clients, load_events
from gtfs_olap.federation import run_federation
from gtfs_olap.model import alarm_threshold, reconstruction_errors, train

COOLDOWN_WINDOWS = 96

MIN_CALM_WINDOWS = 32

def adapt(model, X: np.ndarray, threshold: float, recent: int, epochs: int,
          percentile: float, max_retrains: int, delta: float,
          exclude_alarms: bool = False,
          threshold_on_calm: bool = False) -> tuple[np.ndarray, list[int]]:

    model = copy.deepcopy(model)
    errors = reconstruction_errors(model, X)
    ratio = np.empty(len(X), dtype="float64")

    detector = ADWIN(delta=delta)
    retrains: list[int] = []
    last = -COOLDOWN_WINDOWS

    for i in range(len(X)):
        ratio[i] = errors[i] / threshold if threshold > 0 else np.inf
        detector.update(float(errors[i] > threshold))
        if (detector.drift_detected and i - last >= COOLDOWN_WINDOWS
                and len(retrains) < max_retrains):
            lo = max(0, i - recent + 1)
            span = X[lo:i + 1]
            window = span
            if exclude_alarms:
                calm = errors[lo:i + 1] <= threshold

                if calm.sum() >= MIN_CALM_WINDOWS:
                    window = span[calm]
            train(model, window, epochs=epochs)
            if i + 1 < len(X):
                errors[i + 1:] = reconstruction_errors(model, X[i + 1:])

            basis = window if threshold_on_calm else span
            threshold = alarm_threshold(
                reconstruction_errors(model, basis), percentile)
            detector = ADWIN(delta=delta)
            retrains.append(i)
            last = i
    return ratio, retrains

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", type=Path, default=Path("cechy"))
    ap.add_argument("--events", type=Path, default=Path("cechy_d1"))
    ap.add_argument("--out", type=Path, default=Path("wyniki_e2"))
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--local-epochs", type=int, default=3)
    ap.add_argument("--hidden", type=int, default=64)
    ap.add_argument("--percentile", type=float, default=90.0)
    ap.add_argument("--recent", type=int, default=192, help="okien do douczania")
    ap.add_argument("--retrain-epochs", type=int, default=5)
    ap.add_argument("--max-retrains", type=int, default=5)
    ap.add_argument("--adwin-delta", type=float, default=0.1,
                    help="czułość ADWIN; przy domyślnej 0,002 dryf wychodzi "
                         "u 6 z 9 klientów i późno")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    clients = load_clients(args.features)
    n_features = clients[0].X_train.shape[2]
    logger.info("Model bazowy: FedAvg na czystych danych...")
    base, _ = run_federation(
        clients, FedAvg(min_available_clients=len(clients),
                        min_fit_clients=len(clients)),
        args.rounds, n_features, args.hidden, args.local_epochs, 0.0, args.seed)

    rows = []
    for client in clients:
        X, labels = load_events(args.events, client.name)
        labels = labels.sort_values("window").reset_index(drop=True)
        calibration = np.concatenate([client.X_train, client.X_val])
        threshold = alarm_threshold(
            reconstruction_errors(base, calibration), args.percentile)

        static_ratio = reconstruction_errors(base, X) / threshold
        naive_ratio, retrains = adapt(
            base, X, threshold, args.recent, args.retrain_epochs,
            args.percentile, args.max_retrains, args.adwin_delta)
        ruled_ratio, ruled_retrains = adapt(
            base, X, threshold, args.recent, args.retrain_epochs,
            args.percentile, args.max_retrains, args.adwin_delta,
            exclude_alarms=True)
        calm_ratio, _ = adapt(
            base, X, threshold, args.recent, args.retrain_epochs,
            args.percentile, args.max_retrains, args.adwin_delta,
            exclude_alarms=True, threshold_on_calm=True)
        post = labels["drift"].to_numpy().astype(bool)
        logger.info(f"{client.name}: dostrojeń {len(retrains)} / "
                    f"{len(ruled_retrains)} (naiwny / z regułą), "
                    f"okien po dryfie {int(post.sum())}")

        for variant, ratio in (("statyczny", static_ratio),
                               ("adaptacyjny naiwny", naive_ratio),
                               ("adaptacyjny z regułą", ruled_ratio),
                               ("reguła, próg ostrożny", calm_ratio)):
            for phase, mask in (("przed dryfem", ~post), ("po dryfie", post)):
                if not mask.any():
                    continue
                scores = evaluate(ratio[mask], labels[mask].reset_index(drop=True),
                                  1.0)
                rows.append({"klient": client.name, "wariant": variant,
                             "faza": phase, "dostrojen": len(retrains),
                             **scores.as_dict()})

    frame = pd.DataFrame(rows)
    args.out.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(args.out / "e2.parquet", index=False)

    table = frame.pivot_table(index=["wariant", "faza"],
                              values=["false_alarms_per_day", "recall_event",
                                      "f1_event"], aggfunc="mean")
    print("\n=== E2: fałszywe alarmy i czułość, średnia po klientach ===")
    print(table.round(3).to_string())

    fa = frame.pivot_table(index="klient", columns=["wariant", "faza"],
                           values="false_alarms_per_day")
    rc = frame.pivot_table(index="klient", columns=["wariant", "faza"],
                           values="recall_event")
    static_post = fa[("statyczny", "po dryfie")]
    verdicts = {}
    print("\n=== po dryfie: kryterium K2 ===")
    for variant in ("adaptacyjny naiwny", "adaptacyjny z regułą",
                    "reguła, próg ostrożny"):
        post_fa = fa[(variant, "po dryfie")]
        reduction = 1 - post_fa / static_post.replace(0, np.nan)
        drop = rc[("statyczny", "po dryfie")] - rc[(variant, "po dryfie")]
        met = bool(reduction.mean() >= 0.5 and drop.mean() <= 0.05)
        verdicts[variant] = {"reduction": float(reduction.mean()),
                             "sensitivity_drop": float(drop.mean()), "met": met}
        print(f"{variant:<24} redukcja fałszywych {reduction.mean():>6.1%}   "
              f"spadek czułości {drop.mean():>+6.1%}   "
              f"{'SPEŁNIONE' if met else 'NIESPEŁNIONE'}")

    print("\n=== po dryfie, na klienta ===")
    print(pd.DataFrame({
        "fałsz. statyczny": static_post.round(2),
        "fałsz. naiwny": fa[("adaptacyjny naiwny", "po dryfie")].round(2),
        "fałsz. ostrożny": fa[("reguła, próg ostrożny", "po dryfie")].round(2),
        "czułość stat.": rc[("statyczny", "po dryfie")].round(3),
        "czułość naiwny": rc[("adaptacyjny naiwny", "po dryfie")].round(3),
        "czułość ostrożny": rc[("reguła, próg ostrożny", "po dryfie")].round(3),
    }).to_string())

    (args.out / "e2.json").write_text(json.dumps({
        "events": args.events.name, "percentile": args.percentile,
        "recent_windows": args.recent, "max_retrains": args.max_retrains,
        "adwin_delta": args.adwin_delta,
        "k2": verdicts,
        "table": table.round(4).reset_index().to_dict("records"),
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.success(f"Zapisano {args.out}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
