# empire_to_forge_sync Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the sync bridge Dagster asset that reads `empire.observations` and writes validated rows to `forge.observations` with `source_id='eds_derived'`.

**Architecture:** Watermark-based incremental sync using Dagster cursor. Business logic in `src/ftb/sync/bridge.py` (pure functions), orchestration in `src/ftb/sync/sync_asset.py`. Reuses existing Silver writer, dead letter writer, validation, and collection event writer.

**Tech Stack:** Dagster (asset + schedule + cursor), clickhouse-connect, psycopg2, existing FTB writers/validation.

---

### Task 1: SQL Migrations — `eds_derived` source + ClickHouse reader user

**Files:**
- Create: `db/migrations/postgres/0005_eds_derived_source.sql`
- Create: `db/migrations/clickhouse/0003_empire_reader_user.sql`

**Step 1: Write PG migration**

```sql
-- db/migrations/postgres/0005_eds_derived_source.sql
-- Add eds_derived source for empire_to_forge_sync bridge
INSERT INTO forge.source_catalog (
    source_id, source_name, source_type, api_base_url,
    rate_limit_per_minute, is_active, redistribution_allowed,
    requires_api_key, api_key_secret_path, notes
) VALUES (
    'eds_derived',
    'EDS Derived Metrics',
    'internal',
    NULL,
    NULL,
    true,
    true,
    false,
    NULL,
    'Metrics derived by EDS and synced via empire_to_forge_sync'
);
```

**Step 2: Write CH migration**

```sql
-- db/migrations/clickhouse/0003_empire_reader_user.sql
-- Read-only user for empire_to_forge_sync to query empire.observations
CREATE USER IF NOT EXISTS ch_empire_reader
    IDENTIFIED WITH sha256_password BY 'PLACEHOLDER_REPLACE_WITH_SECRET'
    SETTINGS PROFILE 'readonly_profile';

GRANT SELECT ON empire.observations TO ch_empire_reader;
```

**Step 3: Deploy PG migration to proxmox**

```bash
cat db/migrations/postgres/0005_eds_derived_source.sql | \
  ssh root@192.168.68.11 "docker exec -i empire_postgres psql -U forge_user -d crypto_structured"
```

Expected: `INSERT 0 1`

**Step 4: Generate ch_empire_reader secret and deploy CH migration**

```bash
# Generate password
python3 -c "import secrets; print(secrets.token_hex(16))" > secrets/ch_empire_reader.txt

# Replace placeholder in migration and deploy
SECRET=$(cat secrets/ch_empire_reader.txt)
sed "s/PLACEHOLDER_REPLACE_WITH_SECRET/$SECRET/" db/migrations/clickhouse/0003_empire_reader_user.sql | \
  ssh root@192.168.68.11 "docker exec -i empire_clickhouse clickhouse-client --multiquery"
```

Expected: No errors.

**Step 5: Verify both migrations**

```bash
ssh root@192.168.68.11 "docker exec -i empire_postgres psql -U forge_reader -d crypto_structured -c \"SELECT source_id, source_name, source_type FROM forge.source_catalog WHERE source_id = 'eds_derived'\""
ssh root@192.168.68.11 "docker exec -i empire_clickhouse clickhouse-client --query \"SELECT count() FROM empire.observations\" --user ch_empire_reader --password $(cat secrets/ch_empire_reader.txt)"
```

Expected: 1 row for eds_derived; count returns 6589 (or current count).

**Step 6: Commit**

```bash
git add db/migrations/postgres/0005_eds_derived_source.sql db/migrations/clickhouse/0003_empire_reader_user.sql secrets/ch_empire_reader.txt
git commit -m "feat: add eds_derived source + ch_empire_reader user for sync bridge"
```

Note: Do NOT commit the actual secret value in the CH migration. The migration file keeps `PLACEHOLDER_REPLACE_WITH_SECRET` — the `sed` replacement is a deploy-time operation only. The `secrets/` directory is gitignored (verify with `cat .gitignore`). If not gitignored, do NOT add `secrets/ch_empire_reader.txt`.

---

### Task 2: Business Logic — `src/ftb/sync/bridge.py`

