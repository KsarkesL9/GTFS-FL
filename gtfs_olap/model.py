"""Autoenkoder rekurencyjny do detekcji anomalii (rozdz. 2.2 i 8.1).

Enkoder GRU sprowadza okno ośmiu kwadransów do reprezentacji ukrytej, dekoder
odtwarza z niej wejście. Błąd rekonstrukcji e(t) jest wskaźnikiem anomalności:
próbki odbiegające od nauczonego wzorca odtwarzają się źle.

GRU zamiast LSTM - rozdz. 6.1 dopuszcza oba, a GRU ma o jedną bramkę mniej,
co przy treningu na procesorze (rozdz. 9.1) ma znaczenie.
"""

from __future__ import annotations

import numpy as np
import torch
from torch import nn

DOMYSLNY_PERCENTYL = 99.5


class AutoenkoderGRU(nn.Module):
    def __init__(self, n_cech: int, ukryte: int = 64, warstwy: int = 1):
        super().__init__()
        self.enkoder = nn.GRU(n_cech, ukryte, warstwy, batch_first=True)
        self.dekoder = nn.GRU(ukryte, ukryte, warstwy, batch_first=True)
        self.wyjscie = nn.Linear(ukryte, n_cech)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        _, stan = self.enkoder(x)
        # Reprezentacja ukryta powielona na całą długość sekwencji - dekoder
        # odtwarza wszystkie kwadranse z jednego wektora kontekstu.
        kontekst = stan[-1].unsqueeze(1).repeat(1, x.size(1), 1)
        odtworzone, _ = self.dekoder(kontekst)
        return self.wyjscie(odtworzone)

    def liczba_parametrow(self) -> int:
        return sum(p.numel() for p in self.parameters())


def bledy_rekonstrukcji(model: nn.Module, X: np.ndarray,
                        partia: int = 256) -> np.ndarray:
    """e(t) dla każdej próbki: MSE uśredniony po cechach i kwadransach."""
    model.eval()
    out = []
    with torch.no_grad():
        for i in range(0, len(X), partia):
            x = torch.from_numpy(X[i:i + partia])
            out.append(((model(x) - x) ** 2).mean(dim=(1, 2)).numpy())
    return np.concatenate(out) if out else np.empty(0, dtype="float32")


def prog_alarmowy(bledy: np.ndarray, percentyl: float = DOMYSLNY_PERCENTYL) -> float:
    """Próg z percentyla rozkładu błędów na zbiorze walidacyjnym (rozdz. 8.1)."""
    return float(np.percentile(bledy, percentyl)) if len(bledy) else float("inf")


def trenuj(model: nn.Module, X: np.ndarray, epoki: int = 20, partia: int = 64,
           lr: float = 1e-3, mu: float = 0.0,
           globalne: list[np.ndarray] | None = None) -> float:
    """Trening lokalny. Zwraca średnią stratę z ostatniej epoki.

    mu > 0 włącza składnik proksymalny FedProx: kara za oddalanie się wag
    lokalnych od modelu globalnego, łagodząca skutki niejednorodności
    klientów (rozdz. 2.1)."""
    if len(X) == 0:
        return float("nan")
    model.train()
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    kryterium = nn.MSELoss()
    kotwica = ([torch.from_numpy(w) for w in globalne]
               if mu > 0 and globalne is not None else None)

    strata = float("nan")
    for _ in range(epoki):
        perm = np.random.permutation(len(X))
        sumy, liczba = 0.0, 0
        for i in range(0, len(X), partia):
            x = torch.from_numpy(X[perm[i:i + partia]])
            opt.zero_grad()
            koszt = kryterium(model(x), x)
            if kotwica is not None:
                kara = sum(((p - g) ** 2).sum()
                           for p, g in zip(model.parameters(), kotwica))
                koszt = koszt + (mu / 2) * kara
            koszt.backward()
            opt.step()
            sumy += koszt.item() * len(x)
            liczba += len(x)
        strata = sumy / max(liczba, 1)
    return strata


def wagi_do_tablic(model: nn.Module) -> list[np.ndarray]:
    return [p.detach().cpu().numpy() for p in model.state_dict().values()]


def tablice_do_wag(model: nn.Module, tablice: list[np.ndarray]) -> None:
    stan = model.state_dict()
    for klucz, wartosc in zip(stan.keys(), tablice):
        stan[klucz] = torch.from_numpy(np.asarray(wartosc))
    model.load_state_dict(stan, strict=True)
