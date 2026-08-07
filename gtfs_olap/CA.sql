DO $migracja$
DECLARE
    wersja_docelowa INT := 2;
    zmaterializowane BIGINT;
BEGIN
    CREATE TABLE IF NOT EXISTS _ca_wersja (wersja INT PRIMARY KEY);

    IF EXISTS (SELECT 1 FROM _ca_wersja WHERE wersja = wersja_docelowa) THEN
        RETURN;
    END IF;

    IF to_regclass('ca_opoznienia_15min') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM ca_opoznienia_15min' INTO zmaterializowane;
        RAISE WARNING 'Zmiana definicji agregatów: usuwam % zmaterializowanych '
                      'wierszy z ca_opoznienia_15min i widoków pochodnych.',
                      zmaterializowane;
    END IF;

    DROP MATERIALIZED VIEW IF EXISTS ca_opoznienia_dzien CASCADE;
    DROP MATERIALIZED VIEW IF EXISTS ca_opoznienia_1h CASCADE;
    DROP MATERIALIZED VIEW IF EXISTS ca_opoznienia_15min CASCADE;
    DROP MATERIALIZED VIEW IF EXISTS ca_opoznienia_15min_przystanek CASCADE;

    DELETE FROM _ca_wersja;
    INSERT INTO _ca_wersja VALUES (wersja_docelowa);
END
$migracja$;

CREATE MATERIALIZED VIEW IF NOT EXISTS ca_opoznienia_15min
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('15 minutes', ts, 'Europe/Warsaw') AS kwadrans,
    data_kursu,
    wersja_id,
    linia_id,
    operator_id,
    SUM(opoznienie_s) FILTER (WHERE status = 'OBSERWACJA'
                              AND opoznienie_s BETWEEN -3600 AND 7200) AS suma_opoznien,
    COUNT(*)          FILTER (WHERE status = 'OBSERWACJA'
                              AND opoznienie_s BETWEEN -3600 AND 7200) AS obserwacje,
    COUNT(*)          FILTER (WHERE status = 'OBSERWACJA'
                              AND opoznienie_s BETWEEN -30 AND 60)     AS punktualne,
    MIN(opoznienie_s) FILTER (WHERE status = 'OBSERWACJA'
                              AND opoznienie_s BETWEEN -3600 AND 7200) AS min_opoznienie,
    MAX(opoznienie_s) FILTER (WHERE status = 'OBSERWACJA'
                              AND opoznienie_s BETWEEN -3600 AND 7200) AS max_opoznienie,
    COUNT(*) FILTER (WHERE status = 'OBSERWACJA'
                     AND (opoznienie_s < -3600 OR opoznienie_s > 7200)) AS odrzucone,
    COUNT(*) FILTER (WHERE status = 'ANULOWANY')                    AS anulowane,
    COUNT(*) FILTER (WHERE status = 'POMINIETY')                    AS pominiete
FROM fakt_opoznienia
GROUP BY kwadrans, data_kursu, wersja_id, linia_id, operator_id
WITH NO DATA;

SELECT remove_continuous_aggregate_policy('ca_opoznienia_15min',
    if_not_exists => true);
SELECT add_continuous_aggregate_policy('ca_opoznienia_15min',
    start_offset      => INTERVAL '6 hours',
    end_offset        => INTERVAL '10 minutes',
    schedule_interval => INTERVAL '5 minutes');

CREATE MATERIALIZED VIEW IF NOT EXISTS ca_opoznienia_1h
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', kwadrans, 'Europe/Warsaw') AS godzina,
    data_kursu,
    wersja_id,
    linia_id,
    operator_id,
    SUM(suma_opoznien)  AS suma_opoznien,
    SUM(obserwacje)     AS obserwacje,
    SUM(punktualne)     AS punktualne,
    SUM(anulowane)      AS anulowane,
    SUM(pominiete)      AS pominiete,
    SUM(odrzucone)      AS odrzucone,
    MIN(min_opoznienie) AS min_opoznienie,
    MAX(max_opoznienie) AS max_opoznienie
