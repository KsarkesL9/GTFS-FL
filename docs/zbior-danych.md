# Opis zbioru danych

Charakterystyka zbioru danych pozyskanego ze strumienia GTFS-Realtime ZTM (Metropolia GZM) na potrzeby federacyjnej detekcji anomalii. Statystyki zostały wyznaczone bezpośrednio ze zbioru za pomocą skryptu `python scripts/describe_dataset.py`.

---

## 1. Zakres danych

| Parametr | Wartość |
|---|---|
| Okres zbierania danych | 2026-08-04 – 2026-08-25 (22 dni) |
| Liczba klientów federacji | 9 |
| Liczba operatorów transportowych | 21 |
| Łączna liczba okien 15-minutowych | 18 711 |
| Okna kompletne | 17 744 (94,8%) |
| Liczba obserwacji w oknach kompletnych | 66 422 468 |
| Granica podziału trening / test | 2026-08-19 12:00 |
| Liczba okien treningowych | 13 104 |
| Liczba okien testowych | 5 607 |

Okna niekompletne to przedziały czasowe, w których w danym rejonie lub u danego operatora nie zarejestrowano żadnej obserwacji (np. podczas nocnej przerwy w kursowaniu lub przerw w publikacji danych przez serwery źródłowe). Okna te są pomijane przy konstruowaniu sekwencji uczących i nie biorą udziału w ocenie modeli.

---

## 2. Słownik cech

| Cecha | Znaczenie | Wzór | Jednostka | Uwagi |
|---|---|---|---|---|
| `d` | Średnie opóźnienie w oknie | $S(t) / n(t)$ | s | Suma opóźnień $S$ filtrowana do zakresu fizycznego [−1h, +2h] |
| `w` | Udział obserwacji punktualnych | $q(t) / n(t)$ | 0…1 | Kursy punktualne: opóźnienie w przedziale [−30 s, +60 s] |
| `dmax` | Maksymalne opóźnienie w oknie | $\max(d)$ | s | Filtrowane analogicznie do cechy $d$ |
| `n` | Liczba obserwacji | Liczba rekordów | szt. | Pojedyncza obserwacja to odczyt pozycji pojazdu na zatrzymaniu |
| `p` | Udział pominiętych zatrzymań | $u(t) / n(t)$ | 0…1 | Wartość 0 (źródłowy feed ZTM nie raportuje pominięć ani odwołań) |
| `delta_d` | Zmiana średniego opóźnienia | $d(t) - d(t-1)$ | s | Pierwsze okno każdego klienta uzupełniane zerem |
| `r` | Odchylenie od profilu dobowego | $d(t) - m(\text{godzina}, \text{typ\_dnia})$ | s | Wzorzec dobowy $m$ wyznaczony wyłącznie na zbiorze treningowym |
| `h` | Nieregularność odstępów (headway) | Średnie odch. std. odstępów | s | Liczone dla par (linia, kierunek) w oknie kroczącym 60 min |
| `sin_hour` | Pora doby (sinus) | $\sin(2\pi \cdot \text{minuta\_doby} / 1440)$ | −1…1 | Kodowanie kołowe (zapewnia bliskość 23:45 i 00:00) |
| `cos_hour` | Pora doby (cosinus) | $\cos(2\pi \cdot \text{minuta\_doby} / 1440)$ | −1…1 | Kodowanie kołowe |
| `workday` | Dzień roboczy | Flaga z kalendarza | 0 / 1 | 1 dla dni powszednich wakacyjnych, 0 dla weekendów i świąt |

Wektor wejściowy modelu składa się z 11 cech uporządkowanych następująco:
`[d, w, dmax, n, p, delta_d, r, h, sin_hour, cos_hour, workday]`.

Wielkości bazowe używane do kalkulacji:
- `n` – liczba odnotowanych obserwacji,
- `S` – suma zarejestrowanych opóźnień,
- `q` – liczba kursów punktualnych,
- `u` – liczba pominiętych przystanków (stałe 0 w badanym strumieniu),
- `dmax` – maksymalne odnotowane opóźnienie w oknie.

---

## 3. Statystyki opisowe

Wartości surowe przed standaryzacją, obliczone dla wszystkich okien kompletnych:

| Cecha | Średnia | Odch. std. | Min | Mediana | Maks. | Braki danych |
|---|---|---|---|---|---|---|
| `d` | 104,3 | 162,7 | −48,0 | 84,9 | 5 703 | 0,0% |
| `w` | 0,608 | 0,126 | 0,000 | 0,611 | 1,000 | 0,0% |
| `dmax` | 1 356 | 1 327 | 0,0 | 960,0 | 7 200 | 0,0% |
| `n` | 3 743 | 2 524 | 1,0 | 3 758 | 10 171 | 0,0% |
| `p` | 0,000 | 0,000 | 0,000 | 0,000 | 0,000 | 0,0% |
| `delta_d` | 1,488 | 124,8 | −3 649 | 0,952 | 5 313 | 1,4% |
| `r` | 18,2 | 152,1 | −631,7 | 1,905 | 5 528 | 0,0% |
| `h` | 2 245 | 5 968 | 0,0 | 448,6 | 172 610 | 6,5% |
| `sin_hour` | −0,042 | 0,709 | −1,000 | −0,131 | 1,000 | 0,0% |
| `cos_hour` | −0,044 | 0,703 | −1,000 | −0,065 | 1,000 | 0,0% |
| `workday` | 0,728 | 0,445 | 0,000 | 1,000 | 1,000 | 0,0% |

