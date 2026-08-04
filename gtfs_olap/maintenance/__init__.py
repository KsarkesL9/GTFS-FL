"""Utrzymanie kolektora: eksport, wysyłka na Drive, sprzątanie.

VPS jest przekaźnikiem, nie magazynem. Reguła nadrzędna: nic nie jest kasowane
lokalnie, dopóki `rclone check --checksum` nie potwierdzi kopii zdalnej.
Utraconych migawek GTFS-RT nie da się odtworzyć, więc kasowanie według zegara -
w tym polityki retencji TimescaleDB na faktach - jest zabronione.
"""