**Files:**
- Create: `src/ftb/sync/__init__.py`
- Create: `src/ftb/sync/bridge.py`
- Create: `tests/sync/__init__.py`
- Create: `tests/sync/test_bridge.py`

**Step 1: Write the failing tests**

```python
# tests/sync/__init__.py
# (empty)

# tests/sync/test_bridge.py
"""Tests for empire_to_forge_sync business logic."""
from datetime import datetime, timezone

import pytest

from ftb.sync.bridge import (
    map_empire_to_forge,
    build_empire_query,
    INSTRUMENT_ID_MARKET,
)
from ftb.validation.core import Observation


class TestMapEmpireToForge:
    """Test empire.observations → forge Observation mapping."""

    def test_market_level_metric_maps_instrument_to_none(self):
        """empire uses '__market__' string; forge uses None."""
        rows = [
            {
                "metric_id": "macro.rates.fed_funds_effective",
                "instrument_id": "__market__",
                "source_id": "eds_fred",
                "observed_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
                "value": 5.33,
            }
        ]
        promoted = {"macro.rates.fed_funds_effective"}
        result = map_empire_to_forge(rows, promoted)
        assert len(result) == 1
        assert result[0].instrument_id is None
        assert result[0].source_id == "eds_derived"
        assert result[0].metric_id == "macro.rates.fed_funds_effective"
        assert result[0].value == 5.33

    def test_instrument_scoped_metric_preserves_instrument_id(self):
        """Non-market instrument_id passes through."""
        rows = [
            {
                "metric_id": "chain.valuation.mvrv_ratio",
                "instrument_id": "BTC-USD",
                "source_id": "eds_node_derivation",
                "observed_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
                "value": 1.85,
            }
        ]
        promoted = {"chain.valuation.mvrv_ratio"}
        result = map_empire_to_forge(rows, promoted)
        assert len(result) == 1
        assert result[0].instrument_id == "BTC-USD"

    def test_unpromoted_metrics_filtered_out(self):
        """Only metrics in promoted set are mapped."""
        rows = [
            {
                "metric_id": "some.unknown.metric",
                "instrument_id": "__market__",
                "source_id": "eds_fred",
                "observed_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
                "value": 42.0,
            },
            {
                "metric_id": "macro.rates.fed_funds_effective",
                "instrument_id": "__market__",
                "source_id": "eds_fred",
                "observed_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
                "value": 5.33,
            },
        ]
        promoted = {"macro.rates.fed_funds_effective"}
        result = map_empire_to_forge(rows, promoted)
        assert len(result) == 1
        assert result[0].metric_id == "macro.rates.fed_funds_effective"

    def test_empty_rows_returns_empty_list(self):
        result = map_empire_to_forge([], {"any"})
        assert result == []

    def test_all_observations_get_eds_derived_source(self):
        """Every mapped observation has source_id='eds_derived'."""
        rows = [
            {
                "metric_id": "m1",
                "instrument_id": "__market__",
                "source_id": "eds_fred",
                "observed_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
                "value": 1.0,
            },
            {
                "metric_id": "m2",
                "instrument_id": "BTC-USD",
                "source_id": "eds_node_derivation",
                "observed_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
                "value": 2.0,
            },
        ]
        promoted = {"m1", "m2"}
        result = map_empire_to_forge(rows, promoted)
        assert all(o.source_id == "eds_derived" for o in result)


class TestBuildEmpireQuery:
    """Test SQL query construction for empire.observations."""

    def test_first_run_no_watermark(self):
        """Without watermark, query selects all rows for promoted metrics."""
        sql, params = build_empire_query(
            metric_ids=["macro.rates.fed_funds_effective", "chain.valuation.mvrv_ratio"],
            watermark=None,
        )
        assert "metric_id IN" in sql
        assert "ingested_at >" not in sql
        assert len(params["metric_ids"]) == 2

    def test_incremental_with_watermark(self):
        """With watermark, query adds ingested_at filter."""
        wm = datetime(2024, 1, 15, tzinfo=timezone.utc)
        sql, params = build_empire_query(
            metric_ids=["macro.rates.fed_funds_effective"],
            watermark=wm,
        )
        assert "ingested_at >" in sql
        assert params["watermark"] == wm
```

