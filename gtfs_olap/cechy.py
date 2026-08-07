"""Moduł B: potok cech z rozdz. 8 specyfikacji.

Wejście to Parquety surowych faktów i lookup_schedule z archiwum na Drive,
wyjście to macierz cech na okno 15-minutowe dla każdego klienta federacji.

ODCHYŁKA OD SPECYFIKACJI, cecha h(t). Rozdz. 8 definiuje ją jako odchylenie
standardowe odstępów między kolejnymi przyjazdami kursów tej samej linii.
Zmierzone na danych: w oknie 15-minutowym tylko 0,1% par (linia, przystanek)
ma trzy przyjazdy potrzebne do policzenia odchylenia, a w oknie godzinnym
9,8%. Definicja jest więc niepoliczalna w zadanej granulacji.

Zamiast tego liczymy odstępy między rzeczywistymi ODJAZDAMI kursów tej samej
linii i kierunku, sprowadzonymi do wspólnego punktu odniesienia (pierwszy
przystanek kursu), w oknie 60 minut kończącym się na bieżącym kwadransie.
Przy tej definicji 42,4% grup ma komplet danych. Mierzy to samo zjawisko -
nieregularność następstwa kursów - ale w granulacji, w której da się je
zmierzyć.
"""

from __future__ import annotations

import glob
from pathlib import Path

import numpy as np
import pandas as pd

from gtfs_olap.klienci import OPERATOR_NA_KLIENTA

# Ten sam próg co w CA.sql - artefakty ZTM rzędu 195 godzin zaburzyłyby
# średnią w oknie silniej niż anomalie, które model ma wykrywać.
OPOZNIENIE_MIN = -3600
OPOZNIENIE_MAX = 7200

# Rozdz. 8: za punktualne uznaje się opóźnienie od -30 do 60 sekund.
PUNKTUALNE_MIN = -30
PUNKTUALNE_MAX = 60

OKNO = "15min"
OKNO_ODSTEPOW_MIN = 60
MIN_KURSOW_DO_ODSTEPU = 3

TZ = "Europe/Warsaw"

CECHY = ["d", "w", "dmax", "n", "p", "delta_d", "r", "h",
         "sin_godz", "cos_godz", "dzien_roboczy"]


def _do_sekund(czas: pd.Series) -> pd.Series:
    """'25:30:00' -> 91800. GTFS koduje kursy nocne godzinami >= 24."""
    czesci = czas.str.split(":", expand=True).astype("float64")
    return czesci[0] * 3600 + czesci[1] * 60 + czesci[2]


def wczytaj_fakty(katalog: str | Path) -> pd.DataFrame:
    """Wczytuje wszystkie dobowe Parquety faktów z katalogu."""
    pliki = sorted(glob.glob(str(Path(katalog) / "**" / "*.parquet"), recursive=True))
    if not pliki:
        raise FileNotFoundError(f"Brak plików Parquet w {katalog}")
    df = pd.concat((pd.read_parquet(p) for p in pliki), ignore_index=True)
    df["klient"] = df["operator_id"].map(OPERATOR_NA_KLIENTA)
    nieznane = df["klient"].isna().sum()
    if nieznane:
        raise ValueError(f"{nieznane} obserwacji od operatorów spoza klienci.py")
    df["kwadrans"] = df["ts"].dt.tz_convert(TZ).dt.floor(OKNO)
    return df


def wczytaj_lookup(katalog: str | Path) -> pd.DataFrame:
    """lookup_schedule wszystkich wersji, zredukowany do pierwszego przystanku
    każdego kursu - to punkt odniesienia dla odstępów między kursami."""
    pliki = sorted(glob.glob(str(Path(katalog) / "**" / "*.parquet"), recursive=True))
    if not pliki:
        raise FileNotFoundError(f"Brak plików lookup w {katalog}")
    df = pd.concat((pd.read_parquet(p) for p in pliki), ignore_index=True)
    df = df.sort_values("stop_sequence").drop_duplicates(["wersja_id", "trip_id"])
    df["odjazd_s"] = _do_sekund(df["rozkladowy_przyjazd"])
    return df[["wersja_id", "trip_id", "odjazd_s"]]


