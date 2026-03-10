# Tiingo Adapter Migration Handoff: FTB → EDS

**Date:** 2026-03-10
**Status:** Ready for EDS implementation
**Context:** Tiingo OHLCV adapter was built in FTB by mistake. Must move to EDS per data collection boundary.

---

## What Exists in FTB Today

### Deployed + Running
- 29,000 Silver observations in `forge.observations` (2019-01-01 → 2026-03-09)
- 6h schedule (`tiingo_6h_collection`) — cron `15 */6 * * *`
- 3 instruments: BTC-USD, ETH-USD, SOL-USD
- 2 metrics per instrument per timestamp: `price.spot.close_usd`, `price.spot.volume_usd_24h`
- Bronze Parquet in MinIO `bronze-hot/tiingo/{date}/price/data.parquet`

### Files to Port (FTB → EDS)

| FTB File | Lines | What It Does |
|----------|-------|-------------|
| `src/ftb/adapters/tiingo.py` | 118 | API client + observation extraction. **100% portable.** |
| `src/ftb/adapters/tiingo_asset.py` | 156 | Dagster asset orchestration. **Needs adaptation.** |
| `tests/adapters/test_tiingo.py` | 122 | 9 unit tests. **Portable with import changes.** |

### Files That Stay in FTB
- `src/ftb/writers/` — Silver/Bronze/collection writers (used by `empire_to_forge_sync`)
- `src/ftb/validation/core.py` — observation validation (FTB catalog-dependent)
- `src/ftb/resources.py` — Dagster resources (FTB infrastructure)
- DB migrations — Tiingo rows in `forge.metric_catalog`, `forge.source_catalog`, `forge.instrument_source_map`
- `scripts/backfill_tiingo.py`, `scripts/check_backfill.py` — operational tools (stay as-is or copy)

---

## EDS Target Structure

Following the established FRED/DeFiLlama 4-file pattern:

```
src/eds/track_2_exchange_api/tiingo/
├── __init__.py
├── models.py      # TiingoMetric dataclass + TIINGO_METRICS list + symbol map
├── client.py      # TiingoClient (async httpx) — HTTP fetch only
├── adapter.py     # TiingoAdapter class — collect + transform + load
└── assets.py      # Dagster @asset with AutomationCondition
```

---

## Adaptation Guide (what changes from FTB → EDS)

### 1. models.py — Metric definitions + symbol map

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class TiingoMetric:
    metric_id: str       # "price.spot.close_usd"
    field_name: str      # Tiingo API field: "close", "volumeNotional"
    sync_to_ftb: bool = True

TIINGO_METRICS: list[TiingoMetric] = [
    TiingoMetric("price.spot.close_usd", "close"),
    TiingoMetric("price.spot.volume_usd_24h", "volumeNotional"),
]

FIELD_TO_METRIC = {m.field_name: m for m in TIINGO_METRICS}

# Tiingo ticker → canonical instrument_id
# In FTB this lives in forge.instrument_source_map (PG table).
# In EDS, hardcode it here — the symbol set is small and stable.
SYMBOL_MAP: dict[str, str] = {
    "btcusd": "BTC-USD",
    "ethusd": "ETH-USD",
    "solusd": "SOL-USD",
}
```

**Decision:** FTB reads symbol map from PG (`forge.instrument_source_map`). EDS should hardcode in `models.py` — Tiingo has 3 instruments, no need for DB indirection.

### 2. client.py — HTTP client (async)

Port `fetch_tiingo_crypto()` and `build_tiingo_url()` from `tiingo.py` into an async class:

```python
class TiingoClient:
    def __init__(self, api_key: str):
        self._api_key = api_key
        self._http = httpx.AsyncClient(timeout=30.0)

    async def fetch_ohlcv(self, tickers: list[str], start_date: str, end_date: str) -> list[dict]:
        """Fetch OHLCV from Tiingo crypto endpoint. Returns raw response."""
        ...

    async def close(self):
        await self._http.aclose()
```

**Key change:** FTB uses sync `httpx.Client`. EDS uses async `httpx.AsyncClient` (matches FRED/DeFiLlama pattern).

### 3. adapter.py — Core orchestration

```python
from eds.shared.ch_writer import ChWriter, Observation
from eds.shared.dead_letter import DeadLetterWriter, RejectedRecord

SOURCE_ID = "eds_track_2_tiingo"
CHAIN_ID = "crypto_spot"

class TiingoAdapter:
    def __init__(self, ch_writer: ChWriter, dead_letter: DeadLetterWriter, api_key: str):
        self._writer = ch_writer
        self._dead_letter = dead_letter
        self._client = TiingoClient(api_key=api_key)

    @staticmethod
    def transform(ticker: str, bar: dict, metric: TiingoMetric, instrument_id: str) -> Observation | None:
        """Transform a single Tiingo bar field into an EDS Observation."""
        value = bar.get(metric.field_name)
        if value is None:
            return None
        return Observation(
            metric_id=metric.metric_id,
            chain_id=CHAIN_ID,
            source_id=SOURCE_ID,
            observed_at=datetime.fromisoformat(bar["date"]).replace(tzinfo=UTC),
            value=float(value),
            instrument_id=instrument_id,
        )

    async def collect_and_load(self, start_date: str, end_date: str) -> int:
        """Fetch all tickers, transform, write to empire.observations."""
        ...