**Step 2: Run tests to verify they fail**

```bash
cd /var/home/stephen/Projects/FromTheBridge && python -m pytest tests/sync/test_bridge.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'ftb.sync'`

**Step 3: Write implementation**

```python
# src/ftb/sync/__init__.py
# (empty)

# src/ftb/sync/bridge.py
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
    - Maps instrument_id '__market__' → None (C2 resolution)
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
```

**Step 4: Run tests to verify they pass**

```bash
cd /var/home/stephen/Projects/FromTheBridge && python -m pytest tests/sync/test_bridge.py -v
```

Expected: All 7 tests PASS.

**Step 5: Commit**

```bash
git add src/ftb/sync/ tests/sync/
git commit -m "feat: add sync bridge business logic — map empire obs to forge format"
```

---

### Task 3: Dagster Resource — `ch_empire_reader`

**Files:**
- Modify: `src/ftb/resources.py` (add resource)
- Modify: `docker-compose.yml` (add secret mount)

**Step 1: Add resource to `resources.py`**

Add after `minio_bronze_resource` (line 61):

```python
@resource
def ch_empire_reader_resource(context: InitResourceContext):
    """ClickHouse client with ch_empire_reader credentials (SELECT-only on empire.*)."""
    return clickhouse_connect.get_client(
        host="empire_clickhouse",
        port=8123,
        username="ch_empire_reader",
        password=_read_secret("ch_empire_reader"),
        database="empire",
    )
```

**Step 2: Add secret to `docker-compose.yml`**

In the `empire_dagster_code` service `secrets:` list, add:
```yaml
      - ch_empire_reader
```

In the top-level `secrets:` section, add:
```yaml
  ch_empire_reader:
    file: ./secrets/ch_empire_reader.txt
```

**Step 3: Commit**

```bash
git add src/ftb/resources.py docker-compose.yml
git commit -m "feat: add ch_empire_reader resource + Docker secret mount"
```

---

### Task 4: Dagster Asset — `src/ftb/sync/sync_asset.py`

**Files:**
- Create: `src/ftb/sync/sync_asset.py`
- Create: `tests/sync/test_sync_asset.py`

**Step 1: Write the failing test**

```python
# tests/sync/test_sync_asset.py
"""Tests for empire_to_forge_sync Dagster asset logic.

Tests the validate_and_split helper and metadata output.
Does NOT test Dagster execution context (that's integration).
"""
import json
from datetime import datetime, timezone

import pytest

from ftb.sync.sync_asset import validate_and_split
from ftb.validation.core import Observation
from ftb.writers.silver import DeadLetterRow


@pytest.fixture
def metric_catalog():
    return {
        "macro.rates.fed_funds_effective": {
            "is_nullable": False,
            "expected_range_low": 0.0,
            "expected_range_high": 25.0,
        },
        "chain.valuation.mvrv_ratio": {
            "is_nullable": False,
            "expected_range_low": 0.0,
            "expected_range_high": 100.0,
        },
    }


@pytest.fixture
def instrument_set():
    return {"BTC-USD", "ETH-USD", "SOL-USD"}


class TestValidateAndSplit:
    def test_all_valid(self, metric_catalog, instrument_set):
        obs = [
            Observation("macro.rates.fed_funds_effective", None, "eds_derived",
                       datetime(2024, 1, 15, tzinfo=timezone.utc), 5.33),
        ]
        valid, dead = validate_and_split(obs, metric_catalog, instrument_set)
        assert len(valid) == 1
        assert len(dead) == 0

    def test_unknown_metric_goes_to_dead_letter(self, metric_catalog, instrument_set):
        obs = [
            Observation("unknown.metric", None, "eds_derived",
                       datetime(2024, 1, 15, tzinfo=timezone.utc), 1.0),
        ]
        valid, dead = validate_and_split(obs, metric_catalog, instrument_set)
        assert len(valid) == 0
        assert len(dead) == 1
        assert dead[0].rejection_code == "UNKNOWN_METRIC"

    def test_range_violation_goes_to_dead_letter(self, metric_catalog, instrument_set):
        obs = [
            Observation("macro.rates.fed_funds_effective", None, "eds_derived",
                       datetime(2024, 1, 15, tzinfo=timezone.utc), -5.0),
        ]
        valid, dead = validate_and_split(obs, metric_catalog, instrument_set)
        assert len(valid) == 0
        assert len(dead) == 1
        assert dead[0].rejection_code == "RANGE_VIOLATION"

    def test_mixed_valid_and_invalid(self, metric_catalog, instrument_set):
        obs = [
            Observation("macro.rates.fed_funds_effective", None, "eds_derived",
                       datetime(2024, 1, 15, tzinfo=timezone.utc), 5.33),
            Observation("unknown.metric", None, "eds_derived",
                       datetime(2024, 1, 15, tzinfo=timezone.utc), 1.0),
        ]
        valid, dead = validate_and_split(obs, metric_catalog, instrument_set)
        assert len(valid) == 1
        assert len(dead) == 1

    def test_dead_letter_has_raw_payload(self, metric_catalog, instrument_set):
        obs = [
            Observation("unknown.metric", None, "eds_derived",
                       datetime(2024, 1, 15, tzinfo=timezone.utc), 1.0),
        ]
        _, dead = validate_and_split(obs, metric_catalog, instrument_set)
        payload = json.loads(dead[0].raw_payload)
        assert payload["metric_id"] == "unknown.metric"
        assert payload["value"] == 1.0
```

