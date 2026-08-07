"""Eksport, wysyłka na Drive i sprzątanie bazy.

Zasada: nic nie kasujemy lokalnie, dopóki `rclone check --checksum` nie
potwierdzi kopii zdalnej. Migawek GTFS-RT nie da się odtworzyć, więc żadnych
polityk retencji na faktach.
"""
