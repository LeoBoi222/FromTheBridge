"""Tests for bronze_expiry_audit asset logic."""

from datetime import date, timedelta
from unittest.mock import MagicMock

import pyarrow as pa
import pytest

from ftb.archive.audit_asset import find_at_risk_partitions


class TestFindAtRiskPartitions:
    def test_no_hot_partitions_returns_empty(self):
        mock_catalog = MagicMock()
        mock_table = MagicMock()
        mock_catalog.load_table.return_value = mock_table

        mock_scan = MagicMock()
        mock_table.scan.return_value = mock_scan
        mock_scan.to_arrow_batch_reader.return_value = iter([])

        mock_conn = MagicMock()
        result = find_at_risk_partitions(mock_catalog, mock_conn, date(2026, 3, 10))
        assert result == []

    def test_all_archived_returns_empty(self):
        mock_catalog = MagicMock()
        mock_table = MagicMock()
        mock_catalog.load_table.return_value = mock_table

        # One old partition in hot
        batch = pa.RecordBatch.from_pydict({
            "source_id": ["tiingo"],
            "metric_id": ["price.spot.close_usd"],
            "partition_date": ["2025-12-10"],
        })
        mock_scan = MagicMock()
        mock_table.scan.return_value = mock_scan
        mock_scan.to_arrow_batch_reader.return_value = iter([batch])

        # Same partition is archived
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = [("tiingo", "price.spot.close_usd", "2025-12-10")]

        result = find_at_risk_partitions(mock_catalog, mock_conn, date(2026, 3, 10))
        assert result == []

    def test_unarchived_partition_is_at_risk(self):
        mock_catalog = MagicMock()
        mock_table = MagicMock()
        mock_catalog.load_table.return_value = mock_table

        # Old partition in hot
        batch = pa.RecordBatch.from_pydict({
            "source_id": ["tiingo"],
            "metric_id": ["price.spot.close_usd"],
            "partition_date": ["2025-12-10"],
        })
        mock_scan = MagicMock()
        mock_table.scan.return_value = mock_scan
        mock_scan.to_arrow_batch_reader.return_value = iter([batch])

        # Nothing archived
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = []

        result = find_at_risk_partitions(mock_catalog, mock_conn, date(2026, 3, 10))
        assert len(result) == 1
        assert result[0]["source_id"] == "tiingo"
        assert result[0]["metric_id"] == "price.spot.close_usd"
        assert result[0]["partition_date"] == "2025-12-10"

    def test_mixed_archived_and_unarchived(self):
        mock_catalog = MagicMock()
        mock_table = MagicMock()
        mock_catalog.load_table.return_value = mock_table

        batch = pa.RecordBatch.from_pydict({
            "source_id": ["tiingo", "fred"],
            "metric_id": ["price.spot.close_usd", "macro.rates.fed_funds"],
            "partition_date": ["2025-12-10", "2025-12-11"],
        })
        mock_scan = MagicMock()
        mock_table.scan.return_value = mock_scan
        mock_scan.to_arrow_batch_reader.return_value = iter([batch])

        # Only tiingo is archived
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = [("tiingo", "price.spot.close_usd", "2025-12-10")]

        result = find_at_risk_partitions(mock_catalog, mock_conn, date(2026, 3, 10))
        assert len(result) == 1
        assert result[0]["source_id"] == "fred"
