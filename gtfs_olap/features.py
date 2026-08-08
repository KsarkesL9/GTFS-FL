\
\
\
\
\
\
\
\
\
\
\

from __future__ import annotations

import glob
from pathlib import Path

import numpy as np
import pandas as pd

from gtfs_olap.clients import OPERATOR_TO_CLIENT

DELAY_MIN = -3600
DELAY_MAX = 7200

ON_TIME_MIN = -30
ON_TIME_MAX = 60

WINDOW = "15min"
HEADWAY_WINDOW_MIN = 60
MIN_TRIPS_FOR_HEADWAY = 3

TZ = "Europe/Warsaw"

FEATURES = ["d", "w", "dmax", "n", "p", "delta_d", "r", "h",
            "sin_hour", "cos_hour", "workday"]

def _to_seconds(value: pd.Series) -> pd.Series:

    parts = value.str.split(":", expand=True).astype("float64")
    return parts[0] * 3600 + parts[1] * 60 + parts[2]

def _read_parquet_tree(directory: str | Path, label: str) -> pd.DataFrame:
    files = sorted(glob.glob(str(Path(directory) / "**" / "*.parquet"), recursive=True))
    if not files:
        raise FileNotFoundError(f"Brak plików Parquet ({label}) w {directory}")
    return pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)

def load_facts(directory: str | Path) -> pd.DataFrame:
    df = _read_parquet_tree(directory, "fakty")
    df["client"] = df["operator_id"].map(OPERATOR_TO_CLIENT)
    unmapped = df["client"].isna().sum()
    if unmapped:
        raise ValueError(f"{unmapped} obserwacji od operatorów spoza clients.py")
    df["window"] = df["ts"].dt.tz_convert(TZ).dt.floor(WINDOW)
    return df

def load_lookup(directory: str | Path) -> pd.DataFrame:
\

    df = _read_parquet_tree(directory, "lookup")
    df = df.sort_values("stop_sequence").drop_duplicates(["wersja_id", "trip_id"])
    df["departure_s"] = _to_seconds(df["rozkladowy_przyjazd"])
    return df[["wersja_id", "trip_id", "departure_s"]]

def primary_quantities(facts: pd.DataFrame) -> pd.DataFrame:
    observed = facts["status"] == "OBSERWACJA"
    valid = observed & facts["opoznienie_s"].between(DELAY_MIN, DELAY_MAX)

    df = facts.assign(
        _n=valid.astype("int64"),
        _s=facts["opoznienie_s"].where(valid),
        _q=(valid & facts["opoznienie_s"].between(
            ON_TIME_MIN, ON_TIME_MAX)).astype("int64"),
        _u=(facts["status"] == "POMINIETY").astype("int64"),
        _rejected=(observed & ~valid).astype("int64"),
    )
    return df.groupby(["client", "window"]).agg(
        n=("_n", "sum"),
        S=("_s", "sum"),
        q=("_q", "sum"),
        u=("_u", "sum"),
        dmax=("_s", "max"),
        rejected=("_rejected", "sum"),
    ).reset_index()

