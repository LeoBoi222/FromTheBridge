"""Tests for Bronze writer — Parquet files to MinIO."""
from datetime import date

import pytest

from ftb.writers.bronze import build_bronze_path, payload_to_parquet_bytes


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
        # Parquet magic bytes
        assert buf[:4] == b"PAR1"

    def test_empty_payload_returns_empty_parquet(self):
        buf = payload_to_parquet_bytes([])
        assert buf[:4] == b"PAR1"
