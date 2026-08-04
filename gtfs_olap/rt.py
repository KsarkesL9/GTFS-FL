"""RT ETL: pobiera GTFS-RT TripUpdates i ładuje opóźnienia do hypertable."""

from __future__ import annotations

import gzip
import io
import signal
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import httpx
import psycopg
from google.transit import gtfs_realtime_pb2
from loguru import logger

from gtfs_olap.config import (
    ARCHIVE_VP, DB_URL, DDL, RAW_DIR, RT_INTERVAL_S, RT_TIMEOUT_S, RT_URL, TZ,
    VP_TIMEOUT_S, VP_URL,
)

TRIP_CANCELED = gtfs_realtime_pb2.TripDescriptor.CANCELED
STOP_SKIPPED = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.SKIPPED


_stop = False


def _handle_signal(signum, _frame):
    # SIGTERM nie wykonuje bloku finally - flaga pozwala domknąć iterację.
    global _stop
    _stop = True
    logger.warning(f"Sygnał {signal.Signals(signum).name} - kończę po iteracji")


def _sleep_until(deadline: float) -> None:
    # Sen w kawałkach: PEP 475 wznawia time.sleep po sygnale, więc sleep(20)
    # opóźniałby zamknięcie ponad limit `docker stop`.
    while not _stop:
        left = deadline - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(left, 0.5))


def _archive_raw(raw: bytes, header_ts: int, kind: str) -> bool:
    """Zapis surowej odpowiedzi (D2), partycjonowany dt=/hh= pod pyarrow.

    Nigdy nie propaguje wyjątku: awaria archiwum nie może zatrzymać zapisu
    faktów do bazy."""
    try:
        dt = datetime.fromtimestamp(header_ts, tz=timezone.utc).astimezone(TZ)
        d = RAW_DIR / kind / f"dt={dt:%Y-%m-%d}" / f"hh={dt:%H}"
        final = d / f"{kind}_{header_ts}.pb.gz"
        if final.exists():
            return True
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / (final.name + ".tmp")
        with gzip.open(tmp, "wb", compresslevel=6) as f:
            f.write(raw)
        tmp.replace(final)   # atomowo - uploader nie zobaczy pliku w połowie
        return True
    except Exception as e:
        logger.error(f"Archiwum surowe {kind} nieudane: {e}")
        return False


def _archive_vehicle_positions(client: httpx.Client) -> None:
    # Tylko archiwum, bez parsowania do bazy (D1). Wyjątek połknięty: awaria
    # strumienia pobocznego nie może kosztować migawki tripUpdates.
    try:
        raw = client.get(VP_URL, timeout=VP_TIMEOUT_S).content
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(raw)
        _archive_raw(raw, feed.header.timestamp, "vehiclePositions")
    except Exception as e:
        logger.warning(f"vehiclePositions pominięte: {e}")


# Cały lookup_schedule (1,3 mln wierszy, ~700 MB) trzymamy w pamięci procesu:
# lookup per wiersz przez SELECT to byłoby 1000+ roundtripów na migawkę.
# RT GZM publikuje TripUpdate BEZ stop_id, więc cache indeksujemy po
# (trip_id, stop_sequence), a stop_id bierzemy z niego.
def _is_alive(conn) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return True
    except Exception:
        return False


def _connect_with_retry(max_backoff_s: int = 60):
    """Połączenie z DB z exponential backoff. Próbuje w nieskończoność.

    Backoff: 5s, 10s, 20s, 40s, 60s, 60s, 60s... do skutku.
    Każdą próbę logujemy na WARN, sukces na INFO."""
    backoff = 5
    attempt = 0
    while True:
        attempt += 1
        try:
            conn = psycopg.connect(DB_URL, connect_timeout=10)
            if attempt > 1:
                logger.info(f"DB połączone po {attempt} próbach")
            return conn
        except Exception as e:
            logger.warning(f"DB connect próba {attempt} nieudana: {e}. "
                           f"Czekam {backoff}s...")
            time.sleep(backoff)
            backoff = min(backoff * 2, max_backoff_s)

