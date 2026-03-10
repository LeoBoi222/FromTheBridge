"""Tests for Gold Iceberg table management — create, read partition, overwrite."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pyarrow as pa

from ftb.export.gold_export import GOLD_ARROW_SCHEMA
from ftb.export.gold_iceberg import (
    GOLD_TABLE_NAME,
    ensure_gold_table,
    overwrite_partition,
    read_partition,
)


def _empty_gold_table():
    return pa.table(
        {f.name: pa.array([], type=f.type) for f in GOLD_ARROW_SCHEMA},
        schema=GOLD_ARROW_SCHEMA,
    )


def _sample_gold_table(n=1):
    ts = datetime(2026, 3, 10, tzinfo=UTC)
    ing = datetime(2026, 3, 10, 0, 5, tzinfo=UTC)
    return pa.table({
        "metric_id": ["m1"] * n,
        "instrument_id": [None] * n,
        "observed_at": [ts] * n,
        "value": [1.0] * n,
        "data_version": [1] * n,
        "ingested_at": [ing] * n,
        "metric_domain": ["macro"] * n,
        "year_month": ["2026-03"] * n,
    })


class TestEnsureGoldTable:
    def test_creates_table_if_not_exists(self):
        mock_catalog = MagicMock()
        mock_catalog.load_table.side_effect = Exception("not found")
        mock_catalog.create_table.return_value = MagicMock()

        ensure_gold_table(mock_catalog)
        mock_catalog.create_table.assert_called_once()

    def test_returns_existing_table(self):
        mock_catalog = MagicMock()
        mock_table = MagicMock()
        mock_catalog.load_table.return_value = mock_table

        result = ensure_gold_table(mock_catalog)
        assert result == mock_table
        mock_catalog.create_table.assert_not_called()


class TestReadPartition:
    def test_returns_none_for_empty(self):
        mock_catalog = MagicMock()
        mock_table = MagicMock()
        mock_catalog.load_table.return_value = mock_table
        mock_scan = MagicMock()
        mock_table.scan.return_value = mock_scan
        mock_scan.to_arrow.return_value = _empty_gold_table()

        result = read_partition(mock_catalog, "2026-03", "macro")
        assert result is None

    def test_returns_arrow_table_for_data(self):
        mock_catalog = MagicMock()
        mock_table = MagicMock()
        mock_catalog.load_table.return_value = mock_table
        mock_scan = MagicMock()
        mock_table.scan.return_value = mock_scan
        mock_scan.to_arrow.return_value = _sample_gold_table()

        result = read_partition(mock_catalog, "2026-03", "macro")
        assert result.num_rows == 1


class TestOverwritePartition:
    def test_calls_overwrite_with_filter(self):
        mock_catalog = MagicMock()
        mock_table = MagicMock()
        mock_catalog.load_table.return_value = mock_table

        data = _sample_gold_table()
        overwrite_partition(mock_catalog, data, "2026-03", "macro")
        mock_table.overwrite.assert_called_once()

    def test_overwrite_passes_data(self):
        mock_catalog = MagicMock()
        mock_table = MagicMock()
        mock_catalog.load_table.return_value = mock_table

        data = _sample_gold_table(3)
        overwrite_partition(mock_catalog, data, "2026-03", "macro")
        call_args = mock_table.overwrite.call_args
        assert call_args[0][0].num_rows == 3


class TestGoldTableName:
    def test_table_name(self):
        assert GOLD_TABLE_NAME == "gold.observations"
