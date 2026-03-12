"""Dagster resource definitions — clients for MinIO, ClickHouse, PostgreSQL."""

from pathlib import Path

import clickhouse_connect
import psycopg2
from dagster import InitResourceContext, resource
from minio import Minio

from ftb.writers.bronze import get_iceberg_catalog


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


@resource
def ch_empire_reader_resource(context: InitResourceContext):
    """ClickHouse client with ch_empire_reader credentials (SELECT-only on empire.*)."""
    return clickhouse_connect.get_client(
        host="empire_clickhouse",
        port=8123,
        username="ch_empire_reader",
        password=_read_secret("ch_empire_reader"),
        database="empire",
    )


@resource
def minio_bronze_archive_resource(context: InitResourceContext):
    """MinIO client for Bronze archive bucket writes."""
    return Minio(
        "minio:9001",
        access_key=_read_secret("minio_bronze_archive_key"),
        secret_key=_read_secret("minio_bronze_archive_secret"),
        secure=False,
    )


@resource
def iceberg_catalog_hot_resource(context: InitResourceContext):
    """PyIceberg SqlCatalog for bronze-hot bucket."""
    pg_password = _read_secret("pg_forge_user")
    minio_key = _read_secret("minio_bronze_key")
    minio_secret = _read_secret("minio_bronze_secret")
    pg_uri = f"postgresql+psycopg2://forge_user:{pg_password}@empire_postgres:5432/crypto_structured?options=-csearch_path%3Diceberg_catalog"
    return get_iceberg_catalog(
        pg_uri=pg_uri,
        minio_endpoint="http://minio:9001",
        minio_access_key=minio_key,
        minio_secret_key=minio_secret,
        warehouse="s3://bronze-hot",
    )


@resource
def iceberg_catalog_archive_resource(context: InitResourceContext):
    """PyIceberg SqlCatalog for bronze-archive bucket."""
    pg_password = _read_secret("pg_forge_user")
    minio_key = _read_secret("minio_bronze_archive_key")
    minio_secret = _read_secret("minio_bronze_archive_secret")
    pg_uri = f"postgresql+psycopg2://forge_user:{pg_password}@empire_postgres:5432/crypto_structured?options=-csearch_path%3Diceberg_catalog"
    return get_iceberg_catalog(
        pg_uri=pg_uri,
        minio_endpoint="http://minio:9001",
        minio_access_key=minio_key,
        minio_secret_key=minio_secret,
        warehouse="s3://bronze-archive",
    )


@resource
def ch_export_reader_resource(context: InitResourceContext):
    """ClickHouse client with ch_export_reader credentials (SELECT-only on forge.*)."""
    return clickhouse_connect.get_client(
        host="empire_clickhouse",
        port=8123,
        username="ch_export_reader",
        password=_read_secret("ch_export_reader"),
        database="forge",
    )


@resource
def ch_ops_reader_resource(context: InitResourceContext):
    """ClickHouse client with ch_ops_reader credentials (SELECT-only, health assets)."""
    return clickhouse_connect.get_client(
        host="empire_clickhouse",
        port=8123,
        username="ch_ops_reader",
        password=_read_secret("ch_ops_reader"),
        database="forge",
    )


@resource
def iceberg_catalog_gold_resource(context: InitResourceContext):
    """PyIceberg SqlCatalog for gold bucket."""
    pg_password = _read_secret("pg_forge_user")
    minio_key = _read_secret("minio_gold_key")
    minio_secret = _read_secret("minio_gold_secret")
    pg_uri = f"postgresql+psycopg2://forge_user:{pg_password}@empire_postgres:5432/crypto_structured?options=-csearch_path%3Diceberg_catalog"
    return get_iceberg_catalog(
        pg_uri=pg_uri,
        minio_endpoint="http://minio:9001",
        minio_access_key=minio_key,
        minio_secret_key=minio_secret,
        warehouse="s3://gold",
    )
