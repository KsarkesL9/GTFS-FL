import os
from pathlib import Path
from zoneinfo import ZoneInfo

def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(
            f"Brak wymaganej zmiennej środowiskowej {name}. "
            f"Skopiuj .env.example do .env i uzupełnij."
        )
    return val

DB_URL = _require("GTFS_DB_URL")

CKAN_API = os.getenv(
    "GTFS_CKAN_API",
    "https://otwartedane.metropoliagzm.pl/api/3/action/package_show"
    "?id=rozklady-jazdy-i-lokalizacja-przystankow-gtfs-wersja-rozszerzona",
)
RT_URL = os.getenv(
    "GTFS_RT_URL",
    "https://gtfsrt.transportgzm.pl:5443/gtfsrt/gzm/tripUpdates",
)

VP_URL = os.getenv(
    "GTFS_VP_URL",
    "https://gtfsrt.transportgzm.pl:5443/gtfsrt/gzm/vehiclePositions",
)
ARCHIVE_VP = os.getenv("GTFS_ARCHIVE_VP", "1") == "1"

RT_INTERVAL_S = int(os.getenv("GTFS_RT_INTERVAL_S", "20"))
RT_TIMEOUT_S = float(os.getenv("GTFS_RT_TIMEOUT_S", "10"))
VP_TIMEOUT_S = float(os.getenv("GTFS_VP_TIMEOUT_S", "5"))

RAW_DIR = Path(os.getenv("GTFS_RAW_DIR", "/data/raw"))
EXPORT_DIR = Path(os.getenv("GTFS_EXPORT_DIR", "/data/export"))
STATE_DIR = Path(os.getenv("GTFS_STATE_DIR", "/data/state"))

RCLONE_BIN = os.getenv("GTFS_RCLONE_BIN", "rclone")
RCLONE_REMOTE = os.getenv("GTFS_RCLONE_REMOTE", "gdrive:gtfs-olap")

UPLOAD_QUIET_MIN = int(os.getenv("GTFS_UPLOAD_QUIET_MIN", "10"))

FACTS_RETENTION_H = int(os.getenv("GTFS_FACTS_RETENTION_H", "48"))

HEALTHCHECK_URL = os.getenv("GTFS_HEALTHCHECK_URL", "")
MIN_FREE_GB = float(os.getenv("GTFS_MIN_FREE_GB", "15"))
MAX_ETL_SILENCE_MIN = float(os.getenv("GTFS_MAX_ETL_SILENCE_MIN", "5"))
MAX_STAGING_H = float(os.getenv("GTFS_MAX_STAGING_H", "3"))
MAX_NIGHTLY_H = float(os.getenv("GTFS_MAX_NIGHTLY_H", "26"))

MAX_EMPTY_FEED_CYCLES = int(os.getenv("GTFS_MAX_EMPTY_FEED_CYCLES", "15"))

TZ = ZoneInfo("Europe/Warsaw")

TRANSPORT_TYPES = {"0": "tramwaj", "3": "autobus", "11": "trolejbus"}

DAYS_PL = ["poniedziałek", "wtorek", "środa", "czwartek",
           "piątek", "sobota", "niedziela"]
WEEKDAY_COLS = ["monday", "tuesday", "wednesday", "thursday",
                "friday", "saturday", "sunday"]

DIM_DATA_LOOKBACK_DAYS = 35

DEDUP_KEYS = {
    "agency": ["agency_id"],
    "routes": ["route_id"],
    "routes_ext": ["route_id"],
    "stops": ["stop_id"],
    "stops_ext": ["stop_id"],
    "stops_attributes_ext": ["stop_type_id"],
    "communities_ext": ["community_id"],
    "trips": ["trip_id"],
    "trips_ext": ["trip_id"],
    "stop_times": ["trip_id", "stop_id", "stop_sequence"],
    "calendar": ["service_id", "start_date", "end_date",
                 "monday", "tuesday", "wednesday", "thursday",
                 "friday", "saturday", "sunday"],
    "calendar_dates": ["service_id", "date", "exception_type"],
    "service_ext": ["service_id", "name"],
    "operators_ext": ["operator_id"],
}

