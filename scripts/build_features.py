import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

from gtfs_olap.features import (
    FEATURES, add_profile_deviation, build_features, daily_profile, scaling_stats,
    sequences, standardize,
)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("dane"))
    ap.add_argument("--out", type=Path, default=Path("cechy"))
    ap.add_argument("--train-share", type=float, default=0.7)
    ap.add_argument("--sequence-length", type=int, default=8)
    args = ap.parse_args()

    dim_data = pd.read_parquet(args.data / "wymiary" / "dim_data.parquet")
    logger.info("Liczę wielkości pierwotne i cechy bazowe...")
    df = build_features(args.data / "fakty", args.data / "lookup", dim_data)

    boundary = df["window"].quantile(args.train_share)
    df["split"] = np.where(df["window"] <= boundary, "train", "test")
    logger.info(f"Podział czasowy na {boundary:%Y-%m-%d %H:%M}: "
                f"{(df.split == 'train').sum():,} / {(df.split == 'test').sum():,} okien")

    df = add_profile_deviation(df, daily_profile(df[df["split"] == "train"]))
    stats = scaling_stats(df[df["split"] == "train"])
    df = standardize(df, stats)

    args.out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.out / "features.parquet", index=False, compression="zstd")
    stats.to_parquet(args.out / "scaling_stats.parquet", index=False)

    print(f"\n{'klient':<26} {'okien':>7} {'kompl.':>7} {'sekw.tren':>10} {'sekw.test':>10}")
    for client, group in df.groupby("client", observed=True):
        X_train, _ = sequences(group[group.split == "train"], args.sequence_length)
        X_test, test_labels = sequences(group[group.split == "test"],
                                        args.sequence_length)
        directory = args.out / f"client={client}"
        directory.mkdir(exist_ok=True)
        np.save(directory / "X_train.npy", X_train)
        np.save(directory / "X_test.npy", X_test)
        if len(test_labels):
            test_labels.to_parquet(directory / "test_labels.parquet", index=False)
        print(f"{client:<26} {len(group):>7,} {int(group.complete.sum()):>7,} "
              f"{len(X_train):>10,} {len(X_test):>10,}")

    print(f"\ncechy: {', '.join(FEATURES)}")
    logger.success(f"Zapisano do {args.out}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
