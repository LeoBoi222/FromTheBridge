FROM python:3.12-slim

# System deps for psycopg2, clickhouse-connect, pyarrow
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Install dagster ecosystem
RUN pip install --no-cache-dir \
    dagster \
    dagster-webserver \
    dagster-postgres

# Install the ftb package (editable for code server, readonly for webserver/daemon)
COPY pyproject.toml /opt/empire/pipeline/pyproject.toml
COPY src/ /opt/empire/pipeline/src/

WORKDIR /opt/empire/pipeline
RUN pip install --no-cache-dir -e .

# Dagster home for config
ENV DAGSTER_HOME=/opt/dagster/home
RUN mkdir -p /opt/dagster/home /opt/dagster/storage/logs /opt/dagster/storage/artifacts

COPY docker/dagster/dagster.yaml /opt/dagster/home/dagster.yaml
COPY docker/dagster/workspace.yaml /opt/dagster/home/workspace.yaml

# D1: read_secret utility reads bind-mounted files at /run/secrets/
# Entrypoint set per-service in docker-compose.yml