@dataclass(slots=True)
class ScheduleEntry:
    # slots=True: bez tego każdy z 1,4 mln obiektów niósłby własny __dict__.
    # README mówił o ~200 MB cache'u - realnie było bliżej 1 GB.
    stop_id: str
    sched_arrival: str | None
    linia_id: str | None
    operator_id: str | None
    kierunek: str | None
    offset_dnia: int


class ScheduleCache:
    def __init__(self):
        self._cache: dict[tuple[str, int], ScheduleEntry] = {}
        self._by_trip: dict[str, list[tuple[int, ScheduleEntry]]] = {}
        self.wersja_id: int | None = None

    def __len__(self):
        return len(self._cache)

    def get(self, trip_id: str, seq: int) -> ScheduleEntry | None:
        return self._cache.get((trip_id, seq))

    def get_all_stops(self, trip_id: str) -> list[tuple[int, ScheduleEntry]]:
        """Wszystkie zatrzymania kursu - używane przy obsłudze anulacji."""
        return self._by_trip.get(trip_id, [])
    
    def current_version_in_db(self, conn) -> int | None:
        """Najnowsza wersja rozkładu w bazie, albo None przy błędzie."""
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT wersja_id FROM dim_wersja_rozkladu "
                    "ORDER BY zaladowano DESC LIMIT 1"
                )
                row = cur.fetchone()
                return row[0] if row else None
        except Exception:
            return None

    def load(self):
        logger.info("Ładuję schedule cache...")
        t0 = time.monotonic()
        with psycopg.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT wersja_id, obowiazuje_od, obowiazuje_do FROM dim_wersja_rozkladu "
                    "ORDER BY zaladowano DESC LIMIT 1"
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError(
                        "Brak wersji rozkładu w dim_wersja_rozkladu. "
                        "Uruchom najpierw run_static_etl.py."
                    )
                self.wersja_id, od, do = row
                logger.info(f"Aktywna wersja rozkładu: {self.wersja_id} ({od} → {do})")

            # Zwolnienie starego cache'u przed czytaniem nowego - inaczej przy
            # reloadzie trzymalibyśmy dwie kopie naraz.
            self._cache = {}
            self._by_trip = {}

            # Kursor serwerowy: domyślny buforuje cały wynik po stronie
            # klienta, co daje zbędny szczyt ~1 GB przy 3,7 GB RAM na VPS.
            with conn.cursor(name="lookup_load") as cur:
                cur.itersize = 50_000
                cur.execute("""
                    SELECT trip_id, przystanek_id, stop_sequence, rozkladowy_przyjazd,
                           linia_id, operator_id, kierunek, offset_dnia
                    FROM lookup_schedule WHERE wersja_id = %s
                """, (self.wersja_id,))
                for trip_id, stop_id, seq, arr, lin, op, kier, off in cur:
                    entry = ScheduleEntry(stop_id, arr, lin, op, kier, off)
                    self._cache[(trip_id, seq)] = entry
                    self._by_trip.setdefault(trip_id, []).append((seq, entry))
        logger.info(f"Cache: {len(self._cache):,} wpisów, "
                    f"{len(self._by_trip):,} kursów ({time.monotonic() - t0:.1f}s)")


# TripUpdate z GZM są minimalne: bez start_date (zakładamy dziś w Europe/Warsaw),
# bez stop_id, bez arrival.time - tylko arrival.delay.

