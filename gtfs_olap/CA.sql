-- ============================================================================
-- Migracja definicji agregatów
-- ============================================================================
--
-- CREATE MATERIALIZED VIEW IF NOT EXISTS po cichu POMIJA zmienioną definicję,
-- jeśli widok już istnieje. Bez tego bloku każda zmiana poniższych zapytań
-- działałaby tylko na świeżej bazie, a na działającej maszynie byłaby
-- niewidoczna - i nikt by się nie zorientował.
--
-- !!! UWAGA. Podniesienie WERSJI_AGREGATOW KASUJE zmaterializowaną historię
-- wszystkich agregatów. Przy retencji surowych faktów 48 h odtworzenie
-- czegokolwiek starszego wymaga przeliczenia Parquetów z Google Drive poza
-- bazą. Podnoś świadomie i najlepiej wcześnie w cyklu życia projektu.
DO $migracja$
DECLARE
    wersja_docelowa INT := 2;   -- 2: filtr sensowności opóźnień (-1h/+2h)
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


-- ============================================================================
-- Agregat bazowy: okno 15 minut na linię i operatora
-- ============================================================================
--
-- FILTR SENSOWNOŚCI: opoznienie_s BETWEEN -3600 AND 7200.
-- ZTM publikuje sporadycznie wartości rzędu 195-196 godzin (zweryfikowane
-- skryptem audit_data.py: 3 różne kursy, wszystkie skupione wokół 8,15 doby -
-- to artefakt źródła, nie błąd potoku). Jeden taki wiersz w oknie z ~1000
-- obserwacji przesuwa średnie opóźnienie o ~700 s, podczas gdy amplitudy
-- anomalii A1 z rozdz. 10 to 60-300 s. Nieodfiltrowany artefakt wyglądałby
-- więc jak anomalia kilkukrotnie silniejsza od tych, które model ma wykrywać,
-- i zaburzałby kalibrację progu percentylowego z rozdz. 8.1.
--
-- Filtr obejmuje też licznik obserwacji, żeby suma i mianownik liczyły się na
-- tym samym zbiorze - inaczej d(t) = suma/obserwacje byłoby niespójne.
-- Kolumna odrzucone czyni filtrowanie jawnym i policzalnym w raporcie.
-- Surowe fakty i archiwum na Drive pozostają nietknięte, więc próg da się
-- zrewidować bez utraty danych.
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
    -- Ekstrema
    MIN(opoznienie_s) FILTER (WHERE status = 'OBSERWACJA'
                              AND opoznienie_s BETWEEN -3600 AND 7200) AS min_opoznienie,
    MAX(opoznienie_s) FILTER (WHERE status = 'OBSERWACJA'
                              AND opoznienie_s BETWEEN -3600 AND 7200) AS max_opoznienie,
    -- Jawny ślad filtrowania: ile obserwacji wypadło poza próg sensowności.
    -- Bez tej kolumny odrzucanie byłoby niewidoczne, a w pracy badawczej
    -- każde odrzucenie danych musi dać się policzyć i opisać.
    COUNT(*) FILTER (WHERE status = 'OBSERWACJA'
                     AND (opoznienie_s < -3600 OR opoznienie_s > 7200)) AS odrzucone,
    -- Liczniki zdarzeń:
    COUNT(*) FILTER (WHERE status = 'ANULOWANY')                    AS anulowane,
    COUNT(*) FILTER (WHERE status = 'POMINIETY')                    AS pominiete
FROM fakt_opoznienia
GROUP BY kwadrans, data_kursu, wersja_id, linia_id, operator_id
WITH NO DATA;

-- UWAGA. start_offset MUSI być krótszy niż okno retencji surowych faktów
-- (48h, patrz maintenance/nightly.py). Odświeżenie agregatu przelicza kubełki
-- z surowych danych - jeśli okno sięga poza retencję, przeliczy je z pustki
-- i SKASUJE poprawne, zmaterializowane wiersze. Przy 6h mamy 8x zapasu.
--
-- remove_* przed add_*, bo add_continuous_aggregate_policy(if_not_exists=>true)
-- przy istniejącej polityce o INNEJ konfiguracji nie zmienia jej - wypisuje
-- warning i wychodzi. Sama zmiana wartości w tym pliku by nie zadziałała.
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
-- polityka odswieżania
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
-- Polityka odświeżania
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
    -- Ten sam filtr sensowności co w ca_opoznienia_15min - patrz komentarz tam.
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
-- start_offset jak wyżej - czyta bezpośrednio z fakt_opoznienia, więc podlega
-- temu samemu ograniczeniu retencji.
SELECT remove_continuous_aggregate_policy('ca_opoznienia_15min_przystanek',
    if_not_exists => true);
SELECT add_continuous_aggregate_policy('ca_opoznienia_15min_przystanek',
    start_offset      => INTERVAL '6 hours',
    end_offset        => INTERVAL '10 minutes',
    schedule_interval => INTERVAL '5 minutes');

-- ============================================================================
-- Retencja
-- ============================================================================
--
-- UWAGA. Na fakt_opoznienia i fakt_etl_run NIE MA polityki retencji i nie
-- wolno jej dodawać. Polityka kasuje ślepo, według zegara. W architekturze
-- "VPS jako przekaźnik" surowe fakty żyją lokalnie tylko do czasu wysłania
-- na Drive, a jedyne bezpieczne kasowanie to takie, które nastąpi PO
-- potwierdzonej weryfikacji uploadu. Robi to maintenance/nightly.py przez
-- jawne drop_chunks(). Awaria Drive'a na dwie doby + ślepa retencja =
-- nieodwracalna utrata danych, których rozdz. 7.1 specyfikacji nie pozwala
-- odtworzyć.
--
-- Poniższe remove_* są celowe: usuwają polityki 30/90-dniowe założone przez
-- wcześniejsze wersje tego pliku. Samo skasowanie linii add_* nie usunęłoby
-- polityki już zainstalowanej w bazie.
--
-- (remove_retention_policy używa if_exists, a remove_continuous_aggregate_policy
--  if_not_exists - to niespójność w API TimescaleDB, nie literówka.)
SELECT remove_retention_policy('fakt_opoznienia', if_exists => true);
SELECT remove_retention_policy('fakt_etl_run', if_exists => true);

-- Agregat przystankowy zasila wyłącznie Kepler/Power BI, nie jest wejściem
-- modelu (rozdz. 4.1 wskazuje ca_opoznienia_15min). Jest ~10x większy od
-- niego, więc tu ślepa retencja jest i bezpieczna, i pożądana - dane są
-- odtwarzalne z Parquetów na Drive.
SELECT remove_retention_policy('ca_opoznienia_15min_przystanek', if_exists => true);
SELECT add_retention_policy('ca_opoznienia_15min_przystanek', INTERVAL '14 days');

-- ca_opoznienia_15min, _1h i _dzien zostają bez retencji przez cały projekt.
-- Razem to ~300 MB i jest to bezpośrednie źródło wektora cech z rozdz. 8.
-- _1h i _dzien czytają z _15min, a nie z surowych faktów, więc ich
-- start_offset (14 i 60 dni) jest bezpieczny mimo retencji 48h na faktach.