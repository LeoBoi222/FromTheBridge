"""Core logic for Silver → Gold export.

Pure functions — no Dagster imports. Handles domain mapping, CH query
construction, partition derivation, anomaly guard, Arrow table building,
and partition merge.

Source of truth: FromTheBridge_design_v4.0.md §Silver → Gold Export
"""

from datetime import UTC, datetime

import duckdb
import pyarrow as pa

# v4.0 §Silver → Gold Export — domain mapping
# Gold domain 'onchain' ← catalog 'chain'
# Gold domain 'flows' ← catalog 'flows', 'etf', 'stablecoin'
# derivatives, macro, defi map 1:1
# price, metadata excluded from Phase 1
_CATALOG_TO_GOLD = {
    "chain": "onchain",
    "flows": "flows",
    "etf": "flows",
    "stablecoin": "flows",
    "derivatives": "derivatives",
    "macro": "macro",
    "defi": "defi",
}

_EXCLUDED_DOMAINS = {"price", "metadata", "valuation"}

GOLD_ARROW_SCHEMA = pa.schema([
    pa.field("metric_id", pa.string()),
    pa.field("instrument_id", pa.string()),
    pa.field("observed_at", pa.timestamp("us", tz="UTC")),
    pa.field("value", pa.float64()),
    pa.field("data_version", pa.int64()),
    pa.field("ingested_at", pa.timestamp("us", tz="UTC")),
    pa.field("metric_domain", pa.string()),
    pa.field("year_month", pa.string()),
])


def catalog_to_gold_domain(catalog_domain: str) -> str | None:
    """Map a metric_catalog domain to its Gold partition domain.

    Returns None for domains excluded from Phase 1 export.
    """
    if catalog_domain in _EXCLUDED_DOMAINS:
        return None
    return _CATALOG_TO_GOLD.get(catalog_domain)


def build_export_query(
    last_watermark: datetime | None,
    run_start_ts: datetime,
) -> tuple[str, dict]:
    """Build the ClickHouse export query per v4.0 spec.

    Uses SELECT ... FINAL with watermark delta and 3-minute lag floor.
    """
    if last_watermark is None:
        last_watermark = datetime(1970, 1, 1, tzinfo=UTC)

    sql = (
        "SELECT metric_id, instrument_id, observed_at, value, "
        "ingested_at, data_version "
        "FROM forge.observations FINAL "
        "WHERE ingested_at > %(last_watermark)s "
        "AND ingested_at <= %(run_start_ts)s - INTERVAL 3 MINUTE "
        "ORDER BY metric_id, instrument_id, observed_at"
    )
    return sql, {"last_watermark": last_watermark, "run_start_ts": run_start_ts}


def derive_partitions(rows: list[dict]) -> set[tuple[str, str]]:
    """Derive touched (year_month, metric_domain) partition keys from delta rows."""
    partitions = set()
    for row in rows:
        observed_at = row["observed_at"]
        year_month = observed_at.strftime("%Y-%m")
        partitions.add((year_month, row["metric_domain"]))
    return partitions


def check_anomaly_guard(
    row_count: int,
    rolling_avg: float,
    force_backfill: bool = False,
) -> bool:
    """Check if delta row count is within safe bounds.

    Returns True if safe to proceed, False if anomalous.
    v4.0: Fail if >10x rolling 7-day average or >2M rows.
    force_backfill=True bypasses all checks.
    """
    if force_backfill:
        return True
    if row_count > 2_000_000:
        return False
    return not (rolling_avg > 0 and row_count > rolling_avg * 10)


def build_gold_arrow_table(
    rows: list[dict],
    domain_lookup: dict[str, str],
) -> pa.Table:
    """Transform CH result rows into Gold Arrow table with domain mapping.

    Filters out rows whose catalog domain is excluded from Phase 1.
    Adds metric_domain and year_month partition columns.
    """
    filtered = []
    for row in rows:
        catalog_domain = domain_lookup.get(row["metric_id"])
        if catalog_domain is None:
            continue
        gold_domain = catalog_to_gold_domain(catalog_domain)
        if gold_domain is None:
            continue
        filtered.append({
            "metric_id": row["metric_id"],
            "instrument_id": row.get("instrument_id"),
            "observed_at": row["observed_at"],
            "value": float(row["value"]) if row["value"] is not None else None,
            "data_version": int(row["data_version"]),
            "ingested_at": row["ingested_at"],
            "metric_domain": gold_domain,
            "year_month": row["observed_at"].strftime("%Y-%m"),
        })

    if not filtered:
        return pa.table(
            {f.name: pa.array([], type=f.type) for f in GOLD_ARROW_SCHEMA},
            schema=GOLD_ARROW_SCHEMA,
        )

    return pa.Table.from_pylist(filtered, schema=GOLD_ARROW_SCHEMA)


def merge_partition(
    existing: pa.Table | None,
    new: pa.Table,
) -> pa.Table:
    """Merge new rows into existing partition data by data_version.

    For duplicate (metric_id, instrument_id, observed_at) keys,
    keep the row with the higher data_version.
    """
    if existing is None or existing.num_rows == 0:
        return new

    combined = pa.concat_tables([existing, new])

    conn = duckdb.connect()
    conn.register("combined", combined)
    result = conn.execute("""
        SELECT * FROM (
            SELECT *,
                ROW_NUMBER() OVER (
                    PARTITION BY metric_id,
                                 COALESCE(instrument_id, ''),
                                 observed_at
                    ORDER BY data_version DESC
                ) as _rn
            FROM combined
        ) WHERE _rn = 1
    """).to_arrow_table()
    conn.close()

    merged = result.drop_columns(["_rn"])
    # Re-cast to GOLD_ARROW_SCHEMA — DuckDB may change nullability
    return merged.cast(GOLD_ARROW_SCHEMA)
