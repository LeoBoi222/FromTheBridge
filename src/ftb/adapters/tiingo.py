"""Tiingo crypto OHLCV adapter — fetch, map, validate, write Bronze + Silver."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

import httpx

from ftb.validation.core import Observation

logger = logging.getLogger(__name__)

TIINGO_BASE_URL = "https://api.tiingo.com/tiingo/crypto/prices"

# Metrics extracted from Tiingo OHLCV response (Silver writes)
TIINGO_METRICS = {
    "price.spot.close_usd": "close",
    "price.spot.volume_usd_24h": "volumeNotional",
}


def build_tiingo_url(
    tickers: list[str],
    start_date: str,
    end_date: str,
    resample_freq: str = "1day",
) -> str:
    """Build Tiingo crypto prices endpoint URL."""
    ticker_str = ",".join(tickers)
    return (
        f"{TIINGO_BASE_URL}"
        f"?tickers={ticker_str}"
        f"&startDate={start_date}"
        f"&endDate={end_date}"
        f"&resampleFreq={resample_freq}"
    )


def extract_observations(
    response_data: list[dict],
    symbol_map: dict[str, str],
) -> list[Observation]:
    """Extract Silver observations from Tiingo API response.

    Args:
        response_data: Raw API response (list of ticker objects with priceData).
        symbol_map: Mapping of Tiingo ticker -> canonical instrument_id.
            e.g., {"btcusd": "BTC-USD"}

    Returns:
        List of Observation objects ready for validation and Silver write.
        Tickers not in symbol_map are silently skipped (logged as warning).
    """
    observations: list[Observation] = []

    for ticker_obj in response_data:
        ticker = ticker_obj["ticker"]
        instrument_id = symbol_map.get(ticker)
        if instrument_id is None:
            logger.warning("Skipping unknown ticker: %s", ticker)
            continue

        for bar in ticker_obj.get("priceData", []):
            observed_at = datetime.fromisoformat(bar["date"]).replace(tzinfo=timezone.utc)

            for metric_id, field_name in TIINGO_METRICS.items():
                value = bar.get(field_name)
                observations.append(Observation(
                    metric_id=metric_id,
                    instrument_id=instrument_id,
                    source_id="tiingo",
                    observed_at=observed_at,
                    value=float(value) if value is not None else None,
                ))

    return observations


def fetch_tiingo_crypto(
    api_key: str,
    tickers: list[str],
    start_date: str,
    end_date: str,
) -> list[dict]:
    """Fetch OHLCV data from Tiingo crypto endpoint.

    Returns raw API response as list of dicts.
    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    url = build_tiingo_url(tickers, start_date, end_date)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Token {api_key}",
    }

    with httpx.Client(timeout=30.0) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()


def flatten_price_data(response_data: list[dict]) -> list[dict]:
    """Flatten nested priceData for Bronze Parquet storage.

    Each row gets ticker, baseCurrency, quoteCurrency + all priceData fields.
    """
    rows = []
    for ticker_obj in response_data:
        base = {
            "ticker": ticker_obj["ticker"],
            "baseCurrency": ticker_obj.get("baseCurrency"),
            "quoteCurrency": ticker_obj.get("quoteCurrency"),
        }
        for bar in ticker_obj.get("priceData", []):
            rows.append({**base, **bar})
    return rows
