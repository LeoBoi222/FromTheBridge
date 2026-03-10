"""Bronze cold archive — daily job to copy aged partitions from hot to archive.

Design: v4.0 §Layer 1: Landing Zone (C2)
Schedule: Daily 02:00 UTC
Window: today-9 to today-2 (2-day lag, 88-day safety margin before 90-day hot expiry)
"""

import hashlib
from datetime import date, timedelta

import pyarrow as pa
from dagster import AssetExecutionContext, MetadataValue, asset

from ftb.writers.bronze import (
    BRONZE_ARCHIVE_TABLE,
    BRONZE_HOT_TABLE,
    ensure_bronze_table,
)


def compute_archive_window(today: date) -> tuple[date, date]:
    """Return (start_date, end_date) for the archive window.

    Window: today-9 to today-2 (inclusive).
    """
    return today - timedelta(days=9), today - timedelta(days=2)


def discover_hot_partitions(
    catalog,
    table_name: str,
    start_date: date,
    end_date: date,
) -> list[dict]:
    """Scan bronze-hot Iceberg table metadata for partitions in the date window.

    Returns list of dicts with keys: source_id, metric_id, partition_date, row_count.
    """
    table = ensure_bronze_table(catalog, table_name)
    start_str = start_date.isoformat()
    end_str = end_date.isoformat()

    # Scan with row filter on partition_date range
    scan = table.scan(
        row_filter=f"partition_date >= '{start_str}' and partition_date <= '{end_str}'",
        selected_fields=("source_id", "metric_id", "partition_date"),
    )

    # Group by (source_id, metric_id, partition_date)
    partitions = {}
    for batch in scan.to_arrow_batch_reader():
        for i in range(batch.num_rows):
            key = (
                batch.column("source_id")[i].as_py(),
                batch.column("metric_id")[i].as_py(),
                batch.column("partition_date")[i].as_py(),
            )
            partitions[key] = partitions.get(key, 0) + 1

    return [
        {
            "source_id": k[0],
            "metric_id": k[1],
            "partition_date": k[2],
            "row_count": v,
        }
        for k, v in partitions.items()
    ]


def archive_partition(
    hot_catalog,
    archive_catalog,
    source_id: str,
    metric_id: str,
    partition_date: str,
) -> dict:
    """Read a partition from hot, write to archive. Returns archive metadata."""
    hot_table = ensure_bronze_table(hot_catalog, BRONZE_HOT_TABLE)
    archive_table = ensure_bronze_table(archive_catalog, BRONZE_ARCHIVE_TABLE)

    # Read the specific partition
    scan = hot_table.scan(
        row_filter=(
            f"source_id == '{source_id}' and "
            f"metric_id == '{metric_id}' and "
            f"partition_date == '{partition_date}'"
        ),
    )
    arrow_table = scan.to_arrow()

    if arrow_table.num_rows == 0:
        return {"row_count": 0, "skipped": True}

    # Compute checksum from the Arrow table bytes
    sink = pa.BufferOutputStream()
    writer = pa.ipc.new_stream(sink, arrow_table.schema)
    writer.write_table(arrow_table)
    writer.close()
    raw_bytes = sink.getvalue().to_pybytes()
    checksum = hashlib.sha256(raw_bytes).hexdigest()

    # Append to archive table
    archive_table.append(arrow_table)

    # Extract observed_at bounds
    observed_at_col = arrow_table.column("observed_at")
    observed_at_min = observed_at_col.to_pylist()
    ts_values = [v for v in observed_at_min if v is not None]
    min_ts = min(ts_values) if ts_values else None
    max_ts = max(ts_values) if ts_values else None

    return {
        "row_count": arrow_table.num_rows,
        "byte_size": len(raw_bytes),
        "checksum": checksum,
        "observed_at_min": min_ts,
        "observed_at_max": max_ts,
        "archive_path": f"s3://bronze-archive/{source_id}/{partition_date}/{metric_id}/",
        "skipped": False,
    }


def log_archive_result(
    pg_conn,
    source_id: str,
    metric_id: str,
    partition_date: str,
    archive_meta: dict,
    run_id: str,
) -> None:
    """Insert or update bronze_archive_log in PostgreSQL."""
    if archive_meta.get("skipped"):
        return

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO forge.bronze_archive_log
                (source_id, metric_id, partition_date, archive_path, byte_size,
                 row_count, observed_at_min, observed_at_max, checksum,
                 checksum_verified, archive_job_run_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE, %s)
            ON CONFLICT (source_id, metric_id, partition_date)
            DO UPDATE SET
                archive_path = EXCLUDED.archive_path,
                byte_size = EXCLUDED.byte_size,
                row_count = EXCLUDED.row_count,
                observed_at_min = EXCLUDED.observed_at_min,
                observed_at_max = EXCLUDED.observed_at_max,
                checksum = EXCLUDED.checksum,
                checksum_verified = FALSE,
                verified_at = NULL,
                archived_at = NOW(),
                archive_job_run_id = EXCLUDED.archive_job_run_id
            """,
            (
                source_id,
                metric_id,
                partition_date,
                archive_meta["archive_path"],
                archive_meta.get("byte_size"),
                archive_meta["row_count"],
                archive_meta.get("observed_at_min"),
                archive_meta.get("observed_at_max"),
                archive_meta["checksum"],
                run_id,
            ),
        )
    pg_conn.commit()


@asset(
    group_name="bronze_archive",
    required_resource_keys={"iceberg_catalog_hot", "iceberg_catalog_archive", "pg_forge"},
)
def bronze_cold_archive(context: AssetExecutionContext) -> None:
    """Archive aged partitions from bronze-hot to bronze-archive.

    Window: today-9 to today-2. Daily at 02:00 UTC.
    Idempotent via ON CONFLICT in bronze_archive_log.
    """
    today = date.today()
    start_date, end_date = compute_archive_window(today)
    run_id = context.run_id

    hot_catalog = context.resources.iceberg_catalog_hot
    archive_catalog = context.resources.iceberg_catalog_archive
    pg_conn = context.resources.pg_forge

    context.log.info(f"Archive window: {start_date} to {end_date}")

    # Discover partitions in the hot table
    partitions = discover_hot_partitions(hot_catalog, BRONZE_HOT_TABLE, start_date, end_date)
    context.log.info(f"Found {len(partitions)} partitions to archive")

    archived_count = 0
    skipped_count = 0
    total_rows = 0

    for part in partitions:
        meta = archive_partition(
            hot_catalog,
            archive_catalog,
            part["source_id"],
            part["metric_id"],
            part["partition_date"],
        )

        if meta.get("skipped"):
            skipped_count += 1
            continue

        log_archive_result(
            pg_conn,
            part["source_id"],
            part["metric_id"],
            part["partition_date"],
            meta,
            run_id,
        )
        archived_count += 1
        total_rows += meta["row_count"]

    context.add_output_metadata({
        "archive_window_start": MetadataValue.text(start_date.isoformat()),
        "archive_window_end": MetadataValue.text(end_date.isoformat()),
        "partitions_discovered": MetadataValue.int(len(partitions)),
        "partitions_archived": MetadataValue.int(archived_count),
        "partitions_skipped": MetadataValue.int(skipped_count),
        "total_rows_archived": MetadataValue.int(total_rows),
    })

    context.log.info(
        f"Archive complete: {archived_count} archived, {skipped_count} skipped, {total_rows} rows"
    )