def _process_feed(feed, cache: ScheduleCache) -> list[tuple]:
    """FeedMessage -> krotki do COPY. Trzy ścieżki: kurs CANCELED (ANULOWANY),
    przystanek SKIPPED (POMINIETY), zwykła obserwacja (OBSERWACJA)."""
    snapshot_dt = datetime.fromtimestamp(feed.header.timestamp, tz=timezone.utc)
    snapshot_local_date = snapshot_dt.astimezone(TZ).date()
    wersja_id = cache.wersja_id
    rows = []

    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        trip_id = tu.trip.trip_id
        if not trip_id:
            continue

        # 1. Anulowanie całego kursu - generujemy wiersze dla każdego planowanego przystanku
        if tu.trip.schedule_relationship == TRIP_CANCELED:
            for seq, entry in cache.get_all_stops(trip_id):
                data_kursu = snapshot_local_date - timedelta(days=entry.offset_dnia)
                rows.append((
                    snapshot_dt, wersja_id, trip_id, entry.stop_id, seq,
                    entry.linia_id, entry.operator_id, entry.kierunek,
                    data_kursu, None, "ANULOWANY",
                ))
            continue

        # 2. Normalne przetwarzanie zatrzymań
        for stu in tu.stop_time_update:
            if not stu.HasField("stop_sequence"):
                continue
            entry = cache.get(trip_id, stu.stop_sequence)
            if entry is None:
                continue

            data_kursu = snapshot_local_date - timedelta(days=entry.offset_dnia)

            # 2a. Pojedynczy przystanek pominięty
            if stu.schedule_relationship == STOP_SKIPPED:
                rows.append((
                    snapshot_dt, wersja_id, trip_id, entry.stop_id, stu.stop_sequence,
                    entry.linia_id, entry.operator_id, entry.kierunek,
                    data_kursu, None, "POMINIETY",
                ))
                continue

            # 2b. Normalna obserwacja - musi mieć arrival.delay
            if not stu.HasField("arrival") or not stu.arrival.HasField("delay"):
                continue

            rows.append((
                snapshot_dt, wersja_id, trip_id, entry.stop_id, stu.stop_sequence,
                entry.linia_id, entry.operator_id, entry.kierunek,
                data_kursu, stu.arrival.delay, "OBSERWACJA",
            ))
    return rows

def _insert_rows(conn, rows: list[tuple]):
    """Bulk insert z deduplikacją: COPY do TEMP -> INSERT ON CONFLICT DO NOTHING."""
    if not rows:
        return 0
    cols = ("ts", "wersja_id", "trip_id", "przystanek_id", "stop_sequence", "linia_id",
            "operator_id", "kierunek",
            "data_kursu", "opoznienie_s", "status")

    buf = io.StringIO()
    for row in rows:
        csv_row = []
        for v in row:
            if v is None:
                csv_row.append("")
            elif isinstance(v, (datetime, date)):
                csv_row.append(v.isoformat())
            else:
                s = str(v)
                if "," in s or '"' in s:
                    s = '"' + s.replace('"', '""') + '"'
                csv_row.append(s)
        buf.write(",".join(csv_row) + "\n")
    buf.seek(0)

    cols_sql = ",".join(cols)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TEMP TABLE IF NOT EXISTS _stg "
            "(LIKE fakt_opoznienia INCLUDING DEFAULTS) ON COMMIT DELETE ROWS"
        )
        with cur.copy(f"COPY _stg ({cols_sql}) FROM STDIN WITH (FORMAT CSV, NULL '')") as cp:
            while chunk := buf.read(64 * 1024):
                cp.write(chunk)
        cur.execute(
            f"INSERT INTO fakt_opoznienia ({cols_sql}) "
            f"SELECT {cols_sql} FROM _stg ON CONFLICT DO NOTHING"
        )
        n = cur.rowcount
    conn.commit()
    return n

def _log_etl_run(conn, started_at, snapshot_ts, obserwacje,
                 wstawione, czas_s, status, blad):
    try:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO fakt_etl_run (started_at, snapshot_ts, "
                "obserwacje, wstawione, czas_s, status, blad) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (started_at, snapshot_ts, obserwacje, wstawione,
                 czas_s, status, blad)
            )
        conn.commit()
    except Exception as e:
        logger.error(f"Nie udało się zapisać audit log: {e}")

