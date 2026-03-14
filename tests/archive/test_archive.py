"""Tests for bronze_cold_archive asset logic."""

import hashlib
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pyarrow as pa
import pytest

from ftb.archive.archive_asset import (
    _arrow_ipc_bytes,
    archive_partition,
    compute_archive_window,
    discover_hot_partitions,
    log_archive_result,
    verify_archive_checksum,
)


class TestComputeArchiveWindow:
    def test_standard_window(self):
        today = date(2026, 3, 10)
        start, end = compute_archive_window(today)
        assert start == date(2026, 3, 1)
        assert end == date(2026, 3, 8)

    def test_window_crosses_month_boundary(self):
        today = date(2026, 3, 5)
        start, end = compute_archive_window(today)
        assert start == date(2026, 2, 24)
        assert end == date(2026, 3, 3)

    def test_window_width_is_8_days(self):
        today = date(2026, 6, 15)
        start, end = compute_archive_window(today)
        assert (end - start).days == 7  # inclusive range = 8 days


class TestDiscoverHotPartitions:
    def test_empty_table_returns_empty(self):
        mock_catalog = MagicMock()
        mock_table = MagicMock()
        mock_catalog.load_table.return_value = mock_table

        # Empty scan
        mock_scan = MagicMock()
        mock_table.scan.return_value = mock_scan
        mock_scan.to_arrow_batch_reader.return_value = iter([])

        result = discover_hot_partitions(
            mock_catalog, "bronze.observations_hot",
            date(2026, 3, 1), date(2026, 3, 8),
        )
        assert result == []

    def test_groups_by_partition_key(self):
        mock_catalog = MagicMock()
        mock_table = MagicMock()
        mock_catalog.load_table.return_value = mock_table

        # Create a batch with 3 rows, 2 in same partition
        batch = pa.RecordBatch.from_pydict({
            "source_id": ["tiingo", "tiingo", "fred"],
            "metric_id": ["price.spot.close_usd", "price.spot.close_usd", "macro.rates.fed_funds"],
            "partition_date": ["2026-03-01", "2026-03-01", "2026-03-02"],
        })

        mock_scan = MagicMock()
        mock_table.scan.return_value = mock_scan
        mock_scan.to_arrow_batch_reader.return_value = iter([batch])

        result = discover_hot_partitions(
            mock_catalog, "bronze.observations_hot",
            date(2026, 3, 1), date(2026, 3, 8),
        )
        assert len(result) == 2
        # Check the grouped counts
        by_key = {(r["source_id"], r["metric_id"], r["partition_date"]): r["row_count"] for r in result}
        assert by_key[("tiingo", "price.spot.close_usd", "2026-03-01")] == 2
        assert by_key[("fred", "macro.rates.fed_funds", "2026-03-02")] == 1


class TestArchivePartition:
    def test_empty_scan_returns_skipped(self):
        mock_hot = MagicMock()
        mock_archive = MagicMock()
        mock_table = MagicMock()
        mock_hot.load_table.return_value = mock_table
        mock_archive.load_table.return_value = MagicMock()

        # Empty scan result
        mock_scan = MagicMock()
        mock_table.scan.return_value = mock_scan
        mock_scan.to_arrow.return_value = pa.table({"source_id": pa.array([], type=pa.string())})
        mock_scan.to_arrow.return_value = pa.table({
            "source_id": pa.array([], type=pa.string()),
            "metric_id": pa.array([], type=pa.string()),
            "instrument_id": pa.array([], type=pa.string()),
            "observed_at": pa.array([], type=pa.timestamp("us", tz="UTC")),
            "value": pa.array([], type=pa.float64()),
            "ingested_at": pa.array([], type=pa.timestamp("us", tz="UTC")),
            "partition_date": pa.array([], type=pa.string()),
        })

        result = archive_partition(mock_hot, mock_archive, "tiingo", "price", "2026-03-01")
        assert result["row_count"] == 0
        assert result["skipped"] is True

    def test_archive_writes_and_returns_metadata(self):
        mock_hot = MagicMock()
        mock_archive = MagicMock()
        mock_hot_table = MagicMock()
        mock_archive_table = MagicMock()
        mock_hot.load_table.return_value = mock_hot_table
        mock_archive.load_table.return_value = mock_archive_table

        now = datetime.now(timezone.utc)
        arrow_table = pa.table({
            "source_id": ["tiingo"],
            "metric_id": ["price.spot.close_usd"],
            "instrument_id": ["BTC-USD"],
            "observed_at": pa.array([now], type=pa.timestamp("us", tz="UTC")),
            "value": [48000.0],
            "ingested_at": pa.array([now], type=pa.timestamp("us", tz="UTC")),
            "partition_date": ["2026-03-01"],
        })

        mock_scan = MagicMock()
        mock_hot_table.scan.return_value = mock_scan
        mock_scan.to_arrow.return_value = arrow_table

        result = archive_partition(mock_hot, mock_archive, "tiingo", "price.spot.close_usd", "2026-03-01")
        assert result["row_count"] == 1
        assert result["skipped"] is False
        assert len(result["checksum"]) == 64  # SHA-256
        assert result["observed_at_min"] is not None
        assert result["observed_at_max"] is not None
        mock_archive_table.append.assert_called_once()


