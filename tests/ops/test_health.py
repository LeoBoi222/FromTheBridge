"""Tests for ftb.ops.health — pure health check logic."""
from datetime import UTC, datetime, timedelta

from ftb.ops.health import (
    HealthResult,
    check_export_health,
    check_source_health,
    check_sync_health,
)

NOW = datetime.now(UTC)


class TestCheckSyncHealth:
    """Tests for check_sync_health."""

    def test_green_healthy_sync(self):
        result = check_sync_health(
            last_event={
                "completed_at": NOW - timedelta(hours=1),
                "status": "completed",
                "observations_written": 100,
            },
            dead_letter_24h=0,
            total_observations=2000,
            promoted_metric_count=10,
            metrics_with_data=10,
        )
        assert result.severity == "green"

    def test_red_no_events(self):
        result = check_sync_health(
            last_event=None,
            dead_letter_24h=0,
            total_observations=0,
            promoted_metric_count=5,
            metrics_with_data=0,
        )
        assert result.severity == "red"
        assert result.fields["reason"] == "no_sync_events"

    def test_red_stale_sync(self):
        result = check_sync_health(
            last_event={
                "completed_at": NOW - timedelta(hours=13),
                "status": "completed",
                "observations_written": 100,
            },
            dead_letter_24h=0,
            total_observations=2000,
            promoted_metric_count=10,
            metrics_with_data=10,
        )
        assert result.severity == "red"
        assert result.fields["reason"] == "sync_stale"

    def test_red_failed_sync(self):
        result = check_sync_health(
            last_event={
                "completed_at": NOW - timedelta(hours=1),
                "status": "failed",
                "observations_written": 0,
            },
            dead_letter_24h=0,
            total_observations=2000,
            promoted_metric_count=10,
            metrics_with_data=10,
        )
        assert result.severity == "red"
        assert result.fields["reason"] == "last_sync_failed"

    def test_yellow_dead_letter_spike(self):
        result = check_sync_health(
            last_event={
                "completed_at": NOW - timedelta(hours=1),
                "status": "completed",
                "observations_written": 100,
            },
            dead_letter_24h=15,
            total_observations=2000,
            promoted_metric_count=10,
            metrics_with_data=10,
        )
        assert result.severity == "yellow"
        assert result.fields["reason"] == "dead_letter_spike"

    def test_yellow_low_coverage(self):
        result = check_sync_health(
            last_event={
                "completed_at": NOW - timedelta(hours=1),
                "status": "completed",
                "observations_written": 100,
            },
            dead_letter_24h=0,
            total_observations=2000,
            promoted_metric_count=10,
            metrics_with_data=5,
        )
        assert result.severity == "yellow"
        assert result.fields["reason"] == "low_metric_coverage"

    def test_yellow_approaching_stale(self):
        result = check_sync_health(
            last_event={
                "completed_at": NOW - timedelta(hours=7),
                "status": "completed",
                "observations_written": 100,
            },
            dead_letter_24h=0,
            total_observations=2000,
            promoted_metric_count=10,
            metrics_with_data=10,
        )
        assert result.severity == "yellow"
        assert result.fields["reason"] == "sync_approaching_stale"

    def test_naive_datetime_handling(self):
        """CH returns naive datetimes — should handle without error."""
        naive_time = datetime(2026, 3, 10, 12, 0, 0)  # no tzinfo
        result = check_sync_health(
            last_event={
                "completed_at": naive_time,
                "status": "completed",
                "observations_written": 100,
            },
            dead_letter_24h=0,
            total_observations=2000,
            promoted_metric_count=10,
            metrics_with_data=10,
        )
        # Should not crash — severity depends on age
        assert result.severity in ("green", "yellow", "red")

    def test_custom_cadence(self):
        result = check_sync_health(
            last_event={
                "completed_at": NOW - timedelta(hours=3),
                "status": "completed",
                "observations_written": 100,
            },
            dead_letter_24h=0,
            total_observations=2000,
            promoted_metric_count=10,
            metrics_with_data=10,
            cadence_hours=1.0,  # 1h cadence → 3h = stale
        )
        assert result.severity == "red"

    def test_to_metadata(self):
        result = check_sync_health(
            last_event={
                "completed_at": NOW - timedelta(hours=1),
                "status": "completed",
                "observations_written": 100,
            },
            dead_letter_24h=0,
            total_observations=2000,
            promoted_metric_count=10,
            metrics_with_data=10,
        )
        meta = result.to_metadata()
        assert "severity" in meta
        assert "total_observations" in meta