```

**Key differences from FTB version:**
- Writes to `empire.observations` (not `forge.observations`)
- Uses `ChWriter` + `DeadLetterWriter` from `eds/shared/` (not FTB writers)
- Observation includes `chain_id="crypto_spot"` (FTB's Observation has no chain_id)
- `source_id="eds_track_2_tiingo"` (not `"tiingo"`)
- No Bronze write — EDS doesn't have MinIO Bronze yet. Raw data preservation is optional.
- No PG collection event — EDS doesn't track these. Dagster materialization metadata suffices.
- No validation against metric catalog — EDS adapters don't validate against a catalog. FTB validates at the sync boundary.

### 4. assets.py — Dagster asset

```python
@asset(
    name="tiingo_crypto_prices",
    group_name="eds_track_2",
    description="Tiingo OHLCV → empire.observations (3 instruments × 2 metrics)",
    automation_condition=AutomationCondition.on_cron("0 6 * * *"),  # Daily 06:00 UTC
)
def tiingo_crypto_prices(context: AssetExecutionContext) -> MaterializeResult:
    ch_client = clickhouse_connect.get_client(
        host=os.environ.get("CLICKHOUSE_HOST", "localhost"),
        port=int(os.environ.get("CLICKHOUSE_PORT", "8123")),
        username="eds_writer",
        password=_read_secret("eds_writer"),
        database="empire",
    )
    adapter = TiingoAdapter(
        ch_writer=ChWriter(client=ch_client),
        dead_letter=DeadLetterWriter(client=ch_client),
        api_key=_read_secret("tiingo_api_key"),
    )
    ...
```

**Key differences from FTB:**
- `AutomationCondition.on_cron()` instead of `ScheduleDefinition` (EDS pattern)
- Inline client creation instead of injected `@resource` (EDS pattern)
- Returns `MaterializeResult` (not `Output`)
- No partition definition — EDS Track 3 doesn't use partitions. **Decision needed:** keep daily partitions for backfill or use gap-detection like FRED?
- Reads `eds_writer` secret (not `ch_writer` password)

---

## Decisions for EDS Session

| # | Decision | Recommendation |
|---|----------|---------------|
| 1 | **source_id** | `eds_track_2_tiingo` (matches EDS convention: `eds_track_{N}_{source}`) |
| 2 | **chain_id** | `crypto_spot` (distinguishes from futures data in Track 2) |
| 3 | **Symbol map storage** | Hardcode in `models.py` (3 symbols, stable list) |
| 4 | **Cadence** | Daily (e.g., `0 6 * * *`). OHLCV is daily granularity — 6h was over-polling. Matches FRED pattern. |
| 5a | **Partitioning** | Use gap-detection like FRED (simpler, proven). Drop `DailyPartitionsDefinition`. |
| 5 | **Bronze write** | Skip — EDS has no Bronze layer. FTB handles Bronze via sync bridge. |
| 6 | **Collection events** | Skip — use Dagster MaterializeResult metadata instead. |
| 7 | **Backfill strategy** | After EDS adapter deploys, run once with `start_date=2019-01-01`. Data lands in `empire.observations`. FTB's `empire_to_forge_sync` pulls it over. |
| 8 | **FTB cleanup timing** | After EDS Tiingo is live + 7 days stable: remove FTB adapter files, keep schedule disabled as fallback for 30 days. |

---

## Data Flow: Before vs. After Migration

### Current (wrong — FTB collects directly)
```
Tiingo API → FTB adapter → forge.observations (Silver)
                         → bronze-hot (MinIO)
                         → forge.collection_events (PG)
```

### Target (correct — EDS collects, FTB syncs)
```
Tiingo API → EDS adapter → empire.observations
                              ↓
                     empire_to_forge_sync (FTB asset)
                              ↓
                     forge.observations (Silver)
                   + bronze-hot (MinIO)
                   + forge.collection_events (PG)
```

---

## Secret Requirements for EDS

| Secret | Current Location | EDS Needs |
|--------|-----------------|-----------|
| `tiingo_api_key` | FTB: `/run/secrets/tiingo_api_key` + `TIINGO_API_KEY` env | Mount in EDS code server container |
| `eds_writer` | EDS: `/run/secrets/eds_writer` | Already available |

The Tiingo API key (`/opt/empire/.env` → `TIINGO_API_KEY`) needs to be mounted into the EDS code server container. Add to EDS's docker secret definition or pass as env var.

---

## Test Portability

FTB tests in `tests/adapters/test_tiingo.py` cover:
- URL construction (3 tests)
- Observation extraction (4 tests): symbol mapping, nullability, timestamp parsing, unknown ticker handling
- Basic fetch mock (2 tests)

All are pure unit tests — no ClickHouse or PG required. Port to `tests/track_2/tiingo/test_client.py` and `test_adapter.py` with import changes only.

Add integration tests following EDS pattern (`conftest.py` with `ch_client` fixture, `truncate_tables`).

---

## FTB Post-Migration Checklist

After EDS Tiingo adapter is live and stable (7 days):

1. [ ] Disable `tiingo_6h_collection` schedule in FTB Dagster
2. [ ] Verify `empire_to_forge_sync` is pulling Tiingo data from `empire.observations`
3. [ ] Remove `src/ftb/adapters/tiingo.py` and `tiingo_asset.py`
4. [ ] Remove Tiingo-specific code from `src/ftb/definitions.py` (job, schedule, API key resource)
5. [ ] Remove `tiingo_api_key` secret from FTB docker-compose.yml
6. [ ] Keep `forge.instrument_source_map` Tiingo rows (used by sync bridge)
7. [ ] Keep `forge.metric_catalog` Tiingo rows (FTB still serves these metrics)
8. [ ] Archive `scripts/backfill_tiingo.py` and `scripts/check_backfill.py`
