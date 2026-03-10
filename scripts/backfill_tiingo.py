#!/usr/bin/env python3
"""Launch Tiingo backfill via Dagster GraphQL API.

Generates partition names and launches backfill in chunks to avoid
overwhelming the GraphQL endpoint.

Usage: python3 backfill_tiingo.py [--start 2019-01-01] [--end 2026-03-09] [--chunk-size 500]
"""
import argparse
import json
import sys
import urllib.request
from datetime import date, timedelta


DAGSTER_GRAPHQL = "http://localhost:3010/graphql"

LAUNCH_MUTATION = """
mutation LaunchBackfill($names: [String!]!) {
    launchPartitionBackfill(backfillParams: {
        assetSelection: [{path: ["collect_tiingo_price"]}],
        partitionNames: $names
    }) {
        ... on LaunchBackfillSuccess { backfillId }
        ... on PythonError { message stack }
    }
}
"""

STATUS_QUERY = """
query BackfillStatus($id: String!) {
    partitionBackfillOrError(backfillId: $id) {
        ... on PartitionBackfill {
            id
            status
            numPartitions
            numCancelable
        }
        ... on PythonError { message }
    }
}
"""


def graphql(query: str, variables: dict | None = None) -> dict:
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    req = urllib.request.Request(
        DAGSTER_GRAPHQL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=60)
    return json.loads(resp.read())


def generate_partitions(start: date, end: date, skip: set[str] | None = None) -> list[str]:
    skip = skip or set()
    parts = []
    d = start
    while d <= end:
        iso = d.isoformat()
        if iso not in skip:
            parts.append(iso)
        d += timedelta(days=1)
    return parts


def main():
    parser = argparse.ArgumentParser(description="Launch Tiingo backfill")
    parser.add_argument("--start", default="2019-01-01", help="Start date (inclusive)")
    parser.add_argument("--end", default="2026-03-09", help="End date (inclusive)")
    parser.add_argument("--chunk-size", type=int, default=500, help="Partitions per backfill")
    parser.add_argument("--dry-run", action="store_true", help="Print partition count only")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    partitions = generate_partitions(start, end)
    print(f"Total partitions: {len(partitions)} ({start} to {end})")

    if args.dry_run:
        print("Dry run — not launching.")
        return

    backfill_ids = []
    for i in range(0, len(partitions), args.chunk_size):
        chunk = partitions[i : i + args.chunk_size]
        result = graphql(LAUNCH_MUTATION, {"names": chunk})

        data = result.get("data", {}).get("launchPartitionBackfill", {})
        bid = data.get("backfillId")
        err = data.get("message")

        if bid:
            backfill_ids.append(bid)
            print(f"  Chunk {i // args.chunk_size + 1}: {chunk[0]}..{chunk[-1]} ({len(chunk)} parts) -> backfill {bid}")
        else:
            print(f"  Chunk {i // args.chunk_size + 1}: FAILED — {err}", file=sys.stderr)
            sys.exit(1)

    print(f"\nLaunched {len(backfill_ids)} backfill(s): {', '.join(backfill_ids)}")
    print("Monitor at http://192.168.68.11:3010/overview/backfills")


if __name__ == "__main__":
    main()