def headway_irregularity(facts: pd.DataFrame, lookup: pd.DataFrame) -> pd.DataFrame:
    observed = facts[facts["status"] == "OBSERWACJA"]

    trips = (observed.groupby(
        ["wersja_id", "trip_id", "client", "linia_id", "kierunek", "data_kursu"],
        observed=True)["opoznienie_s"].median().reset_index())
    trips = trips.merge(lookup, on=["wersja_id", "trip_id"], how="inner")

    trips["departure"] = (
        pd.to_datetime(trips["data_kursu"]).dt.tz_localize(TZ)
        + pd.to_timedelta(trips["departure_s"] + trips["opoznienie_s"], unit="s")
    )
    trips = trips.sort_values("departure")
    trips["headway_s"] = (
        trips.groupby(["client", "linia_id", "kierunek"], observed=True)["departure"]
        .diff().dt.total_seconds())
    trips = trips.dropna(subset=["headway_s"])
    trips["window"] = trips["departure"].dt.floor(WINDOW)

    shifts = [pd.Timedelta(minutes=15 * i) for i in range(HEADWAY_WINDOW_MIN // 15)]
    expanded = pd.concat(
        [trips.assign(window=trips["window"] + s) for s in shifts], ignore_index=True)

    per_line = (expanded.groupby(
        ["client", "window", "linia_id", "kierunek"], observed=True)["headway_s"]
        .agg(["std", "count"]).reset_index())
    per_line = per_line[per_line["count"] >= MIN_TRIPS_FOR_HEADWAY - 1]

    return (per_line.groupby(["client", "window"], observed=True)["std"]
            .mean().reset_index(name="h"))

def time_grid(df: pd.DataFrame) -> pd.DataFrame:
\

    windows = pd.date_range(df["window"].min(), df["window"].max(), freq=WINDOW, tz=TZ)
    clients = sorted(df["client"].unique())
    return pd.MultiIndex.from_product(
        [clients, windows], names=["client", "window"]).to_frame(index=False)

def daily_profile(df: pd.DataFrame) -> pd.DataFrame:
\

    return (df.dropna(subset=["d"])
            .groupby(["client", "hour", "workday"], observed=True)["d"]
            .median().reset_index(name="m"))

def build_features(facts_dir: str | Path, lookup_dir: str | Path,
                   dim_data: pd.DataFrame) -> pd.DataFrame:
    facts = load_facts(facts_dir)
    lookup = load_lookup(lookup_dir)

    df = time_grid(facts).merge(primary_quantities(facts),
                                on=["client", "window"], how="left")
    df[["n", "q", "u", "rejected"]] = df[["n", "q", "u", "rejected"]].fillna(0)
    df = df.merge(headway_irregularity(facts, lookup),
                  on=["client", "window"], how="left")

    empty = df["n"] == 0
    df["complete"] = ~empty
    df["d"] = np.where(empty, np.nan, df["S"] / df["n"])
    df["w"] = np.where(empty, np.nan, df["q"] / df["n"])
    df["p"] = np.where(empty, np.nan, df["u"] / df["n"])
    df["dmax"] = df["dmax"].where(~empty)

    df["date"] = df["window"].dt.date
    df["hour"] = df["window"].dt.hour
    df["day_type"] = df["date"].map(dim_data.set_index("data")["typ_dnia"])
    df["workday"] = (df["day_type"].fillna("")
                     .str.startswith("dni robocze").astype(int))

    angle = 2 * np.pi * (df["window"].dt.hour * 60 + df["window"].dt.minute) / 1440
    df["sin_hour"] = np.sin(angle)
    df["cos_hour"] = np.cos(angle)

    df = df.sort_values(["client", "window"])
    df["delta_d"] = df.groupby("client", observed=True)["d"].diff()
    return df.reset_index(drop=True)

def add_profile_deviation(df: pd.DataFrame, profile: pd.DataFrame) -> pd.DataFrame:
    out = df.merge(profile, on=["client", "hour", "workday"], how="left")
    out["r"] = out["d"] - out["m"]
    return out.drop(columns=["m"])

def scaling_stats(df: pd.DataFrame) -> pd.DataFrame:
    stats = df.groupby("client", observed=True)[FEATURES].agg(["mean", "std"])
    stats.columns = [f"{col}_{stat}" for col, stat in stats.columns]
    return stats.reset_index()

def standardize(df: pd.DataFrame, stats: pd.DataFrame) -> pd.DataFrame:
    out = df.merge(stats, on="client", how="left")
    for feature in FEATURES:
        sd = out[f"{feature}_std"].replace(0, np.nan)

        out[feature] = ((out[feature] - out[f"{feature}_mean"]) / sd).fillna(0.0)
    return out.drop(columns=[f"{f}_{s}" for f in FEATURES for s in ("mean", "std")])

def sequences(df: pd.DataFrame, length: int = 8) -> tuple[np.ndarray, pd.DataFrame]:
\

    windows, labels = [], []
    for client, group in df.sort_values("window").groupby("client", observed=True):
        values = group[FEATURES].to_numpy(dtype="float32")
        complete = group["complete"].to_numpy()
        times = group["window"].to_numpy()
        contiguous = (group["window"].diff().dt.total_seconds()
                      .fillna(900) == 900).to_numpy()
        for i in range(length - 1, len(group)):
            span = slice(i - length + 1, i + 1)
            if not complete[span].all() or not contiguous[span][1:].all():
                continue
            windows.append(values[span])
            labels.append((client, times[i]))
    if not windows:
        return (np.empty((0, length, len(FEATURES)), dtype="float32"), pd.DataFrame())
    return np.stack(windows), pd.DataFrame(labels, columns=["client", "window"])
