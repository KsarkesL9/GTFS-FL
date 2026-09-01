from __future__ import annotations

CLIENTS: dict[str, list[str]] = {
    "pkm_katowice": ["6"],
    "pkm_sosnowiec": ["7"],
    "tramwaje_slaskie": ["5"],
    "pkm_gliwice": ["8"],
    "pkm_swierklaniec": ["11"],
    "pkm_tychy": ["44"],
    "trolejbusy_tychy": ["45"],

    "prywatni_zachod": ["9", "10", "41", "52", "54", "56", "57", "58"],

    "prywatni_centrum_wschod": ["4", "24", "53", "55", "61", "63"],
}

OPERATOR_TO_CLIENT: dict[str, str] = {
    operator: client
    for client, operators in CLIENTS.items()
    for operator in operators
}
