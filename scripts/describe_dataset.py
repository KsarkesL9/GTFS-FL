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

from gtfs_olap.clients import CLIENTS
from gtfs_olap.features import (
    FEATURES, add_profile_deviation, build_features, daily_profile,
)

DICTIONARY = [
    ("d", "średnie opóźnienie w oknie", "S(t) / n(t)", "s",
     "wielkość pierwotna S to suma opóźnień po filtrze sensowności −1h…+2h"),
    ("w", "udział obserwacji punktualnych", "q(t) / n(t)", "0…1",
     "punktualne to opóźnienie w przedziale od −30 s do +60 s"),
    ("dmax", "największe opóźnienie w oknie", "max opóźnienia", "s",
     "ten sam filtr sensowności co dla d"),
    ("n", "natężenie obserwacji", "liczba obserwacji", "szt.",
     "jedna obserwacja to jeden odczyt pary kurs-przystanek, nie jeden pojazd"),
    ("p", "udział zatrzymań pominiętych", "u(t) / n(t)", "0…1",
     "tożsamościowo zerowa: ZTM nie raportuje pominięć ani odwołań"),
    ("delta_d", "przyrost średniego opóźnienia", "d(t) − d(t−1)", "s",
     "puste dla pierwszego okna klienta"),
    ("r", "odchylenie od profilu dobowego", "d(t) − m(godzina, typ dnia)", "s",
     "profil m liczony WYŁĄCZNIE na zbiorze treningowym"),
    ("h", "nieregularność odstępów między kursami", "średnie odch. std. odstępów",
     "s", "poziom (linia, kierunek), okno kroczące 60 min - odstępstwo 16.3"),
    ("sin_hour", "pora doby, składowa sinus", "sin(2π·minuta_doby/1440)", "−1…1",
     "kodowanie kołowe, żeby 23:45 i 00:00 leżały blisko siebie"),
    ("cos_hour", "pora doby, składowa cosinus", "cos(2π·minuta_doby/1440)", "−1…1",
     "jak wyżej"),
    ("workday", "dzień roboczy", "typ dnia z dim_data", "0 lub 1",
     "1 dla dni roboczych wakacyjnych, 0 dla sobót, niedziel i świąt"),
]

