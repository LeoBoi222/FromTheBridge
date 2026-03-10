"""Tests for Tiingo adapter — field mapping and observation extraction."""
from datetime import datetime, timezone

import pytest

from ftb.adapters.tiingo import (
    extract_observations,
    build_tiingo_url,
    TIINGO_METRICS,
)
from ftb.validation.core import Observation


@pytest.fixture
def symbol_map():
    """instrument_source_map rows for Tiingo."""
    return {"btcusd": "BTC-USD", "ethusd": "ETH-USD", "solusd": "SOL-USD"}


@pytest.fixture
def sample_response():
    """Tiingo crypto prices API response shape."""
    return [
        {
            "ticker": "btcusd",
            "baseCurrency": "btc",
            "quoteCurrency": "usd",
            "priceData": [
                {
                    "date": "2024-01-15T00:00:00+00:00",
                    "open": 42500.0,
                    "high": 43000.0,
                    "low": 42000.0,
                    "close": 42800.0,
                    "volume": 15.5,
                    "volumeNotional": 663400.0,
                    "tradesDone": 1200,
                },
            ],
        },
        {
            "ticker": "ethusd",
            "baseCurrency": "eth",
            "quoteCurrency": "usd",
            "priceData": [
                {
                    "date": "2024-01-15T00:00:00+00:00",
                    "open": 2500.0,
                    "high": 2550.0,
                    "low": 2480.0,
                    "close": 2520.0,
                    "volume": 100.0,
                    "volumeNotional": 252000.0,
                    "tradesDone": 800,
                },
            ],
        },
    ]


class TestBuildTiingoUrl:
    def test_url_with_tickers_and_dates(self):
        url = build_tiingo_url(
            tickers=["btcusd", "ethusd"],
            start_date="2024-01-15",
            end_date="2024-01-16",
        )
        assert "tickers=btcusd,ethusd" in url
        assert "startDate=2024-01-15" in url
        assert "endDate=2024-01-16" in url
        assert "resampleFreq=1day" in url


class TestExtractObservations:
    def test_extracts_two_metrics_per_instrument(self, sample_response, symbol_map):
        observations = extract_observations(sample_response, symbol_map)
        # 2 instruments x 2 metrics = 4 observations
        assert len(observations) == 4

    def test_close_usd_extracted(self, sample_response, symbol_map):
        observations = extract_observations(sample_response, symbol_map)
        btc_close = [o for o in observations
                     if o.metric_id == "price.spot.close_usd" and o.instrument_id == "BTC-USD"]
        assert len(btc_close) == 1
        assert btc_close[0].value == 42800.0

    def test_volume_extracted(self, sample_response, symbol_map):
        observations = extract_observations(sample_response, symbol_map)
        btc_vol = [o for o in observations
                   if o.metric_id == "price.spot.volume_usd_24h" and o.instrument_id == "BTC-USD"]
        assert len(btc_vol) == 1
        assert btc_vol[0].value == 663400.0

    def test_unknown_ticker_skipped(self, sample_response, symbol_map):
        # Add unknown ticker to response
        response = sample_response + [{
            "ticker": "dogebtc",
            "baseCurrency": "doge",
            "quoteCurrency": "btc",
            "priceData": [{"date": "2024-01-15T00:00:00+00:00",
                           "close": 0.001, "volumeNotional": 10.0,
                           "open": 0.001, "high": 0.001, "low": 0.001,
                           "volume": 100.0, "tradesDone": 50}],
        }]
        observations = extract_observations(response, symbol_map)
        # Still only 4 — dogebtc not in symbol_map
        assert len(observations) == 4

    def test_observed_at_parsed(self, sample_response, symbol_map):
        observations = extract_observations(sample_response, symbol_map)
        assert observations[0].observed_at == datetime(2024, 1, 15, tzinfo=timezone.utc)

    def test_source_id_is_tiingo(self, sample_response, symbol_map):
        observations = extract_observations(sample_response, symbol_map)
        assert all(o.source_id == "tiingo" for o in observations)

    def test_empty_price_data_returns_nothing(self, symbol_map):
        response = [{"ticker": "btcusd", "baseCurrency": "btc",
                     "quoteCurrency": "usd", "priceData": []}]
        observations = extract_observations(response, symbol_map)
        assert len(observations) == 0
