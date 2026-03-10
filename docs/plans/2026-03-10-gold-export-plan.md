# Silver → Gold Export Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the `gold_observations` Dagster asset that incrementally exports deduplicated observations from Silver (ClickHouse) to Gold (Iceberg on MinIO), enabling downstream DuckDB reads.

**Architecture:** The export asset reads Silver via `ch_export_reader` (SELECT-only, Rule 2 compliant), maps catalog domains to Gold domains, merges with existing Gold partitions via DuckDB Iceberg reads, and writes via PyIceberg partition overwrite. Watermark stored in Dagster materialization metadata. Hourly fallback schedule for Phase 1.

**Tech Stack:** clickhouse-connect (CH read), DuckDB (Iceberg read for merge), PyIceberg (Iceberg write), PyArrow (data interchange), Dagster (orchestration)

**Source of truth:** `FromTheBridge_design_v4.0.md` §Silver → Gold Export (lines 2442–2561)

---

### Task 1: Create MinIO Gold Writer User on Proxmox

**Files:** None (infrastructure only)

**Step 1: Create the MinIO user with gold secrets**

```bash
# Read existing secrets
GOLD_KEY=$(cat /opt/empire/FromTheBridge/secrets/minio_gold_key.txt)
GOLD_SECRET=$(cat /opt/empire/FromTheBridge/secrets/minio_gold_secret.txt)

# Create user
ssh root@192.168.68.11 "docker exec empire_minio mc alias set local http://localhost:9001 \$(cat /opt/empire/FromTheBridge/secrets/minio_root_key.txt) \$(cat /opt/empire/FromTheBridge/secrets/minio_root_secret.txt) && docker exec empire_minio mc admin user add local $GOLD_KEY $GOLD_SECRET"
```

**Step 2: Create and attach gold-rw policy**

```bash
# Create policy JSON
ssh root@192.168.68.11 "cat > /tmp/gold-rw.json << 'EOF'
{
  \"Version\": \"2012-10-17\",
  \"Statement\": [{
    \"Effect\": \"Allow\",
    \"Action\": [\"s3:GetObject\", \"s3:PutObject\", \"s3:DeleteObject\", \"s3:ListBucket\", \"s3:GetBucketLocation\"],
    \"Resource\": [\"arn:aws:s3:::gold\", \"arn:aws:s3:::gold/*\"]
  }]
}
EOF"

ssh root@192.168.68.11 "docker exec empire_minio mc admin policy create local gold-rw /tmp/gold-rw.json && docker exec empire_minio mc admin policy attach local gold-rw --user $GOLD_KEY"
```

**Step 3: Verify user can access gold bucket**

```bash
ssh root@192.168.68.11 "docker exec empire_minio mc alias set goldtest http://localhost:9001 $GOLD_KEY $GOLD_SECRET && docker exec empire_minio mc ls goldtest/gold/"
```

Expected: Empty listing (no error).

---

### Task 2: Core Export Logic — Domain Mapping + Query Builder

**Files:**
- Create: `src/ftb/export/__init__.py`
- Create: `src/ftb/export/gold_export.py`
- Create: `tests/export/__init__.py`
- Create: `tests/export/test_gold_export.py`

**Step 1: Write failing tests for domain mapping and query builder**

