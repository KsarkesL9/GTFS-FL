"""Test integracyjny symulacji federacji (kamień milowy tygodnia 1).

Wczytuje sekwencje z potoku cech, uruchamia trening federacyjny wybraną
strategią i porównuje go z modelami czysto lokalnymi.

    python scripts/run_federation.py --strategia fedavg --rundy 5
    python scripts/run_federation.py --strategia fedprox --mu 0.1
    python scripts/run_federation.py --strategia fedmedian
"""

import argparse
import json
from pathlib import Path

import numpy as np
from flwr.server.strategy import FedAvg, FedMedian, FedProx, FedTrimmedAvg
from loguru import logger

from gtfs_olap.cechy import CECHY
from gtfs_olap.federacja import Klient, trenuj_lokalnie, uruchom_federacje
from gtfs_olap.model import bledy_rekonstrukcji, prog_alarmowy

STRATEGIE = {
    "fedavg": FedAvg,
    "fedprox": FedProx,
    "fedmedian": FedMedian,          # obrona odporna, rozdz. 11.2
    "fedtrimmed": FedTrimmedAvg,     # obrona odporna, rozdz. 11.2
}


def wczytaj_klientow(katalog: Path, udzial_walidacyjny: float = 0.2) -> list[Klient]:
    klienci = []
    for kat in sorted(katalog.glob("klient=*")):
        X = np.load(kat / "X_trening.npy")
        if len(X) < 10:
            logger.warning(f"{kat.name}: tylko {len(X)} sekwencji, pomijam")
            continue
        # Podział czasowy, nie losowy - walidacja musi być późniejsza niż trening.
        granica = int(len(X) * (1 - udzial_walidacyjny))
        klienci.append(Klient(kat.name.split("=", 1)[1], X[:granica], X[granica:]))
    return klienci


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cechy", type=Path, default=Path("cechy"))
    ap.add_argument("--wyniki", type=Path, default=Path("wyniki"))
    ap.add_argument("--strategia", choices=list(STRATEGIE), default="fedavg")
    ap.add_argument("--rundy", type=int, default=5)
    ap.add_argument("--epoki-lokalne", type=int, default=3)
    ap.add_argument("--ukryte", type=int, default=64)
    ap.add_argument("--mu", type=float, default=0.1, help="tylko dla fedprox")
    ap.add_argument("--ziarno", type=int, default=0)
    args = ap.parse_args()

    klienci = wczytaj_klientow(args.cechy)
    if not klienci:
        logger.error("Brak klientów z wystarczającą liczbą sekwencji")
        return 1
    n_cech = klienci[0].X_tren.shape[2]
    assert n_cech == len(CECHY), f"{n_cech} cech w danych, {len(CECHY)} w module"

    logger.info(f"{len(klienci)} klientów, {sum(len(k.X_tren) for k in klienci)} "
                f"sekwencji treningowych, {n_cech} cech")

    kwargs = {"min_available_clients": len(klienci),
              "min_fit_clients": len(klienci),
              "fit_metrics_aggregation_fn": lambda m: {
                  "strata": float(np.mean([x["strata"] for _, x in m]))}}
    if args.strategia == "fedprox":
        kwargs["proximal_mu"] = args.mu
    strategia = STRATEGIE[args.strategia](**kwargs)
    mu = args.mu if args.strategia == "fedprox" else 0.0

    globalny, historia = uruchom_federacje(
        klienci, strategia, args.rundy, n_cech, args.ukryte,
        args.epoki_lokalne, mu, args.ziarno)

    # Budżet treningowy musi być równy, inaczej porównanie mierzy liczbę epok,
    # a nie wartość federacji.
    epoki_lokalne_odniesienia = args.rundy * args.epoki_lokalne
    logger.info(f"Modele czysto lokalne, {epoki_lokalne_odniesienia} epok "
                f"(tyle samo co federacja) - odniesienie dla E1...")
    print(f"\n{'klient':<26} {'sekw':>6} {'federacja':>11} {'lokalny':>11} {'zysk':>8} {'próg 99,5':>11}")
    porownanie = {}
    for k in klienci:
        e_fed = bledy_rekonstrukcji(globalny, k.X_wal)
        lokalny = trenuj_lokalnie(k, n_cech, args.ukryte,
                                  epoki=epoki_lokalne_odniesienia, ziarno=args.ziarno)
        e_lok = bledy_rekonstrukcji(lokalny, k.X_wal)
        zysk = 100 * (e_lok.mean() - e_fed.mean()) / e_lok.mean()
        porownanie[k.nazwa] = {
            "federacja": float(e_fed.mean()), "lokalny": float(e_lok.mean()),
            "zysk_proc": float(zysk), "prog": prog_alarmowy(e_fed),
        }
        print(f"{k.nazwa:<26} {len(k.X_tren):>6} {e_fed.mean():>11.5f} "
              f"{e_lok.mean():>11.5f} {zysk:>7.1f}% {prog_alarmowy(e_fed):>11.5f}")

    args.wyniki.mkdir(parents=True, exist_ok=True)
    plik = args.wyniki / f"{args.strategia}.json"
    plik.write_text(json.dumps({
        "strategia": args.strategia, "rundy": args.rundy, "mu": mu,
        "ziarno": args.ziarno, "parametrow": globalny.liczba_parametrow(),
        "mb_na_klienta_na_runde": historia[-1].mb_na_klienta,
        "historia": [{"runda": h.runda, "walidacja": h.walidacja,
                      "czas_s": h.czas_s} for h in historia],
        "porownanie": porownanie,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nparametrów modelu: {globalny.liczba_parametrow():,}")
    print(f"narzut komunikacyjny: {historia[-1].mb_na_klienta:.2f} MB na klienta na rundę")
    logger.success(f"Zapisano {plik}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