class TestCheckExportHealth:
    """Tests for check_export_health."""

    def test_green_healthy(self):
        result = check_export_health(
            last_export_at=NOW - timedelta(minutes=30),
            rows_exported_last_run=100,
            merge_lag_seconds=5.0,
            unmerged_parts=10,
            gold_snapshot_count=5,
        )
        assert result.severity == "green"

    def test_red_consecutive_failures(self):
        result = check_export_health(
            last_export_at=NOW - timedelta(hours=1),
            rows_exported_last_run=100,
            merge_lag_seconds=5.0,
            unmerged_parts=10,
            gold_snapshot_count=5,
            consecutive_failures=3,
        )
        assert result.severity == "red"
        assert result.fields["reason"] == "3_consecutive_failures"

    def test_red_merge_lag_critical(self):
        result = check_export_health(
            last_export_at=NOW - timedelta(minutes=30),
            rows_exported_last_run=100,
            merge_lag_seconds=650.0,
            unmerged_parts=10,
            gold_snapshot_count=5,
        )
        assert result.severity == "red"
        assert result.fields["reason"] == "merge_lag_critical"

    def test_red_export_stale(self):
        result = check_export_health(
            last_export_at=NOW - timedelta(hours=5),
            rows_exported_last_run=100,
            merge_lag_seconds=5.0,
            unmerged_parts=10,
            gold_snapshot_count=5,
        )
        assert result.severity == "red"
        assert result.fields["reason"] == "export_stale"

    def test_yellow_merge_lag_warning(self):
        result = check_export_health(
            last_export_at=NOW - timedelta(minutes=30),
            rows_exported_last_run=100,
            merge_lag_seconds=350.0,
            unmerged_parts=10,
            gold_snapshot_count=5,
        )
        assert result.severity == "yellow"
        assert result.fields["reason"] == "merge_lag_warning"

    def test_yellow_unmerged_parts(self):
        result = check_export_health(
            last_export_at=NOW - timedelta(minutes=30),
            rows_exported_last_run=100,
            merge_lag_seconds=5.0,
            unmerged_parts=55,
            gold_snapshot_count=5,
        )
        assert result.severity == "yellow"
        assert result.fields["reason"] == "unmerged_parts_high"

    def test_yellow_approaching_stale(self):
        result = check_export_health(
            last_export_at=NOW - timedelta(hours=3),
            rows_exported_last_run=100,
            merge_lag_seconds=5.0,
            unmerged_parts=10,
            gold_snapshot_count=5,
        )
        assert result.severity == "yellow"
        assert result.fields["reason"] == "export_approaching_stale"

    def test_yellow_needs_compaction(self):
        result = check_export_health(
            last_export_at=NOW - timedelta(minutes=30),
            rows_exported_last_run=100,
            merge_lag_seconds=5.0,
            unmerged_parts=10,
            gold_snapshot_count=150,
        )
        assert result.severity == "yellow"
        assert result.fields["reason"] == "gold_needs_compaction"

    def test_no_export_history(self):
        result = check_export_health(
            last_export_at=None,
            rows_exported_last_run=None,
            merge_lag_seconds=0.0,
            unmerged_parts=5,
            gold_snapshot_count=0,
        )
        assert result.severity == "green"  # No history is OK for first boot
        assert result.fields["last_export_at"] is None