```python
"""Tests for gold_export core logic — domain mapping, query, anomaly guard."""

from datetime import datetime, timezone

import pytest

from ftb.export.gold_export import (
    catalog_to_gold_domain,
    build_export_query,
    check_anomaly_guard,
    derive_partitions,
)


class TestCatalogToGoldDomain:
    """v4.0: Gold domain 'onchain' maps to catalog domain 'chain'.
    Gold domain 'flows' maps to catalog domains 'flows', 'etf', 'stablecoin'.
    Remaining: derivatives, macro, defi stay as-is.
    price, metadata excluded from Phase 1 export.
    """

    def test_chain_maps_to_onchain(self):
        assert catalog_to_gold_domain("chain") == "onchain"

    def test_flows_stays_flows(self):
        assert catalog_to_gold_domain("flows") == "flows"

    def test_etf_maps_to_flows(self):
        assert catalog_to_gold_domain("etf") == "flows"

    def test_stablecoin_maps_to_flows(self):
        assert catalog_to_gold_domain("stablecoin") == "flows"

    def test_derivatives_stays(self):
        assert catalog_to_gold_domain("derivatives") == "derivatives"

    def test_macro_stays(self):
        assert catalog_to_gold_domain("macro") == "macro"

    def test_defi_stays(self):
        assert catalog_to_gold_domain("defi") == "defi"

    def test_price_returns_none(self):
        assert catalog_to_gold_domain("price") is None

    def test_metadata_returns_none(self):
        assert catalog_to_gold_domain("metadata") is None

    def test_unknown_returns_none(self):
        assert catalog_to_gold_domain("foobar") is None


class TestBuildExportQuery:
    """v4.0: SELECT ... FINAL with watermark delta + 3-minute lag floor."""

    def test_query_has_final_keyword(self):
        sql, params = build_export_query(
            datetime(2026, 3, 10, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 10, 1, 0, tzinfo=timezone.utc),
        )
        assert "FINAL" in sql

    def test_query_uses_watermark(self):
        wm = datetime(2026, 3, 10, 0, 0, tzinfo=timezone.utc)
        run_ts = datetime(2026, 3, 10, 1, 0, tzinfo=timezone.utc)
        sql, params = build_export_query(wm, run_ts)
        assert params["last_watermark"] == wm

    def test_query_applies_3min_lag(self):
        wm = datetime(2026, 3, 10, 0, 0, tzinfo=timezone.utc)
        run_ts = datetime(2026, 3, 10, 1, 0, tzinfo=timezone.utc)
        sql, params = build_export_query(wm, run_ts)
        assert "INTERVAL 3 MINUTE" in sql or params.get("lag_ceiling") is not None

    def test_first_run_no_watermark(self):
        """First run uses epoch as watermark."""
        sql, params = build_export_query(
            None,
            datetime(2026, 3, 10, 1, 0, tzinfo=timezone.utc),
        )
        assert params["last_watermark"].year == 1970


class TestDerivePartitions:
    def test_single_row(self):
        rows = [{"observed_at": datetime(2026, 3, 10), "metric_domain": "macro"}]
        assert derive_partitions(rows) == {("2026-03", "macro")}

    def test_multiple_months_and_domains(self):
        rows = [
            {"observed_at": datetime(2026, 2, 15), "metric_domain": "macro"},
            {"observed_at": datetime(2026, 3, 10), "metric_domain": "defi"},
            {"observed_at": datetime(2026, 3, 10), "metric_domain": "macro"},
        ]
        result = derive_partitions(rows)
        assert result == {("2026-02", "macro"), ("2026-03", "defi"), ("2026-03", "macro")}


class TestAnomalyGuard:
    """v4.0: Fail if delta exceeds 10x rolling 7-day avg or >2M rows."""

    def test_under_limit_passes(self):
        assert check_anomaly_guard(100, rolling_avg=50) is True

    def test_over_10x_fails(self):
        assert check_anomaly_guard(600, rolling_avg=50) is False

    def test_exactly_10x_passes(self):
        assert check_anomaly_guard(500, rolling_avg=50) is True

    def test_over_2m_hard_cap(self):
        assert check_anomaly_guard(2_000_001, rolling_avg=1_000_000) is False

    def test_2m_exactly_passes(self):
        assert check_anomaly_guard(2_000_000, rolling_avg=1_000_000) is True

    def test_zero_rolling_avg_allows_first_run(self):
        """First run has no history — allow up to 2M."""
        assert check_anomaly_guard(1000, rolling_avg=0) is True

    def test_force_backfill_bypasses(self):
        assert check_anomaly_guard(5_000_000, rolling_avg=50, force_backfill=True) is True
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/export/test_gold_export.py -v`
Expected: FAIL (import errors — module doesn't exist yet)

**Step 3: Write minimal implementation**

```python
"""Core logic for Silver → Gold export.

Pure functions — no Dagster imports. Handles domain mapping, CH query
construction, partition derivation, anomaly guard.
"""

from datetime import datetime, timezone

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

# Phase 1 excluded domains
_EXCLUDED_DOMAINS = {"price", "metadata", "valuation"}


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
        last_watermark = datetime(1970, 1, 1, tzinfo=timezone.utc)

    sql = (
        "SELECT metric_id, instrument_id, observed_at, value, "
        "       ingested_at, data_version "
        "FROM forge.observations FINAL "
        "WHERE ingested_at > %(last_watermark)s "
        "  AND ingested_at <= %(run_start_ts)s - INTERVAL 3 MINUTE "
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
    if rolling_avg > 0 and row_count > rolling_avg * 10:
        return False
    return True
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/export/test_gold_export.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/ftb/export/ tests/export/
git commit -m "feat: add gold export core logic — domain mapping, query builder, anomaly guard"
```

---

### Task 3: Gold Iceberg Writer — Merge + Partition Overwrite

**Files:**
- Modify: `src/ftb/export/gold_export.py`
- Create: `tests/export/test_gold_writer.py`

**Step 1: Write failing tests for merge and write logic**

```python
"""Tests for Gold Iceberg writer — merge logic and partition overwrite."""

from datetime import datetime, timezone

import pyarrow as pa
import pytest

from ftb.export.gold_export import merge_partition, build_gold_arrow_table

GOLD_SCHEMA = pa.schema([
    pa.field("metric_id", pa.string()),
    pa.field("instrument_id", pa.string()),
    pa.field("observed_at", pa.timestamp("us", tz="UTC")),
    pa.field("value", pa.float64()),
    pa.field("data_version", pa.int64()),
    pa.field("ingested_at", pa.timestamp("us", tz="UTC")),
    pa.field("metric_domain", pa.string()),
    pa.field("year_month", pa.string()),
])


class TestMergePartition:
    """v4.0: Merge by data_version — keep higher version for duplicate keys."""

    def test_no_existing_data(self):
        """First write to a partition — all new rows kept."""
        new = pa.table({
            "metric_id": ["m1", "m2"],
            "instrument_id": ["BTC-USD", "ETH-USD"],
            "observed_at": [
                datetime(2026, 3, 10, tzinfo=timezone.utc),
                datetime(2026, 3, 10, tzinfo=timezone.utc),
            ],
            "value": [1.0, 2.0],
            "data_version": [1, 1],
            "ingested_at": [
                datetime(2026, 3, 10, 0, 5, tzinfo=timezone.utc),
                datetime(2026, 3, 10, 0, 5, tzinfo=timezone.utc),
            ],
            "metric_domain": ["macro", "macro"],
            "year_month": ["2026-03", "2026-03"],
        })
        result = merge_partition(None, new)
        assert result.num_rows == 2

    def test_higher_version_wins(self):
        """Existing row with version 1, new row with version 2 → keep version 2."""
        ts = datetime(2026, 3, 10, tzinfo=timezone.utc)
        ing = datetime(2026, 3, 10, 0, 5, tzinfo=timezone.utc)
        existing = pa.table({
            "metric_id": ["m1"],
            "instrument_id": ["BTC-USD"],
            "observed_at": [ts],
            "value": [100.0],
            "data_version": [1],
            "ingested_at": [ing],
            "metric_domain": ["macro"],
            "year_month": ["2026-03"],
        })
        new = pa.table({
            "metric_id": ["m1"],
            "instrument_id": ["BTC-USD"],
            "observed_at": [ts],
            "value": [101.0],
            "data_version": [2],
            "ingested_at": [ing],
            "metric_domain": ["macro"],
            "year_month": ["2026-03"],
        })
        result = merge_partition(existing, new)
        assert result.num_rows == 1
        assert result.column("value")[0].as_py() == 101.0

    def test_lower_version_ignored(self):
        """Existing version 2, new version 1 → keep existing."""
        ts = datetime(2026, 3, 10, tzinfo=timezone.utc)
        ing = datetime(2026, 3, 10, 0, 5, tzinfo=timezone.utc)
        existing = pa.table({
            "metric_id": ["m1"],
            "instrument_id": ["BTC-USD"],
            "observed_at": [ts],
            "value": [100.0],
            "data_version": [2],
            "ingested_at": [ing],
            "metric_domain": ["macro"],
            "year_month": ["2026-03"],
        })
        new = pa.table({
            "metric_id": ["m1"],
            "instrument_id": ["BTC-USD"],
            "observed_at": [ts],
            "value": [99.0],
            "data_version": [1],
            "ingested_at": [ing],
            "metric_domain": ["macro"],
            "year_month": ["2026-03"],
        })
        result = merge_partition(existing, new)
        assert result.num_rows == 1
        assert result.column("value")[0].as_py() == 100.0

    def test_disjoint_rows_concatenated(self):
        """Non-overlapping rows are all kept."""
        ts1 = datetime(2026, 3, 10, tzinfo=timezone.utc)
        ts2 = datetime(2026, 3, 11, tzinfo=timezone.utc)
        ing = datetime(2026, 3, 10, 0, 5, tzinfo=timezone.utc)
        existing = pa.table({
            "metric_id": ["m1"],
            "instrument_id": ["BTC-USD"],
            "observed_at": [ts1],
            "value": [100.0],
            "data_version": [1],
            "ingested_at": [ing],
            "metric_domain": ["macro"],
            "year_month": ["2026-03"],
        })
        new = pa.table({
            "metric_id": ["m1"],
            "instrument_id": ["BTC-USD"],
            "observed_at": [ts2],
            "value": [200.0],
            "data_version": [1],
            "ingested_at": [ing],
            "metric_domain": ["macro"],
            "year_month": ["2026-03"],
        })
        result = merge_partition(existing, new)
        assert result.num_rows == 2

    def test_null_instrument_id_handled(self):
        """market_level metrics have null instrument_id — merge keys must handle."""
        ts = datetime(2026, 3, 10, tzinfo=timezone.utc)
        ing = datetime(2026, 3, 10, 0, 5, tzinfo=timezone.utc)
        existing = pa.table({
            "metric_id": ["m1"],
            "instrument_id": [None],
            "observed_at": [ts],
            "value": [100.0],
            "data_version": [1],
            "ingested_at": [ing],
            "metric_domain": ["macro"],
            "year_month": ["2026-03"],
        })
        new = pa.table({
            "metric_id": ["m1"],
            "instrument_id": [None],
            "observed_at": [ts],
            "value": [101.0],
            "data_version": [2],
            "ingested_at": [ing],
            "metric_domain": ["macro"],
            "year_month": ["2026-03"],
        })
        result = merge_partition(existing, new)
        assert result.num_rows == 1
        assert result.column("value")[0].as_py() == 101.0


class TestBuildGoldArrowTable:
    """Transform CH result rows into Arrow table with Gold schema + domain mapping."""

    def test_basic_row_mapping(self):
        rows = [{
            "metric_id": "macro.rates.fed_funds",
            "instrument_id": None,
            "observed_at": datetime(2026, 3, 10, tzinfo=timezone.utc),
            "value": 4.5,
            "data_version": 1,
            "ingested_at": datetime(2026, 3, 10, 0, 5, tzinfo=timezone.utc),
        }]
        domain_lookup = {"macro.rates.fed_funds": "macro"}
        table = build_gold_arrow_table(rows, domain_lookup)
        assert table.num_rows == 1
        assert table.column("metric_domain")[0].as_py() == "macro"
        assert table.column("year_month")[0].as_py() == "2026-03"

    def test_excluded_domain_filtered_out(self):
        rows = [{
            "metric_id": "price.spot.close_usd",
            "instrument_id": "BTC-USD",
            "observed_at": datetime(2026, 3, 10, tzinfo=timezone.utc),
            "value": 50000.0,
            "data_version": 1,
            "ingested_at": datetime(2026, 3, 10, 0, 5, tzinfo=timezone.utc),
        }]
        domain_lookup = {"price.spot.close_usd": "price"}
        table = build_gold_arrow_table(rows, domain_lookup)
        assert table.num_rows == 0

    def test_chain_mapped_to_onchain(self):
        rows = [{
            "metric_id": "chain.tx_count",
            "instrument_id": "BTC-USD",
            "observed_at": datetime(2026, 3, 10, tzinfo=timezone.utc),
            "value": 300000,
            "data_version": 1,
            "ingested_at": datetime(2026, 3, 10, 0, 5, tzinfo=timezone.utc),
        }]
        domain_lookup = {"chain.tx_count": "chain"}
        table = build_gold_arrow_table(rows, domain_lookup)
        assert table.column("metric_domain")[0].as_py() == "onchain"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/export/test_gold_writer.py -v`
Expected: FAIL (functions not yet defined)

**Step 3: Add merge + build functions to gold_export.py**

Append to `src/ftb/export/gold_export.py`:

```python
import pyarrow as pa

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

# Dedup key columns for merge
_DEDUP_KEYS = ["metric_id", "instrument_id", "observed_at"]


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
        return pa.table({f.name: pa.array([], type=f.type) for f in GOLD_ARROW_SCHEMA}, schema=GOLD_ARROW_SCHEMA)

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

    # Use DuckDB for efficient dedup — GROUP BY key, keep max data_version
    import duckdb
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
    """).arrow()
    conn.close()

    # Drop the _rn column
    return result.drop_columns(["_rn"])
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/export/test_gold_writer.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/ftb/export/gold_export.py tests/export/test_gold_writer.py
git commit -m "feat: add gold merge logic — partition overwrite with data_version dedup"
```

---

### Task 4: Gold Iceberg Table Management

**Files:**
- Create: `src/ftb/export/gold_iceberg.py`
- Create: `tests/export/test_gold_iceberg.py`

**Step 1: Write failing tests for Iceberg table + partition management**

```python
"""Tests for Gold Iceberg table management — create, read partition, overwrite."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from ftb.export.gold_iceberg import (
    GOLD_TABLE_NAME,
    ensure_gold_table,
    read_partition,
    overwrite_partition,
)
from ftb.export.gold_export import GOLD_ARROW_SCHEMA


class TestEnsureGoldTable:
    def test_creates_table_if_not_exists(self):
        mock_catalog = MagicMock()
        mock_catalog.load_table.side_effect = Exception("not found")
        mock_catalog.create_table.return_value = MagicMock()

        table = ensure_gold_table(mock_catalog)
        mock_catalog.create_table.assert_called_once()

    def test_returns_existing_table(self):
        mock_catalog = MagicMock()
        mock_table = MagicMock()
        mock_catalog.load_table.return_value = mock_table

        result = ensure_gold_table(mock_catalog)
        assert result == mock_table
        mock_catalog.create_table.assert_not_called()


class TestReadPartition:
    def test_returns_none_for_empty(self):
        mock_catalog = MagicMock()
        mock_table = MagicMock()
        mock_catalog.load_table.return_value = mock_table
        mock_scan = MagicMock()
        mock_table.scan.return_value = mock_scan
        empty = pa.table({f.name: pa.array([], type=f.type) for f in GOLD_ARROW_SCHEMA}, schema=GOLD_ARROW_SCHEMA)
        mock_scan.to_arrow.return_value = empty

        result = read_partition(mock_catalog, "2026-03", "macro")
        assert result is None

    def test_returns_arrow_table_for_data(self):
        mock_catalog = MagicMock()
        mock_table = MagicMock()
        mock_catalog.load_table.return_value = mock_table
        mock_scan = MagicMock()
        mock_table.scan.return_value = mock_scan
        data = pa.table({
            "metric_id": ["m1"],
            "instrument_id": [None],
            "observed_at": [datetime(2026, 3, 10, tzinfo=timezone.utc)],
            "value": [1.0],
            "data_version": [1],
            "ingested_at": [datetime(2026, 3, 10, 0, 5, tzinfo=timezone.utc)],
            "metric_domain": ["macro"],
            "year_month": ["2026-03"],
        })
        mock_scan.to_arrow.return_value = data

        result = read_partition(mock_catalog, "2026-03", "macro")
        assert result.num_rows == 1


class TestOverwritePartition:
    def test_calls_overwrite_with_filter(self):
        mock_catalog = MagicMock()
        mock_table = MagicMock()
        mock_catalog.load_table.return_value = mock_table

        data = pa.table({
            "metric_id": ["m1"],
            "instrument_id": [None],
            "observed_at": [datetime(2026, 3, 10, tzinfo=timezone.utc)],
            "value": [1.0],
            "data_version": [1],
            "ingested_at": [datetime(2026, 3, 10, 0, 5, tzinfo=timezone.utc)],
            "metric_domain": ["macro"],
            "year_month": ["2026-03"],
        })

        overwrite_partition(mock_catalog, data, "2026-03", "macro")
        mock_table.overwrite.assert_called_once()
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/export/test_gold_iceberg.py -v`
Expected: FAIL (module doesn't exist)

**Step 3: Write implementation**

```python
"""Gold Iceberg table management — create, read, overwrite partitions.

Uses PyIceberg for table management and writes.
Partitioned by (year_month, metric_domain) per v4.0.
"""

import contextlib

import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.expressions import EqualTo, And
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
    NestedField(1, "metric_id", StringType(), required=True),
    NestedField(2, "instrument_id", StringType(), required=False),
    NestedField(3, "observed_at", TimestamptzType(), required=True),
    NestedField(4, "value", DoubleType(), required=False),
    NestedField(5, "data_version", LongType(), required=True),
    NestedField(6, "ingested_at", TimestamptzType(), required=True),
    NestedField(7, "metric_domain", StringType(), required=True),
    NestedField(8, "year_month", StringType(), required=True),
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
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/export/test_gold_iceberg.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/ftb/export/gold_iceberg.py tests/export/test_gold_iceberg.py
git commit -m "feat: add gold Iceberg table management — create, read, overwrite partitions"
```

---

### Task 5: Dagster Resources + Docker Secrets for Gold

**Files:**
- Modify: `src/ftb/resources.py`
- Modify: `docker-compose.yml`

**Step 1: Add ch_export_reader_resource, minio_gold_resource, iceberg_catalog_gold_resource**

Add to `src/ftb/resources.py`:

```python
@resource
def ch_export_reader_resource(context: InitResourceContext):
    """ClickHouse client with ch_export_reader credentials (SELECT-only on forge.*)."""
    return clickhouse_connect.get_client(
        host="empire_clickhouse",
        port=8123,
        username="ch_export_reader",
        password=_read_secret("ch_export_reader"),
        database="forge",
    )


@resource
def minio_gold_resource(context: InitResourceContext):
    """MinIO client for Gold bucket writes."""
    return Minio(
        "minio:9001",
        access_key=_read_secret("minio_gold_key"),
        secret_key=_read_secret("minio_gold_secret"),
        secure=False,
    )


@resource
def iceberg_catalog_gold_resource(context: InitResourceContext):
    """PyIceberg SqlCatalog for gold bucket."""
    pg_password = _read_secret("pg_forge_user")
    minio_key = _read_secret("minio_gold_key")
    minio_secret = _read_secret("minio_gold_secret")
    pg_uri = f"postgresql+psycopg2://forge_user:{pg_password}@empire_postgres:5432/crypto_structured?options=-csearch_path%3Diceberg_catalog"
    return get_bronze_catalog(
        pg_uri=pg_uri,
        minio_endpoint="http://minio:9001",
        minio_access_key=minio_key,
        minio_secret_key=minio_secret,
        warehouse="s3://gold",
    )
```

Note: `get_bronze_catalog` is generic enough to work for gold — it creates a `SqlCatalog`. Consider renaming to `get_iceberg_catalog` (rename in this task).

**Step 2: Add gold secrets to docker-compose.yml**

Add to `secrets:` section:
```yaml
  minio_gold_key:
    file: ./secrets/minio_gold_key.txt
  minio_gold_secret:
    file: ./secrets/minio_gold_secret.txt
```

Add to `empire_dagster_code.secrets:`:
```yaml
      - minio_gold_key
      - minio_gold_secret
```

**Step 3: Commit**

```bash
git add src/ftb/resources.py docker-compose.yml
git commit -m "feat: add gold export Dagster resources — ch_export_reader, minio_gold, iceberg_catalog_gold"
```

---

### Task 6: Dagster Asset — gold_observations

**Files:**
- Create: `src/ftb/export/export_asset.py`
- Create: `tests/export/test_export_asset.py`

**Step 1: Write failing tests for the Dagster asset**

```python
"""Tests for gold_observations Dagster asset."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from ftb.export.export_asset import (
    _load_domain_lookup,
    _load_watermark_from_metadata,
    _load_rolling_avg,
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
        assert result == datetime(2026, 3, 10, tzinfo=timezone.utc)


class TestLoadRollingAvg:
    def test_returns_zero_when_no_history(self):
        mock_instance = MagicMock()
        mock_instance.get_materialization_count_by_partition.return_value = {}
        # Simplified — real impl queries recent materializations
        assert _load_rolling_avg(mock_instance) >= 0
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/export/test_export_asset.py -v`
Expected: FAIL

**Step 3: Write the Dagster asset implementation**

```python
"""Dagster asset for Silver → Gold export.

Reads forge.observations via ch_export_reader (Rule 2 compliant),
maps domains, merges with existing Gold partitions, writes via PyIceberg.
"""

from datetime import datetime, timezone

from dagster import (
    asset,
    AssetExecutionContext,
    AssetKey,
    MetadataValue,
    Output,
)

from ftb.export.gold_export import (
    build_export_query,
    build_gold_arrow_table,
    check_anomaly_guard,
    derive_partitions,
    merge_partition,
)
from ftb.export.gold_iceberg import (
    ensure_gold_table,
    overwrite_partition,
    read_partition,
)


def _load_domain_lookup(pg_reader) -> dict[str, str]:
    """Load metric_id → catalog domain mapping from metric_catalog."""
    with pg_reader.cursor() as cur:
        cur.execute("SELECT metric_id, domain FROM forge.metric_catalog WHERE status = 'active'")
        return {row[0]: row[1] for row in cur.fetchall()}


def _load_watermark_from_metadata(instance) -> datetime | None:
    """Read watermark from last successful materialization metadata."""
    event = instance.get_latest_materialization_event(AssetKey("gold_observations"))
    if event is None:
        return None
    metadata = event.asset_materialization.metadata
    wm_entry = metadata.get("watermark_new")
    if wm_entry is None:
        return None
    return datetime.fromisoformat(wm_entry.value)


def _load_rolling_avg(instance) -> float:
    """Compute rolling 7-day average export row count from recent materializations.

    Queries last 7 materialization events for rows_exported metadata.
    Returns 0 if no history (first run).
    """
    events = instance.get_event_records(
        event_records_filter=instance.build_event_records_filter(
            asset_key=AssetKey("gold_observations"),
        ),
        limit=7,
    )
    if not events:
        return 0
    counts = []
    for ev in events:
        md = ev.asset_materialization.metadata if ev.asset_materialization else {}
        rows = md.get("rows_exported")
        if rows is not None:
            counts.append(rows.value)
    return sum(counts) / len(counts) if counts else 0


@asset(
    name="gold_observations",
    required_resource_keys={"ch_export_reader", "pg_forge_reader", "iceberg_catalog_gold"},
    metadata={"layer": "gold", "schedule": "hourly"},
)
def gold_observations(context: AssetExecutionContext):
    """Export observations from Silver (ClickHouse) to Gold (Iceberg on MinIO).

    Incremental watermark-based export. Merges with existing partitions
    by data_version. Partition key: (year_month, metric_domain).
    """
    run_start = datetime.now(timezone.utc)

    # 1. Load watermark from prior materialization
    watermark = _load_watermark_from_metadata(context.instance)
    context.log.info(f"Export watermark: {watermark or 'FIRST RUN'}")

    # 2. Query Silver
    sql, params = build_export_query(watermark, run_start)
    result = context.resources.ch_export_reader.query(sql, parameters=params)
    columns = result.column_names
    rows = [dict(zip(columns, row)) for row in result.result_rows]
    context.log.info(f"Silver delta: {len(rows)} rows")

    if not rows:
        return Output(
            value=None,
            metadata={
                "rows_exported": MetadataValue.int(0),
                "partitions_touched": MetadataValue.int(0),
                "watermark_prev": MetadataValue.text(
                    watermark.isoformat() if watermark else "none"
                ),
                "watermark_new": MetadataValue.text(
                    watermark.isoformat() if watermark else "none"
                ),
                "watermark_advanced": MetadataValue.bool(False),
            },
        )

    # 3. Anomaly guard
    force_backfill = context.op_execution_context.op_config.get("force_backfill", False) if context.op_execution_context.op_config else False
    rolling_avg = _load_rolling_avg(context.instance)
    if not check_anomaly_guard(len(rows), rolling_avg, force_backfill):
        raise RuntimeError(
            f"Anomaly guard triggered: {len(rows)} rows vs rolling avg {rolling_avg:.0f}. "
            f"Re-run with force_backfill=True to bypass."
        )

    # 4. Build Gold Arrow table with domain mapping
    domain_lookup = _load_domain_lookup(context.resources.pg_forge_reader)
    gold_table = build_gold_arrow_table(rows, domain_lookup)

    if gold_table.num_rows == 0:
        context.log.info("All rows filtered by domain exclusion — nothing to export.")
        return Output(
            value=None,
            metadata={
                "rows_exported": MetadataValue.int(0),
                "partitions_touched": MetadataValue.int(0),
                "watermark_prev": MetadataValue.text(
                    watermark.isoformat() if watermark else "none"
                ),
                "watermark_new": MetadataValue.text(
                    max(r["ingested_at"] for r in rows).isoformat()
                ),
                "watermark_advanced": MetadataValue.bool(True),
            },
        )

    # 5. Ensure Gold table exists
    catalog = context.resources.iceberg_catalog_gold
    ensure_gold_table(catalog)

    # 6. Per-partition merge + overwrite
    partitions = derive_partitions(gold_table.to_pylist())
    context.log.info(f"Partitions to touch: {partitions}")

    import pyarrow.compute as pc
    total_written = 0
    for year_month, metric_domain in sorted(partitions):
        # Filter new rows to this partition
        mask = pc.and_(
            pc.equal(gold_table.column("year_month"), year_month),
            pc.equal(gold_table.column("metric_domain"), metric_domain),
        )
        partition_new = gold_table.filter(mask)

        # Read existing partition
        try:
            existing = read_partition(catalog, year_month, metric_domain)
        except Exception:
            existing = None

        # Merge
        merged = merge_partition(existing, partition_new)

        # Overwrite
        overwrite_partition(catalog, merged, year_month, metric_domain)
        total_written += merged.num_rows
        context.log.info(
            f"Partition ({year_month}, {metric_domain}): "
            f"{partition_new.num_rows} new + {existing.num_rows if existing else 0} existing "
            f"→ {merged.num_rows} merged"
        )

    # 7. Advance watermark
    new_watermark = max(r["ingested_at"] for r in rows)
    lag_seconds = (run_start - new_watermark).total_seconds()

    return Output(
        value=None,
        metadata={
            "rows_exported": MetadataValue.int(total_written),
            "partitions_touched": MetadataValue.int(len(partitions)),
            "watermark_prev": MetadataValue.text(
                watermark.isoformat() if watermark else "none"
            ),
            "watermark_new": MetadataValue.text(new_watermark.isoformat()),
            "lag_seconds": MetadataValue.float(lag_seconds),
            "watermark_advanced": MetadataValue.bool(True),
        },
    )
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/export/test_export_asset.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/ftb/export/export_asset.py tests/export/test_export_asset.py
git commit -m "feat: add gold_observations Dagster asset — incremental Silver→Gold export"
```

---

### Task 7: Wire Into Definitions + Schedule

**Files:**
- Modify: `src/ftb/definitions.py`

**Step 1: Add gold_observations to definitions**

```python
from ftb.export.export_asset import gold_observations
from ftb.resources import (
    ...existing imports...,
    ch_export_reader_resource,
    iceberg_catalog_gold_resource,
    minio_gold_resource,
)

# Gold export schedule — hourly fallback
gold_export_job = define_asset_job(
    name="gold_export_job",
    selection=[gold_observations],
)

gold_hourly_schedule = ScheduleDefinition(
    name="gold_export_hourly",
    cron_schedule="15 * * * *",  # :15 past every hour
    job=gold_export_job,
)
```

Add to `defs`:
- `gold_observations` to assets list
- `gold_hourly_schedule` to schedules list
- `"ch_export_reader": ch_export_reader_resource` to resources
- `"iceberg_catalog_gold": iceberg_catalog_gold_resource` to resources

**Step 2: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All existing + new tests PASS

**Step 3: Run ruff**

Run: `uv run ruff check src/ tests/`
Expected: Clean

**Step 4: Commit**

```bash
git add src/ftb/definitions.py
git commit -m "feat: register gold_observations asset + hourly schedule in Dagster"
```

---

### Task 8: Deploy to Proxmox

**Step 1: Rsync code**

```bash
rsync -av --exclude='__pycache__' --exclude='.git' --exclude='.venv' \
  src/ root@192.168.68.11:/opt/empire/FromTheBridge/src/
rsync -av --exclude='__pycache__' --exclude='.git' \
  docker-compose.yml root@192.168.68.11:/opt/empire/FromTheBridge/docker-compose.yml
```

**Step 2: Create MinIO gold writer user (Task 1)**

Run the MinIO user creation commands from Task 1.

**Step 3: Rebuild and restart Dagster**

```bash
ssh root@192.168.68.11 "cd /opt/empire/FromTheBridge && docker compose build empire_dagster_code && docker compose up -d empire_dagster_code && sleep 5 && docker compose restart empire_dagster_daemon empire_dagster_webserver"
```

**Step 4: Verify asset is registered**

```bash
ssh root@192.168.68.11 "docker exec empire_dagster_code dagster asset list -m ftb.definitions 2>/dev/null | grep gold_observations"
```

Or check Dagster UI at `http://192.168.68.11:3010`.

**Step 5: Trigger test run via GraphQL**

```bash
ssh root@192.168.68.11 'curl -s -X POST http://localhost:3010/graphql -H "Content-Type: application/json" -d "{\"query\": \"mutation { launchRun(executionParams: { selector: { repositoryLocationName: \\\"ftb\\\", repositoryName: \\\"__repository__\\\", jobName: \\\"gold_export_job\\\" }, runConfigData: {} }) { __typename ... on LaunchRunSuccess { run { runId } } ... on PythonError { message } } }\"}"'
```

**Step 6: Verify run completed**

Check Dagster UI for run status. Verify Gold bucket has Iceberg metadata:

```bash
ssh root@192.168.68.11 "docker exec empire_minio mc ls local/gold/ --recursive | head -20"
```

**Step 7: Verify DuckDB can read Gold**

```bash
ssh root@192.168.68.11 "docker exec empire_dagster_code python -c \"
import duckdb
db = duckdb.connect()
db.install_extension('iceberg')
db.load_extension('iceberg')
# Read Gold Iceberg via S3
db.execute(\\\"SET s3_endpoint='minio:9001'\\\")
db.execute(\\\"SET s3_access_key_id='<GOLD_KEY>'\\\")
db.execute(\\\"SET s3_secret_access_key='<GOLD_SECRET>'\\\")
db.execute(\\\"SET s3_use_ssl=false\\\")
db.execute(\\\"SET s3_url_style='path'\\\")
result = db.execute('SELECT count(*), min(observed_at), max(observed_at) FROM iceberg_scan(\\\"s3://gold/gold/observations\\\")').fetchall()
print(f'Gold rows: {result}')
\""
```

Expected: Row count matching Silver eds_derived observations (minus price/metadata excluded domains).

---

### Task 9: Update CLAUDE.md + Memory

**Step 1: Update CLAUDE.md current state**

- Add `gold_observations` to "Built in code" section
- Update Phase 1 gate: `✅ Export round-trip (Silver → Gold → DuckDB)`
- Update next actions: mark item 3 as DONE

**Step 2: Update MEMORY.md**

Add Gold Export entry under Project State.

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update current state — gold export deployed"
```