**Step 2: Run tests to verify they fail**

```bash
cd /var/home/stephen/Projects/FromTheBridge && python -m pytest tests/sync/test_sync_asset.py -v
```

Expected: FAIL — `ImportError: cannot import name 'validate_and_split' from 'ftb.sync.sync_asset'`

**Step 3: Write implementation**

```python
# src/ftb/sync/sync_asset.py
"""Dagster asset for empire_to_forge_sync — the EDS→FTB bridge.

Reads empire.observations for promoted metrics, validates against
forge.metric_catalog, writes to forge.observations with source_id='eds_derived'.
Uses cursor-based incremental sync (watermark on ingested_at).
"""
import json
from datetime import datetime, timezone

from dagster import asset, AssetExecutionContext, Output, MetadataValue

from ftb.sync.bridge import map_empire_to_forge, build_empire_query
from ftb.validation.core import Observation, validate_observation
from ftb.writers.silver import DeadLetterRow, write_observations, write_dead_letter
from ftb.writers.collection import write_collection_event


def validate_and_split(
    observations: list[Observation],
    metric_catalog: dict[str, dict],
    instrument_set: set[str],
) -> tuple[list[Observation], list[DeadLetterRow]]:
    """Validate observations and split into valid + dead letter lists."""
    valid = []
    dead = []
    for obs in observations:
        result = validate_observation(obs, metric_catalog, instrument_set)
        if result.is_valid:
            valid.append(obs)
        else:
            dead.append(
                DeadLetterRow(
                    source_id=obs.source_id,
                    metric_id=obs.metric_id,
                    instrument_id=obs.instrument_id,
                    raw_payload=json.dumps({
                        "metric_id": obs.metric_id,
                        "instrument_id": obs.instrument_id,
                        "value": obs.value,
                        "observed_at": obs.observed_at.isoformat(),
                    }),
                    rejection_reason=result.rejection_reason or "",
                    rejection_code=result.rejection_code or "",
                )
            )
    return valid, dead


def _load_promoted_metrics(pg_reader) -> dict[str, dict]:
    """Load metric_catalog rows that list eds_derived as a source."""
    with pg_reader.cursor() as cur:
        cur.execute(
            "SELECT metric_id, is_nullable, expected_range_low, expected_range_high "
            "FROM forge.metric_catalog "
            "WHERE 'eds_derived' = ANY(sources) AND status = 'active'"
        )
        return {
            row[0]: {
                "is_nullable": row[1],
                "expected_range_low": row[2],
                "expected_range_high": row[3],
            }
            for row in cur.fetchall()
        }


def _load_instrument_set(pg_reader) -> set[str]:
    """Load instrument IDs from forge.instruments."""
    with pg_reader.cursor() as cur:
        cur.execute("SELECT instrument_id FROM forge.instruments")
        return {row[0] for row in cur.fetchall()}


def _query_empire(ch_reader, metric_ids: list[str], watermark: datetime | None) -> list[dict]:
    """Query empire.observations for promoted metrics since watermark."""
    sql, params = build_empire_query(metric_ids, watermark)
    result = ch_reader.query(sql, parameters=params)
    columns = result.column_names
    return [dict(zip(columns, row)) for row in result.result_rows]


@asset(
    name="empire_to_forge_sync",
    required_resource_keys={"ch_empire_reader", "ch_writer", "pg_forge", "pg_forge_reader"},
    metadata={"source_id": "eds_derived", "cadence_hours": 6},
)
def empire_to_forge_sync(context: AssetExecutionContext):
    """Sync promoted metrics from empire.observations → forge.observations.

    Incremental: uses Dagster cursor to track last ingested_at watermark.
    """
    started_at = datetime.now(timezone.utc)

    # 1. Load catalog
    metric_catalog = _load_promoted_metrics(context.resources.pg_forge_reader)
    if not metric_catalog:
        context.log.info("No promoted metrics found for eds_derived — nothing to sync.")
        return Output(
            value=None,
            metadata={"observations_written": MetadataValue.int(0), "status": MetadataValue.text("no_promoted_metrics")},
        )

    instrument_set = _load_instrument_set(context.resources.pg_forge_reader)
    metric_ids = list(metric_catalog.keys())

    # 2. Read watermark
    cursor_str = context.cursor
    watermark = datetime.fromisoformat(cursor_str) if cursor_str else None
    context.log.info(f"Sync watermark: {watermark or 'FIRST RUN (full sync)'}")

    # 3. Query empire
    rows = _query_empire(context.resources.ch_empire_reader, metric_ids, watermark)
    context.log.info(f"Queried {len(rows)} rows from empire.observations")
    if not rows:
        return Output(
            value=None,
            metadata={"observations_written": MetadataValue.int(0), "status": MetadataValue.text("no_new_rows")},
        )

    # 4. Map to forge Observations
    observations = map_empire_to_forge(rows, set(metric_ids))

    # 5. Validate + split
    valid_obs, dead_letters = validate_and_split(observations, metric_catalog, instrument_set)
    context.log.info(f"Valid: {len(valid_obs)}, Dead letter: {len(dead_letters)}")

    # 6. Write Silver
    written = write_observations(context.resources.ch_writer, valid_obs)
    rejected = write_dead_letter(context.resources.ch_writer, dead_letters)

    # 7. Collection event
    write_collection_event(
        context.resources.pg_forge,
        source_id="eds_derived",
        status="completed" if not dead_letters else "partial",
        started_at=started_at,
        observations_written=written,
        observations_rejected=rejected,
        metrics_covered=list({o.metric_id for o in valid_obs}),
        instruments_covered=list({o.instrument_id for o in valid_obs if o.instrument_id}),
    )

    # 8. Update watermark to max ingested_at from batch
    max_ingested = max(row["ingested_at"] for row in rows)
    context.update_cursor(max_ingested.isoformat())
    context.log.info(f"Updated watermark to {max_ingested.isoformat()}")

    return Output(
        value=None,
        metadata={
            "observations_written": MetadataValue.int(written),
            "observations_rejected": MetadataValue.int(rejected),
            "watermark": MetadataValue.text(max_ingested.isoformat()),
            "metrics_synced": MetadataValue.text(", ".join(sorted({o.metric_id for o in valid_obs}))),
        },
    )
```

