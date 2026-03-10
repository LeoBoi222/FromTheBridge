"""Bronze writer — raw API payloads to Iceberg tables on MinIO.

Uses PyIceberg with a SQL catalog backed by PostgreSQL (empire_postgres).
Iceberg tables live in s3://bronze-hot/ and s3://bronze-archive/.
"""

import contextlib
import hashlib
import io
from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table import Table
from pyiceberg.transforms import IdentityTransform
from pyiceberg.types import (
    DoubleType,
    NestedField,
    StringType,
    TimestamptzType,
)

# Bronze observation schema — raw landing with flexible payload column
BRONZE_SCHEMA = Schema(
    NestedField(1, "source_id", StringType(), required=True),
    NestedField(2, "metric_id", StringType(), required=True),
    NestedField(3, "instrument_id", StringType(), required=False),
    NestedField(4, "observed_at", TimestamptzType(), required=True),
    NestedField(5, "value", DoubleType(), required=False),
    NestedField(6, "ingested_at", TimestamptzType(), required=True),
    NestedField(7, "partition_date", StringType(), required=True),
)

BRONZE_PARTITION_SPEC = PartitionSpec(
    PartitionField(source_id=1, field_id=1001, transform=IdentityTransform(), name="source_id"),
    PartitionField(source_id=7, field_id=1002, transform=IdentityTransform(), name="partition_date"),
    PartitionField(source_id=2, field_id=1003, transform=IdentityTransform(), name="metric_id"),
)

BRONZE_HOT_TABLE = "bronze.observations_hot"
BRONZE_ARCHIVE_TABLE = "bronze.observations_archive"


def get_iceberg_catalog(
    pg_uri: str,
    minio_endpoint: str,
    minio_access_key: str,
    minio_secret_key: str,
    warehouse: str = "s3://bronze-hot",
) -> SqlCatalog:
    """Create a PyIceberg SqlCatalog backed by PostgreSQL with MinIO storage."""
    # Derive catalog name from warehouse bucket (e.g. "s3://bronze-hot" -> "bronze-hot")
    catalog_name = warehouse.replace("s3://", "")
    return SqlCatalog(
        catalog_name,
        **{
            "uri": pg_uri,
            "warehouse": warehouse,
            "s3.endpoint": minio_endpoint,
            "s3.access-key-id": minio_access_key,
            "s3.secret-access-key": minio_secret_key,
            "s3.region": "us-east-1",
            "init_catalog_tables": "true",
        },
    )


# Backward compat alias
get_bronze_catalog = get_iceberg_catalog


def ensure_bronze_table(catalog: SqlCatalog, table_name: str = BRONZE_HOT_TABLE) -> Table:
    """Create the Bronze Iceberg table if it doesn't exist. Returns the table."""
    namespace = table_name.split(".")[0]
    with contextlib.suppress(Exception):
        catalog.create_namespace(namespace)

    try:
        return catalog.load_table(table_name)
    except Exception:
        return catalog.create_table(
            table_name,
            schema=BRONZE_SCHEMA,
            partition_spec=BRONZE_PARTITION_SPEC,
        )


def write_bronze(
    catalog: SqlCatalog,
    source_id: str,
    partition_date: date,
    metric_id: str,
    observations: list[dict],
    table_name: str = BRONZE_HOT_TABLE,
) -> int:
    """Write observations to the Bronze Iceberg table.

    Each observation dict should have: metric_id, instrument_id, observed_at, value, ingested_at.
    Returns the number of rows written.
    """
    if not observations:
        return 0

    table = ensure_bronze_table(catalog, table_name)

    # Build Arrow table matching the Iceberg schema
    rows = []
    date_str = partition_date.isoformat()
    for obs in observations:
        rows.append({
            "source_id": source_id,
            "metric_id": obs.get("metric_id", metric_id),
            "instrument_id": obs.get("instrument_id"),
            "observed_at": obs["observed_at"],
            "value": float(obs["value"]) if obs.get("value") is not None else None,
            "ingested_at": obs["ingested_at"],
            "partition_date": date_str,
        })

    arrow_table = pa.Table.from_pylist(rows, schema=table.schema().as_arrow())
    table.append(arrow_table)
    return len(rows)


def build_bronze_path(source_id: str, partition_date: date, metric_domain: str) -> str:
    """Build MinIO object path for a Bronze partition. Kept for backward compat."""
    return f"{source_id}/{partition_date.isoformat()}/{metric_domain}/data.parquet"


def compute_parquet_checksum(data: bytes) -> str:
    """Compute SHA-256 hex digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def payload_to_parquet_bytes(payload: list[dict]) -> bytes:
    """Convert a list of dicts to Parquet bytes. Used for checksum computation."""
    table = pa.table({}) if not payload else pa.Table.from_pylist(payload)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()