def wielkosci_pierwotne(fakty: pd.DataFrame) -> pd.DataFrame:
    """n(t), S(t), q(t), u(t) i ekstrema na (klient, kwadrans) - rozdz. 8."""
    obs = fakty["status"] == "OBSERWACJA"
    sensowne = obs & fakty["opoznienie_s"].between(OPOZNIENIE_MIN, OPOZNIENIE_MAX)

    df = fakty.assign(
        _n=sensowne.astype("int64"),
        _S=fakty["opoznienie_s"].where(sensowne),
        _q=(sensowne & fakty["opoznienie_s"].between(
            PUNKTUALNE_MIN, PUNKTUALNE_MAX)).astype("int64"),
        _u=(fakty["status"] == "POMINIETY").astype("int64"),
        _odrzucone=(obs & ~sensowne).astype("int64"),
    )
    return df.groupby(["klient", "kwadrans"]).agg(
        n=("_n", "sum"),
        S=("_S", "sum"),
        q=("_q", "sum"),
        u=("_u", "sum"),
        dmax=("_S", "max"),
        odrzucone=("_odrzucone", "sum"),
    ).reset_index()


def nieregularnosc_odstepow(fakty: pd.DataFrame, lookup: pd.DataFrame) -> pd.DataFrame:
    """h(t) - patrz odchyłka opisana w docstringu modułu."""
    obs = fakty[fakty["status"] == "OBSERWACJA"]
    # Jedno opóźnienie na kurs: mediana po wszystkich jego obserwacjach jest
    # odporniejsza niż pojedynczy odczyt.
    kursy = (obs.groupby(["wersja_id", "trip_id", "klient", "linia_id", "kierunek",
                          "data_kursu"], observed=True)["opoznienie_s"]
             .median().reset_index())
    kursy = kursy.merge(lookup, on=["wersja_id", "trip_id"], how="inner")

    kursy["odjazd"] = (
        pd.to_datetime(kursy["data_kursu"]).dt.tz_localize(TZ)
        + pd.to_timedelta(kursy["odjazd_s"] + kursy["opoznienie_s"], unit="s")
    )
    kursy = kursy.sort_values("odjazd")
    kursy["odstep_s"] = (kursy.groupby(["klient", "linia_id", "kierunek"],
                                       observed=True)["odjazd"].diff()
                         .dt.total_seconds())
    kursy = kursy.dropna(subset=["odstep_s"])
    kursy["kwadrans"] = kursy["odjazd"].dt.floor(OKNO)

    # Okno kroczące 60 minut: każdy odstęp trafia do czterech kolejnych
    # kwadransów, licząc od tego, w którym nastąpił odjazd.
    przesuniecia = [pd.Timedelta(minutes=15 * i)
                    for i in range(OKNO_ODSTEPOW_MIN // 15)]
    rozszerzone = pd.concat(
        [kursy.assign(kwadrans=kursy["kwadrans"] + p) for p in przesuniecia],
        ignore_index=True)

    na_linie = (rozszerzone.groupby(["klient", "kwadrans", "linia_id", "kierunek"],
                                    observed=True)["odstep_s"]
                .agg(["std", "count"]).reset_index())
    na_linie = na_linie[na_linie["count"] >= MIN_KURSOW_DO_ODSTEPU - 1]

    return (na_linie.groupby(["klient", "kwadrans"], observed=True)["std"]
            .mean().reset_index(name="h"))


def siatka_czasu(df: pd.DataFrame) -> pd.DataFrame:
    """Pełna siatka 15-minutowa dla każdego klienta.

    Bez niej brakujące okna byłyby niewidoczne, a sekwencje wejściowe modelu
    sklejałyby obserwacje po obu stronach luki."""
    kwadranse = pd.date_range(df["kwadrans"].min(), df["kwadrans"].max(),
                              freq=OKNO, tz=TZ)
    klienci = sorted(df["klient"].unique())
    return pd.MultiIndex.from_product([klienci, kwadranse],
                                      names=["klient", "kwadrans"]).to_frame(index=False)


def profil_dobowy(df: pd.DataFrame) -> pd.DataFrame:
    """Mediana d wg godziny doby i typu dnia - podstawa cechy r(t).

    Rozdz. 8 wymaga, żeby profil był liczony WYŁĄCZNIE na danych treningowych,
    inaczej informacja o zbiorze testowym przecieka do cechy."""
    return (df.dropna(subset=["d"])
            .groupby(["klient", "godzina", "dzien_roboczy"], observed=True)["d"]
            .median().reset_index(name="m"))


def zbuduj_cechy(katalog_faktow: str | Path, katalog_lookup: str | Path,
                 dim_data: pd.DataFrame) -> pd.DataFrame:
    """Pełna macierz cech na (klient, kwadrans)."""
    fakty = wczytaj_fakty(katalog_faktow)
    lookup = wczytaj_lookup(katalog_lookup)

    df = siatka_czasu(fakty).merge(wielkosci_pierwotne(fakty),
                                   on=["klient", "kwadrans"], how="left")
    df[["n", "q", "u", "odrzucone"]] = df[["n", "q", "u", "odrzucone"]].fillna(0)
    df = df.merge(nieregularnosc_odstepow(fakty, lookup),
                  on=["klient", "kwadrans"], how="left")

    # Okno bez obserwacji nie ma sensownego d ani w - zostaje NaN i jest
    # oznaczone jako niekompletne, żeby budowniczy sekwencji je odrzucił.
    puste = df["n"] == 0
    df["kompletne"] = ~puste
    df["d"] = np.where(puste, np.nan, df["S"] / df["n"])
    df["w"] = np.where(puste, np.nan, df["q"] / df["n"])
    df["p"] = np.where(puste, np.nan, df["u"] / df["n"])
    df["dmax"] = df["dmax"].where(~puste)

    df["data"] = df["kwadrans"].dt.date
    df["godzina"] = df["kwadrans"].dt.hour
    typy = dim_data.set_index("data")["typ_dnia"]
    df["typ_dnia"] = df["data"].map(typy)
    df["dzien_roboczy"] = df["typ_dnia"].fillna("").str.startswith("dni robocze").astype(int)

    kat = 2 * np.pi * (df["kwadrans"].dt.hour * 60 + df["kwadrans"].dt.minute) / 1440
    df["sin_godz"] = np.sin(kat)
    df["cos_godz"] = np.cos(kat)

    df = df.sort_values(["klient", "kwadrans"])
    df["delta_d"] = df.groupby("klient", observed=True)["d"].diff()
    return df.reset_index(drop=True)


def dodaj_odchylenie_od_profilu(df: pd.DataFrame, profil: pd.DataFrame) -> pd.DataFrame:
    """r(t) = d(t) - m(godzina, typ dnia), z profilu treningowego."""
    out = df.merge(profil, on=["klient", "godzina", "dzien_roboczy"], how="left")
    out["r"] = out["d"] - out["m"]
    return out.drop(columns=["m"])


def statystyki_standaryzacji(df: pd.DataFrame) -> pd.DataFrame:
    """Średnia i odchylenie każdej cechy, osobno dla klienta (rozdz. 7.2)."""
    st = df.groupby("klient", observed=True)[CECHY].agg(["mean", "std"])
    st.columns = [f"{c}_{s}" for c, s in st.columns]
    return st.reset_index()


def standaryzuj(df: pd.DataFrame, statystyki: pd.DataFrame) -> pd.DataFrame:
    """Standaryzacja per klient. NaN-y (puste okna, brak h) -> 0, czyli
    średnia rozkładu treningowego."""
    out = df.merge(statystyki, on="klient", how="left")
    for c in CECHY:
        sd = out[f"{c}_std"].replace(0, np.nan)
        out[c] = ((out[c] - out[f"{c}_mean"]) / sd).fillna(0.0)
    return out.drop(columns=[f"{c}_{s}" for c in CECHY for s in ("mean", "std")])


def sekwencje(df: pd.DataFrame, dlugosc: int = 8) -> tuple[np.ndarray, pd.DataFrame]:
    """Sekwencje wejściowe modelu: X(t) z okien t-(dlugosc-1) .. t.

    Sekwencje przecinające lukę w zbieraniu są odrzucane, zgodnie z regułą
    z rozdz. 4.2. Zwraca tablicę (próbki, dlugosc, cechy) i opis próbek."""
    okna, opisy = [], []
    for klient, g in df.sort_values("kwadrans").groupby("klient", observed=True):
        wartosci = g[CECHY].to_numpy(dtype="float32")
        kompletne = g["kompletne"].to_numpy()
        czasy = g["kwadrans"].to_numpy()
        ciagle = (g["kwadrans"].diff().dt.total_seconds().fillna(900) == 900).to_numpy()
        for i in range(dlugosc - 1, len(g)):
            wycinek = slice(i - dlugosc + 1, i + 1)
            if not kompletne[wycinek].all() or not ciagle[wycinek][1:].all():
                continue
            okna.append(wartosci[wycinek])
            opisy.append((klient, czasy[i]))
    if not okna:
        return np.empty((0, dlugosc, len(CECHY)), dtype="float32"), pd.DataFrame()
    return (np.stack(okna),
            pd.DataFrame(opisy, columns=["klient", "kwadrans"]))