**Step 4: Run tests to verify they pass**

```bash
cd /var/home/stephen/Projects/FromTheBridge && python -m pytest tests/sync/test_sync_asset.py -v
```

Expected: All 5 tests PASS.

**Step 5: Commit**

```bash
git add src/ftb/sync/sync_asset.py tests/sync/test_sync_asset.py
git commit -m "feat: add empire_to_forge_sync Dagster asset with cursor-based incremental sync"
```

---

### Task 5: Wire Into Definitions + Schedule

**Files:**
- Modify: `src/ftb/definitions.py`

**Step 1: Update definitions.py**

```python
# Add import at top (after existing imports):
from ftb.sync.sync_asset import empire_to_forge_sync
from ftb.resources import (
    ch_empire_reader_resource,
    ch_writer_resource,
    minio_bronze_resource,
    pg_forge_resource,
    pg_forge_reader_resource,
)

# Add job + schedule after tiingo definitions:
sync_job = define_asset_job(
    name="empire_to_forge_sync_job",
    selection=[empire_to_forge_sync],
)

sync_6h_schedule = ScheduleDefinition(
    name="empire_to_forge_sync_6h",
    cron_schedule="30 */6 * * *",  # :30 past every 6th hour (offset from Tiingo at :15)
    job=sync_job,
)

# Update Definitions:
defs = dagster.Definitions(
    assets=[collect_tiingo_price, empire_to_forge_sync],
    schedules=[tiingo_6h_schedule, sync_6h_schedule],
    resources={
        "ch_writer": ch_writer_resource,
        "ch_empire_reader": ch_empire_reader_resource,
        "pg_forge": pg_forge_resource,
        "pg_forge_reader": pg_forge_reader_resource,
        "minio_bronze": minio_bronze_resource,
        "tiingo_api_key": tiingo_api_key_resource,
    },
)
```

