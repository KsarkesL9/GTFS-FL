"""Budowa macierzy cech dla klientów federacji (Moduł B, rozdz. 7.2).

Czyta Parquety z archiwum, liczy cechy z rozdz. 8, dzieli zbiór czasowo,
standaryzuje osobno dla każdego klienta i zapisuje sekwencje wejściowe.

Profil dobowy i statystyki standaryzacji liczone są WYŁĄCZNIE na części
treningowej - inaczej informacja o zbiorze testowym przecieka do cech.

    python scripts/build_features.py --dane dane --wyjscie cechy
    python scripts/build_features.py --udzial-treningowy 0.7 --dlugosc-sekwencji 8
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

from gtfs_olap.cechy import (
    CECHY, dodaj_odchylenie_od_profilu, profil_dobowy, sekwencje,
    standaryzuj, statystyki_standaryzacji, zbuduj_cechy,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dane", type=Path, default=Path("dane"))
    ap.add_argument("--wyjscie", type=Path, default=Path("cechy"))
    ap.add_argument("--udzial-treningowy", type=float, default=0.7)
    ap.add_argument("--dlugosc-sekwencji", type=int, default=8)
    args = ap.parse_args()

    dim_data = pd.read_parquet(args.dane / "wymiary" / "dim_data.parquet")
    logger.info("Liczę wielkości pierwotne i cechy bazowe...")
    df = zbuduj_cechy(args.dane / "fakty", args.dane / "lookup", dim_data)

    granica = df["kwadrans"].quantile(args.udzial_treningowy)
    df["zbior"] = np.where(df["kwadrans"] <= granica, "trening", "test")
    logger.info(f"Podział czasowy na {granica:%Y-%m-%d %H:%M}: "
                f"{(df.zbior=='trening').sum():,} / {(df.zbior=='test').sum():,} okien")

    trening = df[df["zbior"] == "trening"]
    df = dodaj_odchylenie_od_profilu(df, profil_dobowy(trening))
    statystyki = statystyki_standaryzacji(df[df["zbior"] == "trening"])
    df = standaryzuj(df, statystyki)

    args.wyjscie.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.wyjscie / "cechy.parquet", index=False, compression="zstd")
    statystyki.to_parquet(args.wyjscie / "statystyki_standaryzacji.parquet", index=False)

    print(f"\n{'klient':<26} {'okien':>7} {'kompl.':>7} {'sekw.tren':>10} {'sekw.test':>10}")
    for klient, g in df.groupby("klient", observed=True):
        X_tr, _ = sekwencje(g[g.zbior == "trening"], args.dlugosc_sekwencji)
        X_te, opis_te = sekwencje(g[g.zbior == "test"], args.dlugosc_sekwencji)
        kat = args.wyjscie / f"klient={klient}"
        kat.mkdir(exist_ok=True)
        np.save(kat / "X_trening.npy", X_tr)
        np.save(kat / "X_test.npy", X_te)
        if len(opis_te):
            opis_te.to_parquet(kat / "opis_test.parquet", index=False)
        print(f"{klient:<26} {len(g):>7,} {int(g.kompletne.sum()):>7,} "
              f"{len(X_tr):>10,} {len(X_te):>10,}")

    print(f"\ncechy: {', '.join(CECHY)}")
    logger.success(f"Zapisano do {args.wyjscie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
