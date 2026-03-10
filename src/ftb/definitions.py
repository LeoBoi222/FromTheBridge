"""Dagster definitions entry point for the FTB pipeline.

Assets are registered here as adapters are built. The code server loads this module
via the -m flag: dagster api grpc -m ftb.definitions
"""
import os
from pathlib import Path

import dagster

from ftb.adapters.tiingo_asset import collect_tiingo_price
from ftb.resources import (
    ch_writer_resource,
    minio_bronze_resource,
    pg_forge_resource,
    pg_forge_reader_resource,
)


def _read_secret(name: str) -> str:
    """Read a Docker secret from /run/secrets/."""
    path = Path(f"/run/secrets/{name}")
    if path.exists():
        return path.read_text().strip()
    return ""


@dagster.resource
def tiingo_api_key_resource(context):
    """Tiingo API key — read from /run/secrets/tiingo_api_key or TIINGO_API_KEY env."""
    secret = _read_secret("tiingo_api_key")
    if secret:
        return secret
    return os.environ.get("TIINGO_API_KEY", "")


defs = dagster.Definitions(
    assets=[collect_tiingo_price],
    resources={
        "ch_writer": ch_writer_resource,
        "pg_forge": pg_forge_resource,
        "pg_forge_reader": pg_forge_reader_resource,
        "minio_bronze": minio_bronze_resource,
        "tiingo_api_key": tiingo_api_key_resource,
    },
)