Note: `sync_6h_schedule` does NOT need `execution_fn` or `partition_key` — this asset is unpartitioned (cursor-based).

**Step 2: Verify Dagster loads definitions locally**

```bash
cd /var/home/stephen/Projects/FromTheBridge && python -c "from ftb.definitions import defs; print(f'Assets: {len(defs.get_all_asset_specs())}'); print(f'Schedules: {len(list(defs.schedules))}')"
```

Expected: `Assets: 2`, `Schedules: 2`

**Step 3: Commit**

```bash
git add src/ftb/definitions.py
git commit -m "feat: register empire_to_forge_sync asset + 6h schedule in Dagster definitions"
```

---

### Task 6: Integration Test — Full Sync Path (mocked clients)

**Files:**
- Create: `tests/sync/test_integration.py`

**Step 1: Write integration test**

```python
# tests/sync/test_integration.py
"""Integration test — full sync path with mocked DB clients."""
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from ftb.sync.bridge import map_empire_to_forge
from ftb.sync.sync_asset import validate_and_split
from ftb.validation.core import Observation


@pytest.fixture
def empire_rows():
    """Simulated empire.observations query result."""
    return [
        {
            "metric_id": "chain.valuation.mvrv_ratio",
            "instrument_id": "BTC-USD",
            "source_id": "eds_node_derivation",
            "observed_at": datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc),
            "ingested_at": datetime(2024, 6, 15, 12, 5, tzinfo=timezone.utc),
            "value": 2.15,
        },
        {
            "metric_id": "chain.valuation.mvrv_ratio",
            "instrument_id": "__market__",
            "source_id": "eds_node_derivation",
            "observed_at": datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc),
            "ingested_at": datetime(2024, 6, 15, 12, 5, tzinfo=timezone.utc),
            "value": 1.95,
        },
        {
            "metric_id": "not.promoted.metric",
            "instrument_id": "__market__",
            "source_id": "eds_fred",
            "observed_at": datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc),
            "ingested_at": datetime(2024, 6, 15, 12, 5, tzinfo=timezone.utc),
            "value": 42.0,
        },
    ]


@pytest.fixture
def metric_catalog():
    return {
        "chain.valuation.mvrv_ratio": {
            "is_nullable": False,
            "expected_range_low": 0.0,
            "expected_range_high": 100.0,
        },
    }


@pytest.fixture
def instrument_set():
    return {"BTC-USD", "ETH-USD", "SOL-USD"}


class TestFullSyncPath:
    def test_end_to_end_map_validate_split(self, empire_rows, metric_catalog, instrument_set):
        """Full path: empire rows → map → validate → split."""
        promoted = {"chain.valuation.mvrv_ratio"}

        # Map
        observations = map_empire_to_forge(empire_rows, promoted)
        assert len(observations) == 2  # 3rd row filtered (not promoted)

        # Validate + split
        valid, dead = validate_and_split(observations, metric_catalog, instrument_set)

        # BTC-USD row is valid, __market__ mapped to None is also valid (market-level)
        assert len(valid) == 2
        assert len(dead) == 0

        # Verify source_id rewrite
        assert all(o.source_id == "eds_derived" for o in valid)

        # Verify instrument_id mapping
        instruments = {o.instrument_id for o in valid}
        assert "BTC-USD" in instruments
        assert None in instruments  # was __market__

    def test_watermark_advances(self, empire_rows):
        """max(ingested_at) from batch becomes next watermark."""
        max_ingested = max(r["ingested_at"] for r in empire_rows)
        assert max_ingested == datetime(2024, 6, 15, 12, 5, tzinfo=timezone.utc)
```

