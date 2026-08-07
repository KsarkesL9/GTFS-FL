# GTFS OLAP

Hurtownia danych i potok ETL dla strumienia GTFS-Realtime ZTM (Metropolia GZM).
Zbiera opóźnienia komunikacji miejskiej co 20 sekund, składuje w TimescaleDB
i archiwizuje surowe migawki na Google Drive.

Projekt zasila badania nad federacyjną detekcją anomalii, stąd nacisk na
ciągłość zbierania i odtwarzalność.

## Architektura

VPS pracuje jako **przekaźnik, nie magazyn**. Surowe fakty żyją lokalnie 48 godzin,
trafiają na Drive jako Parquet i dopiero wtedy są kasowane. Agregaty ciągłe
zostają w bazie przez cały projekt.

Trzy usługi w `docker compose`:

| usługa | rola |
|---|---|
| `db` | TimescaleDB, port tylko na pętli zwrotnej |
| `collector` | pętla RT co 20 s, archiwum surowych migawek |
| `maintenance` | supercronic: wysyłka co 15 min, zadanie nocne 03:30, static ETL 04:00, healthcheck co 10 min |

## Model danych

Schemat gwiazdy: hipertabela `fakt_opoznienia` oraz wymiary `dim_linia`,
`dim_przystanek`, `dim_operator`, `dim_data`, `dim_wersja_rozkladu`.
`lookup_schedule` trzyma zdenormalizowany rozkład na potrzeby dopasowania w RAM.
Agregaty ciągłe: `ca_opoznienia_15min`, `_1h`, `_dzien` oraz wariant przystankowy.
`fakt_etl_run` rejestruje każdy przebieg kolektora.

## Decyzje projektowe

**Kasowanie warunkowe od wysyłki.** Na faktach nie ma polityki retencji
TimescaleDB — kasuje `maintenance/nightly.py` przez `drop_chunks`, wyłącznie po
udanym `rclone check --checksum`. Utraconych migawek GTFS-RT nie da się odtworzyć.

**Wersjonowanie rozkładu po odcisku treści.** ZTM zmienia rozkłady bardzo często.
Nowa wersja powstaje tylko przy realnej zmianie (SHA-256 treści), dzięki czemu
`dim_wersja_rozkladu` pozostaje rejestrem faktycznych zmian, a nie uruchomień crona.

**Doba operacyjna.** Kurs o 24:30 jedzie według rozkładu poprzedniego dnia;
`offset_dnia` przenosi go do właściwej `data_kursu`.

**Cache rozkładu w pamięci.** 1,3 mln wierszy (~700 MB) w słowniku procesu.
RT publikuje `TripUpdate` bez `stop_id`, więc dopasowanie idzie po
`(trip_id, stop_sequence)`.

**Filtr sensowności opóźnień.** ZTM sporadycznie publikuje wartości rzędu
195 godzin. Agregaty odrzucają dane spoza zakresu −1 h…+2 h i zliczają je
w kolumnie `odrzucone`. Surowe fakty zostają nietknięte.

## Uruchomienie

Na VPS:

```bash
cp .env.example .env && docker compose up -d --build
```

```bash
docker compose run --rm maintenance python scripts/run_static_etl.py
```

Lokalnie — nakładka przywraca ścieżki i wolumen z bazą deweloperską:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d db
```

```bash
pip install -e . && python scripts/run_static_etl.py && python scripts/run_rt_etl.py --once
```

Konfigurację rclone utwórz przez `rclone authorize "drive"` i skopiuj do
`secrets/rclone.conf`. Świeży serwer przygotowuje `scripts/bootstrap_vps.sh`.

## Skrypty

| skrypt | do czego |
|---|---|
| `run_static_etl.py` | słowniki i `lookup_schedule` z paczek CKAN |
| `run_rt_etl.py` | pętla RT, `--once` dla pojedynczego cyklu |
| `audit_data.py` | co łapiemy, co gubimy, sensowność kolumn |
| `inventory_gaps.py` | luki w zbieraniu na podstawie `fakt_etl_run` |
| `check_dim_data.py` | diagnostyka `typ_dnia` |
| `backfill_export.py` | eksport zakresu dat na Drive |
| `build_features.py` | macierz cech i sekwencje wejściowe modelu |
| `run_federation.py` | trening federacyjny, porównanie z modelem lokalnym |
| `verify_pipeline.py` | kontrola poprawności potoku cech i agregacji |
| `bootstrap_vps.sh` | przygotowanie świeżego serwera |

## Część uczeniowa

Instalacja zależności modelu (osobno, bo kolektor ich nie potrzebuje):

```bash
pip install -e ".[uczenie]"
```

Podział na dziewięciu klientów federacji definiuje `gtfs_olap/clients.py`.
Cechy z rozdz. 8 liczy `gtfs_olap/features.py`, autoenkoder GRU jest
w `gtfs_olap/model.py`, a symulacja federacji w `gtfs_olap/federation.py`.
Agregację wykonują strategie Flower: FedAvg, FedProx, FedMedian i FedTrimmedAvg.
