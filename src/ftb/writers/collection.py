"""Collection event writer — records adapter runs in PostgreSQL."""
from __future__ import annotations

from datetime import datetime, timezone

COLLECTION_EVENT_SQL = """
INSERT INTO forge.collection_events (
    source_id, metric_id, instrument_id, started_at, completed_at,
    status, observations_written, observations_rejected,
    metrics_covered, instruments_covered, error_detail, metadata
) VALUES (
    %(source_id)s, %(metric_id)s, %(instrument_id)s, %(started_at)s, %(completed_at)s,
    %(status)s, %(observations_written)s, %(observations_rejected)s,
    %(metrics_covered)s, %(instruments_covered)s, %(error_detail)s, %(metadata)s::jsonb
)
"""


def build_collection_event_params(
    source_id: str,
    status: str,
    metric_id: str | None = None,
    instrument_id: str | None = None,
    started_at: datetime | None = None,
    observations_written: int | None = None,
    observations_rejected: int | None = None,
    metrics_covered: list[str] | None = None,
    instruments_covered: list[str] | None = None,
    error_detail: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Build parameter dict for collection_events INSERT."""
    now = datetime.now(timezone.utc)
    return {
        "source_id": source_id,
        "metric_id": metric_id,
        "instrument_id": instrument_id,
        "started_at": started_at or now,
        "completed_at": now,
        "status": status,
        "observations_written": observations_written,
        "observations_rejected": observations_rejected,
        "metrics_covered": metrics_covered,
        "instruments_covered": instruments_covered,
        "error_detail": error_detail,
        "metadata": "{}" if metadata is None else str(metadata),
    }


def write_collection_event(conn, **kwargs) -> None:
    """Write a collection event to forge.collection_events."""
    params = build_collection_event_params(**kwargs)
    with conn.cursor() as cur:
        cur.execute(COLLECTION_EVENT_SQL, params)
    conn.commit()
