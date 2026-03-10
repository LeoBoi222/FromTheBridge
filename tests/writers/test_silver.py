"""Tests for Silver writer — ClickHouse observations + dead_letter."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from ftb.validation.core import Observation
from ftb.writers.silver import (
    build_observations_batch,
    build_dead_letter_batch,
    DeadLetterRow,
)


def _obs(metric_id="price.spot.close_usd", instrument_id="BTC-USD",
         value=48000.0, observed_at=None, source_id="tiingo"):
    return Observation(
        metric_id=metric_id,
        instrument_id=instrument_id,
        source_id=source_id,
        observed_at=observed_at or datetime(2024, 1, 1, tzinfo=timezone.utc),
        value=value,
    )


class TestBuildObservationsBatch:
    def test_builds_correct_columns(self):
        obs = [_obs()]
        rows, columns = build_observations_batch(obs)
        assert columns == [
            "metric_id", "instrument_id", "source_id",
            "observed_at", "ingested_at", "value", "data_version",
        ]
        assert len(rows) == 1

    def test_row_values(self):
        obs = [_obs(value=48000.0)]
        rows, _ = build_observations_batch(obs)
        row = rows[0]
        assert row[0] == "price.spot.close_usd"  # metric_id
        assert row[1] == "BTC-USD"                # instrument_id
        assert row[2] == "tiingo"                 # source_id
        assert row[5] == 48000.0                  # value
        assert row[6] == 1                        # data_version

    def test_null_instrument_preserved(self):
        obs = [_obs(instrument_id=None)]
        rows, _ = build_observations_batch(obs)
        assert rows[0][1] is None

    def test_multiple_observations(self):
        obs = [_obs(value=100.0), _obs(value=200.0)]
        rows, _ = build_observations_batch(obs)
        assert len(rows) == 2


class TestBuildDeadLetterBatch:
    def test_builds_correct_columns(self):
        dead = [DeadLetterRow(
            source_id="tiingo",
            metric_id="price.spot.close_usd",
            instrument_id="BTC-USD",
            raw_payload='{"close": null}',
            rejection_reason="null value not allowed",
            rejection_code="NULL_VIOLATION",
        )]
        rows, columns = build_dead_letter_batch(dead)
        assert "rejection_code" in columns
        assert "raw_payload" in columns
        assert len(rows) == 1
