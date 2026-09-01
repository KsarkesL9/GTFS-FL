from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

DURATIONS = (1, 2, 4)

A1_AMPLITUDES_S = (60, 120, 300)
A2_KEEP_RATIOS = (0.3, 0.5, 0.7)
A2_MIXED_SKIP = 0.1
A3_PUNCTUALITY_RATIOS = (0.3, 0.5)
A3_DMAX_BUMP_S = 180
D1_SHIFTS_S = (30, 60)
D2_LAMBDA = 0.3

ANOMALY_KINDS = ("A1", "A2", "A2m", "A3")
DRIFT_KINDS = ("D1", "D2")

BUFFER_WINDOWS = 8

DRIFT_TAIL_WINDOWS = 5 * 96

@dataclass(frozen=True)
class Event:
    client: str
    start: pd.Timestamp
    duration: int
    kind: str
    param: float

def _configs(kind: str) -> list[float]:
    return {
        "A1": list(A1_AMPLITUDES_S),
        "A2": list(A2_KEEP_RATIOS),
        "A2m": list(A2_KEEP_RATIOS),
        "A3": list(A3_PUNCTUALITY_RATIOS),
        "D1": list(D1_SHIFTS_S),
        "D2": [D2_LAMBDA],
    }[kind]

def _place(complete: np.ndarray, taken: np.ndarray, duration: int,
           rng: np.random.Generator) -> int | None:

    starts = np.arange(BUFFER_WINDOWS, len(complete) - duration - BUFFER_WINDOWS)
    if not len(starts):
        return None
    free = np.array([
        complete[s - BUFFER_WINDOWS:s + duration + BUFFER_WINDOWS].all()
        and not taken[s - BUFFER_WINDOWS:s + duration + BUFFER_WINDOWS].any()
        for s in starts])
    if not free.any():
        return None
    return int(rng.choice(starts[free]))

def sample_events(df: pd.DataFrame, repeats: int, seed: int,
                  kinds: tuple[str, ...] = ANOMALY_KINDS,
                  split: str = "test") -> list[Event]:

    rng = np.random.default_rng(seed)
    events: list[Event] = []
    for client, group in df[df["split"] == split].groupby("client", observed=True):
        group = group.sort_values("window")
        complete = group["complete"].to_numpy()
        windows = group["window"].to_numpy()
        taken = np.zeros(len(group), dtype=bool)

        plan = [(kind, param, duration)
                for kind in kinds
                for param in _configs(kind)
                for duration in DURATIONS] * repeats
        rng.shuffle(plan)
        for kind, param, duration in plan:
            start = _place(complete, taken, duration, rng)
            if start is None:
                continue
            taken[start - BUFFER_WINDOWS:start + duration + BUFFER_WINDOWS] = True
            events.append(Event(client, pd.Timestamp(windows[start]),
                                duration, kind, float(param)))
    return events

def sample_drift(df: pd.DataFrame, kind: str, param: float, seed: int,
                 split: str = "test") -> list[Event]:

    events: list[Event] = []
    for client, group in df[df["split"] == split].groupby("client", observed=True):
        group = group.sort_values("window")
        windows = group["window"].to_numpy()
        start = max(BUFFER_WINDOWS, len(group) - DRIFT_TAIL_WINDOWS)
        start = min(start, max(0, len(group) - 1))
        events.append(Event(client, pd.Timestamp(windows[start]),
                            len(group) - start, kind, float(param)))
    return events

def daily_shape(train: pd.DataFrame) -> pd.DataFrame:

    profile = (train.dropna(subset=["d"])
               .groupby(["client", "hour"], observed=True)["d"]
               .median().reset_index(name="m"))
    span = profile.groupby("client", observed=True)["m"].transform(
        lambda s: s.max() - s.min())
    low = profile.groupby("client", observed=True)["m"].transform("min")
    profile["s"] = np.where(span > 0, (profile["m"] - low) / span, 0.0)
    return profile[["client", "hour", "s"]]

def inject(df: pd.DataFrame, events: list[Event],
           shape: pd.DataFrame | None = None) -> pd.DataFrame:

    out = df.sort_values(["client", "window"]).reset_index(drop=True).copy()
    out["event_id"] = -1
    out["event_kind"] = ""
    out["event_param"] = 0.0
    out["drift"] = False

    events = sorted(events, key=lambda e: e.kind not in DRIFT_KINDS)

    if shape is not None:
        out = out.merge(shape, on=["client", "hour"], how="left")
        out["s"] = out["s"].fillna(0.0)

    index = pd.MultiIndex.from_frame(out[["client", "window"]])
    for event_id, event in enumerate(events):
        span = pd.date_range(event.start, periods=event.duration, freq="15min")
        rows = index.get_indexer(pd.MultiIndex.from_product([[event.client], span]))
        rows = rows[rows >= 0]
        if not len(rows):
            continue
        p = event.param

        if event.kind == "A1":
            out.loc[rows, "S"] += p * out.loc[rows, "n"]
            out.loc[rows, "dmax"] += p

            out.loc[rows, "q"] = 0
        elif event.kind in ("A2", "A2m"):
            for column in ("n", "S", "q", "u"):
                out.loc[rows, column] *= p
            if event.kind == "A2m":
                out.loc[rows, "u"] += A2_MIXED_SKIP * out.loc[rows, "n"]
        elif event.kind == "A3":
            out.loc[rows, "q"] *= p
            out.loc[rows, "dmax"] += A3_DMAX_BUMP_S
        elif event.kind == "D1":
            out.loc[rows, "S"] += p * out.loc[rows, "n"]
        elif event.kind == "D2":
            out.loc[rows, "S"] *= 1 + p * out.loc[rows, "s"]
        else:
            raise ValueError(f"nieznany typ zdarzenia: {event.kind}")

        if event.kind in DRIFT_KINDS:
            out.loc[rows, "drift"] = True
        else:
            out.loc[rows, "event_id"] = event_id
            out.loc[rows, "event_kind"] = event.kind
            out.loc[rows, "event_param"] = p

    touched = ((out["event_id"] >= 0) | out["drift"]) & (out["n"] > 0)
    for derived, primary in (("d", "S"), ("w", "q"), ("p", "u")):
        out.loc[touched, derived] = out.loc[touched, primary] / out.loc[touched, "n"]
    out["delta_d"] = out.groupby("client", observed=True)["d"].diff()
    return out.drop(columns=["s"], errors="ignore")

