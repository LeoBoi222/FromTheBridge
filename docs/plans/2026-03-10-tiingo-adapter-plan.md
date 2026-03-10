# Tiingo Adapter Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the first adapter (Tiingo crypto OHLCV) with shared writer infrastructure, end-to-end from API fetch through Bronze and Silver writes, registered as a Dagster SDA with daily partitions.

**Architecture:** Composition pattern — adapter orchestrates calls to shared writers (bronze.py, silver.py, collection.py). New `instrument_source_map` table for cross-source symbol resolution. Dagster daily partitions for backfill + live collection.

**Tech Stack:** Python 3.12, Dagster 1.9+, httpx, clickhouse-connect, minio, pyarrow

---

### Task 1: Add Python Dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `docker/dagster/Dockerfile`

**Step 1: Update pyproject.toml**

Add to `dependencies`:
```toml
dependencies = [
    "dagster>=1.9",
    "dagster-postgres>=0.25",
    "httpx>=0.27",
    "clickhouse-connect>=0.7",
    "minio>=7.2",
    "pyarrow>=15.0",
    "psycopg2-binary>=2.9",
]
```

**Step 2: Update Dockerfile**

Replace the pip install dagster block to install from pyproject.toml only (remove separate dagster install since it's now in pyproject.toml):

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /opt/empire/pipeline/pyproject.toml
COPY src/ /opt/empire/pipeline/src/

WORKDIR /opt/empire/pipeline
RUN pip install --no-cache-dir -e ".[dagster-webserver]"

ENV DAGSTER_HOME=/opt/dagster/home
RUN mkdir -p /opt/dagster/home /opt/dagster/storage/logs /opt/dagster/storage/artifacts

COPY docker/dagster/dagster.yaml /opt/dagster/home/dagster.yaml
COPY docker/dagster/workspace.yaml /opt/dagster/home/workspace.yaml
```

Add `dagster-webserver` as an optional dependency in pyproject.toml:
```toml
[project.optional-dependencies]
dagster-webserver = ["dagster-webserver>=1.9"]
```

This avoids installing webserver in the code server container (only needed by the webserver service).

**Step 3: Verify locally**

Run: `cd /var/home/stephen/Projects/FromTheBridge && uv sync`
Expected: All dependencies resolve

**Step 4: Commit**

```bash
git add pyproject.toml docker/dagster/Dockerfile
git commit -m "chore: add adapter dependencies (httpx, clickhouse-connect, minio, pyarrow)"
```

---

### Task 2: Create instrument_source_map Migration

**Files:**
- Create: `db/migrations/postgres/0005_instrument_source_map.sql`

**Step 1: Write migration**

```sql
-- =============================================================================
-- instrument_source_map — cross-source symbol resolution
-- Target: empire_postgres (port 5433), database: crypto_structured
-- Execute: cat this_file.sql | ssh root@192.168.68.11 "docker exec -i empire_postgres psql -U crypto_user -d crypto_structured"
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS forge.instrument_source_map (
    instrument_id   TEXT NOT NULL REFERENCES forge.instruments(instrument_id),
    source_id       TEXT NOT NULL REFERENCES forge.source_catalog(source_id),
    source_symbol   TEXT NOT NULL,
    PRIMARY KEY (instrument_id, source_id)
);

CREATE INDEX idx_instrument_source_map_source
    ON forge.instrument_source_map (source_id);

-- Grant permissions (match existing pattern)
GRANT SELECT, INSERT ON forge.instrument_source_map TO forge_writer;
GRANT SELECT ON forge.instrument_source_map TO forge_reader;

-- Seed Tiingo mappings
INSERT INTO forge.instrument_source_map (instrument_id, source_id, source_symbol) VALUES
    ('BTC-USD', 'tiingo', 'btcusd'),
    ('ETH-USD', 'tiingo', 'ethusd'),
    ('SOL-USD', 'tiingo', 'solusd')
ON CONFLICT DO NOTHING;

-- Seed metric_lineage rows for Tiingo (if not present)
INSERT INTO forge.metric_lineage (metric_id, source_id, compute_agent, is_primary) VALUES
    ('price.spot.close_usd',      'tiingo', 'collect_tiingo_price', true),
    ('price.spot.volume_usd_24h', 'tiingo', 'collect_tiingo_price', true),
    ('price.spot.ohlcv',          'tiingo', 'collect_tiingo_price', true)
ON CONFLICT DO NOTHING;

COMMIT;
```

**Step 2: Deploy migration**

Run: `cat db/migrations/postgres/0005_instrument_source_map.sql | ssh root@192.168.68.11 "docker exec -i empire_postgres psql -U crypto_user -d crypto_structured"`
Expected: `CREATE TABLE`, `CREATE INDEX`, `GRANT`, `INSERT 0 3`, `INSERT 0 3`

**Step 3: Verify**

Run: `ssh root@192.168.68.11 "docker exec -i empire_postgres psql -U forge_reader -d crypto_structured -c \"SELECT * FROM forge.instrument_source_map;\""`
Expected: 3 rows (BTC-USD/btcusd, ETH-USD/ethusd, SOL-USD/solusd)

**Step 4: Commit**

```bash
git add db/migrations/postgres/0005_instrument_source_map.sql
git commit -m "feat: add instrument_source_map table with Tiingo seed data"
```

---

### Task 3: Validation Module

**Files:**
- Create: `src/ftb/validation/__init__.py`
- Create: `src/ftb/validation/core.py`
- Create: `tests/validation/__init__.py`
- Create: `tests/validation/test_core.py`

**Step 1: Write failing tests**

```python
# tests/validation/test_core.py
"""Tests for observation validation logic."""
from datetime import datetime, timezone

import pytest

from ftb.validation.core import validate_observation, Observation, ValidationResult


@pytest.fixture
def metric_catalog():
    """Minimal metric catalog for testing."""
    return {
        "price.spot.close_usd": {
            "is_nullable": False,
            "expected_range_low": None,
            "expected_range_high": None,
        },
        "price.spot.volume_usd_24h": {
            "is_nullable": False,
            "expected_range_low": 0.0,
            "expected_range_high": None,
        },
    }


@pytest.fixture
def instrument_set():
    return {"BTC-USD", "ETH-USD", "SOL-USD"}


def _obs(metric_id="price.spot.close_usd", instrument_id="BTC-USD",
         value=48000.0, observed_at=None):
    return Observation(
        metric_id=metric_id,
        instrument_id=instrument_id,
        source_id="tiingo",
        observed_at=observed_at or datetime(2024, 1, 1, tzinfo=timezone.utc),
        value=value,
    )


class TestValidateObservation:
    def test_valid_observation_passes(self, metric_catalog, instrument_set):
        result = validate_observation(_obs(), metric_catalog, instrument_set)
        assert result.is_valid
        assert result.rejection_code is None

    def test_unknown_metric_rejected(self, metric_catalog, instrument_set):
        result = validate_observation(
            _obs(metric_id="fake.metric"), metric_catalog, instrument_set
        )
        assert not result.is_valid
        assert result.rejection_code == "UNKNOWN_METRIC"

    def test_unknown_instrument_rejected(self, metric_catalog, instrument_set):
        result = validate_observation(
            _obs(instrument_id="DOGE-USD"), metric_catalog, instrument_set
        )
        assert not result.is_valid
        assert result.rejection_code == "UNKNOWN_INSTRUMENT"

    def test_null_value_rejected_when_not_nullable(self, metric_catalog, instrument_set):
        result = validate_observation(
            _obs(value=None), metric_catalog, instrument_set
        )
        assert not result.is_valid
        assert result.rejection_code == "NULL_VIOLATION"

    def test_below_range_rejected(self, metric_catalog, instrument_set):
        result = validate_observation(
            _obs(metric_id="price.spot.volume_usd_24h", value=-100.0),
            metric_catalog, instrument_set,
        )
        assert not result.is_valid
        assert result.rejection_code == "RANGE_VIOLATION"

    def test_no_range_bounds_skips_check(self, metric_catalog, instrument_set):
        result = validate_observation(
            _obs(value=999999999.0), metric_catalog, instrument_set
        )
        assert result.is_valid
```

**Step 2: Run tests — verify they fail**

Run: `cd /var/home/stephen/Projects/FromTheBridge && uv run pytest tests/validation/test_core.py -v`
Expected: FAIL (module not found)

**Step 3: Implement validation module**

```python
# src/ftb/validation/__init__.py
# (empty)
```

```python
# src/ftb/validation/core.py
"""Per-observation validation against metric catalog definitions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Observation:
    """A single metric observation ready for Silver write."""
    metric_id: str
    instrument_id: str | None
    source_id: str
    observed_at: datetime
    value: float | None


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Result of validating a single observation."""
    is_valid: bool
    rejection_code: str | None = None
    rejection_reason: str | None = None


def validate_observation(
    obs: Observation,
    metric_catalog: dict[str, dict],
    instrument_set: set[str],
) -> ValidationResult:
    """Validate observation against catalog rules.

    Returns ValidationResult with is_valid=True on success, or
    is_valid=False with rejection_code and rejection_reason on failure.
    """
    # Check metric exists
    metric = metric_catalog.get(obs.metric_id)
    if metric is None:
        return ValidationResult(
            is_valid=False,
            rejection_code="UNKNOWN_METRIC",
            rejection_reason=f"metric_id '{obs.metric_id}' not in catalog",
        )

    # Check instrument exists (skip for market-level)
    if obs.instrument_id is not None and obs.instrument_id not in instrument_set:
        return ValidationResult(
            is_valid=False,
            rejection_code="UNKNOWN_INSTRUMENT",
            rejection_reason=f"instrument_id '{obs.instrument_id}' not in instruments",
        )

    # Check nullability
    if obs.value is None and not metric.get("is_nullable", False):
        return ValidationResult(
            is_valid=False,
            rejection_code="NULL_VIOLATION",
            rejection_reason=f"metric '{obs.metric_id}' does not allow null values",
        )

    # Check range bounds (if defined)
    if obs.value is not None:
        range_low = metric.get("expected_range_low")
        range_high = metric.get("expected_range_high")
        if range_low is not None and obs.value < range_low:
            return ValidationResult(
                is_valid=False,
                rejection_code="RANGE_VIOLATION",
                rejection_reason=f"value {obs.value} below range_low {range_low}",
            )
        if range_high is not None and obs.value > range_high:
            return ValidationResult(
                is_valid=False,
                rejection_code="RANGE_VIOLATION",
                rejection_reason=f"value {obs.value} above range_high {range_high}",
            )

    return ValidationResult(is_valid=True)
```

**Step 4: Run tests — verify they pass**

Run: `uv run pytest tests/validation/test_core.py -v`
Expected: 6 passed

**Step 5: Commit**

```bash
git add src/ftb/validation/ tests/validation/
git commit -m "feat: add observation validation module with tests"
```

---

### Task 4: Silver Writer (ClickHouse)

**Files:**
- Create: `src/ftb/writers/__init__.py`
- Create: `src/ftb/writers/silver.py`
- Create: `tests/writers/__init__.py`
- Create: `tests/writers/test_silver.py`

**Step 1: Write failing tests**

```python
# tests/writers/test_silver.py
"""Tests for Silver writer — ClickHouse observations + dead_letter."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from ftb.validation.core import Observation
from ftb.writers.silver import (
    build_observations_batch,
    build_dead_letter_batch,
    DeadLetterRow,
)


def _obs(metric_id="price.spot.close_usd", instrument_id="BTC-USD",
         value=48000.0, observed_at=None, source_id="tiingo"):
    return Observation(
        metric_id=metric_id,
        instrument_id=instrument_id,
        source_id=source_id,
        observed_at=observed_at or datetime(2024, 1, 1, tzinfo=timezone.utc),
        value=value,
    )


class TestBuildObservationsBatch:
    def test_builds_correct_columns(self):
        obs = [_obs()]
        rows, columns = build_observations_batch(obs)
        assert columns == [
            "metric_id", "instrument_id", "source_id",
            "observed_at", "ingested_at", "value", "data_version",
        ]
        assert len(rows) == 1

    def test_row_values(self):
        obs = [_obs(value=48000.0)]
        rows, _ = build_observations_batch(obs)
        row = rows[0]
        assert row[0] == "price.spot.close_usd"  # metric_id
        assert row[1] == "BTC-USD"                # instrument_id
        assert row[2] == "tiingo"                 # source_id
        assert row[5] == 48000.0                  # value
        assert row[6] == 1                        # data_version

    def test_null_instrument_preserved(self):
        obs = [_obs(instrument_id=None)]
        rows, _ = build_observations_batch(obs)
        assert rows[0][1] is None

    def test_multiple_observations(self):
        obs = [_obs(value=100.0), _obs(value=200.0)]
        rows, _ = build_observations_batch(obs)
        assert len(rows) == 2


class TestBuildDeadLetterBatch:
    def test_builds_correct_columns(self):
        dead = [DeadLetterRow(
            source_id="tiingo",
            metric_id="price.spot.close_usd",
            instrument_id="BTC-USD",
            raw_payload='{"close": null}',
            rejection_reason="null value not allowed",
            rejection_code="NULL_VIOLATION",
        )]
        rows, columns = build_dead_letter_batch(dead)
        assert "rejection_code" in columns
        assert "raw_payload" in columns
        assert len(rows) == 1
```

**Step 2: Run tests — verify they fail**

Run: `uv run pytest tests/writers/test_silver.py -v`
Expected: FAIL

**Step 3: Implement Silver writer**

```python
# src/ftb/writers/__init__.py
# (empty)
```

```python
# src/ftb/writers/silver.py
"""Silver writers — ClickHouse observations + dead_letter INSERTs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ftb.validation.core import Observation

OBSERVATIONS_COLUMNS = [
    "metric_id", "instrument_id", "source_id",
    "observed_at", "ingested_at", "value", "data_version",
]

DEAD_LETTER_COLUMNS = [
    "source_id", "metric_id", "instrument_id", "raw_payload",
    "rejection_reason", "rejection_code", "collected_at", "rejected_at",
]


@dataclass(frozen=True, slots=True)
class DeadLetterRow:
    """A rejected observation for the dead letter table."""
    source_id: str
    metric_id: str | None
    instrument_id: str | None
    raw_payload: str
    rejection_reason: str
    rejection_code: str


def build_observations_batch(
    observations: list[Observation],
) -> tuple[list[tuple], list[str]]:
    """Build batch rows for forge.observations INSERT."""
    now = datetime.now(timezone.utc)
    rows = []
    for obs in observations:
        rows.append((
            obs.metric_id,
            obs.instrument_id,
            obs.source_id,
            obs.observed_at,
            now,          # ingested_at
            obs.value,
            1,            # data_version (first insert)
        ))
    return rows, OBSERVATIONS_COLUMNS


def build_dead_letter_batch(
    dead_letters: list[DeadLetterRow],
) -> tuple[list[tuple], list[str]]:
    """Build batch rows for forge.dead_letter INSERT."""
    now = datetime.now(timezone.utc)
    rows = []
    for dl in dead_letters:
        rows.append((
            dl.source_id,
            dl.metric_id,
            dl.instrument_id,
            dl.raw_payload,
            dl.rejection_reason,
            dl.rejection_code,
            now,  # collected_at
            now,  # rejected_at
        ))
    return rows, DEAD_LETTER_COLUMNS


def write_observations(client, observations: list[Observation]) -> int:
    """INSERT validated observations to forge.observations. Returns row count."""
    if not observations:
        return 0
    rows, columns = build_observations_batch(observations)
    client.insert("forge.observations", rows, column_names=columns)
    return len(rows)


def write_dead_letter(client, dead_letters: list[DeadLetterRow]) -> int:
    """INSERT rejected rows to forge.dead_letter. Returns row count."""
    if not dead_letters:
        return 0
    rows, columns = build_dead_letter_batch(dead_letters)
    client.insert("forge.dead_letter", rows, column_names=columns)
    return len(rows)
```

**Step 4: Run tests**

Run: `uv run pytest tests/writers/test_silver.py -v`
Expected: All passed

**Step 5: Commit**

```bash
git add src/ftb/writers/ tests/writers/
git commit -m "feat: add Silver writer (ClickHouse observations + dead_letter)"
```

---

### Task 5: Bronze Writer (MinIO Parquet)

**Files:**
- Create: `src/ftb/writers/bronze.py`
- Create: `tests/writers/test_bronze.py`

**Step 1: Write failing tests**

```python
# tests/writers/test_bronze.py
"""Tests for Bronze writer — Parquet files to MinIO."""
from datetime import date

import pytest

from ftb.writers.bronze import build_bronze_path, payload_to_parquet_bytes


class TestBuildBronzePath:
    def test_standard_path(self):
        path = build_bronze_path("tiingo", date(2024, 1, 15), "price")
        assert path == "tiingo/2024-01-15/price/data.parquet"

    def test_different_source(self):
        path = build_bronze_path("coinalyze", date(2024, 6, 1), "derivatives")
        assert path == "coinalyze/2024-06-01/derivatives/data.parquet"


class TestPayloadToParquetBytes:
    def test_creates_valid_parquet(self):
        payload = [
            {"ticker": "btcusd", "close": 48000.0, "volume": 100.0},
            {"ticker": "ethusd", "close": 3200.0, "volume": 50.0},
        ]
        buf = payload_to_parquet_bytes(payload)
        assert len(buf) > 0
        # Parquet magic bytes
        assert buf[:4] == b"PAR1"

    def test_empty_payload_returns_empty_parquet(self):
        buf = payload_to_parquet_bytes([])
        assert buf[:4] == b"PAR1"
```

**Step 2: Run tests — verify they fail**

Run: `uv run pytest tests/writers/test_bronze.py -v`
Expected: FAIL

**Step 3: Implement Bronze writer**

```python
# src/ftb/writers/bronze.py
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
```

**Step 4: Run tests**

Run: `uv run pytest tests/writers/test_bronze.py -v`
Expected: All passed

**Step 5: Commit**

```bash
git add src/ftb/writers/bronze.py tests/writers/test_bronze.py
git commit -m "feat: add Bronze writer (Parquet to MinIO)"
```

---

### Task 6: Collection Event Writer (PostgreSQL)

**Files:**
- Create: `src/ftb/writers/collection.py`
- Create: `tests/writers/test_collection.py`

**Step 1: Write failing tests**

```python
# tests/writers/test_collection.py
"""Tests for collection event writer."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, call

import pytest

from ftb.writers.collection import build_collection_event_params


class TestBuildCollectionEventParams:
    def test_builds_correct_params(self):
        params = build_collection_event_params(
            source_id="tiingo",
            metric_id=None,
            instrument_id=None,
            status="completed",
            observations_written=42,
            observations_rejected=3,
            metrics_covered=["price.spot.close_usd", "price.spot.volume_usd_24h"],
            instruments_covered=["BTC-USD", "ETH-USD", "SOL-USD"],
        )
        assert params["source_id"] == "tiingo"
        assert params["status"] == "completed"
        assert params["observations_written"] == 42
        assert params["observations_rejected"] == 3
        assert params["metrics_covered"] == ["price.spot.close_usd", "price.spot.volume_usd_24h"]
        assert params["instruments_covered"] == ["BTC-USD", "ETH-USD", "SOL-USD"]

    def test_failed_status(self):
        params = build_collection_event_params(
            source_id="tiingo",
            status="failed",
            error_detail="API returned 500",
        )
        assert params["status"] == "failed"
        assert params["error_detail"] == "API returned 500"
```

**Step 2: Run tests — verify they fail**

Run: `uv run pytest tests/writers/test_collection.py -v`
Expected: FAIL

**Step 3: Implement collection writer**

```python
# src/ftb/writers/collection.py
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
```

**Step 4: Run tests**

Run: `uv run pytest tests/writers/test_collection.py -v`
Expected: All passed

**Step 5: Commit**

```bash
git add src/ftb/writers/collection.py tests/writers/test_collection.py
git commit -m "feat: add collection event writer (PostgreSQL)"
```

---

### Task 7: Dagster Resources

**Files:**
- Create: `src/ftb/resources.py`

**Step 1: Implement resource definitions**

The Dagster code server container has secrets mounted at `/run/secrets/`. Resources read credentials from there.

```python
# src/ftb/resources.py
"""Dagster resource definitions — clients for MinIO, ClickHouse, PostgreSQL."""
from __future__ import annotations

from pathlib import Path

import clickhouse_connect
import psycopg2
from dagster import resource, InitResourceContext
from minio import Minio


def _read_secret(name: str) -> str:
    """Read a Docker secret from /run/secrets/."""
    return Path(f"/run/secrets/{name}").read_text().strip()


@resource
def ch_writer_resource(context: InitResourceContext):
    """ClickHouse client with ch_writer credentials (INSERT-only)."""
    return clickhouse_connect.get_client(
        host="empire_clickhouse",
        port=8123,
        username="ch_writer",
        password=_read_secret("ch_writer"),
        database="forge",
    )


@resource
def pg_forge_resource(context: InitResourceContext):
    """PostgreSQL connection with forge_user credentials."""
    return psycopg2.connect(
        host="empire_postgres",
        port=5432,
        dbname="crypto_structured",
        user="forge_user",
        password=_read_secret("pg_forge_user"),
    )


@resource
def pg_forge_reader_resource(context: InitResourceContext):
    """PostgreSQL connection with forge_reader credentials (SELECT-only)."""
    return psycopg2.connect(
        host="empire_postgres",
        port=5432,
        dbname="crypto_structured",
        user="forge_reader",
        password=_read_secret("pg_forge_reader"),
    )


@resource
def minio_bronze_resource(context: InitResourceContext):
    """MinIO client for Bronze bucket writes."""
    return Minio(
        "empire_minio:9001",
        access_key=_read_secret("minio_bronze_key"),
        secret_key=_read_secret("minio_bronze_secret"),
        secure=False,
    )
```

**Step 2: Commit**

```bash
git add src/ftb/resources.py
git commit -m "feat: add Dagster resource definitions (CH, PG, MinIO)"
```

---

### Task 8: Tiingo Adapter

**Files:**
- Create: `src/ftb/adapters/__init__.py`
- Create: `src/ftb/adapters/tiingo.py`
- Create: `tests/adapters/__init__.py`
- Create: `tests/adapters/test_tiingo.py`

**Step 1: Write failing tests**

Test the pure functions (field mapping, observation extraction) — not the Dagster asset itself.

```python
# tests/adapters/test_tiingo.py
"""Tests for Tiingo adapter — field mapping and observation extraction."""
from datetime import datetime, timezone

import pytest

from ftb.adapters.tiingo import (
    extract_observations,
    build_tiingo_url,
    TIINGO_METRICS,
)
from ftb.validation.core import Observation


@pytest.fixture
def symbol_map():
    """instrument_source_map rows for Tiingo."""
    return {"btcusd": "BTC-USD", "ethusd": "ETH-USD", "solusd": "SOL-USD"}


@pytest.fixture
def sample_response():
    """Tiingo crypto prices API response shape."""
    return [
        {
            "ticker": "btcusd",
            "baseCurrency": "btc",
            "quoteCurrency": "usd",
            "priceData": [
                {
                    "date": "2024-01-15T00:00:00+00:00",
                    "open": 42500.0,
                    "high": 43000.0,
                    "low": 42000.0,
                    "close": 42800.0,
                    "volume": 15.5,
                    "volumeNotional": 663400.0,
                    "tradesDone": 1200,
                },
            ],
        },
        {
            "ticker": "ethusd",
            "baseCurrency": "eth",
            "quoteCurrency": "usd",
            "priceData": [
                {
                    "date": "2024-01-15T00:00:00+00:00",
                    "open": 2500.0,
                    "high": 2550.0,
                    "low": 2480.0,
                    "close": 2520.0,
                    "volume": 100.0,
                    "volumeNotional": 252000.0,
                    "tradesDone": 800,
                },
            ],
        },
    ]


class TestBuildTiingoUrl:
    def test_url_with_tickers_and_dates(self):
        url = build_tiingo_url(
            tickers=["btcusd", "ethusd"],
            start_date="2024-01-15",
            end_date="2024-01-16",
        )
        assert "tickers=btcusd,ethusd" in url
        assert "startDate=2024-01-15" in url
        assert "endDate=2024-01-16" in url
        assert "resampleFreq=1day" in url


class TestExtractObservations:
    def test_extracts_two_metrics_per_instrument(self, sample_response, symbol_map):
        observations = extract_observations(sample_response, symbol_map)
        # 2 instruments × 2 metrics = 4 observations
        assert len(observations) == 4

    def test_close_usd_extracted(self, sample_response, symbol_map):
        observations = extract_observations(sample_response, symbol_map)
        btc_close = [o for o in observations
                     if o.metric_id == "price.spot.close_usd" and o.instrument_id == "BTC-USD"]
        assert len(btc_close) == 1
        assert btc_close[0].value == 42800.0

    def test_volume_extracted(self, sample_response, symbol_map):
        observations = extract_observations(sample_response, symbol_map)
        btc_vol = [o for o in observations
                   if o.metric_id == "price.spot.volume_usd_24h" and o.instrument_id == "BTC-USD"]
        assert len(btc_vol) == 1
        assert btc_vol[0].value == 663400.0

    def test_unknown_ticker_skipped(self, sample_response, symbol_map):
        # Add unknown ticker to response
        response = sample_response + [{
            "ticker": "dogebtc",
            "baseCurrency": "doge",
            "quoteCurrency": "btc",
            "priceData": [{"date": "2024-01-15T00:00:00+00:00",
                           "close": 0.001, "volumeNotional": 10.0,
                           "open": 0.001, "high": 0.001, "low": 0.001,
                           "volume": 100.0, "tradesDone": 50}],
        }]
        observations = extract_observations(response, symbol_map)
        # Still only 4 — dogebtc not in symbol_map
        assert len(observations) == 4

    def test_observed_at_parsed(self, sample_response, symbol_map):
        observations = extract_observations(sample_response, symbol_map)
        assert observations[0].observed_at == datetime(2024, 1, 15, tzinfo=timezone.utc)

    def test_source_id_is_tiingo(self, sample_response, symbol_map):
        observations = extract_observations(sample_response, symbol_map)
        assert all(o.source_id == "tiingo" for o in observations)

    def test_empty_price_data_returns_nothing(self, symbol_map):
        response = [{"ticker": "btcusd", "baseCurrency": "btc",
                     "quoteCurrency": "usd", "priceData": []}]
        observations = extract_observations(response, symbol_map)
        assert len(observations) == 0
```

**Step 2: Run tests — verify they fail**

Run: `uv run pytest tests/adapters/test_tiingo.py -v`
Expected: FAIL

**Step 3: Implement Tiingo adapter**

```python
# src/ftb/adapters/__init__.py
# (empty)
```

```python
# src/ftb/adapters/tiingo.py
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
```

**Step 4: Run tests**

Run: `uv run pytest tests/adapters/test_tiingo.py -v`
Expected: All passed

**Step 5: Commit**

```bash
git add src/ftb/adapters/ tests/adapters/
git commit -m "feat: add Tiingo adapter — fetch, field mapping, observation extraction"
```

---

### Task 9: Dagster Asset + Definitions

**Files:**
- Create: `src/ftb/adapters/tiingo_asset.py`
- Modify: `src/ftb/definitions.py`

**Step 1: Implement the Dagster asset**

This module wires the adapter to Dagster — it depends on resources and calls the shared writers.

```python
# src/ftb/adapters/tiingo_asset.py
"""Dagster Software-Defined Asset for Tiingo crypto OHLCV collection."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

from dagster import (
    asset,
    AssetExecutionContext,
    DailyPartitionsDefinition,
    MetadataValue,
    Output,
)

from ftb.adapters.tiingo import (
    extract_observations,
    fetch_tiingo_crypto,
    flatten_price_data,
)
from ftb.validation.core import validate_observation
from ftb.writers.bronze import write_bronze
from ftb.writers.silver import write_observations, write_dead_letter, DeadLetterRow
from ftb.writers.collection import write_collection_event

logger = logging.getLogger(__name__)

TIINGO_PARTITIONS = DailyPartitionsDefinition(start_date="2014-01-01")


@asset(
    name="collect_tiingo_price",
    partitions_def=TIINGO_PARTITIONS,
    required_resource_keys={"ch_writer", "pg_forge", "pg_forge_reader", "minio_bronze", "tiingo_api_key"},
    metadata={"source_id": "tiingo", "cadence_hours": 6},
)
def collect_tiingo_price(context: AssetExecutionContext):
    """Collect Tiingo crypto OHLCV → Bronze + Silver."""
    partition_date = date.fromisoformat(context.partition_key)
    started_at = datetime.now(timezone.utc)

    # Load symbol map from catalog
    pg_reader = context.resources.pg_forge_reader
    with pg_reader.cursor() as cur:
        cur.execute(
            "SELECT source_symbol, instrument_id "
            "FROM forge.instrument_source_map WHERE source_id = 'tiingo'"
        )
        symbol_map = {row[0]: row[1] for row in cur.fetchall()}

    # Load metric catalog for validation
    with pg_reader.cursor() as cur:
        cur.execute(
            "SELECT metric_id, is_nullable, expected_range_low, expected_range_high "
            "FROM forge.metric_catalog WHERE 'tiingo' = ANY(sources)"
        )
        metric_catalog = {
            row[0]: {
                "is_nullable": row[1],
                "expected_range_low": row[2],
                "expected_range_high": row[3],
            }
            for row in cur.fetchall()
        }

    # Load instrument set
    with pg_reader.cursor() as cur:
        cur.execute("SELECT instrument_id FROM forge.instruments WHERE is_active = true")
        instrument_set = {row[0] for row in cur.fetchall()}

    tickers = list(symbol_map.keys())
    if not tickers:
        logger.warning("No Tiingo tickers in instrument_source_map")
        return

    # 1. Fetch from Tiingo API
    api_key = context.resources.tiingo_api_key
    next_date = date.fromordinal(partition_date.toordinal() + 1)
    response_data = fetch_tiingo_crypto(
        api_key=api_key,
        tickers=tickers,
        start_date=partition_date.isoformat(),
        end_date=next_date.isoformat(),
    )

    # 2. Bronze write — raw response as flattened Parquet
    flat_rows = flatten_price_data(response_data)
    bronze_path = write_bronze(
        minio_client=context.resources.minio_bronze,
        bucket="bronze-hot",
        source_id="tiingo",
        partition_date=partition_date,
        metric_domain="price",
        payload=flat_rows,
    )
    logger.info("Bronze written: %s (%d rows)", bronze_path, len(flat_rows))

    # 3. Extract observations
    observations = extract_observations(response_data, symbol_map)

    # 4. Validate
    valid_obs = []
    dead_letters = []
    for obs in observations:
        result = validate_observation(obs, metric_catalog, instrument_set)
        if result.is_valid:
            valid_obs.append(obs)
        else:
            dead_letters.append(DeadLetterRow(
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
            ))

    # 5. Silver write
    written = write_observations(context.resources.ch_writer, valid_obs)
    rejected = write_dead_letter(context.resources.ch_writer, dead_letters)

    # 6. Collection event
    metrics_covered = list({o.metric_id for o in valid_obs})
    instruments_covered = list({o.instrument_id for o in valid_obs if o.instrument_id})

    write_collection_event(
        context.resources.pg_forge,
        source_id="tiingo",
        status="completed" if not dead_letters else "partial",
        started_at=started_at,
        observations_written=written,
        observations_rejected=rejected,
        metrics_covered=metrics_covered,
        instruments_covered=instruments_covered,
    )

    logger.info(
        "Tiingo collection complete: %d written, %d rejected, partition=%s",
        written, rejected, partition_date,
    )

    return Output(
        value=None,
        metadata={
            "observations_written": MetadataValue.int(written),
            "observations_rejected": MetadataValue.int(rejected),
            "bronze_path": MetadataValue.text(bronze_path),
            "partition_date": MetadataValue.text(partition_date.isoformat()),
            "instruments_covered": MetadataValue.text(", ".join(instruments_covered)),
            "metrics_covered": MetadataValue.text(", ".join(metrics_covered)),
        },
    )
```

**Step 2: Update definitions.py**

```python
# src/ftb/definitions.py
"""Dagster definitions entry point for the FTB pipeline.

Assets are registered here as adapters are built. The code server loads this module
via the -m flag: dagster api grpc -m ftb.definitions
"""

from pathlib import Path

import dagster

from ftb.adapters.tiingo_asset import collect_tiingo_price
from ftb.resources import (
    ch_writer_resource,
    minio_bronze_resource,
    pg_forge_resource,
    pg_forge_reader_resource,
)


def _read_secret(name: str) -> str:
    """Read a Docker secret from /run/secrets/."""
    path = Path(f"/run/secrets/{name}")
    if path.exists():
        return path.read_text().strip()
    return ""


@dagster.resource
def tiingo_api_key_resource(context):
    """Tiingo API key — read from /run/secrets/tiingo_api_key or TIINGO_API_KEY env."""
    import os

    secret = _read_secret("tiingo_api_key")
    if secret:
        return secret
    return os.environ.get("TIINGO_API_KEY", "")


defs = dagster.Definitions(
    assets=[collect_tiingo_price],
    resources={
        "ch_writer": ch_writer_resource,
        "pg_forge": pg_forge_resource,
        "pg_forge_reader": pg_forge_reader_resource,
        "minio_bronze": minio_bronze_resource,
        "tiingo_api_key": tiingo_api_key_resource,
    },
)
```

**Step 3: Commit**

```bash
git add src/ftb/adapters/tiingo_asset.py src/ftb/definitions.py
git commit -m "feat: register collect_tiingo_price Dagster asset with resources"
```

---

### Task 10: Docker Secrets for Tiingo API Key

**Files:**
- Modify: `docker-compose.yml` (add tiingo_api_key secret + mount to code server)
- Modify: `scripts/init_secrets.sh` (copy Tiingo key from /opt/empire/.env)

**Step 1: Add Tiingo secret to docker-compose.yml**

In the `secrets:` section, add:
```yaml
  tiingo_api_key:
    file: ./secrets/external_apis/tiingo.txt
```

In `empire_dagster_code` service, add `tiingo_api_key` to its `secrets:` list.

**Step 2: Populate the secret on proxmox**

Run: `ssh root@192.168.68.11 "grep TIINGO_API_KEY /opt/empire/.env | cut -d= -f2- | tr -d '\n' > /opt/empire/FromTheBridge/secrets/external_apis/tiingo.txt && chmod 600 /opt/empire/FromTheBridge/secrets/external_apis/tiingo.txt"`

**Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "chore: mount Tiingo API key as Docker secret for code server"
```

---

### Task 11: Deploy and Smoke Test

**Step 1: Sync to proxmox**

Run: `rsync -av --exclude='.git' --exclude='.venv' --exclude='__pycache__' /var/home/stephen/Projects/FromTheBridge/ root@192.168.68.11:/opt/empire/FromTheBridge/`

**Step 2: Deploy migration**

Run: `cat db/migrations/postgres/0005_instrument_source_map.sql | ssh root@192.168.68.11 "docker exec -i empire_postgres psql -U crypto_user -d crypto_structured"`

**Step 3: Rebuild Dagster image**

Run: `ssh root@192.168.68.11 "cd /opt/empire/FromTheBridge && docker compose build empire_dagster_code && docker compose up -d"`

**Step 4: Verify Dagster loads the asset**

Run: `ssh root@192.168.68.11 "docker logs empire_dagster_code 2>&1 | tail -20"`
Expected: No import errors, gRPC server listening on 4266

**Step 5: Verify via webserver**

Open `http://192.168.68.11:3010` — should show `collect_tiingo_price` asset in the asset graph.

**Step 6: Trigger single partition test**

From Dagster UI, materialize `collect_tiingo_price` for partition `2024-01-15` (a recent known-good date).

**Step 7: Verify Silver write**

Run: `ssh root@192.168.68.11 "docker exec -i empire_clickhouse clickhouse-client --user ch_ops_reader --password \$(cat /opt/empire/FromTheBridge/secrets/ch_ops_reader.txt) --query \"SELECT metric_id, instrument_id, observed_at, value FROM forge.observations WHERE source_id = 'tiingo' LIMIT 10\""`

Expected: Rows with `price.spot.close_usd` and `price.spot.volume_usd_24h` for BTC-USD, ETH-USD, SOL-USD.

**Step 8: Verify Bronze write**

Run: `ssh root@192.168.68.11 "docker exec -i empire_minio mc ls local/bronze-hot/tiingo/2024-01-15/price/"`
Expected: `data.parquet` file

**Step 9: Verify collection event**

Run: `ssh root@192.168.68.11 "docker exec -i empire_postgres psql -U forge_reader -d crypto_structured -c \"SELECT source_id, status, observations_written, observations_rejected FROM forge.collection_events WHERE source_id = 'tiingo' ORDER BY started_at DESC LIMIT 1;\""`
Expected: One row, status=completed, observations_written > 0

---

### Task 12: Run Full Test Suite

**Step 1: Run all tests locally**

Run: `cd /var/home/stephen/Projects/FromTheBridge && uv run pytest tests/ -v`
Expected: All tests pass

**Step 2: Run ruff**

Run: `uv run ruff check src/ tests/`
Expected: No errors

**Step 3: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix: address test/lint issues from full suite run"
```
