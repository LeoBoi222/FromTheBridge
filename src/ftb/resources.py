"""Dagster resource definitions — clients for MinIO, ClickHouse, PostgreSQL."""
from __future__ import annotations

from pathlib import Path

import clickhouse_connect
import psycopg2
from dagster import resource, InitResourceContext
from minio import Minio


def _read_secret(name: str) -> str:
    """Read a Docker secret from /run/secrets/."""
    return Path(f"/run/secrets/{name}").read_text().strip()


@resource
def ch_writer_resource(context: InitResourceContext):
    """ClickHouse client with ch_writer credentials (INSERT-only)."""
    return clickhouse_connect.get_client(
        host="empire_clickhouse",
        port=8123,
        username="ch_writer",
        password=_read_secret("ch_writer"),
        database="forge",
    )


@resource
def pg_forge_resource(context: InitResourceContext):
    """PostgreSQL connection with forge_user credentials."""
    return psycopg2.connect(
        host="empire_postgres",
        port=5432,
        dbname="crypto_structured",
        user="forge_user",
        password=_read_secret("pg_forge_user"),
    )


@resource
def pg_forge_reader_resource(context: InitResourceContext):
    """PostgreSQL connection with forge_reader credentials (SELECT-only)."""
    return psycopg2.connect(
        host="empire_postgres",
        port=5432,
        dbname="crypto_structured",
        user="forge_reader",
        password=_read_secret("pg_forge_reader"),
    )


@resource
def minio_bronze_resource(context: InitResourceContext):
    """MinIO client for Bronze bucket writes."""
    return Minio(
        "minio:9001",
        access_key=_read_secret("minio_bronze_key"),
        secret_key=_read_secret("minio_bronze_secret"),
        secure=False,
    )
