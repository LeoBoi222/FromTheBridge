"""Business logic for empire_to_forge_sync bridge.

Maps empire.observations rows to forge Observation dataclasses.
Pure functions — no Dagster imports.
"""
from __future__ import annotations

from datetime import datetime

from ftb.validation.core import Observation

INSTRUMENT_ID_MARKET = "__market__"
SOURCE_ID = "eds_derived"


def map_empire_to_forge(
    rows: list[dict],
    promoted_metrics: set[str],
) -> list[Observation]:
    """Map empire.observations rows to forge Observations.

    - Filters to promoted metrics only
    - Maps instrument_id '__market__' -> None (C2 resolution)
    - Overwrites source_id to 'eds_derived'
    """
    observations = []
    for row in rows:
        if row["metric_id"] not in promoted_metrics:
            continue
        instrument_id = row["instrument_id"]
        if instrument_id == INSTRUMENT_ID_MARKET:
            instrument_id = None
        observations.append(
            Observation(
                metric_id=row["metric_id"],
                instrument_id=instrument_id,
                source_id=SOURCE_ID,
                observed_at=row["observed_at"],
                value=row["value"],
            )
        )
    return observations


def build_empire_query(
    metric_ids: list[str],
    watermark: datetime | None = None,
) -> tuple[str, dict]:
    """Build parameterized query for empire.observations.

    Returns (sql, params) for use with clickhouse-connect.
    """
    params: dict = {"metric_ids": metric_ids}

    sql = (
        "SELECT metric_id, instrument_id, source_id, observed_at, ingested_at, value "
        "FROM empire.observations "
        "WHERE metric_id IN %(metric_ids)s"
    )

    if watermark is not None:
        sql += " AND ingested_at > %(watermark)s"
        params["watermark"] = watermark

    sql += " ORDER BY ingested_at ASC"
    return sql, params