FROM ca_opoznienia_15min
GROUP BY godzina, data_kursu, wersja_id, linia_id, operator_id
WITH NO DATA;
SELECT add_continuous_aggregate_policy('ca_opoznienia_1h',
    start_offset      => INTERVAL '14 days',
    end_offset        => INTERVAL '30 minutes',
    schedule_interval => INTERVAL '15 minutes',
    if_not_exists     => true);

CREATE MATERIALIZED VIEW IF NOT EXISTS ca_opoznienia_dzien
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', godzina, 'Europe/Warsaw') AS data,
    data_kursu,
    wersja_id,
    linia_id,
    operator_id,
    SUM(suma_opoznien)  AS suma_opoznien,
    SUM(obserwacje)     AS obserwacje,
    SUM(punktualne)     AS punktualne,
    SUM(anulowane)      AS anulowane,
    SUM(pominiete)      AS pominiete,
    SUM(odrzucone)      AS odrzucone,
    MIN(min_opoznienie) AS min_opoznienie,
    MAX(max_opoznienie) AS max_opoznienie
FROM ca_opoznienia_1h
GROUP BY data, data_kursu, wersja_id, linia_id, operator_id
WITH NO DATA;
SELECT add_continuous_aggregate_policy('ca_opoznienia_dzien',
    start_offset      => INTERVAL '60 days',
    end_offset        => INTERVAL '2 hours',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists     => true);

CREATE MATERIALIZED VIEW IF NOT EXISTS ca_opoznienia_15min_przystanek
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('15 minutes', ts, 'Europe/Warsaw') AS kwadrans,
    data_kursu,
    wersja_id,
    przystanek_id,
    linia_id,
    SUM(opoznienie_s) FILTER (WHERE status = 'OBSERWACJA'
                              AND opoznienie_s BETWEEN -3600 AND 7200) AS suma_opoznien,
    COUNT(*)          FILTER (WHERE status = 'OBSERWACJA'
                              AND opoznienie_s BETWEEN -3600 AND 7200) AS obserwacje,
    COUNT(*)          FILTER (WHERE status = 'OBSERWACJA'
                              AND opoznienie_s BETWEEN -30 AND 60)     AS punktualne,
    MIN(opoznienie_s) FILTER (WHERE status = 'OBSERWACJA'
                              AND opoznienie_s BETWEEN -3600 AND 7200) AS min_opoznienie,
    MAX(opoznienie_s) FILTER (WHERE status = 'OBSERWACJA'
                              AND opoznienie_s BETWEEN -3600 AND 7200) AS max_opoznienie,
    COUNT(*) FILTER (WHERE status = 'OBSERWACJA'
                     AND (opoznienie_s < -3600 OR opoznienie_s > 7200)) AS odrzucone,
    COUNT(*) FILTER (WHERE status = 'ANULOWANY')                    AS anulowane,
    COUNT(*) FILTER (WHERE status = 'POMINIETY')                    AS pominiete
FROM fakt_opoznienia
GROUP BY kwadrans, data_kursu, wersja_id, przystanek_id, linia_id
WITH NO DATA;
SELECT remove_continuous_aggregate_policy('ca_opoznienia_15min_przystanek',
    if_not_exists => true);
SELECT add_continuous_aggregate_policy('ca_opoznienia_15min_przystanek',
    start_offset      => INTERVAL '6 hours',
    end_offset        => INTERVAL '10 minutes',
    schedule_interval => INTERVAL '5 minutes');

SELECT remove_retention_policy('fakt_opoznienia', if_exists => true);
SELECT remove_retention_policy('fakt_etl_run', if_exists => true);

SELECT remove_retention_policy('ca_opoznienia_15min_przystanek', if_exists => true);
SELECT add_retention_policy('ca_opoznienia_15min_przystanek', INTERVAL '14 days');
