"""Mapowanie operatorów ZTM na klientów federacji.

Sześciu dużych operatorów osobno, trolejbusy osobno (jedyny trzeci środek
transportu), czternastu przewoźników prywatnych scalonych w dwa klienty wg
dominującego miasta przystanków.

Grupowanie po geografii, nie po opóźnieniu - to ostatnie wprowadzałoby zmienną
celu do definicji klienta i zawyżało pozorną korzyść z personalizacji.

UWAGA na ID operatorów: konsorcja nakładają się członkami (Pawelec siedzi
w 9, 41, 52, 58 i 61), więc podziału "po firmie" nie da się zrobić - to nie
jest partycja. Jednostką jest operator_id, czyli podmiot kontraktujący.
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
    # Gliwice, Zabrze, Bytom, Ruda Śląska, Tarnowskie Góry, Chorzów
    "prywatni_zachod": ["9", "10", "41", "52", "54", "56", "57", "58"],
    # Katowice, Będzin, Dąbrowa Górnicza
    "prywatni_centrum_wschod": ["4", "24", "53", "55", "61", "63"],
}

OPERATOR_TO_CLIENT: dict[str, str] = {
    operator: client
    for client, operators in CLIENTS.items()
    for operator in operators
}
