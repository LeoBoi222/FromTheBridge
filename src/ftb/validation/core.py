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