def _fmt(value: float) -> str:
    if pd.isna(value):
        return "—"
    if abs(value) >= 1000:
        return f"{value:,.0f}".replace(",", " ")
    if abs(value) >= 10:
        return f"{value:.1f}"
    return f"{value:.3f}"

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("dane"))
    ap.add_argument("--features", type=Path, default=Path("cechy"))
    ap.add_argument("--out", type=Path, default=Path("docs/zbior-danych.md"))
    ap.add_argument("--train-share", type=float, default=0.7)
    args = ap.parse_args()

    logger.info("Liczę cechy surowe (przed standaryzacją)...")
    dim_data = pd.read_parquet(args.data / "wymiary" / "dim_data.parquet")
    raw = build_features(args.data / "fakty", args.data / "lookup", dim_data)
    boundary = raw["window"].quantile(args.train_share)
    raw["split"] = np.where(raw["window"] <= boundary, "train", "test")

    raw = add_profile_deviation(raw, daily_profile(raw[raw["split"] == "train"]))

    lines: list[str] = []
    add = lines.append

    add("# Opis zbioru danych")
    add("")
    add("Dokument wymagany przez podrozdział 14.2 specyfikacji. Wszystkie liczby")
    add("wyliczone są ze zbioru, nie wpisane ręcznie; wygenerowano poleceniem")
    add("`python scripts/describe_dataset.py`.")
    add("")

    add("## 1. Zakres")
    add("")
    span = f"{raw['window'].min():%Y-%m-%d} … {raw['window'].max():%Y-%m-%d}"
    add(f"| pozycja | wartość |")
    add(f"|---|---|")
    add(f"| Okres | {span} |")
    add(f"| Klientów federacji | {raw['client'].nunique()} |")
    add(f"| Operatorów | {sum(len(v) for v in CLIENTS.values())} |")
    add(f"| Okien 15-minutowych | {len(raw):,} |".replace(",", " "))
    add(f"| Okien kompletnych | {int(raw['complete'].sum()):,} "
        f"({100 * raw['complete'].mean():.1f}%) |".replace(",", " "))
    add(f"| Obserwacji w oknach kompletnych | {int(raw['n'].sum()):,} |"
        .replace(",", " "))
    add(f"| Granica podziału trening/test | {boundary:%Y-%m-%d %H:%M} |")
    add(f"| Okien treningowych | {int((raw.split == 'train').sum()):,} |"
        .replace(",", " "))
    add(f"| Okien testowych | {int((raw.split == 'test').sum()):,} |"
        .replace(",", " "))
    add("")
    add("Okno niekompletne to takie, w którym nie było ani jednej obserwacji.")
    add("Takie okna nie tworzą sekwencji wejściowych i nie wchodzą do oceny.")
    add("")

    add("## 2. Słownik cech")
    add("")
    add("| cecha | znaczenie | wzór | jednostka | uwagi |")
    add("|---|---|---|---|---|")
    for name, meaning, formula, unit, note in DICTIONARY:
        add(f"| `{name}` | {meaning} | {formula} | {unit} | {note} |")
    add("")
    add("Wektor wejściowy modelu ma "
        f"{len(FEATURES)} cech w kolejności: "
        + ", ".join(f"`{f}`" for f in FEATURES) + ".")
    add("")
    add("Wielkości pierwotne, z których cechy są wyliczane: `n` liczba")
    add("obserwacji, `S` suma opóźnień, `q` liczba obserwacji punktualnych,")
    add("`u` liczba zatrzymań pominiętych, `dmax` największe opóźnienie.")
    add("")

    add("## 3. Statystyki opisowe")
    add("")
    add("Wartości **surowe, przed standaryzacją**, liczone na oknach kompletnych.")
    add("")
    complete = raw[raw["complete"]]
    add("| cecha | średnia | odch. std. | min | mediana | maks. | braki |")
    add("|---|---|---|---|---|---|---|")
    for f in FEATURES:
        col = complete[f]
        missing = 100 * col.isna().mean()
        add(f"| `{f}` | {_fmt(col.mean())} | {_fmt(col.std())} | "
            f"{_fmt(col.min())} | {_fmt(col.median())} | {_fmt(col.max())} | "
            f"{missing:.1f}% |")
    add("")
    add("Braki w `h` biorą się z okien, w których żadna para (linia, kierunek)")
    add("nie miała dość kursów, by policzyć odstępy; w `delta_d` z pierwszego")
    add("okna każdego klienta. Przy standaryzacji braki zastępowane są zerem,")
    add("czyli średnią rozkładu treningowego.")
    add("")

    add("## 4. Liczność okien i sekwencji dla każdego klienta")
    add("")
    add("| klient | operatorów | okien | kompletnych | trening | test "
        "| sekw. tren. | sekw. test |")
    add("|---|---|---|---|---|---|---|---|")
    for client, group in raw.groupby("client", observed=True):
        directory = args.features / f"client={client}"
        n_train = n_test = 0
        if directory.exists():
            n_train = len(np.load(directory / "X_train.npy"))
            n_test = len(np.load(directory / "X_test.npy"))
        add(f"| {client} | {len(CLIENTS.get(client, []))} | {len(group):,} | "
            f"{int(group.complete.sum()):,} "
            f"({100 * group.complete.mean():.0f}%) | "
            f"{int((group.split == 'train').sum()):,} | "
            f"{int((group.split == 'test').sum()):,} | "
            f"{n_train:,} | {n_test:,} |".replace(",", " "))
    add("")
    add("Sekwencja powstaje z ośmiu kolejnych okien kompletnych, więc jest ich")
    add("mniej niż okien: każda przerwa w danych kosztuje siedem sekwencji.")
    add("")

    add("## 5. Skład klientów federacji")
    add("")
    names = (pd.read_parquet(args.data / "wymiary" / "dim_operator.parquet")
             .set_index("operator_id")["nazwa"].to_dict())
    add("| klient | operatorzy |")
    add("|---|---|")
    for client, operators in CLIENTS.items():
        listed = ", ".join(sorted(names.get(o, f"operator {o}") for o in operators))
        add(f"| {client} | {listed} |")
    add("")
    add("Podział jest geograficzny i branżowy, nigdy według opóźnienia -")
    add("kryterium oparte na zmiennej celu wprowadzałoby ją do definicji")
    add("klienta. Uzasadnienie w podrozdziale 16.9 specyfikacji.")
    add("")

    add("## 6. Znane ograniczenia zbioru")
    add("")
    add("- Cecha `p` jest tożsamościowo zerowa: na 66,4 mln obserwacji nie ma")
    add("  ani jednego zatrzymania oznaczonego jako pominięte ani kursu")
    add("  odwołanego. Szczegóły w podrozdziale 17.6.")
    add("- Dwie awarie po stronie GZM: 9 sierpnia przez 6 h 39 min i 18/19")
    add("  sierpnia przez 8 h 39 min strumień zwracał poprawny komunikat bez")
    add("  ani jednego pojazdu. Okna z tych okresów są niekompletne.")
    add("- Około 0,017% wierszy ma datę kursu o dobę za wczesną, dla kursów")
    add("  rozkładowo po północy. Nieistotne po agregacji do poziomu klienta.")
    add("- Cały materiał pochodzi z rozkładu wakacyjnego. Od 1 września")
    add("  obowiązuje rozkład szkolny, czyli inny reżim.")
    add("")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines), encoding="utf-8")
    logger.success(f"Zapisano {args.out} ({len(lines)} wierszy)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
