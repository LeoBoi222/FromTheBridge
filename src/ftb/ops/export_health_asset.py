"""Dagster asset: ftb_ops.export_health — monitors Silver→Gold export.

Checks export freshness, merge lag, unmerged parts, Gold snapshot count.
Uses ch_ops_reader (Rule 2 compliant) and iceberg_catalog_gold.

Source of truth: FromTheBridge_design_v4.0.md §Solo Operator Operations
"""
from datetime import UTC, datetime

from dagster import (
    AssetExecutionContext,
    AssetKey,
    DagsterEventType,
    EventRecordsFilter,
    MetadataValue,
    Output,
    asset,
)

from ftb.ops.health import check_export_health


def _get_last_export_info(instance) -> tuple[datetime | None, int | None, int]:
    """Get last export timestamp, row count, and consecutive failure count.

    Returns (last_success_at, rows_exported, consecutive_failures).
    """
    try:
        records = instance.get_event_records(
            EventRecordsFilter(
                event_type=DagsterEventType.ASSET_MATERIALIZATION,
                asset_key=AssetKey("gold_observations"),
            ),
            limit=10,
        )
    except Exception:
        return None, None, 0

    if not records:
        return None, None, 0

    # Find last successful materialization
    last_success_at = None
    rows_exported = None
    consecutive_failures = 0

    for record in records:
        mat = record.event_log_entry.dagster_event.step_materialization_data
        if mat and mat.materialization and mat.materialization.metadata:
            meta = mat.materialization.metadata
            rows_entry = meta.get("rows_exported")
            if rows_entry is not None:
                if last_success_at is None:
                    last_success_at = record.event_log_entry.timestamp
                    rows_exported = rows_entry.value
                break
        # No materialization data = failure
        consecutive_failures += 1

    if last_success_at is not None and isinstance(last_success_at, (int, float)):
        last_success_at = datetime.fromtimestamp(last_success_at, tz=UTC)

    return last_success_at, rows_exported, consecutive_failures


def _get_merge_lag(ch_ops_reader) -> float:
    """Current merge lag in seconds for forge.observations."""
    result = ch_ops_reader.query(
        "SELECT max(elapsed) FROM system.merges "
        "WHERE database = 'forge' AND table = 'observations'"
    )
    val = result.result_rows[0][0]
    return float(val) if val is not None else 0.0


def _get_unmerged_parts(ch_ops_reader) -> int:
    """Active (non-merged) part count for forge.observations."""
    result = ch_ops_reader.query(
        "SELECT count(*) FROM system.parts "
        "WHERE database = 'forge' AND table = 'observations' AND active = 1"
    )
    return result.result_rows[0][0]


def _get_gold_snapshot_count(iceberg_catalog) -> int:
    """Count Iceberg snapshots in gold.observations table."""
    try:
        table = iceberg_catalog.load_table("gold.observations")
        # PyIceberg snapshots
        return len(list(table.metadata.snapshots))
    except Exception:
        return 0


@asset(
    key_prefix=["ftb_ops"],
    name="export_health",
    required_resource_keys={"ch_ops_reader", "iceberg_catalog_gold"},
    metadata={"kind": "health_check", "target": "gold_observations"},
)
def export_health(context: AssetExecutionContext):
    """Monitor Silver→Gold export freshness, merge lag, and Gold table health."""
    ch = context.resources.ch_ops_reader
    catalog = context.resources.iceberg_catalog_gold

    last_export_at, rows_last, consecutive_failures = _get_last_export_info(
        context.instance
    )
    merge_lag = _get_merge_lag(ch)
    unmerged = _get_unmerged_parts(ch)
    snapshots = _get_gold_snapshot_count(catalog)

    result = check_export_health(
        last_export_at=last_export_at,
        rows_exported_last_run=rows_last,
        merge_lag_seconds=merge_lag,
        unmerged_parts=unmerged,
        gold_snapshot_count=snapshots,
        consecutive_failures=consecutive_failures,
    )

    meta = result.to_metadata()
    context.log.info(f"export_health: severity={result.severity} fields={result.fields}")

    return Output(
        value=None,
        metadata={k: MetadataValue.text(str(v)) for k, v in meta.items()},
    )
