"""Weryfikacja poprawności potoku cech i federacji.

Sprawdza własności, które muszą zachodzić, żeby wyniki eksperymentów były
wiarygodne. Uruchamiać po każdej zmianie w cechy.py, model.py lub federacja.py.

    python scripts/verify_pipeline.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from flwr.common import Code, FitRes, Status, ndarrays_to_parameters, parameters_to_ndarrays
from flwr.server.strategy import FedAvg

from gtfs_olap.cechy import CECHY, profil_dobowy
from gtfs_olap.klienci import KLIENCI, OPERATOR_NA_KLIENTA
from gtfs_olap.model import AutoenkoderGRU, wagi_do_tablic

BLEDY: list[str] = []


def sprawdz(warunek: bool, opis: str, szczegol: str = "") -> None:
    if warunek:
        print(f"  OK   {opis}")
    else:
        print(f"  BLAD {opis}  {szczegol}")
        BLEDY.append(opis)


def podzial_klientow(fakty: pd.DataFrame) -> None:
    print("\n[1] Podzial na klientow federacji")
    operatorzy = [o for lista in KLIENCI.values() for o in lista]
    sprawdz(len(operatorzy) == len(set(operatorzy)),
            "zaden operator nie nalezy do dwoch klientow")
    sprawdz(6 <= len(KLIENCI) <= 10,
            f"liczba klientow w zakresie 6-10 (jest {len(KLIENCI)})")
    w_danych = set(fakty.operator_id.unique())
    brakujacy = w_danych - set(operatorzy)
    sprawdz(not brakujacy, "kazdy operator z danych ma przypisanego klienta",
            f"brakuje: {brakujacy}")
    sprawdz(fakty.operator_id.map(OPERATOR_NA_KLIENTA).notna().all(),
            "zadna obserwacja nie gubi sie przy mapowaniu")


def poprawnosc_cech(c: pd.DataFrame) -> None:
    print("\n[2] Poprawnosc wyliczenia cech")
    print("    (na surowych wartosciach, przed standaryzacja)")
    kompl = c[c.kompletne]
    sprawdz(np.allclose(kompl.d, kompl.S / kompl.n), "d(t) = S(t)/n(t)")
    sprawdz(np.allclose(kompl.w, kompl.q / kompl.n), "w(t) = q(t)/n(t)")
    sprawdz(np.allclose(kompl.p, kompl.u / kompl.n), "p(t) = u(t)/n(t)")
    sprawdz(c[~c.kompletne].d.isna().all(),
            "puste okna nie maja wyliczonego d(t)")
    sprawdz(c.groupby("klient").kwadrans.apply(
        lambda s: s.diff().dropna().eq(pd.Timedelta("15min")).all()).all(),
        "siatka czasu jest ciagla co 15 minut")


def brak_przecieku(c: pd.DataFrame) -> None:
    print("\n[3] Brak przecieku danych testowych do cech")
    tren = c[c.zbior == "trening"]
    test = c[c.zbior == "test"]
    sprawdz(tren.kwadrans.max() < test.kwadrans.min(),
            "podzial jest czasowy - caly trening przed calym testem")

    # Profil liczony z calosci roznilby sie od tego z samego treningu.
    p_tren = profil_dobowy(tren).set_index(["klient", "godzina", "dzien_roboczy"])["m"]
    p_calosc = profil_dobowy(c).set_index(["klient", "godzina", "dzien_roboczy"])["m"]
    wspolne = p_tren.index.intersection(p_calosc.index)
    sprawdz(not np.allclose(p_tren[wspolne], p_calosc[wspolne]),
            "profil dobowy policzony z treningu, nie z calosci")

    # Standaryzacja z treningu: srednia ~0 na treningu, ale nie na tescie.
    zmienne = [k for k in CECHY if c[c.zbior == "trening"][k].std() > 1e-9]
    sr_tren = np.abs(tren[zmienne].mean()).max()
    sprawdz(sr_tren < 0.05,
            f"standaryzacja wyzerowala srednia na TRENINGU (max |sr|={sr_tren:.4f})")


def sekwencje_bez_luk(katalog: Path, c: pd.DataFrame) -> None:
    print("\n[4] Sekwencje wejsciowe modelu")
    laczne, ok_ksztalt, ok_nan = 0, True, True
    for kat in sorted(katalog.glob("klient=*")):
        for plik in ("X_trening.npy", "X_test.npy"):
            X = np.load(kat / plik)
            laczne += len(X)
            if len(X) and (X.shape[1] != 8 or X.shape[2] != len(CECHY)):
                ok_ksztalt = False
            if len(X) and np.isnan(X).any():
                ok_nan = False
    sprawdz(ok_ksztalt, f"kazda sekwencja ma ksztalt (8, {len(CECHY)})")
    sprawdz(ok_nan, "zadna sekwencja nie zawiera NaN")
    sprawdz(laczne > 0, f"zbudowano sekwencje (razem {laczne})")

    # Sekwencje moga powstac tylko z osmiu kolejnych KOMPLETNYCH okien.
    mozliwe = 0
    for _, g in c.sort_values("kwadrans").groupby("klient"):
        k = g.kompletne.to_numpy()
        mozliwe += sum(k[i - 7:i + 1].all() for i in range(7, len(k)))
    sprawdz(laczne <= mozliwe,
            f"liczba sekwencji nie przekracza liczby ciaglych okien ({laczne} <= {mozliwe})")


def agregacja_flower() -> None:
    print("\n[5] Agregacja federacyjna (Flower FedAvg)")
    torch.manual_seed(0)
    modele = [AutoenkoderGRU(len(CECHY), 16) for _ in range(3)]
    liczebnosci = [100, 200, 700]
    wyniki = [(None, FitRes(status=Status(Code.OK, ""),
                            parameters=ndarrays_to_parameters(wagi_do_tablic(m)),
                            num_examples=n, metrics={}))
              for m, n in zip(modele, liczebnosci)]

    strategia = FedAvg(min_available_clients=3, min_fit_clients=3)
    zagregowane, _ = strategia.aggregate_fit(1, wyniki, [])
    wynik = parameters_to_ndarrays(zagregowane)

    razem = sum(liczebnosci)
    oczekiwane = [
        sum(w * n for w, n in zip(warstwy, liczebnosci)) / razem
        for warstwy in zip(*[wagi_do_tablic(m) for m in modele])
    ]
    sprawdz(all(np.allclose(a, b, atol=1e-6) for a, b in zip(wynik, oczekiwane)),
            "wynik agregacji rowny sredniej wazonej liczebnoscia klientow")
    sprawdz(not np.allclose(wynik[0], wagi_do_tablic(modele[0])[0]),
            "model globalny rozni sie od modelu pojedynczego klienta")


def model_odtwarza_ksztalt() -> None:
    print("\n[6] Autoenkoder")
    m = AutoenkoderGRU(len(CECHY), 64)
    x = torch.randn(5, 8, len(CECHY))
    sprawdz(m(x).shape == x.shape, "wyjscie ma ksztalt wejscia")
    sprawdz(10_000 < m.liczba_parametrow() < 1_000_000,
            f"rozmiar modelu w widelkach z rozdz. 13 ({m.liczba_parametrow():,})")


def main() -> int:
    from gtfs_olap.cechy import wczytaj_fakty, zbuduj_cechy

    dane, cechy_kat = Path("dane"), Path("cechy")
    fakty = wczytaj_fakty(dane / "fakty")
    surowe = zbuduj_cechy(dane / "fakty", dane / "lookup",
                          pd.read_parquet(dane / "wymiary" / "dim_data.parquet"))
    gotowe = pd.read_parquet(cechy_kat / "cechy.parquet")

    podzial_klientow(fakty)
    poprawnosc_cech(surowe)
    brak_przecieku(gotowe)
    sekwencje_bez_luk(cechy_kat, gotowe)
    agregacja_flower()
    model_odtwarza_ksztalt()

    print("\n" + "=" * 60)
    if BLEDY:
        print(f"NIEPOWODZENIE: {len(BLEDY)} sprawdzen nie przeszlo")
        for b in BLEDY:
            print("  -", b)
        return 1
    print("WSZYSTKIE SPRAWDZENIA PRZESZLY")
    return 0


if __name__ == "__main__":
    sys.exit(main())
