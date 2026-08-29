# GTFS-FL: Federacyjna detekcja anomalii w transporcie publicznym (ZTM GZM)

System zbierania danych ze strumienia GTFS-Realtime (Metropolia GZM), przetwarzania ich w architekturze hurtowni danych (TimescaleDB / PostgreSQL) oraz federacyjnego uczenia maszynowego (Flower) do detekcji anomalii w ruchu komunikacji miejskiej.

---

## Główne moduły projektu

1. **ETL i archiwizacja**: Pobieranie danych GTFS-RT co 20 sekund, ciągła agregacja w TimescaleDB oraz archiwizacja surowych migawek.
2. **Potok inżynierii cech**: Agregacja w oknach 15-minutowych dla 9 węzłów federacji (21 operatorów transportowych).
3. **Uczenie federacyjne (FL)**: Symulacja federacyjna z autoenkoderem GRU oparta na bibliotece Flower (algorytmy: FedAvg, FedProx, FedMedian, FedTrimmedAvg).
4. **Ewaluacja anomalii**: Generator syntetycznych incydentów ruchowych (A1–D2) oraz badania adaptacji do dryfu (E1–E5).

---

## Wymagania

- Docker i Docker Compose v2
- Python 3.10+ (do uruchamiania eksperymentów FL)
- `rclone` (opcjonalnie, do automatycznej synchronizacji z dyskiem chmurowym)

---

## Szybki start

### 1. Uruchomienie bazy danych i kolektora ETL

```bash
# Przygotowanie katalogów i uprawnień
sudo mkdir -p /srv/gtfs/pgdata /srv/gtfs/data && sudo chown -R 10001:10001 /srv/gtfs/data

# Konfiguracja środowiska
cp .env.example .env

# Start usług w Dockerze
docker compose up -d --build

# Inicjalizacja słowników i rozkładów jazdy (Static ETL)
docker compose run --rm maintenance python scripts/run_static_etl.py
```

### 2. Środowisko lokalne (Dev / Windows)

Do pracy developerskiej przygotowano konfigurację z wolumenem lokalnym:

```bash
# Uruchomienie samej bazy danych
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d db

# Instalacja zależności w środowisku Python
pip install -e ".[uczenie]"

# Jednorazowy testowy przebieg pobierania danych RT
python scripts/run_rt_etl.py --once
```

---

## Architektura bazy danych

Projekt wykorzystuje schemat gwiazdy zoptymalizowany pod kątem szeregów czasowych:
- **Hipertabela faktów**: `fakt_opoznienia` (pojedyncze obserwacje odchyleń od rozkładu na zatrzymaniach).
- **Tabele wymiarów**: `dim_linia`, `dim_przystanek`, `dim_operator`, `dim_data`, `dim_wersja_rozkladu`.
- **Szybki bufor**: `lookup_schedule` (zdenormalizowany rozkład ładowany do pamięci podręcznej kolektora).
- **Agregaty ciągłe (TimescaleDB)**: `ca_opoznienia_15min` (podstawa cech modeli FL), `ca_opoznienia_1h`, `ca_opoznienia_dzien`.
- **Monitoring działania**: `fakt_etl_run` (rejestr każdego cyklu pobierania, używany do wykrywania przerw w danych).

---

## Przegląd skryptów

| Skrypt | Zastosowanie |
|---|---|
| `scripts/run_static_etl.py` | Pobieranie i ładowanie rozkładów statycznych z CKAN |
| `scripts/run_rt_etl.py` | Główna pętla zbierania danych czasu rzeczywistego (GTFS-RT) |
| `scripts/build_features.py` | Budowanie macierzy 11 cech i sekwencji czasowych |
| `scripts/run_federation.py` | Trening modeli federacyjnych vs modele lokalne |
| `scripts/inject_events.py` | Wstrzykiwanie syntetycznych anomalii opóźnień (A1–D2) |
| `scripts/run_e1.py` – `run_e5.py` | Wykonywanie eksperymentów badawczych E1–E5 |
| `scripts/aggregate_seeds.py` | Zbiorcze podsumowanie wyników z wielu ziaren losowości |
| `scripts/make_figures.py` | Generowanie wykresów i wizualizacji wyników |
| `scripts/describe_dataset.py` | Generowanie pełnego raportu statystycznego o zbiorze danych |
| `scripts/audit_data.py` | Audyt spójności i jakości zebranych danych |
| `scripts/verify_pipeline.py` | Testy poprawności i niezmienników potoku danych |

---

## Konfiguracja zmiennych środowiskowych

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `GTFS_DB_URL` | — | Connection string do bazy PostgreSQL / TimescaleDB (wymagany) |
| `GTFS_RAW_DIR` | `/data/raw` | Katalog bufora surowych migawek przed wysyłką |
| `GTFS_EXPORT_DIR` | `/data/export` | Katalog dobowych eksportów Parquet |
| `GTFS_RT_INTERVAL_S` | `20` | Interwał odpytywania feedu GTFS-RT (w sekundach) |
| `GTFS_ARCHIVE_VP` | `1` | Flaga archiwizacji surowych pakietów `VehiclePositions` |
| `GTFS_RCLONE_REMOTE` | `gdrive:gtfs-olap` | Nazwa zdalnego zasobu rclone do kopii zapasowych |
| `GTFS_FACTS_RETENTION_H` | `48` | Czas przechowywania surowych faktów w bazie (godziny) |
| `GTFS_UPLOAD_QUIET_MIN` | `10` | Czas bezczynności przed wysłaniem plików do archiwum |
| `GTFS_MIN_FREE_GB` | `15` | Próg wolnego miejsca na dysku uruchamiający ostrzeżenie |

---

## Dokumentacja zbioru danych

Szczegółowy opis statystyczny zbioru, definicje 11 cech oraz charakterystykę podziału na węzły federacji zawiera plik [docs/zbior-danych.md](docs/zbior-danych.md).