DDL = """
CREATE TABLE IF NOT EXISTS dim_linia (
    linia_id            TEXT PRIMARY KEY,
    nazwa_krotka        TEXT,
    nazwa_dluga         TEXT,
    srodek_transportu   TEXT,
    typ_linii           TEXT
);
CREATE TABLE IF NOT EXISTS dim_przystanek (
    przystanek_id       TEXT PRIMARY KEY,
    nazwa               TEXT,
    szer_geo            DOUBLE PRECISION,
    dl_geo              DOUBLE PRECISION,
    gmina               TEXT,
    miasto              TEXT,
    typ_przystanku      TEXT
);
CREATE TABLE IF NOT EXISTS dim_operator (
    operator_id         TEXT PRIMARY KEY,
    nazwa               TEXT
);
CREATE TABLE IF NOT EXISTS dim_data (
    data                DATE PRIMARY KEY,
    rok                 SMALLINT,
    miesiac             SMALLINT,
    tydzien_iso         SMALLINT,
    dzien_tygodnia      SMALLINT,
    nazwa_dnia          TEXT,
    typ_dnia            TEXT
);
CREATE TABLE IF NOT EXISTS dim_wersja_rozkladu (
    wersja_id      SERIAL PRIMARY KEY,
    nazwa_paczki   TEXT,
    obowiazuje_od  DATE,
    obowiazuje_do  DATE,
    odcisk         TEXT,
    zaladowano     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- Dla baz założonych przed wprowadzeniem odcisku (CREATE IF NOT EXISTS
-- nie dodaje kolumn do istniejącej tabeli).
ALTER TABLE dim_wersja_rozkladu ADD COLUMN IF NOT EXISTS odcisk TEXT;
CREATE INDEX IF NOT EXISTS idx_wersja_obowiazuje
    ON dim_wersja_rozkladu (obowiazuje_od, obowiazuje_do);

-- Lookup dla RT - NIE wymiar OLAP. Wersjonowany append-only.
CREATE TABLE IF NOT EXISTS lookup_schedule (
    wersja_id            INT NOT NULL REFERENCES dim_wersja_rozkladu(wersja_id),
    trip_id              TEXT NOT NULL,
    przystanek_id        TEXT NOT NULL,
    stop_sequence        INT  NOT NULL,
    rozkladowy_przyjazd  TEXT,
    linia_id             TEXT,
    kierunek             TEXT,
    kierunek_opis        TEXT,
    operator_id          TEXT,
    offset_dnia          SMALLINT NOT NULL DEFAULT 0,
    PRIMARY KEY (wersja_id, trip_id, przystanek_id, stop_sequence)
);
CREATE INDEX IF NOT EXISTS idx_lookup_trip
    ON lookup_schedule (wersja_id, trip_id);

CREATE TABLE IF NOT EXISTS fakt_opoznienia (
    ts                   TIMESTAMPTZ NOT NULL,
    wersja_id            INT  REFERENCES dim_wersja_rozkladu(wersja_id),
    trip_id              TEXT NOT NULL,
    przystanek_id        TEXT NOT NULL REFERENCES dim_przystanek(przystanek_id),
    stop_sequence        INT  NOT NULL,
    linia_id             TEXT REFERENCES dim_linia(linia_id),
    operator_id          TEXT REFERENCES dim_operator(operator_id),
    kierunek             TEXT,
    data_kursu           DATE REFERENCES dim_data(data),
    opoznienie_s         INT,
    status               TEXT NOT NULL DEFAULT 'OBSERWACJA',
    PRIMARY KEY (trip_id, przystanek_id, stop_sequence, ts)
);
SELECT create_hypertable('fakt_opoznienia', 'ts',
    chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_fakt_linia_ts
    ON fakt_opoznienia (linia_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_fakt_operator_ts
    ON fakt_opoznienia (operator_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_fakt_wersja
    ON fakt_opoznienia (wersja_id);

CREATE TABLE IF NOT EXISTS fakt_etl_run (
    started_at        TIMESTAMPTZ NOT NULL,
    snapshot_ts       TIMESTAMPTZ,
    obserwacje        INT,
    wstawione         INT,
    czas_s            NUMERIC(6, 3),
    status            TEXT NOT NULL,
    blad              TEXT,
    PRIMARY KEY (started_at)
);
SELECT create_hypertable('fakt_etl_run', 'started_at',
    chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_etl_status_started
    ON fakt_etl_run (status, started_at DESC);
"""

CA_DDL = (Path(__file__).parent / "CA.sql").read_text(encoding="utf-8")
