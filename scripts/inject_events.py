\
\
\
\
\
\
\
\
\

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

from gtfs_olap.events import (
    ANOMALY_KINDS, D2_LAMBDA, D1_SHIFTS_S, daily_shape, inject, sample_drift,
    sample_events,
)
from gtfs_olap.features import (
    add_profile_deviation, build_features, daily_profile, sequences, standardize,
)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("dane"))
    ap.add_argument("--features", type=Path, default=Path("cechy"),
                    help="katalog czystych cech - źródło statystyk skalowania")
    ap.add_argument("--out", type=Path, default=Path("cechy_zdarzenia"))
    ap.add_argument("--train-share", type=float, default=0.7)
    ap.add_argument("--sequence-length", type=int, default=8)
    ap.add_argument("--kinds", nargs="*", default=list(ANOMALY_KINDS))
    ap.add_argument("--drift", choices=["D1", "D2"], default=None,
                    help="zamiast anomalii wstrzykuje jeden dryf na klienta")
    ap.add_argument("--drift-param", type=float, default=None)
    ap.add_argument("--repeats", type=int, default=20,
                    help="powtórzeń na konfigurację u klienta (10.2)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    dim_data = pd.read_parquet(args.data / "wymiary" / "dim_data.parquet")
    logger.info("Liczę cechy bazowe (czyste)...")
    df = build_features(args.data / "fakty", args.data / "lookup", dim_data)
    boundary = df["window"].quantile(args.train_share)
    df["split"] = np.where(df["window"] <= boundary, "train", "test")
    train = df[df["split"] == "train"]

    shape = None
    if args.drift:
        param = args.drift_param
        if param is None:
            param = D1_SHIFTS_S[-1] if args.drift == "D1" else D2_LAMBDA
        events = sample_drift(df, args.drift, param, seed=args.seed)
        if args.drift == "D2":
            shape = daily_shape(train)
        logger.info(f"Dryf {args.drift} (parametr {param}) u {len(events)} klientów")
    else:
        events = sample_events(df, repeats=args.repeats, seed=args.seed,
                               kinds=tuple(args.kinds))
        logger.info(f"Rozmieszczono {len(events)} zdarzeń typów "
                    f"{sorted({e.kind for e in events})} "
                    f"(limit narzuca długość zbioru testowego i bufory 2 h)")

    df = inject(df, events, shape=shape)

    df = add_profile_deviation(df, daily_profile(df[df["split"] == "train"]))
    stats = pd.read_parquet(args.features / "scaling_stats.parquet")
    df = standardize(df, stats)

    args.out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"client": e.client, "start": e.start, "duration": e.duration,
                   "kind": e.kind, "param": e.param} for e in events]).to_parquet(
        args.out / "events.parquet", index=False)

    print(f"\n{'klient':<26} {'zdarzeń':>8} {'sekw.test':>10} {'anomalnych':>11}")
    total_seq = total_anom = 0
    for client, group in df[df["split"] == "test"].groupby("client", observed=True):
        group = group.sort_values("window").copy()

        in_event = (group["event_id"].to_numpy() >= 0)
        touches = np.zeros(len(group), dtype=bool)
        for i in np.flatnonzero(in_event):
            touches[i:i + args.sequence_length] = True
        group["touches"] = touches

        X, labels = sequences(group, args.sequence_length)
        if not len(labels):
            continue
        labels = labels.merge(
            group[["client", "window", "event_id", "event_kind", "event_param",
                   "touches"]],
            on=["client", "window"], how="left")
        directory = args.out / f"client={client}"
        directory.mkdir(exist_ok=True)
        np.save(directory / "X_test.npy", X)
        labels.to_parquet(directory / "test_labels.parquet", index=False)

        anomalous = int((labels["event_id"] >= 0).sum())
        total_seq += len(X)
        total_anom += anomalous
        print(f"{client:<26} {sum(e.client == client for e in events):>8} "
              f"{len(X):>10,} {anomalous:>11,}")

    share = 100 * total_anom / total_seq if total_seq else 0
    print(f"\nrazem {total_seq:,} sekwencji, {total_anom:,} oznaczonych ({share:.1f}%)")
    logger.success(f"Zapisano do {args.out}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
