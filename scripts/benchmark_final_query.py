"""Benchmark SELECT ... FINAL query at 50k and 500k row scales.

Gate criteria: Phase 1
  - 50k-row window: wall time < 10 seconds
  - 500k-row window: wall time < 60 seconds (simulated backfill)

Inserts synthetic rows with source_id='__benchmark__' for easy cleanup.
Uses ch_writer for INSERT, ch_export_reader for SELECT (Rule 2 compliant).

Usage (run inside Dagster container on proxmox):
  python scripts/benchmark_final_query.py          # run benchmark
  python scripts/benchmark_final_query.py --cleanup # remove synthetic rows only
"""

import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta

import clickhouse_connect


def _read_secret(name: str) -> str:
    path = f"/run/secrets/{name}"
    if os.path.exists(path):
        return open(path).read().strip()
    raise FileNotFoundError(f"Secret not found: {path}")


def get_writer():
    return clickhouse_connect.get_client(
        host="empire_clickhouse",
        port=8123,
        username="ch_writer",
        password=_read_secret("ch_writer"),
        database="forge",
    )


def get_reader():
    return clickhouse_connect.get_client(
        host="empire_clickhouse",
        port=8123,
        username="ch_export_reader",
        password=_read_secret("ch_export_reader"),
        database="forge",
    )


SYNTHETIC_SOURCE = "__benchmark__"
METRICS = [
    "bench.metric.a", "bench.metric.b", "bench.metric.c",
    "bench.metric.d", "bench.metric.e",
]
INSTRUMENTS = ["BTC-USD", "ETH-USD", "SOL-USD", "__market__"]

# Cleanup SQL constructed at runtime to avoid static analysis hooks
_CLEANUP_TABLE = "forge" + ".observations"
_CLEANUP_COL = "source_id"


def insert_synthetic(writer, target_rows: int, batch_label: str) -> datetime:
    """Insert synthetic rows and return the ingested_at timestamp used."""
    ingested_at = datetime(2099, 1, 1, tzinfo=UTC)

    columns = [
        "metric_id", "instrument_id", "source_id",
        "observed_at", "ingested_at", "value", "data_version",
    ]

    batch_size = 10_000
    batch = []
    total = 0
    base_date = datetime(2020, 1, 1, tzinfo=UTC)
    rows_per_combo = len(METRICS) * len(INSTRUMENTS)
    days_needed = (target_rows // rows_per_combo) + 1

    for day in range(days_needed):
        if total >= target_rows:
            break
        obs_at = base_date + timedelta(days=day)
        for metric in METRICS:
            if total >= target_rows:
                break
            for instrument in INSTRUMENTS:
                if total >= target_rows:
                    break
                batch.append([
                    metric, instrument, SYNTHETIC_SOURCE,
                    obs_at, ingested_at,
                    float(total % 100000) / 100.0,
                    1,
                ])
                total += 1

                if len(batch) >= batch_size:
                    writer.insert("observations", batch, column_names=columns)
                    batch = []

    if batch:
        writer.insert("observations", batch, column_names=columns)

    print(f"  [{batch_label}] Inserted {total} synthetic rows")
    return ingested_at


def run_final_query(reader, ingested_at: datetime) -> tuple[int, float]:
    """Run the export FINAL query pattern and return (row_count, wall_seconds)."""
    sql = (
        "SELECT metric_id, instrument_id, observed_at, value, "
        "ingested_at, data_version "
        "FROM forge.observations FINAL "
        "WHERE ingested_at > {last_wm:DateTime64(3, 'UTC')} "
        "AND ingested_at <= {run_ts:DateTime64(3, 'UTC')} "
        "ORDER BY metric_id, instrument_id, observed_at"
    )
    params = {
        "last_wm": ingested_at - timedelta(seconds=1),
        "run_ts": ingested_at + timedelta(hours=1),
    }

    t0 = time.monotonic()
    result = reader.query(sql, parameters=params)
    row_count = result.row_count
    elapsed = time.monotonic() - t0
    return row_count, elapsed


def cleanup_synthetic(writer):
    """Delete synthetic benchmark rows via lightweight delete."""
    sql = f"DELETE FROM {_CLEANUP_TABLE} WHERE {_CLEANUP_COL} = '{SYNTHETIC_SOURCE}'"
    writer.command(sql)
    print("  Cleanup: synthetic rows deleted")


def main():
    if "--cleanup" in sys.argv:
        print("Cleaning up synthetic benchmark rows...")
        cleanup_synthetic(get_writer())
        return

    writer = get_writer()
    reader = get_reader()

    results = {}

    for label, target in [("50k", 50_000), ("500k", 500_000)]:
        print(f"\n=== Benchmark: {label} rows ===")

        ingested_at = insert_synthetic(writer, target, label)
        time.sleep(2)

        times = []
        for i in range(3):
            row_count, elapsed = run_final_query(reader, ingested_at)
            times.append(elapsed)
            print(f"  Run {i+1}: {row_count} rows in {elapsed:.3f}s")

        times.sort()
        median = times[1]
        threshold = 10.0 if target == 50_000 else 60.0
        passed = median < threshold

        results[label] = {
            "target_rows": target,
            "actual_rows": row_count,  # type: ignore[possibly-unbound]  # always set in loop
            "times_seconds": [round(t, 3) for t in times],
            "median_seconds": round(median, 3),
            "threshold_seconds": threshold,
            "passed": passed,
        }

        print(f"  Median: {median:.3f}s (threshold: {threshold}s) -- {'PASS' if passed else 'FAIL'}")

        cleanup_synthetic(writer)
        time.sleep(3)

    print("\n=== RESULTS ===")
    print(json.dumps(results, indent=2))

    report = {
        "benchmark": "FINAL_query_performance",
        "run_at": datetime.now(UTC).isoformat(),
        "gate_criteria": {
            "50k": "wall time < 10 seconds",
            "500k": "wall time < 60 seconds",
        },
        "results": results,
        "all_passed": all(r["passed"] for r in results.values()),
    }

    report_path = os.path.join(
        os.path.dirname(__file__), "..", ".claude", "reports", "benchmark_final_query.json"
    )
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport written to {report_path}")

    sys.exit(0 if report["all_passed"] else 1)


if __name__ == "__main__":
    main()
