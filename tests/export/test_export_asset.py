"""Tests for gold_observations Dagster asset helpers."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from ftb.export.export_asset import (
    _load_domain_lookup,
    _load_watermark_from_metadata,
)


class TestLoadDomainLookup:
    def test_returns_metric_to_domain_map(self):
        mock_pg = MagicMock()
        mock_cur = MagicMock()
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cur
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchall.return_value = [
            ("macro.rates.fed_funds", "macro"),
            ("defi.tvl.total", "defi"),
            ("price.spot.close_usd", "price"),
        ]

        result = _load_domain_lookup(mock_pg)
        assert result == {
            "macro.rates.fed_funds": "macro",
            "defi.tvl.total": "defi",
            "price.spot.close_usd": "price",
        }

    def test_empty_catalog(self):
        mock_pg = MagicMock()
        mock_cur = MagicMock()
        mock_pg.cursor.return_value.__enter__ = lambda s: mock_cur
        mock_pg.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchall.return_value = []

        result = _load_domain_lookup(mock_pg)
        assert result == {}


class TestLoadWatermarkFromMetadata:
    def test_returns_none_when_no_prior_run(self):
        mock_instance = MagicMock()
        mock_instance.get_latest_materialization_event.return_value = None
        result = _load_watermark_from_metadata(mock_instance)
        assert result is None

    def test_returns_datetime_from_metadata(self):
        mock_instance = MagicMock()
        mock_event = MagicMock()
        mock_event.asset_materialization.metadata = {
            "watermark_new": MagicMock(value="2026-03-10T00:00:00+00:00")
        }
        mock_instance.get_latest_materialization_event.return_value = mock_event
        result = _load_watermark_from_metadata(mock_instance)
        assert result == datetime(2026, 3, 10, tzinfo=UTC)

    def test_returns_none_when_no_watermark_metadata(self):
        mock_instance = MagicMock()
        mock_event = MagicMock()
        mock_event.asset_materialization.metadata = {}
        mock_instance.get_latest_materialization_event.return_value = mock_event
        result = _load_watermark_from_metadata(mock_instance)
        assert result is None
