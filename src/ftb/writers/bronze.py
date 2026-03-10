"""Bronze writer — raw API payloads to Parquet files in MinIO."""
from __future__ import annotations

import io
from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq


def build_bronze_path(source_id: str, partition_date: date, metric_domain: str) -> str:
    """Build MinIO object path for a Bronze partition."""
    return f"{source_id}/{partition_date.isoformat()}/{metric_domain}/data.parquet"


def payload_to_parquet_bytes(payload: list[dict]) -> bytes:
    """Convert a list of dicts (raw API response rows) to Parquet bytes."""
    if not payload:
        # Empty table with no columns
        table = pa.table({})
    else:
        table = pa.Table.from_pylist(payload)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def write_bronze(
    minio_client,
    bucket: str,
    source_id: str,
    partition_date: date,
    metric_domain: str,
    payload: list[dict],
) -> str:
    """Write raw payload as Parquet to MinIO. Returns the object path."""
    path = build_bronze_path(source_id, partition_date, metric_domain)
    data = payload_to_parquet_bytes(payload)
    minio_client.put_object(
        bucket,
        path,
        io.BytesIO(data),
        length=len(data),
        content_type="application/octet-stream",
    )
    return path
