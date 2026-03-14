"""Tests for bronze_expiry_audit asset logic."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from ftb.archive.audit_asset import find_at_risk_partitions


class TestFindAtRiskPartitions:
    """Tests for find_at_risk_partitions using DuckDB partition discovery."""

    @patch("ftb.archive.audit_asset.discover_partitions_duckdb")
    def test_no_hot_partitions_returns_empty(self, mock_discover):
        mock_discover.return_value = ([], 5.0)
        mock_conn = MagicMock()

        result, elapsed = find_at_risk_partitions(MagicMock(), mock_conn, date(2026, 3, 10))
        assert result == []
        assert elapsed == 5.0

    @patch("ftb.archive.audit_asset.discover_partitions_duckdb")
    def test_all_archived_returns_empty(self, mock_discover):
        mock_discover.return_value = ([
            {"source_id": "tiingo", "metric_id": "price.spot.close_usd", "partition_date": "2025-12-10"},
        ], 3.2)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = [("tiingo", "price.spot.close_usd", "2025-12-10")]

        result, elapsed = find_at_risk_partitions(MagicMock(), mock_conn, date(2026, 3, 10))
        assert result == []
        assert elapsed == 3.2

    @patch("ftb.archive.audit_asset.discover_partitions_duckdb")
    def test_unarchived_partition_is_at_risk(self, mock_discover):
        mock_discover.return_value = ([
            {"source_id": "tiingo", "metric_id": "price.spot.close_usd", "partition_date": "2025-12-10"},
        ], 2.1)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = []

        result, elapsed = find_at_risk_partitions(MagicMock(), mock_conn, date(2026, 3, 10))
        assert len(result) == 1
        assert result[0]["source_id"] == "tiingo"
        assert result[0]["metric_id"] == "price.spot.close_usd"
        assert result[0]["partition_date"] == "2025-12-10"

    @patch("ftb.archive.audit_asset.discover_partitions_duckdb")
    def test_mixed_archived_and_unarchived(self, mock_discover):
        mock_discover.return_value = ([
            {"source_id": "tiingo", "metric_id": "price.spot.close_usd", "partition_date": "2025-12-10"},
            {"source_id": "fred", "metric_id": "macro.rates.fed_funds", "partition_date": "2025-12-11"},
        ], 4.0)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = [("tiingo", "price.spot.close_usd", "2025-12-10")]

        result, _ = find_at_risk_partitions(MagicMock(), mock_conn, date(2026, 3, 10))
        assert len(result) == 1
        assert result[0]["source_id"] == "fred"

    @patch("ftb.archive.audit_asset.discover_partitions_duckdb")
    def test_passes_correct_filter_to_duckdb(self, mock_discover):
        mock_discover.return_value = ([], 1.0)
        mock_conn = MagicMock()

        find_at_risk_partitions(MagicMock(), mock_conn, date(2026, 3, 10))

        # Cutoff = 2026-03-10 - 85 days = 2025-12-15
        call_args = mock_discover.call_args
        assert "2025-12-15" in call_args.kwargs.get("partition_date_filter", call_args[0][2] if len(call_args[0]) > 2 else "")
