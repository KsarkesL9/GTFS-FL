FROM rclone/rclone:1.68 AS rclone

FROM python:3.12-slim

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

# UID stały, bo /data jest bind-mountem z hosta (patrz .env.example).
# .config/rclone tworzymy jawnie - Docker dorobiłby go jako root.
RUN useradd -u 10001 -m gtfs \
 && mkdir -p /data /home/gtfs/.config/rclone \
 && chown -R gtfs:gtfs /data /home/gtfs
USER gtfs

CMD ["python", "scripts/run_rt_etl.py"]
