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
