"""DuckDB-based partition discovery over Iceberg metadata.

Design: v4.0 §Phase Gates — C2 partition discovery.
Uses PyIceberg to resolve metadata_location from SqlCatalog,
then DuckDB iceberg_metadata() for fast manifest-level scanning.
"""

import time

import duckdb

from ftb.writers.bronze import BRONZE_HOT_TABLE, ensure_bronze_table


def _resolve_iceberg_params(catalog, table_name: str) -> tuple[str, str, str]:
    """Derive DuckDB iceberg_scan parameters from PyIceberg SqlCatalog.

    PyIceberg SqlCatalog stores metadata_location as:
      s3://bronze-hot/bronze/observations_hot/metadata/00001-uuid.metadata.json
    DuckDB iceberg_scan needs:
      table_root = s3://bronze-hot/bronze/observations_hot
      version = 00001
      version_name_format = 00001-uuid%s.metadata.json (with version baked in)

    The version_name_format is needed because PyIceberg uses UUID-suffixed filenames
    that DuckDB's default format patterns can't match.

    Returns (table_root, version, version_name_format).
    """
    table = ensure_bronze_table(catalog, table_name)
    metadata_location = table.metadata_location
    # Strip /metadata/<filename> to get table root
    idx = metadata_location.rfind("/metadata/")
    if idx == -1:
        raise ValueError(f"Unexpected metadata_location format: {metadata_location}")
    table_root = metadata_location[:idx]
    # Extract version and build format string from filename
    # Filename: 00001-ba6ab442-1ffe-4416-af03-00993cabdca5.metadata.json
    filename = metadata_location[idx + len("/metadata/"):]
    version = filename.split("-")[0]  # "00001"
    # Build version_name_format: replace ".metadata.json" with "%s.metadata.json"
    # so DuckDB can substitute the compression suffix (empty string for uncompressed)
    name_format = filename.replace(".metadata.json", "%s.metadata.json")
    return table_root, version, name_format


def _configure_duckdb_s3(con: duckdb.DuckDBPyConnection, catalog) -> None:
    """Configure DuckDB S3 settings to match the PyIceberg catalog's MinIO config."""
    props = catalog.properties
    con.execute("SET s3_endpoint = %s" % _sql_str(
        props.get("s3.endpoint", "http://minio:9001").replace("http://", "").replace("https://", "")
    ))
    con.execute("SET s3_access_key_id = %s" % _sql_str(props.get("s3.access-key-id", "")))
    con.execute("SET s3_secret_access_key = %s" % _sql_str(props.get("s3.secret-access-key", "")))
    con.execute("SET s3_region = %s" % _sql_str(props.get("s3.region", "us-east-1")))
    con.execute("SET s3_url_style = 'path'")
    con.execute("SET s3_use_ssl = false")


def _sql_str(value: str) -> str:
    """Escape a string for use in a DuckDB SET statement."""
    return "'" + value.replace("'", "''") + "'"


def discover_partitions_duckdb(
    catalog,
    table_name: str = BRONZE_HOT_TABLE,
    partition_date_filter: str | None = None,
) -> tuple[list[dict], float]:
    """Discover partitions using DuckDB over Iceberg metadata (no data file scanning).

    Args:
        catalog: PyIceberg SqlCatalog instance.
        table_name: Iceberg table identifier (e.g. "bronze.observations_hot").
        partition_date_filter: Optional SQL WHERE clause fragment for partition_date,
            e.g. "partition_date < '2026-01-01'".

    Returns:
        Tuple of (partition_list, elapsed_ms).
        Each partition dict has keys: source_id, metric_id, partition_date.
    """
    table_root, version, name_format = _resolve_iceberg_params(catalog, table_name)

    con = duckdb.connect()
    con.execute("INSTALL iceberg")
    con.execute("LOAD iceberg")
    _configure_duckdb_s3(con, catalog)

    where = f"WHERE {partition_date_filter}" if partition_date_filter else ""
    query = f"""
        SELECT DISTINCT source_id, metric_id, partition_date
        FROM iceberg_scan('{table_root}',
             allow_moved_paths := true,
             version := '{version}',
             version_name_format := '{name_format}')
        {where}
        ORDER BY source_id, partition_date, metric_id
    """

    t0 = time.monotonic()
    result = con.execute(query).fetchall()
    elapsed_ms = (time.monotonic() - t0) * 1000

    con.close()

    return [
        {"source_id": r[0], "metric_id": r[1], "partition_date": r[2]}
        for r in result
    ], elapsed_ms
