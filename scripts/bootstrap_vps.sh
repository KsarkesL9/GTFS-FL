#!/usr/bin/env bash
# Przygotowanie świeżego VPS-a (Ubuntu/Debian) pod kolektor gtfs-olap.
#
# Uruchom JAKO ZWYKŁY UŻYTKOWNIK z sudo, nie jako root:
#     bash scripts/bootstrap_vps.sh
#
# Skrypt jest idempotentny - można go puścić ponownie po nieudanym przebiegu.
#
# CELOWO NIE WYŁĄCZA LOGOWANIA HASŁEM. To jedyny krok, który potrafi
# zamknąć Cię poza serwerem, więc wymaga ręcznego potwierdzenia, że
# logujesz się kluczem. Instrukcja wypisuje się na końcu.

set -euo pipefail

info() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ostrz() { printf '\033[1;33m[!] %s\033[0m\n' "$*"; }

if [[ $EUID -eq 0 ]]; then
    ostrz "Uruchom jako zwykły użytkownik z sudo, nie jako root."
    exit 1
fi

# ---------------------------------------------------------------- system ---
info "Aktualizacja systemu"
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y

info "Strefa czasowa Europe/Warsaw"
# Bez tego static ETL między 23:00 a północą wybierze paczki na złą dobę.
sudo timedatectl set-timezone Europe/Warsaw

info "Pakiety podstawowe"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates curl git ufw unattended-upgrades

# ------------------------------------------------------------------ swap ---
if swapon --show | grep -q '/swapfile'; then
    info "Swap już istnieje - pomijam"
else
    info "Swap 4 GB"
    # Ubezpieczenie od szczytu pamięci przy ładowaniu cache'u rozkładu.
    sudo fallocate -l 4G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    grep -q '/swapfile' /etc/fstab || \
        echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

# ---------------------------------------------------------------- docker ---
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    info "Docker z Compose v2 już zainstalowany - pomijam"
else
    info "Instalacja Dockera z oficjalnego repozytorium"
    # Repo dystrybucji ma starego Dockera bez Compose v2.
    sudo install -m 0755 -d /etc/apt/keyrings
    DYSTRYBUCJA=$(. /etc/os-release && echo "$ID")
    KODOWA=$(. /etc/os-release && echo "$VERSION_CODENAME")
    sudo curl -fsSL "https://download.docker.com/linux/${DYSTRYBUCJA}/gpg" \
        -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${DYSTRYBUCJA} ${KODOWA} stable" \
        | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    sudo apt-get update
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
        docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    sudo systemctl enable --now docker
fi

sudo usermod -aG docker "$USER"

# -------------------------------------------------------------- firewall ---
info "Firewall - tylko SSH"
sudo ufw allow OpenSSH
sudo ufw --force enable

# ------------------------------------------------------------- katalogi ----
info "Katalogi danych"
# 10001 to UID użytkownika z Dockerfile - bind-mount musi mieć zgodnego właściciela.
sudo mkdir -p /srv/gtfs/pgdata /srv/gtfs/data
sudo chown -R 10001:10001 /srv/gtfs/data

# ---------------------------------------------------------------- koniec ---
cat <<'KONIEC'

============================================================
 GOTOWE. System, swap, Docker, firewall i katalogi ustawione.
============================================================

1. WYLOGUJ SIĘ I ZALOGUJ PONOWNIE (grupa docker działa od nowej sesji):

     exit

2. Sprawdź Dockera - musi zadziałać BEZ sudo:

     docker compose version
     docker run --rm hello-world

3. DOPIERO TERAZ zabezpiecz SSH. Najpierw sprawdź, CZYM się logujesz:

     sudo grep "Accepted" /var/log/auth.log | tail -3

   Musi być "Accepted publickey". Jeśli widzisz "Accepted password" -
   NIE WYKONUJ kroku 4, bo zamkniesz się poza serwerem. Zamiast tego
   najpierw wgraj swój klucz publiczny do ~/.ssh/authorized_keys.

4. Wyłączenie logowania hasłem (plik 50-cloud-init.conf wygrywa
   pierwszeństwem nad pozostałymi - w sshd liczy się PIERWSZA wartość):

     sudo sed -i 's/^PasswordAuthentication yes/PasswordAuthentication no/' \
         /etc/ssh/sshd_config.d/50-cloud-init.conf
     sudo sshd -T | grep -i passwordauthentication
     sudo systemctl restart ssh

   Potem otwórz DRUGIE okno terminala i zaloguj się. Pierwsze zostaw
   otwarte jako drogę odwrotu, aż potwierdzisz, że działa.

5. Dalej: klonowanie repo, secrets/rclone.conf, .env  (kroki E.1-E.7)

KONIEC