def run_loop(interval_s: int = RT_INTERVAL_S, once: bool = False):
    """Główna pętla pollingu. Kończy na Ctrl+C lub SIGTERM.

    Reconnect z backoff, auto-reload cache'u po zmianie wersji rozkładu,
    żaden wyjątek iteracji nie zabija pętli."""

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    init_conn = _connect_with_retry()
    try:
        with init_conn.cursor() as cur:
            cur.execute(DDL)
        init_conn.commit()
    finally:
        init_conn.close()

    cache = ScheduleCache()
    conn = _connect_with_retry()
    cache.load()

    # Jeden klient na proces - inaczej 4320 handshake'ów TLS na dobę.
    client = httpx.Client(
        timeout=RT_TIMEOUT_S,
        limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=120.0),
        headers={"User-Agent": "gtfs-olap/0.2 (badania akademickie)"},
    )

    last_snapshot_ts = 0
    next_tick = time.monotonic()
    try:
        while not _stop:
            started_at = datetime.now(tz=timezone.utc)
            t_start = time.monotonic()
            snapshot_ts = None
            obserwacje = 0
            wstawione = 0
            status = "OK"
            blad = None

            if not _is_alive(conn):
                logger.warning("Connection martwy, reconnect...")
                try:
                    conn.close()
                except Exception:
                    pass
                conn = _connect_with_retry()
                cache.load()  

            db_version = cache.current_version_in_db(conn)
            if db_version is not None and db_version != cache.wersja_id:
                logger.info(f"Wykryto nową wersję rozkładu w DB: "
                            f"{cache.wersja_id} → {db_version}. Reload cache.")
                cache.load()

            try:
                raw = client.get(RT_URL).content
                feed = gtfs_realtime_pb2.FeedMessage()
                feed.ParseFromString(raw)
                snapshot_ts = datetime.fromtimestamp(
                    feed.header.timestamp, tz=timezone.utc
                )

                # Przed przetwarzaniem - archiwum ma przetrwać także błąd
                # w _process_feed. Duplikat odcina samo _archive_raw.
                _archive_raw(raw, feed.header.timestamp, "tripUpdates")

                if feed.header.timestamp <= last_snapshot_ts:
                    logger.debug("Snapshot już przetworzony, pomijam")
                    status = "SKIPPED"
                else:
                    rows = _process_feed(feed, cache)
                    obserwacje = len(rows)
                    wstawione = _insert_rows(conn, rows) if rows else 0
                    last_snapshot_ts = feed.header.timestamp

                    snap = snapshot_ts.strftime("%H:%M:%S")
                    logger.info(
                        f"snap={snap} obs={obserwacje:,} ins={wstawione:,} "
                        f"t={time.monotonic() - t_start:.2f}s"
                    )
            except Exception as e:
                logger.error(f"Iteracja nieudana: {e}")
                status = "ERROR"
                blad = str(e)[:500]

            # Przed archiwum pozycji - fakt_etl_run mierzy potok ETL.
            czas_s = round(time.monotonic() - t_start, 3)

            try:
                _log_etl_run(conn, started_at, snapshot_ts, obserwacje,
                             wstawione, czas_s, status, blad)
            except Exception as e:
                logger.error(f"Audit log w sekcji nieudany: {e}")

            if ARCHIVE_VP:
                _archive_vehicle_positions(client)

            if once or _stop:
                break

            # Takt oparty na deadline, nie sleep() po iteracji: nierówne
            # próbkowanie trafiałoby wprost w cechę n(t) i produkowało
            # fałszywe anomalie A2 z przyczyn infrastrukturalnych.
            next_tick += interval_s
            drift = time.monotonic() - next_tick
            if drift > 0:
                # Po długiej iteracji resync zamiast serii nadrabiających.
                if drift > interval_s:
                    logger.warning(f"Takt opóźniony o {drift:.1f}s - resync")
                next_tick = time.monotonic()
            else:
                _sleep_until(next_tick)
    finally:
        client.close()
        try:
            conn.close()
        except Exception:
            pass
        logger.info("Pętla RT zamknięta")