class TestLogArchiveResult:
    def test_skipped_does_nothing(self):
        mock_conn = MagicMock()
        log_archive_result(mock_conn, "s", "m", "2026-03-01", {"skipped": True}, "run1")
        mock_conn.cursor.assert_not_called()

    def test_inserts_archive_log(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        now = datetime.now(timezone.utc)
        meta = {
            "archive_path": "s3://bronze-archive/tiingo/2026-03-01/price/",
            "byte_size": 1024,
            "row_count": 10,
            "observed_at_min": now,
            "observed_at_max": now,
            "checksum": "abc123",
            "skipped": False,
        }

        log_archive_result(mock_conn, "tiingo", "price.spot.close_usd", "2026-03-01", meta, "run-123")
        mock_cursor.execute.assert_called_once()
        sql = mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO forge.bronze_archive_log" in sql
        assert "ON CONFLICT" in sql
        mock_conn.commit.assert_called_once()


class TestVerifyArchiveChecksum:
    def _make_arrow_table(self):
        now = datetime.now(timezone.utc)
        return pa.table({
            "source_id": ["tiingo"],
            "metric_id": ["price.spot.close_usd"],
            "instrument_id": ["BTC-USD"],
            "observed_at": pa.array([now], type=pa.timestamp("us", tz="UTC")),
            "value": [48000.0],
            "ingested_at": pa.array([now], type=pa.timestamp("us", tz="UTC")),
            "partition_date": ["2026-03-01"],
        })

    def test_checksum_match_updates_verified_true(self):
        arrow_table = self._make_arrow_table()
        expected_checksum = hashlib.sha256(_arrow_ipc_bytes(arrow_table)).hexdigest()

        mock_catalog = MagicMock()
        mock_archive_table = MagicMock()
        mock_catalog.load_table.return_value = mock_archive_table
        mock_scan = MagicMock()
        mock_archive_table.scan.return_value = mock_scan
        mock_scan.to_arrow.return_value = arrow_table

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = verify_archive_checksum(
            mock_catalog, mock_conn,
            "tiingo", "price.spot.close_usd", "2026-03-01",
            expected_checksum,
        )
        assert result is True
        # Verify UPDATE was called with True
        sql = mock_cursor.execute.call_args[0][0]
        assert "UPDATE forge.bronze_archive_log" in sql
        assert "checksum_verified" in sql
        params = mock_cursor.execute.call_args[0][1]
        assert params[0] is True  # checksum_verified = True

    def test_checksum_mismatch_updates_verified_false(self):
        arrow_table = self._make_arrow_table()

        mock_catalog = MagicMock()
        mock_archive_table = MagicMock()
        mock_catalog.load_table.return_value = mock_archive_table
        mock_scan = MagicMock()
        mock_archive_table.scan.return_value = mock_scan
        mock_scan.to_arrow.return_value = arrow_table

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = verify_archive_checksum(
            mock_catalog, mock_conn,
            "tiingo", "price.spot.close_usd", "2026-03-01",
            "wrong_checksum_value",
        )
        assert result is False
        params = mock_cursor.execute.call_args[0][1]
        assert params[0] is False  # checksum_verified = False

    def test_empty_readback_returns_false(self):
        mock_catalog = MagicMock()
        mock_archive_table = MagicMock()
        mock_catalog.load_table.return_value = mock_archive_table
        mock_scan = MagicMock()
        mock_archive_table.scan.return_value = mock_scan
        mock_scan.to_arrow.return_value = pa.table({
            "source_id": pa.array([], type=pa.string()),
            "metric_id": pa.array([], type=pa.string()),
            "instrument_id": pa.array([], type=pa.string()),
            "observed_at": pa.array([], type=pa.timestamp("us", tz="UTC")),
            "value": pa.array([], type=pa.float64()),
            "ingested_at": pa.array([], type=pa.timestamp("us", tz="UTC")),
            "partition_date": pa.array([], type=pa.string()),
        })

        mock_conn = MagicMock()
        result = verify_archive_checksum(
            mock_catalog, mock_conn,
            "tiingo", "price.spot.close_usd", "2026-03-01",
            "some_checksum",
        )
        assert result is False
        mock_conn.cursor.assert_not_called()


class TestArrowIpcBytes:
    def test_deterministic_output(self):
        now = datetime.now(timezone.utc)
        table = pa.table({
            "source_id": ["tiingo"],
            "observed_at": pa.array([now], type=pa.timestamp("us", tz="UTC")),
        })
        b1 = _arrow_ipc_bytes(table)
        b2 = _arrow_ipc_bytes(table)
        assert b1 == b2
        assert len(b1) > 0
