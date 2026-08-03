"""Utrzymanie kolektora na VPS: eksport, wysyłka na Drive, sprzątanie.

Architektura "VPS jako przekaźnik": maszyna nie jest magazynem, tylko
punktem pośrednim. Surowe dane żyją na niej wyłącznie do czasu
potwierdzonego zapisu na Google Drive.

Reguła nadrzędna całego modułu: NIC nie jest kasowane lokalnie, dopóki
`rclone check --checksum` nie potwierdzi kopii zdalnej. Utraconych migawek
GTFS-RT nie da się odtworzyć (rozdz. 7.1 specyfikacji), więc ślepe
kasowanie według zegara - w tym polityki retencji TimescaleDB na
fakt_opoznienia - jest w tym projekcie zabronione.
"""
