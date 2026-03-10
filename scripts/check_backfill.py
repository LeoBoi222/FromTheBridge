#!/usr/bin/env python3
"""Check status of all active Tiingo backfills."""
import json
import urllib.request

DAGSTER_GRAPHQL = "http://localhost:3010/graphql"

def graphql(query, variables=None):
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    req = urllib.request.Request(
        DAGSTER_GRAPHQL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=30)
    return json.loads(resp.read())


# Get backfill statuses
result = graphql("""
{
    partitionBackfillsOrError(limit: 10) {
        ... on PartitionBackfills {
            results {
                id
                status
                numPartitions
                numCancelable
            }
        }
    }
}
""")

backfills = result["data"]["partitionBackfillsOrError"]["results"]
print(f"{'ID':<12} {'Status':<20} {'Partitions':<12} {'Cancelable':<10}")
print("-" * 54)
for b in backfills:
    print(f"{b['id']:<12} {b['status']:<20} {b['numPartitions']:<12} {b['numCancelable']:<10}")

# Get run counts
for status in ["SUCCESS", "FAILURE", "STARTED", "QUEUED"]:
    result = graphql(f"""
    {{
        runsOrError(filter: {{statuses: [{status}]}}, limit: 1) {{
            ... on Runs {{ count }}
        }}
    }}
    """)
    count = result["data"]["runsOrError"]["count"]
    print(f"\nRuns {status}: {count}")

# Check collection events
print("\n--- Collection Event Summary ---")
result = graphql("""
{
    runsOrError(filter: {statuses: [SUCCESS]}, limit: 1) {
        ... on Runs { count }
    }
}
""")
print(f"Total successful runs: {result['data']['runsOrError']['count']}")
