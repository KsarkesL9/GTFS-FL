"""Podział operatorów ZTM na klientów federacji.

Sześciu dużych operatorów pozostaje osobno - to realne granice organizacyjne.
Trolejbusy zostają osobno mimo małego udziału, bo to jedyny trzeci środek
transportu w sieci. Czternastu przewoźników prywatnych scalono w dwa klienty
zbiorcze według dominującego miasta przystanków.

Podział geograficzny, a nie po wielkości czy opóźnieniu: grupowanie po
opóźnieniu wprowadzałoby zmienną celu do definicji klienta, sztucznie go
ujednolicając i zawyżając pozorną korzyść z personalizacji.

Konsorcja nakładają się członkami (Pawelec występuje w 9, 41, 52, 58 i 61),
więc podział "po firmie" nie jest partycją. operator_id to podmiot
kontraktujący i naturalny posiadacz danych, więc to on jest jednostką podziału.
"""

from __future__ import annotations

CLIENTS: dict[str, list[str]] = {
    "pkm_katowice": ["6"],
    "pkm_sosnowiec": ["7"],
    "tramwaje_slaskie": ["5"],
    "pkm_gliwice": ["8"],
    "pkm_swierklaniec": ["11"],
    "pkm_tychy": ["44"],
    "trolejbusy_tychy": ["45"],
    # Dominujące miasto przystanków: Gliwice, Zabrze, Bytom, Ruda Śląska,
    # Tarnowskie Góry, Chorzów.
    "prywatni_zachod": ["9", "10", "41", "52", "54", "56", "57", "58"],
    # Dominujące miasto: Katowice, Będzin, Dąbrowa Górnicza.
    "prywatni_centrum_wschod": ["4", "24", "53", "55", "61", "63"],
}

OPERATOR_TO_CLIENT: dict[str, str] = {
    operator: client
    for client, operators in CLIENTS.items()
    for operator in operators
}


def client_of(operator_id: str) -> str | None:
    """Klient federacji dla danego operatora, albo None jeśli nieznany."""
    return OPERATOR_TO_CLIENT.get(str(operator_id))
