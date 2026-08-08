# gtfs-olap

Kolektor i hurtownia danych dla strumienia GTFS-Realtime ZTM (Metropolia GZM).
Odpytuje feed co 20 sekund, składuje opóźnienia w TimescaleDB i archiwizuje
surowe migawki na Google Drive. Na tych danych stoi część uczeniowa —
federacyjna detekcja anomalii.

## Wymagania

- Docker z Compose v2
- rclone skonfigurowany na Google Drive (`secrets/rclone.conf`)
- Python 3.10+ do części uczeniowej

## Szybki start

```bash
sudo mkdir -p /srv/gtfs/pgdata /srv/gtfs/data && sudo chown -R 10001:10001 /srv/gtfs/data
cp .env.example .env    # uzupełnij hasło i remote rclone
docker compose up -d --build
docker compose run --rm maintenance python scripts/run_static_etl.py
```

Static ETL trzeba puścić raz ręcznie — zasila słowniki i rozkład, bez których
kolektor nie ruszy. Potem harmonogram prowadzi się sam.

Lokalnie (Windows/dev) nakładka podmienia linuksowe ścieżki na wolumen Dockera:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d db
pip install -e ".[uczenie]"
python scripts/run_rt_etl.py --once
```

## Zmienne środowiskowe

| zmienna | domyślnie | uwagi |
|---|---|---|
| `GTFS_DB_URL` | — | wymagana, bez fallbacku |
| `GTFS_RAW_DIR` | `/data/raw` | staging przed wysyłką |
| `GTFS_EXPORT_DIR` | `/data/export` | Parquety dobowe |
| `GTFS_RT_INTERVAL_S` | `20` | takt odpytywania feedu |
| `GTFS_ARCHIVE_VP` | `1` | archiwum `vehiclePositions`, bez parsowania |
| `GTFS_RCLONE_REMOTE` | `gdrive:gtfs-olap` | cel archiwizacji |
| `GTFS_FACTS_RETENTION_H` | `48` | **musi być > `start_offset` agregatów (6h)** |
| `GTFS_UPLOAD_QUIET_MIN` | `10` | ile ciszy w katalogu przed wysyłką |
| `GTFS_HEALTHCHECK_URL` | pusta | pusta = tylko logi |
| `GTFS_MIN_FREE_GB` | `15` | próg alarmu o dysku |

## Baza

Schemat gwiazdy. Hipertabela `fakt_opoznienia` (jedna obserwacja opóźnienia na
zatrzymaniu) plus wymiary `dim_linia`, `dim_przystanek`, `dim_operator`,
`dim_data`, `dim_wersja_rozkladu`. `lookup_schedule` trzyma zdenormalizowany
rozkład — kolektor ładuje go w całości do pamięci, bo feed nie podaje `stop_id`
i trzeba dopasowywać po `(trip_id, stop_sequence)`.

Agregaty ciągłe: `ca_opoznienia_15min` (źródło cech modelu), `_1h`, `_dzien`
oraz wariant przystankowy pod wizualizacje. `fakt_etl_run` rejestruje każdy
przebieg kolektora i jest jedynym źródłem do inwentaryzacji luk.

Surowe fakty żyją lokalnie 48 godzin. Kasuje je zadanie nocne — ale dopiero po
tym, jak `rclone check --checksum` potwierdzi kopię na Drive. Polityki retencji
TimescaleDB na faktach celowo nie ma, bo kasowałaby według zegara.

## Skrypty

| skrypt | do czego |
|---|---|
| `run_static_etl.py` | słowniki i `lookup_schedule` z paczek CKAN |
| `run_rt_etl.py` | pętla RT, `--once` dla jednego cyklu |
| `audit_data.py` | pokrycie strumienia i sensowność kolumn |
| `inventory_gaps.py` | luki w zbieraniu z `fakt_etl_run` |
| `check_dim_data.py` | diagnostyka `typ_dnia` |
| `backfill_export.py` | eksport zakresu dat na Drive |
| `build_features.py` | macierz cech i sekwencje wejściowe |
| `run_federation.py` | trening federacyjny vs model lokalny |
| `verify_pipeline.py` | niezmienniki potoku i agregacji |
| `bootstrap_vps.sh` | przygotowanie świeżego serwera |

## Harmonogram

Kontener `maintenance` odpala przez supercronic: wysyłkę archiwum co 15 minut,
zadanie nocne o 03:30, static ETL o 04:00 i healthcheck co 10 minut.

## Część uczeniowa

`clients.py` mapuje 21 operatorów na 9 klientów federacji, `features.py` liczy
cechy na okno 15-minutowe, `model.py` to autoenkoder GRU (~40 tys. parametrów),
`federation.py` uruchamia symulację na strategiach Flower — FedAvg, FedProx,
FedMedian, FedTrimmedAvg.
