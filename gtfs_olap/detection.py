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

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, f1_score

WINDOW_MINUTES = 15

@dataclass
class Scores:
    n_windows: int
    n_ignored: int
    n_anomalous: int
    n_events: int
    f1_window: float
    pr_auc_window: float
    f1_event: float
    pr_auc_event: float
    recall_event: float
    delay_median_min: float
    delay_p90_min: float
    false_alarms_per_day: float

    def as_dict(self) -> dict:
        return asdict(self)

def _episodes(alarm: np.ndarray, contiguous: np.ndarray) -> list[tuple[int, int]]:
\

    spans, start = [], None
    for i, flag in enumerate(alarm):
        if flag and start is None:
            start = i
        elif start is not None and (not flag or not contiguous[i]):
            spans.append((start, i - 1))
            start = i if flag else None
    if start is not None:
        spans.append((start, len(alarm) - 1))
    return spans

def _event_counts(alarm: np.ndarray, contiguous: np.ndarray,
                  event_id: np.ndarray) -> tuple[int, int, int, int]:
\

    spans = _episodes(alarm, contiguous)
    hit = sum(1 for a, b in spans if (event_id[a:b + 1] >= 0).any())
    detected = {int(e) for a, b in spans for e in np.unique(event_id[a:b + 1]) if e >= 0}
    return hit, len(spans), len(detected), len(set(event_id[event_id >= 0].tolist()))

def _event_pr(alarm: np.ndarray, contiguous: np.ndarray,
              event_id: np.ndarray) -> tuple[float, float]:
\
\
\
\
\
\
\

    _, _, detected, total = _event_counts(alarm, contiguous, event_id)
    recall = detected / total if total else 0.0
    fired = alarm.sum()
    precision = float((alarm & (event_id >= 0)).sum() / fired) if fired else 0.0
    return recall, precision

def _event_pr_curve(errors: np.ndarray, contiguous: np.ndarray,
                    event_id: np.ndarray, steps: int = 60) -> float:
\
\
\
\
\
\
\
\
\

    values = np.unique(errors)
    if len(values) < 2:
        return float("nan")
    grid = (values[:-1] + values[1:]) / 2
    if len(grid) > steps:
        grid = grid[np.linspace(0, len(grid) - 1, steps).astype(int)]
    grid = grid[::-1]
    if not (event_id >= 0).any():
        return float("nan")
    points = []
    for threshold in grid:
        alarm = errors > threshold
        if not alarm.any():
            continue
        points.append(_event_pr(alarm, contiguous, event_id))
    if not points:
        return float("nan")

    points.sort(key=lambda p: p[0])
    recalls = np.array([r for r, _ in points])
    precisions = np.array([p for _, p in points])
    envelope = np.maximum.accumulate(precisions[::-1])[::-1]

    area, previous = 0.0, 0.0
    for recall, precision in zip(recalls, envelope):
        if recall > previous:
            area += (recall - previous) * precision
            previous = recall
    return float(area)

def detection_delays(errors: np.ndarray, labels: pd.DataFrame,
                     threshold: float) -> np.ndarray:
\
\
\

    order = np.argsort(labels["window"].to_numpy())
    errors = np.asarray(errors)[order]
    event_id = labels.iloc[order]["event_id"].to_numpy()
    alarm = errors > threshold
    out = []
    for event in sorted(set(event_id[event_id >= 0].tolist())):
        rows = np.flatnonzero(event_id == event)
        fired = rows[alarm[rows]]
        if len(fired):
            out.append((fired[0] - rows[0]) * WINDOW_MINUTES)
    return np.array(out, dtype="float64")

