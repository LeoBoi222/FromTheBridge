FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /opt/empire/pipeline/pyproject.toml
COPY src/ /opt/empire/pipeline/src/

WORKDIR /opt/empire/pipeline
RUN pip install --no-cache-dir -e ".[dagster-webserver]"

ENV DAGSTER_HOME=/opt/dagster/home
RUN mkdir -p /opt/dagster/home /opt/dagster/storage/logs /opt/dagster/storage/artifacts

COPY docker/dagster/dagster.yaml /opt/dagster/home/dagster.yaml
COPY docker/dagster/workspace.yaml /opt/dagster/home/workspace.yaml
