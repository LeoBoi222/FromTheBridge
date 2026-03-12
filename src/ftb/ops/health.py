"""Health check logic for ops assets — pure functions, no Dagster imports.

Computes freshness, coverage, dead letter rates, and severity levels
for sync, export, and adapter health monitoring.

Source of truth: FromTheBridge_design_v4.0.md §Solo Operator Operations
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass
class HealthResult:
    """Result of a health check with severity and detail fields."""

    severity: str  # "green", "yellow", "red"
    fields: dict[str, object]

    def to_metadata(self) -> dict[str, object]:
        """Flatten to dict suitable for Dagster MetadataValue."""
        out: dict[str, object] = {"severity": self.severity}
        out.update(self.fields)
        return out


# --- Sync health ---


def check_sync_health(
    last_event: dict | None,
    dead_letter_24h: int,
    total_observations: int,
    promoted_metric_count: int,
    metrics_with_data: int,
    cadence_hours: float = 6.0,
) -> HealthResult:
    """Evaluate empire_to_forge_sync health.

    Args:
        last_event: Most recent collection_event for eds_derived (dict with
            completed_at, status, observations_written, observations_rejected,
            metrics_covered).
        dead_letter_24h: Dead letter count in last 24h for eds_derived.
        total_observations: Total forge.observations for eds_derived.
        promoted_metric_count: Count of metrics in catalog with eds_derived source.
        metrics_with_data: Distinct metric_ids in forge.observations for eds_derived.
        cadence_hours: Expected sync cadence (default 6h).
    """
    now = datetime.now(UTC)
    severity = "green"
    fields: dict[str, object] = {
        "total_observations": total_observations,
        "dead_letter_24h": dead_letter_24h,
        "promoted_metric_count": promoted_metric_count,
        "metrics_with_data": metrics_with_data,
    }

    if last_event is None:
        return HealthResult(severity="red", fields={**fields, "reason": "no_sync_events"})

    completed_at = last_event["completed_at"]
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=UTC)

    age_hours = (now - completed_at).total_seconds() / 3600
    fields["last_sync_at"] = completed_at.isoformat()
    fields["last_sync_age_hours"] = round(age_hours, 1)
    fields["last_sync_status"] = last_event["status"]
    fields["last_observations_written"] = last_event.get("observations_written", 0)

    # Coverage check
    coverage_pct = (metrics_with_data / promoted_metric_count * 100) if promoted_metric_count > 0 else 0
    fields["metric_coverage_pct"] = round(coverage_pct, 1)

    # Severity rules (v4.0 §Solo Operator Operations)
    if age_hours > cadence_hours * 2:
        severity = "red"
        fields["reason"] = "sync_stale"
    elif last_event["status"] == "failed":
        severity = "red"
        fields["reason"] = "last_sync_failed"
    elif dead_letter_24h > 10:
        severity = "yellow"
        fields["reason"] = "dead_letter_spike"
    elif coverage_pct < 80:
        severity = "yellow"
        fields["reason"] = "low_metric_coverage"
    elif age_hours > cadence_hours:
        severity = "yellow"
        fields["reason"] = "sync_approaching_stale"

    return HealthResult(severity=severity, fields=fields)


# --- Export health ---


def check_export_health(
    last_export_at: datetime | None,
    rows_exported_last_run: int | None,
    merge_lag_seconds: float,
    unmerged_parts: int,
    gold_snapshot_count: int,
    consecutive_failures: int = 0,
) -> HealthResult:
    """Evaluate Silver→Gold export health.

    Args:
        last_export_at: Timestamp of last successful gold_observations materialization.
        rows_exported_last_run: Rows exported in last run (None if no history).
        merge_lag_seconds: Current ClickHouse merge lag for forge.observations.
        unmerged_parts: Number of unmerged parts in forge.observations.
        gold_snapshot_count: Number of Iceberg snapshots in gold table.
        consecutive_failures: Number of consecutive failed export runs.
    """
    now = datetime.now(UTC)
    severity = "green"
    fields: dict[str, object] = {
        "merge_lag_seconds": round(merge_lag_seconds, 1),
        "unmerged_parts": unmerged_parts,
        "gold_snapshot_count": gold_snapshot_count,
        "consecutive_failures": consecutive_failures,
    }

    if last_export_at is not None:
        if last_export_at.tzinfo is None:
            last_export_at = last_export_at.replace(tzinfo=UTC)
        age_hours = (now - last_export_at).total_seconds() / 3600
        fields["last_export_at"] = last_export_at.isoformat()
        fields["last_export_age_hours"] = round(age_hours, 1)
        fields["rows_exported_last_run"] = rows_exported_last_run
    else:
        age_hours = None
        fields["last_export_at"] = None
        fields["rows_exported_last_run"] = None

    # Red alert triggers (v4.0 §FTB red alert triggers)
    if consecutive_failures >= 3:
        severity = "red"
        fields["reason"] = "3_consecutive_failures"
    elif merge_lag_seconds > 600:
        severity = "red"
        fields["reason"] = "merge_lag_critical"
    elif age_hours is not None and age_hours > 4:
        severity = "red"
        fields["reason"] = "export_stale"
    # Yellow thresholds
    elif merge_lag_seconds > 300:
        severity = "yellow"
        fields["reason"] = "merge_lag_warning"
    elif unmerged_parts > 50:
        severity = "yellow"
        fields["reason"] = "unmerged_parts_high"
    elif age_hours is not None and age_hours > 2:
        severity = "yellow"
        fields["reason"] = "export_approaching_stale"
    elif gold_snapshot_count > 100:
        severity = "yellow"
        fields["reason"] = "gold_needs_compaction"
    elif rows_exported_last_run == 0 and age_hours is not None and age_hours > 1:
        severity = "yellow"
        fields["reason"] = "zero_rows_exported"

    return HealthResult(severity=severity, fields=fields)


# --- Adapter health (per-source) ---


def check_source_health(
    source_id: str,
    last_observation_at: datetime | None,
    observations_24h: int,
    dead_letter_24h: int,
    metric_ids_observed: int,
    metric_ids_expected: int,
    instrument_ids_observed: int,
    instrument_ids_expected: int,
    cadence_hours: float,
) -> HealthResult:
    """Evaluate per-source adapter health.

    Args:
        source_id: Source being checked.
        last_observation_at: MAX(observed_at) for this source.
        observations_24h: Observation count in last 24h.
        dead_letter_24h: Dead letter count in last 24h.
        metric_ids_observed: Distinct metric_ids with data for this source.
        metric_ids_expected: Metrics in catalog listing this source.
        instrument_ids_observed: Distinct instrument_ids with data.
        instrument_ids_expected: Instruments mapped to this source.
        cadence_hours: Expected collection cadence from source_catalog.
    """
    now = datetime.now(UTC)
    severity = "green"
    fields: dict[str, object] = {
        "source_id": source_id,
        "observations_24h": observations_24h,
        "dead_letter_24h": dead_letter_24h,
        "metric_ids_observed": metric_ids_observed,
        "metric_ids_expected": metric_ids_expected,
        "instrument_ids_observed": instrument_ids_observed,
        "instrument_ids_expected": instrument_ids_expected,
    }

    if last_observation_at is not None:
        if last_observation_at.tzinfo is None:
            last_observation_at = last_observation_at.replace(tzinfo=UTC)
        age_hours = (now - last_observation_at).total_seconds() / 3600
        fields["last_observation_at"] = last_observation_at.isoformat()
        fields["last_observation_age_hours"] = round(age_hours, 1)
    else:
        age_hours = None
        fields["last_observation_at"] = None
        fields["last_observation_age_hours"] = None

    # Coverage
    metric_coverage = (
        (metric_ids_observed / metric_ids_expected * 100)
        if metric_ids_expected > 0
        else 100  # No expected metrics = nothing to miss
    )
    instrument_coverage = (
        (instrument_ids_observed / instrument_ids_expected * 100)
        if instrument_ids_expected > 0
        else 100  # Sources without instrument requirements are fully covered
    )
    fields["metric_coverage_pct"] = round(metric_coverage, 1)
    fields["instrument_coverage_pct"] = round(instrument_coverage, 1)

    # Severity rules (v4.0 §adapter_health)
    if age_hours is not None and age_hours > cadence_hours * 2:
        severity = "red"
        fields["reason"] = "data_stale"
    elif observations_24h == 0 and age_hours is not None:
        severity = "red"
        fields["reason"] = "zero_observations_24h"
    elif dead_letter_24h > 10:
        severity = "yellow"
        fields["reason"] = "dead_letter_spike"
    elif metric_coverage < 80:
        severity = "yellow"
        fields["reason"] = "low_metric_coverage"
    elif instrument_coverage < 80:
        severity = "yellow"
        fields["reason"] = "low_instrument_coverage"
    elif age_hours is not None and age_hours > cadence_hours:
        severity = "yellow"
        fields["reason"] = "approaching_stale"
    elif age_hours is None:
        # No data at all — source not yet collecting
        severity = "yellow"
        fields["reason"] = "no_data"

    return HealthResult(severity=severity, fields=fields)
