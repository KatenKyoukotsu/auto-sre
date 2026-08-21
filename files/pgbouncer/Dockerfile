FROM ubuntu:24.04

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends pgbouncer postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# pgbouncer не любит root
RUN useradd --system --home /var/lib/pgbouncer --shell /usr/sbin/nologin pgbouncer \
    && mkdir -p /var/lib/pgbouncer /var/log/pgbouncer /etc/pgbouncer \
    && chown -R pgbouncer:pgbouncer /var/lib/pgbouncer /var/log/pgbouncer /etc/pgbouncer

USER pgbouncer
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
