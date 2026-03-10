"""Tests for Bronze writer — Iceberg tables on MinIO via PyIceberg."""

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from ftb.writers.bronze import (
    BRONZE_HOT_TABLE,
    BRONZE_SCHEMA,
    BRONZE_PARTITION_SPEC,
    build_bronze_path,
    compute_parquet_checksum,
    ensure_bronze_table,
    get_bronze_catalog,
    payload_to_parquet_bytes,
    write_bronze,
)


class TestBuildBronzePath:
    def test_standard_path(self):
        path = build_bronze_path("tiingo", date(2024, 1, 15), "price")
        assert path == "tiingo/2024-01-15/price/data.parquet"

    def test_different_source(self):
        path = build_bronze_path("coinalyze", date(2024, 6, 1), "derivatives")
        assert path == "coinalyze/2024-06-01/derivatives/data.parquet"


class TestPayloadToParquetBytes:
    def test_creates_valid_parquet(self):
        payload = [
            {"ticker": "btcusd", "close": 48000.0, "volume": 100.0},
            {"ticker": "ethusd", "close": 3200.0, "volume": 50.0},
        ]
        buf = payload_to_parquet_bytes(payload)
        assert len(buf) > 0
        assert buf[:4] == b"PAR1"

    def test_empty_payload_returns_empty_parquet(self):
        buf = payload_to_parquet_bytes([])
        assert buf[:4] == b"PAR1"


class TestComputeParquetChecksum:
    def test_deterministic(self):
        data = b"test data for checksum"
        c1 = compute_parquet_checksum(data)
        c2 = compute_parquet_checksum(data)
        assert c1 == c2
        assert len(c1) == 64  # SHA-256 hex

    def test_different_data_different_checksum(self):
        c1 = compute_parquet_checksum(b"data1")
        c2 = compute_parquet_checksum(b"data2")
        assert c1 != c2


class TestGetBronzeCatalog:
    @patch("ftb.writers.bronze.SqlCatalog")
    def test_creates_catalog_with_correct_params(self, mock_catalog_cls):
        get_bronze_catalog(
            pg_uri="postgresql+psycopg2://user:pass@host/db",
            minio_endpoint="http://minio:9001",
            minio_access_key="key",
            minio_secret_key="secret",
            warehouse="s3://bronze-hot",
        )
        mock_catalog_cls.assert_called_once()
        call_kwargs = mock_catalog_cls.call_args
        assert call_kwargs[0][0] == "bronze-hot"
        props = call_kwargs[1]
        assert props["uri"] == "postgresql+psycopg2://user:pass@host/db"
        assert props["warehouse"] == "s3://bronze-hot"
        assert props["s3.endpoint"] == "http://minio:9001"
        assert props["s3.access-key-id"] == "key"
        assert props["s3.secret-access-key"] == "secret"
        assert props["init_catalog_tables"] == "true"


class TestEnsureBronzeTable:
    def test_loads_existing_table(self):
        mock_catalog = MagicMock()
        mock_table = MagicMock()
        mock_catalog.load_table.return_value = mock_table

        result = ensure_bronze_table(mock_catalog, BRONZE_HOT_TABLE)
        assert result == mock_table
        mock_catalog.load_table.assert_called_once_with(BRONZE_HOT_TABLE)

    def test_creates_table_if_not_exists(self):
        mock_catalog = MagicMock()
        mock_catalog.load_table.side_effect = Exception("NoSuchTableError")
        mock_table = MagicMock()
        mock_catalog.create_table.return_value = mock_table

        result = ensure_bronze_table(mock_catalog, BRONZE_HOT_TABLE)
        assert result == mock_table
        mock_catalog.create_table.assert_called_once_with(
            BRONZE_HOT_TABLE,
            schema=BRONZE_SCHEMA,
            partition_spec=BRONZE_PARTITION_SPEC,
        )

    def test_creates_namespace_silently(self):
        mock_catalog = MagicMock()
        mock_catalog.create_namespace.side_effect = Exception("already exists")
        mock_catalog.load_table.return_value = MagicMock()

        # Should not raise
        ensure_bronze_table(mock_catalog, BRONZE_HOT_TABLE)
        mock_catalog.create_namespace.assert_called_once_with("bronze")


class TestWriteBronze:
    def test_empty_observations_returns_zero(self):
        mock_catalog = MagicMock()
        result = write_bronze(mock_catalog, "test_source", date(2024, 1, 1), "test_metric", [])
        assert result == 0

    def test_writes_observations_to_iceberg(self):
        mock_catalog = MagicMock()
        mock_table = MagicMock()
        mock_catalog.load_table.return_value = mock_table
        # Mock schema().as_arrow() to return a valid Arrow schema
        mock_arrow_schema = pa.schema([
            pa.field("source_id", pa.string(), nullable=False),
            pa.field("metric_id", pa.string(), nullable=False),
            pa.field("instrument_id", pa.string(), nullable=True),
            pa.field("observed_at", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("value", pa.float64(), nullable=True),
            pa.field("ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("partition_date", pa.string(), nullable=False),
        ])
        mock_table.schema.return_value.as_arrow.return_value = mock_arrow_schema

        now = datetime.now(timezone.utc)
        observations = [
            {
                "metric_id": "price.spot.close_usd",
                "instrument_id": "BTC-USD",
                "observed_at": now,
                "value": 48000.0,
                "ingested_at": now,
            },
        ]

        result = write_bronze(mock_catalog, "tiingo", date(2024, 1, 15), "price.spot.close_usd", observations)
        assert result == 1
        mock_table.append.assert_called_once()

    def test_handles_null_values(self):
        mock_catalog = MagicMock()
        mock_table = MagicMock()
        mock_catalog.load_table.return_value = mock_table
        mock_arrow_schema = pa.schema([
            pa.field("source_id", pa.string(), nullable=False),
            pa.field("metric_id", pa.string(), nullable=False),
            pa.field("instrument_id", pa.string(), nullable=True),
            pa.field("observed_at", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("value", pa.float64(), nullable=True),
            pa.field("ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("partition_date", pa.string(), nullable=False),
        ])
        mock_table.schema.return_value.as_arrow.return_value = mock_arrow_schema

        now = datetime.now(timezone.utc)
        observations = [
            {
                "metric_id": "test_metric",
                "instrument_id": None,
                "observed_at": now,
                "value": None,
                "ingested_at": now,
            },
        ]

        result = write_bronze(mock_catalog, "test", date(2024, 1, 1), "test_metric", observations)
        assert result == 1
