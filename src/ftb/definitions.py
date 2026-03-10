"""Dagster definitions entry point for the FTB pipeline.

Assets are registered here as adapters are built. The code server loads this module
via the -m flag: dagster api grpc -m ftb.definitions
"""
import os
from datetime import datetime, timezone
from pathlib import Path

import dagster
from dagster import ScheduleDefinition, define_asset_job

from ftb.adapters.tiingo_asset import collect_tiingo_price, TIINGO_PARTITIONS
from ftb.sync.sync_asset import empire_to_forge_sync
from ftb.resources import (
    ch_empire_reader_resource,
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


# Job for scheduled Tiingo collection
tiingo_collection_job = define_asset_job(
    name="tiingo_collection_job",
    selection=[collect_tiingo_price],
    partitions_def=TIINGO_PARTITIONS,
)

# Collect every 6 hours, materializing today's partition.
# Running 4x/day ensures we catch late Tiingo updates.
tiingo_6h_schedule = ScheduleDefinition(
    name="tiingo_6h_collection",
    cron_schedule="15 */6 * * *",  # :15 past every 6th hour
    job=tiingo_collection_job,
    execution_fn=lambda context: dagster.RunRequest(
        partition_key=datetime.now(timezone.utc).date().isoformat(),
    ),
)


# Job + schedule for empire_to_forge_sync (unpartitioned, cursor-based)
sync_job = define_asset_job(
    name="empire_to_forge_sync_job",
    selection=[empire_to_forge_sync],
)

sync_6h_schedule = ScheduleDefinition(
    name="empire_to_forge_sync_6h",
    cron_schedule="30 */6 * * *",  # :30 past every 6th hour (offset from Tiingo at :15)
    job=sync_job,
)


defs = dagster.Definitions(
    assets=[collect_tiingo_price, empire_to_forge_sync],
    schedules=[tiingo_6h_schedule, sync_6h_schedule],
    resources={
        "ch_writer": ch_writer_resource,
        "ch_empire_reader": ch_empire_reader_resource,
        "pg_forge": pg_forge_resource,
        "pg_forge_reader": pg_forge_reader_resource,
        "minio_bronze": minio_bronze_resource,
        "tiingo_api_key": tiingo_api_key_resource,
    },
)