def evaluate(errors: np.ndarray, labels: pd.DataFrame, threshold: float) -> Scores:
\
\
\
\
\
\

    order = np.argsort(labels["window"].to_numpy())
    errors = np.asarray(errors)[order]
    frame = labels.iloc[order]
    ignored = 0
    if "touches" in frame.columns:
        drop = frame["touches"].to_numpy() & (frame["event_id"].to_numpy() < 0)
        ignored = int(drop.sum())
        errors, frame = errors[~drop], frame[~drop]
    event_id = frame["event_id"].to_numpy()
    windows = pd.to_datetime(frame["window"])
    contiguous = (windows.diff().dt.total_seconds().fillna(WINDOW_MINUTES * 60)
                  == WINDOW_MINUTES * 60).to_numpy()

    truth = event_id >= 0
    alarm = errors > threshold

    hit, episodes, _, _ = _event_counts(alarm, contiguous, event_id)
    events = sorted(set(event_id[truth].tolist()))
    recall_e, precision_e = _event_pr(alarm, contiguous, event_id)
    f1_e = (2 * precision_e * recall_e / (precision_e + recall_e)
            if precision_e + recall_e else 0.0)
    false = episodes - hit

    delays = detection_delays(errors, frame, threshold)

    clean_days = (~truth).sum() * WINDOW_MINUTES / 1440

    return Scores(
        n_windows=len(errors),
        n_ignored=ignored,
        n_anomalous=int(truth.sum()),
        n_events=len(events),
        f1_window=float(f1_score(truth, alarm, zero_division=0)),
        pr_auc_window=float(average_precision_score(truth, errors))
        if truth.any() else float("nan"),
        f1_event=float(f1_e),
        pr_auc_event=_event_pr_curve(errors, contiguous, event_id),
        recall_event=float(recall_e),
        delay_median_min=float(np.median(delays)) if len(delays) else float("nan"),
        delay_p90_min=float(np.percentile(delays, 90)) if len(delays) else float("nan"),
        false_alarms_per_day=float(false / clean_days) if clean_days else float("nan"),
    )

def _self_check() -> None:

    n = 200
    windows = pd.date_range("2026-08-20", periods=n, freq="15min", tz="Europe/Warsaw")
    event_id = np.full(n, -1)
    event_id[50:54] = 0
    event_id[120:124] = 1
    rng = np.random.default_rng(0)
    errors = rng.normal(1.0, 0.05, n)
    errors[event_id >= 0] = 5.0
    labels = pd.DataFrame({"window": windows, "event_id": event_id})

    perfect = evaluate(errors, labels, threshold=3.0)
    assert perfect.recall_event == 1.0, "oba zdarzenia powinny być wykryte"
    assert perfect.f1_window == 1.0, "brak fałszywych alarmów, czułość pełna"
    assert perfect.false_alarms_per_day == 0.0, "fałszywe alarmy z niczego"
    assert perfect.delay_median_min == 0.0, "alarm powinien paść w pierwszym oknie"
    assert perfect.n_events == 2 and perfect.n_anomalous == 8

    tail = labels.assign(touches=False)
    tail.loc[50:60, "touches"] = True
    tail.loc[120:130, "touches"] = True
    spill = errors.copy()
    spill[54:58] = 5.0
    with_zone = evaluate(spill, tail, threshold=3.0)
    without_zone = evaluate(spill, labels, threshold=3.0)
    assert with_zone.n_ignored == 14, f"zła liczba pominiętych: {with_zone.n_ignored}"
    assert with_zone.n_windows == n - 14, "strefa nie zmniejszyła zbioru ocenianego"

    assert with_zone.f1_window == 1.0, "strefa nie usunęła ogona z oceny okien"
    assert without_zone.f1_window < 1.0, "bez strefy ogon powinien psuć precyzję"
    assert with_zone.recall_event == without_zone.recall_event == 1.0

    blind = evaluate(errors, labels, threshold=100.0)
    assert blind.recall_event == 0.0 and blind.f1_event == 0.0, "próg powyżej wszystkiego"
    assert np.isnan(blind.delay_median_min), "brak alarmów, brak opóźnienia"

    noisy = errors.copy()
    noisy[10] = 5.0
    scores = evaluate(noisy, labels, threshold=3.0)
    assert scores.recall_event == 1.0, "czułość nie powinna ucierpieć"
    assert scores.false_alarms_per_day > 0, "fałszywy alarm niezauważony"
    assert scores.f1_event < 1.0, "precyzja zdarzeń powinna spaść"

    late = errors.copy()
    late[50:52] = 1.0
    delayed = evaluate(late, labels, threshold=3.0)
    assert delayed.delay_median_min == 15.0, "mediana opóźnienia detekcji zła"

    assert perfect.pr_auc_event > 0.99, "AP zdarzeń powinno być bliskie jedynki"

    flat = evaluate(rng.normal(1.0, 0.05, n), labels, threshold=0.0)
    share = 8 / n
    assert flat.pr_auc_event < 5 * share, (
        f"AP zdarzeń {flat.pr_auc_event:.2f} dla detektora bez sygnału - "
        f"metryka zdegenerowana")
    assert perfect.pr_auc_window > 0.99, "AP okien powinno być bliskie jedynki"
    print(f"detection._self_check: OK (F1 okien {perfect.f1_window:.2f}, "
          f"F1 zdarzeń {perfect.f1_event:.2f}, "
          f"AP zdarzeń {perfect.pr_auc_event:.2f})")

if __name__ == "__main__":
    _self_check()