class TestCheckSourceHealth:
    """Tests for check_source_health."""

    def test_green_healthy_source(self):
        result = check_source_health(
            source_id="fred",
            last_observation_at=NOW - timedelta(hours=12),
            observations_24h=50,
            dead_letter_24h=0,
            metric_ids_observed=10,
            metric_ids_expected=10,
            instrument_ids_observed=0,
            instrument_ids_expected=0,
            cadence_hours=24.0,
        )
        assert result.severity == "green"

    def test_red_data_stale(self):
        result = check_source_health(
            source_id="coinalyze",
            last_observation_at=NOW - timedelta(hours=20),
            observations_24h=0,
            dead_letter_24h=0,
            metric_ids_observed=7,
            metric_ids_expected=7,
            instrument_ids_observed=4,
            instrument_ids_expected=4,
            cadence_hours=8.0,
        )
        assert result.severity == "red"
        assert result.fields["reason"] == "data_stale"

    def test_red_zero_observations(self):
        result = check_source_health(
            source_id="coinalyze",
            last_observation_at=NOW - timedelta(hours=5),
            observations_24h=0,
            dead_letter_24h=0,
            metric_ids_observed=7,
            metric_ids_expected=7,
            instrument_ids_observed=4,
            instrument_ids_expected=4,
            cadence_hours=8.0,
        )
        assert result.severity == "red"
        assert result.fields["reason"] == "zero_observations_24h"

    def test_yellow_dead_letter_spike(self):
        result = check_source_health(
            source_id="fred",
            last_observation_at=NOW - timedelta(hours=12),
            observations_24h=50,
            dead_letter_24h=15,
            metric_ids_observed=10,
            metric_ids_expected=10,
            instrument_ids_observed=0,
            instrument_ids_expected=0,
            cadence_hours=24.0,
        )
        assert result.severity == "yellow"
        assert result.fields["reason"] == "dead_letter_spike"

    def test_yellow_low_metric_coverage(self):
        result = check_source_health(
            source_id="fred",
            last_observation_at=NOW - timedelta(hours=12),
            observations_24h=50,
            dead_letter_24h=0,
            metric_ids_observed=5,
            metric_ids_expected=10,
            instrument_ids_observed=0,
            instrument_ids_expected=0,
            cadence_hours=24.0,
        )
        assert result.severity == "yellow"
        assert result.fields["reason"] == "low_metric_coverage"

    def test_yellow_low_instrument_coverage(self):
        result = check_source_health(
            source_id="coinalyze",
            last_observation_at=NOW - timedelta(hours=5),
            observations_24h=50,
            dead_letter_24h=0,
            metric_ids_observed=7,
            metric_ids_expected=7,
            instrument_ids_observed=2,
            instrument_ids_expected=4,
            cadence_hours=8.0,
        )
        assert result.severity == "yellow"
        assert result.fields["reason"] == "low_instrument_coverage"

    def test_yellow_no_data_yet_with_expectations(self):
        """Source in catalog with expected metrics but no data — low coverage."""
        result = check_source_health(
            source_id="sosovalue",
            last_observation_at=None,
            observations_24h=0,
            dead_letter_24h=0,
            metric_ids_observed=0,
            metric_ids_expected=2,
            instrument_ids_observed=0,
            instrument_ids_expected=0,
            cadence_hours=24.0,
        )
        assert result.severity == "yellow"
        assert result.fields["reason"] == "low_metric_coverage"

    def test_yellow_no_data_no_expectations(self):
        """Source in catalog with no metrics assigned yet — no_data."""
        result = check_source_health(
            source_id="sosovalue",
            last_observation_at=None,
            observations_24h=0,
            dead_letter_24h=0,
            metric_ids_observed=0,
            metric_ids_expected=0,
            instrument_ids_observed=0,
            instrument_ids_expected=0,
            cadence_hours=24.0,
        )
        assert result.severity == "yellow"
        assert result.fields["reason"] == "no_data"

    def test_no_instrument_requirement(self):
        """Sources without instrument_source_map get 100% instrument coverage."""
        result = check_source_health(
            source_id="fred",
            last_observation_at=NOW - timedelta(hours=12),
            observations_24h=50,
            dead_letter_24h=0,
            metric_ids_observed=10,
            metric_ids_expected=10,
            instrument_ids_observed=0,
            instrument_ids_expected=0,
            cadence_hours=24.0,
        )
        assert result.fields["instrument_coverage_pct"] == 100

    def test_coverage_percentages(self):
        result = check_source_health(
            source_id="coinalyze",
            last_observation_at=NOW - timedelta(hours=2),
            observations_24h=100,
            dead_letter_24h=0,
            metric_ids_observed=6,
            metric_ids_expected=8,
            instrument_ids_observed=3,
            instrument_ids_expected=4,
            cadence_hours=8.0,
        )
        assert result.fields["metric_coverage_pct"] == 75.0
        assert result.fields["instrument_coverage_pct"] == 75.0

    def test_naive_datetime(self):
        """CH returns naive datetimes — should normalize to UTC."""
        naive = datetime(2026, 3, 10, 12, 0, 0)
        result = check_source_health(
            source_id="test",
            last_observation_at=naive,
            observations_24h=10,
            dead_letter_24h=0,
            metric_ids_observed=5,
            metric_ids_expected=5,
            instrument_ids_observed=0,
            instrument_ids_expected=0,
            cadence_hours=24.0,
        )
        assert result.severity in ("green", "yellow", "red")


class TestHealthResult:
    """Tests for HealthResult dataclass."""

    def test_to_metadata(self):
        hr = HealthResult(severity="green", fields={"count": 42, "name": "test"})
        meta = hr.to_metadata()
        assert meta["severity"] == "green"
        assert meta["count"] == 42
        assert meta["name"] == "test"
