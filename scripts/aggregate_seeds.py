\
\
\
\
\
\
\

import json
from pathlib import Path

import numpy as np
import pandas as pd

SEEDS = (0, 1, 2)

def _mean_sd(values: list[float]) -> str:
    a = np.array(values, dtype="float64")
    return f"{a.mean():.3f} ±{a.std(ddof=1):.3f}"

def e2() -> None:
    print("\n=== E2: adaptacja po dryfie, 3 ziarna x 2 typy dryfu ===")
    print(f"{'dryf':<6}{'wariant':<24}{'redukcja fałszywych':>22}{'spadek czułości':>20}")
    for drift in ("d1", "d2"):
        payloads = [json.loads((Path(f"wyniki_e2_{drift}_s{s}") / "e2.json")
                               .read_text(encoding="utf-8")) for s in SEEDS]
        for variant in payloads[0]["k2"]:
            red = [p["k2"][variant]["reduction"] for p in payloads]
            drop = [p["k2"][variant]["sensitivity_drop"] for p in payloads]
            met = sum(p["k2"][variant]["met"] for p in payloads)
            print(f"{drift.upper():<6}{variant:<24}{_mean_sd(red):>22}"
                  f"{_mean_sd(drop):>20}   K2 spełnione w {met}/3")

def e3() -> None:
    print("\n=== E3: transfer wiedzy, 3 ziarna ===")
    rows = {}
    wins = []
    for s in SEEDS:
        p = json.loads((Path(f"wyniki_e3_s{s}") / "e3.json").read_text(encoding="utf-8"))
        wins.append(p["wins_over_local"])
        for variant, v in p["summary"].items():
            rows.setdefault(variant, []).append(v["f1_event"])
    print(f"{'wariant':<30}{'F1 zdarzeń':>18}")
    for variant, values in rows.items():
        print(f"{variant:<30}{_mean_sd(values):>18}")
    print(f"federacja lepsza od lokalnego u {sum(wins)} z {3 * 9} układów")

def e4() -> None:
    print("\n=== E4: wrażliwość, 3 ziarna ===")
    payloads = [json.loads((Path(f"wyniki_e4_s{s}") / "e4.json")
                           .read_text(encoding="utf-8")) for s in SEEDS]
    print("długość sekwencji:")
    for length in payloads[0]["dlugosc_sekwencji"]:
        vals = [p["dlugosc_sekwencji"][length]["f1_event"] for p in payloads]
        print(f"  {length:>3} okien   {_mean_sd(vals)}")
    print("ablacja grup cech (zmiana F1 wobec pełnego zestawu):")
    base = [p["ablacja_cech"]["pełny zestaw"]["f1_event"] for p in payloads]
    print(f"  {'pełny zestaw':<28}{_mean_sd(base)}")
    for name in payloads[0]["ablacja_cech"]:
        if name == "pełny zestaw":
            continue
        deltas = [p["ablacja_cech"][name]["f1_event"] - b
                  for p, b in zip(payloads, base)]
        print(f"  {name:<28}{_mean_sd(deltas)}")
    print("percentyl progu:")
    for q in payloads[0]["percentyl_progu"]:
        f1 = [p["percentyl_progu"][q]["f1_event"] for p in payloads]
        fa = [p["percentyl_progu"][q]["false_alarms_per_day"] for p in payloads]
        print(f"  P{q:<6} F1 {_mean_sd(f1)}   fałsz/dobę {_mean_sd(fa)}")
    print("rundy federacji przy 15 epokach:")
    for r in payloads[0]["czestosc_rund"]:
        f1 = [p["czestosc_rund"][r]["f1_event"] for p in payloads]
        print(f"  {r:>3} rund   F1 {_mean_sd(f1)}")

def e5() -> None:
    print("\n=== E5: odporność, 3 ziarna ===")
    frames = [pd.read_parquet(Path(f"wyniki_e5_s{s}") / "e5.parquet") for s in SEEDS]
    frame = pd.concat(frames, keys=SEEDS, names=["ziarno"]).reset_index(level=0)
    pivot = frame.pivot_table(index=["test", "agregacja"], columns="ziarno",
                              values="f1_event")
    print(f"{'test':<28}{'agregacja':<14}{'F1 zdarzeń':>18}")
    for (test, aggregation), row in pivot.iterrows():
        print(f"{test:<28}{aggregation:<14}{_mean_sd(list(row.values)):>18}")

    print("\nszkoda wyrządzona przez atak (spadek F1 wobec T1, agregacja uśredniająca):")
    for test in ("T2 zatrucie danych", "T4 skalowanie x10",
                 "T5 atak ukierunkowany (A1)"):
        damage = []
        for f in frames:
            avg = f[f["agregacja"] == "uśredniająca"].set_index("test")["f1_event"]
            reference = ("T1 bez ataku" if not test.startswith("T5")
                         else "T5 odniesienie (A1)")
            if test in avg.index and reference in avg.index:
                damage.append(avg[reference] - avg[test])
        if damage:
            a = np.array(damage)
            verdict = ("szkoda mniejsza niż rozrzut międzyziarnowy"
                       if a.mean() <= a.std(ddof=1) else "szkoda istotna")
            print(f"  {test:<30}{_mean_sd(damage):>18}   {verdict}")

def main() -> int:
    e2()
    e3()
    e4()
    e5()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