Brakujące wartości w cechach:
- `h`: występują w oknach o bardzo niskiej intensywności ruchu (np. nocnych), w których na danej trasie nie było wystarczającej liczby kursów do oszacowania odchylenia odstępów,
- `delta_d`: dotyczy wyłącznie pierwszego okna czasowego w serii każdego klienta.

Podczas standaryzacji brakujące wartości są uzupełniane zerem (odpowiadającym średniej cechy w zbiorze treningowym).

---

## 4. Wolumen danych w podziale na klientów

| Klient | Operatorzy | Wszystkie okna | Okna kompletne | Okna trening | Okna test | Sekwencje trening | Sekwencje test |
|---|---|---|---|---|---|---|---|
| `pkm_gliwice` | 1 | 2 079 | 2 004 (96%) | 1 456 | 623 | 1 274 | 576 |
| `pkm_katowice` | 1 | 2 079 | 2 004 (96%) | 1 456 | 623 | 1 274 | 576 |
| `pkm_sosnowiec` | 1 | 2 079 | 1 987 (96%) | 1 456 | 623 | 1 226 | 556 |
| `pkm_swierklaniec` | 1 | 2 079 | 2 002 (96%) | 1 456 | 623 | 1 266 | 576 |
| `pkm_tychy` | 1 | 2 079 | 1 950 (94%) | 1 456 | 623 | 1 210 | 551 |
| `prywatni_centrum_wschod` | 6 | 2 079 | 2 001 (96%) | 1 456 | 623 | 1 267 | 572 |
| `prywatni_zachod` | 8 | 2 079 | 1 970 (95%) | 1 456 | 623 | 1 202 | 564 |
| `tramwaje_slaskie` | 1 | 2 079 | 1 995 (96%) | 1 456 | 623 | 1 266 | 564 |
| `trolejbusy_tychy` | 1 | 2 079 | 1 831 (88%) | 1 456 | 623 | 1 154 | 512 |

Każda sekwencja wejściowa modelu składa się z 8 kolejnych okien kompletnych ($L=8$, czyli 2 godziny ciągłego ruchu). Każda luka w ciągłości danych powoduje odrzucenie do 7 sąsiednich sekwencji brzegowych.

---

## 5. Podział na węzły federacji

Podział na 9 klientów odzwierciedla strukturę organizacyjną i terytorialną transportu publicznego w Metropolii GZM:

| Klient federacji | Operatorzy wchodzący w skład węzła |
|---|---|
| `pkm_katowice` | Przedsiębiorstwo Komunikacji Miejskiej Katowice sp. z o.o. |
| `pkm_sosnowiec` | Przedsiębiorstwo Komunikacji Miejskiej sp. z o.o. w Sosnowcu |
| `tramwaje_slaskie` | Tramwaje Śląskie S.A. |
| `pkm_gliwice` | Przedsiębiorstwo Komunikacji Miejskiej sp. z o.o. w Gliwicach |
| `pkm_swierklaniec` | Przedsiębiorstwo Komunikacji Metropolitalnej sp. z o.o. w Świerklańcu |
| `pkm_tychy` | Przedsiębiorstwo Komunikacji Miejskiej sp. z o.o. w Tychach |
| `trolejbusy_tychy` | Tyskie Linie Trolejbusowe sp. z o.o. |
| `prywatni_zachod` | Konsorcja i operatorzy prywatni obsługujący rejon zachodni (m.in. IREX, METEOR, TRANSGÓR, Kłosok, Nowak Transport, Pawelec, PKS Południe) |
| `prywatni_centrum_wschod` | Konsorcja i operatorzy prywatni obsługujący rejon centralny i wschodni (m.in. Intrans, Pawelec, PKS Gostynin, PKS Tarnobrzeg, PKS Grodzisk Mazowiecki, TRANSGÓR) |

Podział został zdefiniowany w oparciu o naturalne kryteria geograficzne i przewoźnicze, bez uwzględniania statystyk opóźnień (co zapobiega wyciekowi informacji o zmiennej celu do struktury podziału).

---

## 6. Uwagi i ograniczenia zbioru

- **Cecha `p` (pominięcia)**: W badanym okresie (66,4 mln obserwacji) feed GTFS-RT organizatora nie przekazywał informacji o pominięciach przystanków ani odwołanych kursach (wartość stała 0).
- **Ciągłość strumienia**: W dniach 9 sierpnia (przez 6h 39m) oraz w nocy 18/19 sierpnia (przez 8h 39m) wystąpiły przerwy w publikacji danych po stronie ZTM (feed zwracał puste komunikaty bez pojazdów). Okna te zostały automatycznie oznaczone jako niekompletne.
- **Przejście przez północ**: Około 0,017% kursów nocnych po północy posiada w danych źródłowych datę przypisaną do doby poprzedniej – nie wpływa to na jakość po agregacji na poziomie okien czasowych klienta.
- **Sezonowość**: Zbiór obejmuje reżim wakacyjny (sierpień 2026), charakteryzujący się mniejszym natężeniem ruchu w porównaniu z okresem roku szkolnego.