def _frame(periods: int = 400) -> pd.DataFrame:
    windows = pd.date_range("2026-08-20", periods=periods, freq="15min",
                            tz="Europe/Warsaw")
    df = pd.DataFrame({
        "client": "test_client", "window": windows, "split": "test",
        "n": 100.0, "S": 5000.0, "q": 70.0, "u": 0.0, "dmax": 300.0,
        "complete": True,
    })
    df["hour"] = df["window"].dt.hour
    df["d"] = df["S"] / df["n"]
    df["w"] = df["q"] / df["n"]
    df["p"] = df["u"] / df["n"]
    return df

def _self_check() -> None:
    df = _frame()

    events = sample_events(df, repeats=1, seed=0)
    assert events, "nie rozmieszczono ani jednego zdarzenia"
    out = inject(df, events)

    for event in events:
        span = pd.date_range(event.start, periods=event.duration, freq="15min")
        rows = out[out["window"].isin(span)]
        if event.kind == "A1":
            assert np.allclose(rows["d"], 50.0 + event.param), "A1: d nie wzrosło o A"
            assert np.allclose(rows["dmax"], 300.0 + event.param), "A1: dmax bez zmian"
            assert (rows["w"] == 0).all(), "A1: punktualność nie spadła"
        elif event.kind == "A2":
            assert np.allclose(rows["n"], 100.0 * event.param), "A2: n nie spadło"
            assert np.allclose(rows["d"], 50.0), "A2: d powinno zostać nietknięte"
            assert np.allclose(rows["w"], 0.7), "A2: w powinno zostać nietknięte"
        elif event.kind == "A2m":
            assert np.allclose(rows["n"], 100.0 * event.param), "A2m: n nie spadło"
            assert np.allclose(rows["p"], A2_MIXED_SKIP), "A2m: p nie wzrosło o 0,1"
        elif event.kind == "A3":
            assert np.allclose(rows["w"], 0.7 * event.param), "A3: w nie spadło"
            assert np.allclose(rows["d"], 50.0), "A3: d powinno zostać nietknięte"
            assert np.allclose(rows["dmax"], 300.0 + A3_DMAX_BUMP_S), "A3: dmax bez zmian"

    clean = out[out["event_id"] < 0]
    for column, value in (("d", 50.0), ("w", 0.7), ("n", 100.0), ("dmax", 300.0)):
        assert np.allclose(clean[column], value), f"okna czyste ruszone w {column}"

    starts = sorted(int((e.start - df["window"].iloc[0]).total_seconds() // 900)
                    for e in events)
    spans = {int((e.start - df["window"].iloc[0]).total_seconds() // 900): e.duration
             for e in events}
    for earlier, later in zip(starts, starts[1:]):
        assert later - (earlier + spans[earlier]) >= BUFFER_WINDOWS, "zdarzenia za blisko"
    assert starts[0] >= BUFFER_WINDOWS, "zdarzenie w buforze początkowym"

    assert sample_events(df, repeats=1, seed=0) == events, "ziarno nie powtarza układu"
    assert sample_events(df, repeats=1, seed=1) != events, "ziarna dają to samo"

    d1 = sample_drift(df, "D1", 60, seed=0)
    assert d1[0].duration <= DRIFT_TAIL_WINDOWS or len(df) <= DRIFT_TAIL_WINDOWS,         "ogon dryfu dłuższy niż wymagany"
    after = inject(df, d1)
    assert after["drift"].sum() == d1[0].duration, "dryf źle oznaczony"
    assert (after["event_id"] == -1).all(), "dryf nie jest zdarzeniem do wykrycia"
    t0 = d1[0].start
    assert np.allclose(after.loc[after["window"] >= t0, "d"], 110.0), "D1: brak przesunięcia"
    assert np.allclose(after.loc[after["window"] < t0, "d"], 50.0), "D1: ruszone przed t0"

    shape = daily_shape(df.assign(d=lambda x: x["d"] + x["hour"]))
    d2 = sample_drift(df, "D2", D2_LAMBDA, seed=0)
    after2 = inject(df, d2, shape=shape)
    tail = after2[after2["window"] >= d2[0].start]
    assert (tail["d"] >= 50.0 - 1e-9).all(), "D2: profil zszedł poniżej bazy"
    assert tail["d"].max() > 50.0, "D2: profil w ogóle nie wzrósł"
    assert np.allclose(after2.loc[after2["window"] < d2[0].start, "d"], 50.0), \
        "D2: ruszone przed t0"

    both = inject(df, d1 + events)
    assert (both["event_id"] >= 0).sum() == (out["event_id"] >= 0).sum(),         "dryf zjadł etykiety anomalii"
    assert both["drift"].sum() == d1[0].duration, "dryf zniknął po dołożeniu anomalii"

    kinds = sorted({e.kind for e in events})
    print(f"events._self_check: OK (typy {kinds}, {len(events)} zdarzeń, "
          f"{int((out.event_id >= 0).sum())} okien anomalnych, D1 i D2 sprawdzone)")

if __name__ == "__main__":
    _self_check()