**Step 2: Run all sync tests**

```bash
cd /var/home/stephen/Projects/FromTheBridge && python -m pytest tests/sync/ -v
```

Expected: All tests PASS (7 bridge + 5 asset + 2 integration = 14 total).

**Step 3: Commit**

```bash
git add tests/sync/test_integration.py
git commit -m "test: add integration test for full sync path"
```

---

### Task 7: Deploy + Verify on Proxmox

**Files:**
- No new files — deploy existing code

**Step 1: Run full test suite locally**

```bash
cd /var/home/stephen/Projects/FromTheBridge && python -m pytest tests/ -v
```

Expected: All tests PASS (existing Tiingo tests + new sync tests).

**Step 2: Rsync to proxmox**

```bash
rsync -av --exclude='__pycache__' --exclude='.git' \
  /var/home/stephen/Projects/FromTheBridge/src/ \
  root@192.168.68.11:/opt/empire/FromTheBridge/src/

rsync -av --exclude='__pycache__' --exclude='.git' \
  /var/home/stephen/Projects/FromTheBridge/docker-compose.yml \
  root@192.168.68.11:/opt/empire/FromTheBridge/docker-compose.yml

rsync -av \
  /var/home/stephen/Projects/FromTheBridge/secrets/ch_empire_reader.txt \
  root@192.168.68.11:/opt/empire/FromTheBridge/secrets/ch_empire_reader.txt
```

**Step 3: Rebuild and restart Dagster**

```bash
ssh root@192.168.68.11 'cd /opt/empire/FromTheBridge && docker compose build empire_dagster_code && docker compose up -d empire_dagster_code && sleep 5 && docker compose restart empire_dagster_daemon empire_dagster_webserver'
```

Wait for containers to stabilize (~15s).

**Step 4: Verify code server loaded the new asset**

```bash
ssh root@192.168.68.11 "docker logs empire_dagster_code 2>&1 | tail -20"
```

Expected: gRPC server started, no import errors.

**Step 5: Verify in Dagster UI**

Open `http://192.168.68.11:3010` → Assets → should see `empire_to_forge_sync` alongside `collect_tiingo_price`.

**Step 6: Commit deployment verification**

No code changes — just confirm deployment is green.

---

### Task 8: First Promotion Test (optional — requires EDS metrics to promote)

This task is contingent on having metrics in `forge.metric_catalog` with `eds_derived` in their sources array. Currently 0 metrics reference eds_derived.

**To test the sync path end-to-end:**

1. Identify an EDS metric with >=7 days freshness in `empire.observations`
2. Add `eds_derived` to that metric's sources array in `forge.metric_catalog`
3. Trigger `empire_to_forge_sync` manually in Dagster UI
4. Verify rows appear in `forge.observations` with `source_id='eds_derived'`
5. Verify collection event logged in `forge.collection_events`

This is a manual smoke test, not automated — it requires live DB state.

---

## Summary

| Task | What | Files | Tests |
|------|------|-------|-------|
| 1 | SQL migrations + deploy | 2 SQL files | Manual verify |
| 2 | Business logic (`bridge.py`) | 2 src + 2 test | 7 unit |
| 3 | Dagster resource | 2 modified | — |
| 4 | Dagster asset | 1 src + 1 test | 5 unit |
| 5 | Definitions wiring | 1 modified | Import check |
| 6 | Integration test | 1 test | 2 integration |
| 7 | Deploy to proxmox | — | Manual verify |
| 8 | First promotion (optional) | — | Manual smoke |

**Total:** ~14 automated tests, 4 new source files, 3 modified files, 2 SQL migrations.
