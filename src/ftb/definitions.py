"""Dagster definitions entry point for the FTB pipeline.

Assets are registered here as adapters are built. The code server loads this module
via the -m flag: dagster api grpc -m ftb.definitions
"""
import dagster
from dagster import ScheduleDefinition, define_asset_job

from ftb.archive.archive_asset import bronze_cold_archive
from ftb.archive.audit_asset import bronze_expiry_audit
from ftb.export.export_asset import gold_observations
from ftb.resources import (
    ch_empire_reader_resource,
    ch_export_reader_resource,
    ch_writer_resource,
    iceberg_catalog_archive_resource,
    iceberg_catalog_gold_resource,
    iceberg_catalog_hot_resource,
    minio_bronze_archive_resource,
    minio_bronze_resource,
    pg_forge_reader_resource,
    pg_forge_resource,
)
from ftb.sync.sync_asset import empire_to_forge_sync

# Job + schedule for empire_to_forge_sync (unpartitioned, cursor-based)
sync_job = define_asset_job(
    name="empire_to_forge_sync_job",
    selection=[empire_to_forge_sync],
)

sync_6h_schedule = ScheduleDefinition(
    name="empire_to_forge_sync_6h",
    cron_schedule="30 */6 * * *",  # :30 past every 6th hour
    job=sync_job,
)

# Job + schedule for bronze archive (daily 02:00 UTC)
archive_job = define_asset_job(
    name="bronze_archive_job",
    selection=[bronze_cold_archive, bronze_expiry_audit],
)

archive_daily_schedule = ScheduleDefinition(
    name="bronze_archive_daily",
    cron_schedule="0 2 * * *",  # 02:00 UTC daily
    job=archive_job,
)

# Gold export job + hourly fallback schedule (v4.0 §Silver → Gold Export)
gold_export_job = define_asset_job(
    name="gold_export_job",
    selection=[gold_observations],
)

gold_hourly_schedule = ScheduleDefinition(
    name="gold_export_hourly",
    cron_schedule="15 * * * *",  # :15 past every hour
    job=gold_export_job,
)


defs = dagster.Definitions(
    assets=[empire_to_forge_sync, bronze_cold_archive, bronze_expiry_audit, gold_observations],
    schedules=[sync_6h_schedule, archive_daily_schedule, gold_hourly_schedule],
    resources={
        "ch_writer": ch_writer_resource,
        "ch_empire_reader": ch_empire_reader_resource,
        "ch_export_reader": ch_export_reader_resource,
        "pg_forge": pg_forge_resource,
        "pg_forge_reader": pg_forge_reader_resource,
        "minio_bronze": minio_bronze_resource,
        "minio_bronze_archive": minio_bronze_archive_resource,
        "iceberg_catalog_hot": iceberg_catalog_hot_resource,
        "iceberg_catalog_archive": iceberg_catalog_archive_resource,
        "iceberg_catalog_gold": iceberg_catalog_gold_resource,
    },
)
