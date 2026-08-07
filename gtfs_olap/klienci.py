"""Podział operatorów ZTM na klientów federacji.

Dziewięć klientów. Sześciu dużych operatorów pozostaje osobno, bo to realne
granice organizacyjne. Trolejbusy zostają osobno mimo małego udziału - to
jedyny trzeci środek transportu w sieci. Kilkunastu przewoźników prywatnych
scalono w dwa klienty zbiorcze wg dominującego miasta przystanków.

Dlaczego wg geografii, a nie wielkości czy opóźnień: grupowanie po opóźnieniu
wprowadzałoby zmienną celu do definicji klienta, sztucznie ujednolicając go
i zawyżając pozorną korzyść z personalizacji. Geografia jest neutralna wobec
celu i odwzorowuje faktyczny obszar operacyjny.

Konsorcja nakładają się członkami (Pawelec występuje w 9, 41, 52, 58 i 61),
więc podział "po firmie" nie jest partycją. operator_id to podmiot
kontraktujący i naturalny posiadacz danych, więc to on jest jednostką podziału.
"""

from __future__ import annotations

KLIENCI: dict[str, list[str]] = {
    "pkm_katowice":     ["6"],
    "pkm_sosnowiec":    ["7"],
    "tramwaje_slaskie": ["5"],
    "pkm_gliwice":      ["8"],
    "pkm_swierklaniec": ["11"],
    "pkm_tychy":        ["44"],
    "trolejbusy_tychy": ["45"],
    # Dominujące miasto przystanków: Gliwice, Zabrze, Bytom, Ruda Śląska,
    # Tarnowskie Góry, Chorzów.
    "prywatni_zachod":  ["9", "10", "41", "52", "54", "56", "57", "58"],
    # Dominujące miasto: Katowice, Będzin, Dąbrowa Górnicza.
    "prywatni_centrum_wschod": ["4", "24", "53", "55", "61", "63"],
}

OPERATOR_NA_KLIENTA: dict[str, str] = {
    op: klient for klient, operatorzy in KLIENCI.items() for op in operatorzy
}


def klient(operator_id: str) -> str | None:
    """Klient federacji dla danego operatora, albo None jeśli nieznany."""
    return OPERATOR_NA_KLIENTA.get(str(operator_id))
