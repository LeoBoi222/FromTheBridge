"""Dagster asset for Silver → Gold export.

Reads forge.observations via ch_export_reader (Rule 2 compliant),
maps domains, merges with existing Gold partitions, writes via PyIceberg.

Source of truth: FromTheBridge_design_v4.0.md §Silver → Gold Export
"""

from datetime import UTC, datetime

import pyarrow.compute as pc
from dagster import (
    AssetExecutionContext,
    AssetKey,
    Config,
    MetadataValue,
    Output,
    asset,
)

from ftb.export.gold_export import (
    build_export_query,
    build_gold_arrow_table,
    check_anomaly_guard,
    derive_partitions,
    merge_partition,
)
from ftb.export.gold_iceberg import (
    ensure_gold_table,
    overwrite_partition,
    read_partition,
)


def _load_domain_lookup(pg_reader) -> dict[str, str]:
    """Load metric_id → catalog domain mapping from metric_catalog."""
    with pg_reader.cursor() as cur:
        cur.execute(
            "SELECT metric_id, domain FROM forge.metric_catalog WHERE status = 'active'"
        )
        return {row[0]: row[1] for row in cur.fetchall()}


def _load_watermark_from_metadata(instance) -> datetime | None:
    """Read watermark from last successful materialization metadata."""
    event = instance.get_latest_materialization_event(AssetKey("gold_observations"))
    if event is None:
        return None
    metadata = event.asset_materialization.metadata
    wm_entry = metadata.get("watermark_new")
    if wm_entry is None:
        return None
    return datetime.fromisoformat(wm_entry.value)


def _get_rolling_avg(instance) -> float:
    """Estimate rolling average from recent materialization metadata.

    Returns 0 if no history (first run — anomaly guard allows up to 2M).
    """
    try:
        result = instance.fetch_materializations(
            AssetKey("gold_observations"),
            limit=7,
        )
        records = result.records
    except Exception:
        return 0

    if not records:
        return 0

    counts = []
    for record in records:
        mat = record.asset_materialization
        if mat and mat.metadata:
            rows_entry = mat.metadata.get("rows_exported")
            if rows_entry is not None:
                counts.append(rows_entry.value)

    return sum(counts) / len(counts) if counts else 0


class GoldExportConfig(Config):
    force_backfill: bool = False


@asset(
    name="gold_observations",
    required_resource_keys={"ch_export_reader", "pg_forge_reader", "iceberg_catalog_gold"},
    metadata={"layer": "gold", "schedule": "hourly"},
)
def gold_observations(context: AssetExecutionContext, config: GoldExportConfig):
    """Export observations from Silver (ClickHouse) to Gold (Iceberg on MinIO).

    Incremental watermark-based export. Merges with existing partitions
    by data_version. Partition key: (year_month, metric_domain).
    """
    run_start = datetime.now(UTC)

    # 1. Load watermark from prior materialization
    watermark = _load_watermark_from_metadata(context.instance)
    context.log.info(f"Export watermark: {watermark or 'FIRST RUN'}")

    # 2. Query Silver
    sql, params = build_export_query(watermark, run_start)
    result = context.resources.ch_export_reader.query(sql, parameters=params)
    columns = result.column_names
    rows = [dict(zip(columns, row, strict=False)) for row in result.result_rows]
    context.log.info(f"Silver delta: {len(rows)} rows")

    if not rows:
        return Output(
            value=None,
            metadata={
                "rows_exported": MetadataValue.int(0),
                "partitions_touched": MetadataValue.int(0),
                "watermark_prev": MetadataValue.text(
                    watermark.isoformat() if watermark else "none"
                ),
                "watermark_new": MetadataValue.text(
                    watermark.isoformat() if watermark else "none"
                ),
                "watermark_advanced": MetadataValue.bool(False),
            },
        )

    # 3. Anomaly guard
    force_backfill = config.force_backfill
    rolling_avg = _get_rolling_avg(context.instance)
    if not check_anomaly_guard(len(rows), rolling_avg, force_backfill):
        raise RuntimeError(
            f"Anomaly guard triggered: {len(rows)} rows vs rolling avg {rolling_avg:.0f}. "
            f"Re-run with force_backfill=True to bypass."
        )

    # 4. Build Gold Arrow table with domain mapping
    domain_lookup = _load_domain_lookup(context.resources.pg_forge_reader)
    gold_table = build_gold_arrow_table(rows, domain_lookup)

    if gold_table.num_rows == 0:
        new_watermark = max(r["ingested_at"] for r in rows)
        context.log.info("All rows filtered by domain exclusion — nothing to export.")
        return Output(
            value=None,
            metadata={
                "rows_exported": MetadataValue.int(0),
                "partitions_touched": MetadataValue.int(0),
                "watermark_prev": MetadataValue.text(
                    watermark.isoformat() if watermark else "none"
                ),
                "watermark_new": MetadataValue.text(new_watermark.isoformat()),
                "watermark_advanced": MetadataValue.bool(True),
            },
        )

    # 5. Ensure Gold table exists
    catalog = context.resources.iceberg_catalog_gold
    ensure_gold_table(catalog)

    # 6. Per-partition merge + overwrite
    partitions = derive_partitions(gold_table.to_pylist())
    context.log.info(f"Partitions to touch: {partitions}")

    total_written = 0
    for year_month, metric_domain in sorted(partitions):
        # Filter new rows to this partition
        mask = pc.and_(
            pc.equal(gold_table.column("year_month"), year_month),
            pc.equal(gold_table.column("metric_domain"), metric_domain),
        )
        partition_new = gold_table.filter(mask)

        # Read existing partition
        try:
            existing = read_partition(catalog, year_month, metric_domain)
        except Exception:
            existing = None

        # Merge
        merged = merge_partition(existing, partition_new)

        # Overwrite
        overwrite_partition(catalog, merged, year_month, metric_domain)
        total_written += merged.num_rows
        context.log.info(
            f"Partition ({year_month}, {metric_domain}): "
            f"{partition_new.num_rows} new + "
            f"{existing.num_rows if existing else 0} existing "
            f"→ {merged.num_rows} merged"
        )

    # 7. Advance watermark
    new_watermark = max(r["ingested_at"] for r in rows)
    # CH may return naive datetime — normalize to UTC
    if new_watermark.tzinfo is None:
        new_watermark = new_watermark.replace(tzinfo=UTC)
    lag_seconds = (run_start - new_watermark).total_seconds()

    return Output(
        value=None,
        metadata={
            "rows_exported": MetadataValue.int(total_written),
            "partitions_touched": MetadataValue.int(len(partitions)),
            "watermark_prev": MetadataValue.text(
                watermark.isoformat() if watermark else "none"
            ),
            "watermark_new": MetadataValue.text(new_watermark.isoformat()),
            "lag_seconds": MetadataValue.float(lag_seconds),
            "watermark_advanced": MetadataValue.bool(True),
        },
    )
