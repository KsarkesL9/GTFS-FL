# rclone bierzemy z obrazu oficjalnego zamiast pobierać binarkę curl-em -
# mniej ruchomych części i żadnego zgadywania numeru wersji.
FROM rclone/rclone:1.68 AS rclone

FROM python:3.12-slim

# Wersja supercronica pinowana świadomie. Bump wymaga sprawdzenia release'u -
# nieistniejący tag wywali build, co jest zachowaniem pożądanym.
ARG SUPERCRONIC_VERSION=v0.2.33

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Warsaw

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY --from=rclone /usr/local/bin/rclone /usr/local/bin/rclone

RUN curl -fsSL \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64" \
      -o /usr/local/bin/supercronic \
 && chmod 0755 /usr/local/bin/supercronic

WORKDIR /app
COPY pyproject.toml ./
COPY gtfs_olap ./gtfs_olap
COPY scripts ./scripts
COPY crontab ./crontab
RUN pip install --no-cache-dir -e .

# Nie-root. UID jest stały, bo katalog /data jest bind-mountem z hosta i musi
# mieć zgodnego właściciela - patrz .env.example.
RUN useradd -u 10001 -m gtfs && mkdir -p /data && chown gtfs:gtfs /data
USER gtfs

CMD ["python", "scripts/run_rt_etl.py"]
