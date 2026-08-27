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

import argparse
import json
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from flwr.server.strategy import FedAvg, FedProx
from loguru import logger
from sklearn.metrics import precision_recall_curve

from gtfs_olap.detection import detection_delays
from gtfs_olap.experiments import load_clients, load_events
from gtfs_olap.features import FEATURES
from gtfs_olap.federation import (
    Client, run_federation, train_centralized, train_local,
)
from gtfs_olap.model import alarm_threshold, reconstruction_errors

plt.rcParams.update({"figure.dpi": 140, "font.size": 9,
                     "axes.grid": True, "grid.alpha": 0.3})

DROPPED = ["d", "dmax", "delta_d"]
PERCENTILE = 90.0
LABELS = {"lokalny": "lokalny", "fedavg": "FedAvg", "fedprox": "FedProx",
          "scentralizowany": "scentralizowany"}

def _save(fig, path: Path) -> None:
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    logger.success(f"zapisano {path}")

def convergence(out: Path) -> None:

    fig, ax = plt.subplots(figsize=(6, 3.6))
    for name, path in (("FedAvg", "wyniki/fedavg.json"),
                       ("FedProx", "wyniki/fedprox.json"),
                       ("FedMedian", "wyniki/fedmedian.json"),
                       ("FedTrimmedAvg", "wyniki/fedtrimmed.json"),
                       ("FedAvg, 15 rund", "wyniki_r15/fedavg.json")):
        if not Path(path).exists():
            continue
        history = json.loads(Path(path).read_text(encoding="utf-8"))["history"]
        rounds = [h["round"] for h in history]
        loss = [float(np.mean(list(h["validation"].values()))) for h in history]
        style = "--" if "15" in name else "-"
        ax.plot(rounds, loss, style, marker="o", markersize=3, label=name)
    ax.set_xlabel("runda federacji")
    ax.set_ylabel("średni błąd rekonstrukcji\nna zbiorze walidacyjnym")
    ax.set_title("Zbieżność uczenia federacyjnego")
    ax.legend(fontsize=8)
    _save(fig, out / "01_zbieznosc_federacji.png")

def communication(out: Path) -> None:

    path = Path("wyniki_e4_s0/e4.json")
    if not path.exists():
        logger.warning("brak wyników E4, pomijam wykres narzutu")
        return
    payloads = [json.loads(Path(f"wyniki_e4_s{s}/e4.json").read_text(encoding="utf-8"))
                for s in (0, 1, 2) if Path(f"wyniki_e4_s{s}/e4.json").exists()]
    rounds = sorted(int(r) for r in payloads[0]["czestosc_rund"])
    f1 = [np.mean([p["czestosc_rund"][str(r)]["f1_event"] for p in payloads])
          for r in rounds]
    sd = [np.std([p["czestosc_rund"][str(r)]["f1_event"] for p in payloads], ddof=1)
          for r in rounds]
    mb = [payloads[0]["czestosc_rund"][str(r)]["mb_per_round"] * r for r in rounds]

    fig, ax = plt.subplots(figsize=(6, 3.6))
    ax.errorbar(mb, f1, yerr=sd, marker="o", capsize=3)
    for x, y, r in zip(mb, f1, rounds):
        ax.annotate(f"{r} rund", (x, y), textcoords="offset points",
                    xytext=(6, 6), fontsize=8)
    ax.set_xlabel("dane przesłane łącznie [MB na klienta]")
    ax.set_ylabel("F1 na poziomie zdarzeń")
    ax.set_title("Narzut komunikacyjny wobec jakości detekcji\n"
                 "(stały budżet 15 epok, 3 ziarna)")
    _save(fig, out / "02_narzut_komunikacyjny.png")

