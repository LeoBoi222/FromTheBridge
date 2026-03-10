"""Gold Iceberg table management — create, read, overwrite partitions.

Uses PyIceberg for table management and writes.
Partitioned by (year_month, metric_domain) per v4.0 §Silver → Gold Export.
"""

import contextlib

import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.expressions import And, EqualTo
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table import Table
from pyiceberg.transforms import IdentityTransform
from pyiceberg.types import (
    DoubleType,
    LongType,
    NestedField,
    StringType,
    TimestamptzType,
)

GOLD_TABLE_NAME = "gold.observations"

GOLD_ICEBERG_SCHEMA = Schema(
    NestedField(1, "metric_id", StringType(), required=False),
    NestedField(2, "instrument_id", StringType(), required=False),
    NestedField(3, "observed_at", TimestamptzType(), required=False),
    NestedField(4, "value", DoubleType(), required=False),
    NestedField(5, "data_version", LongType(), required=False),
    NestedField(6, "ingested_at", TimestamptzType(), required=False),
    NestedField(7, "metric_domain", StringType(), required=False),
    NestedField(8, "year_month", StringType(), required=False),
)

GOLD_PARTITION_SPEC = PartitionSpec(
    PartitionField(source_id=8, field_id=1001, transform=IdentityTransform(), name="year_month"),
    PartitionField(source_id=7, field_id=1002, transform=IdentityTransform(), name="metric_domain"),
)


def ensure_gold_table(catalog: SqlCatalog) -> Table:
    """Create the Gold Iceberg table if it doesn't exist."""
    namespace = GOLD_TABLE_NAME.split(".")[0]
    with contextlib.suppress(Exception):
        catalog.create_namespace(namespace)

    try:
        return catalog.load_table(GOLD_TABLE_NAME)
    except Exception:
        return catalog.create_table(
            GOLD_TABLE_NAME,
            schema=GOLD_ICEBERG_SCHEMA,
            partition_spec=GOLD_PARTITION_SPEC,
        )


def read_partition(
    catalog: SqlCatalog,
    year_month: str,
    metric_domain: str,
) -> pa.Table | None:
    """Read an existing Gold partition. Returns None if empty."""
    table = catalog.load_table(GOLD_TABLE_NAME)
    scan = table.scan(
        row_filter=And(
            EqualTo("year_month", year_month),
            EqualTo("metric_domain", metric_domain),
        )
    )
    result = scan.to_arrow()
    if result.num_rows == 0:
        return None
    return result


def overwrite_partition(
    catalog: SqlCatalog,
    data: pa.Table,
    year_month: str,
    metric_domain: str,
) -> None:
    """Atomic partition overwrite — replaces all rows for (year_month, metric_domain)."""
    table = catalog.load_table(GOLD_TABLE_NAME)
    table.overwrite(
        data,
        overwrite_filter=And(
            EqualTo("year_month", year_month),
            EqualTo("metric_domain", metric_domain),
        ),
    )