def _improved_models(features: Path):
    clients = load_clients(features)
    keep = [i for i, f in enumerate(FEATURES) if f not in DROPPED]
    clients = [Client(c.name, c.X_train[:, :, keep], c.X_val[:, :, keep])
               for c in clients]
    n_features = len(keep)
    kwargs = {"min_available_clients": len(clients), "min_fit_clients": len(clients)}
    logger.info("Trenuję modele do krzywych PR i opóźnień...")
    fedavg, _ = run_federation(clients, FedAvg(**kwargs), 5, n_features, 64, 3, 0.0, 0)
    fedprox, _ = run_federation(clients, FedProx(proximal_mu=0.1, **kwargs), 5,
                                n_features, 64, 3, 0.1, 0)
    central = train_centralized(clients, n_features, 64, 15, 0)
    locals_ = {c.name: train_local(c, n_features, 64, 15, 0) for c in clients}
    return clients, keep, {"lokalny": locals_, "fedavg": fedavg,
                           "fedprox": fedprox, "scentralizowany": central}

def pr_and_delays(out: Path, features: Path, events: Path) -> None:
    clients, keep, models = _improved_models(features)

    curves, delays = {}, {}
    for variant, model in models.items():
        truth_all, score_all, per_variant = [], [], []
        for client in clients:
            X, labels = load_events(events, client.name)
            X = X[:, :, keep]
            actual = model[client.name] if variant == "lokalny" else model
            threshold = alarm_threshold(
                reconstruction_errors(
                    actual, np.concatenate([client.X_train, client.X_val])),
                PERCENTILE)
            errors = reconstruction_errors(actual, X)
            if "touches" in labels.columns:
                drop = labels["touches"].to_numpy() & (labels["event_id"].to_numpy() < 0)
                errors, labels = errors[~drop], labels[~drop].reset_index(drop=True)
            truth_all.append((labels["event_id"].to_numpy() >= 0))

            score_all.append(errors / threshold)
            per_variant.append(detection_delays(errors, labels, threshold))
        curves[variant] = (np.concatenate(truth_all), np.concatenate(score_all))
        delays[variant] = np.concatenate(per_variant)

    fig, ax = plt.subplots(figsize=(6, 4))
    for variant, (truth, score) in curves.items():
        precision, recall, _ = precision_recall_curve(truth, score)
        ax.plot(recall, precision, label=LABELS[variant])
    base = curves["fedavg"][0].mean()
    ax.axhline(base, color="grey", linestyle=":", linewidth=1)
    ax.annotate(f"losowy detektor ({base:.2f})", (0.55, base),
                textcoords="offset points", xytext=(0, 5), fontsize=8, color="grey")
    ax.set_xlabel("czułość (jaki ułamek okien anomalnych wykryto)")
    ax.set_ylabel("precyzja (jaki ułamek alarmów był trafny)")
    ax.set_title("Krzywe precyzji i czułości, konfiguracja poprawiona")
    ax.legend(fontsize=8)
    _save(fig, out / "03_krzywe_precyzji_czulosci.png")

    order = [v for v in ("lokalny", "fedavg", "fedprox", "scentralizowany")
             if len(delays[v])]
    buckets = sorted({int(d) for v in order for d in delays[v]})
    fig, ax = plt.subplots(figsize=(6.5, 3.8))
    width = 0.8 / len(order)
    positions = np.arange(len(buckets))
    for i, variant in enumerate(order):
        values = delays[variant]
        share = [100 * np.mean(values == b) for b in buckets]
        bars = ax.bar(positions + i * width - 0.4 + width / 2, share, width,
                      label=f"{LABELS[variant]} (n={len(values)})")
        for bar, value in zip(bars, share):
            if value >= 3:
                ax.annotate(f"{value:.0f}%", (bar.get_x() + bar.get_width() / 2,
                                              value), ha="center", va="bottom",
                            fontsize=6.5)
    ax.set_xticks(positions)
    ax.set_xticklabels([f"{b} min" for b in buckets])
    ax.set_xlabel("opóźnienie wykrycia")
    ax.set_ylabel("udział wykrytych zdarzeń [%]")
    ax.set_ylim(0, 100)
    ax.set_title("Jak szybko pada alarm po rozpoczęciu zdarzenia\n"
                 "(tylko zdarzenia wykryte; rozdzielczość równa oknu 15 min)")
    ax.legend(fontsize=8)
    _save(fig, out / "04_opoznienie_detekcji.png")

def configurations(out: Path) -> None:

    base = json.loads(Path("wyniki_e1_3ziarna/e1.json").read_text(encoding="utf-8"))
    better = json.loads(Path("wyniki_e1_poprawiona/e1.json").read_text(encoding="utf-8"))
    variants = ["lokalny", "fedavg", "fedprox", "scentralizowany"]

    fig, axes = plt.subplots(1, 2, figsize=(9, 3.8))
    x = np.arange(len(variants))
    for ax, key, title, ylabel in (
            (axes[0], "f1_event", "Jakość detekcji", "F1 na poziomie zdarzeń"),
            (axes[1], "false_alarms_per_day", "Fałszywe alarmy",
             "alarmy fałszywe na dobę")):
        b = [base["summary"][key][v] for v in variants]
        n = [better["summary"][key][v] for v in variants]
        ax.bar(x - 0.2, b, 0.4, label="bazowa: 8 okien, 11 cech")
        ax.bar(x + 0.2, n, 0.4, label="poprawiona: 12 okien, 8 cech")
        ax.set_xticks(x)
        ax.set_xticklabels([LABELS[v] for v in variants], rotation=20, ha="right")
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.set_ylim(0, max(b + n) * 1.28)
        for xi, value in zip(x - 0.2, b):
            ax.annotate(f"{value:.2f}", (xi, value), ha="center",
                        va="bottom", fontsize=7)
        for xi, value in zip(x + 0.2, n):
            ax.annotate(f"{value:.2f}", (xi, value), ha="center",
                        va="bottom", fontsize=7)
    handles, texts = axes[0].get_legend_handles_labels()
    fig.legend(handles, texts, loc="lower center", ncol=2, fontsize=8,
               bbox_to_anchor=(0.5, -0.06))
    fig.suptitle("Wpływ poprawionej konfiguracji cech i długości sekwencji",
                 fontsize=10)
    _save(fig, out / "05_konfiguracja_bazowa_vs_poprawiona.png")

def ablation(out: Path) -> None:
    payloads = [json.loads(Path(f"wyniki_e4_s{s}/e4.json").read_text(encoding="utf-8"))
                for s in (0, 1, 2) if Path(f"wyniki_e4_s{s}/e4.json").exists()]
    if not payloads:
        return
    base = [p["ablacja_cech"]["pełny zestaw"]["f1_event"] for p in payloads]
    names = [n for n in payloads[0]["ablacja_cech"] if n != "pełny zestaw"]
    deltas = {n: [p["ablacja_cech"][n]["f1_event"] - b
                  for p, b in zip(payloads, base)] for n in names}
    order = sorted(names, key=lambda n: np.mean(deltas[n]))

    fig, ax = plt.subplots(figsize=(6.5, 3.8))
    means = [np.mean(deltas[n]) for n in order]
    sds = [np.std(deltas[n], ddof=1) for n in order]
    colours = ["tab:red" if m < 0 else "tab:green" for m in means]
    ax.barh([n.replace("bez: ", "") for n in order], means, xerr=sds,
            color=colours, capsize=3)
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_xlabel("zmiana F1 po usunięciu grupy cech")
    ax.set_title("Które cechy naprawdę pomagają\n"
                 "(słupek w prawo = usunięcie cechy POPRAWIA wynik)")
    _save(fig, out / "06_ablacja_cech.png")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("wykresy"))
    ap.add_argument("--features", type=Path, default=Path("cechy_L12"))
    ap.add_argument("--events", type=Path, default=Path("cechy_zdarzenia_L12"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    convergence(args.out)
    communication(args.out)
    configurations(args.out)
    ablation(args.out)
    pr_and_delays(args.out, args.features, args.events)
    print(f"\nwykresy w {args.out}: "
          f"{', '.join(sorted(p.name for p in args.out.glob('*.png')))}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
