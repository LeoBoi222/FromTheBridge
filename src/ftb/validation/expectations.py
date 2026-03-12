"""Great Expectations integration for Bronze → Silver validation.

Builds a dynamic bronze_core suite from the metric catalog and instrument set,
runs batch validation, and maps failures to DeadLetterRow entries.

v4.0 §2413-2457 — 8 core expectations, catalog-driven conditionality.
"""
import json
from collections import Counter

import great_expectations as gx
import pandas as pd
from great_expectations.expectations import (
    ExpectColumnValuesToBeBetween,
    ExpectColumnValuesToBeInSet,
    ExpectColumnValuesToNotBeNull,
    ExpectCompoundColumnsToBeUnique,
)

from ftb.validation.core import Observation
from ftb.writers.silver import DeadLetterRow

# Map GE expectation types to rejection codes.
# Order matters — first match wins when a row fails multiple expectations.
_REJECTION_PRIORITY = [
    "NULL_METRIC",
    "NULL_TIMESTAMP",
    "UNKNOWN_METRIC",
    "UNKNOWN_INSTRUMENT",
    "NULL_VIOLATION",
    "RANGE_VIOLATION",
    "DUPLICATE_OBSERVATION",
]


def build_bronze_core_suite(
    metric_catalog: dict[str, dict],
    instrument_set: set[str],
) -> gx.ExpectationSuite:
    """Build the bronze_core expectation suite from catalog metadata.

    8 core expectations per v4.0 §2427-2436. Expectations #5 and #8
    are catalog-driven (only generated when range bounds or non-nullable
    metrics exist).
    """
    expectations = []

    # 1. metric_id not null
    expectations.append(
        ExpectColumnValuesToNotBeNull(column="metric_id")
    )

    # 7. observed_at not null
    expectations.append(
        ExpectColumnValuesToNotBeNull(column="observed_at")
    )

    # 3. metric_id in catalog set
    if metric_catalog:
        expectations.append(
            ExpectColumnValuesToBeInSet(
                column="metric_id",
                value_set=list(metric_catalog.keys()),
                condition_parser="pandas",
                row_condition="metric_id.notnull()",
            )
        )

    # 4. instrument_id in instrument set (only for rows where instrument_id is not null)
    if instrument_set:
        expectations.append(
            ExpectColumnValuesToBeInSet(
                column="instrument_id",
                value_set=list(instrument_set),
                condition_parser="pandas",
                row_condition="instrument_id.notnull()",
            )
        )

    # 2. instrument_id not null — run broadly, suppress for market-level in mapper
    #    (option b from v4.0: run broadly, filter in the mapper)
    #    We skip this as a GE expectation since null instrument_id is valid for
    #    market-level metrics. The instrument_id-in-set check above handles
    #    invalid non-null instrument IDs.

    # 5. value in range (catalog-driven, conditional per metric)
    for metric_id, meta in metric_catalog.items():
        low = meta.get("expected_range_low")
        high = meta.get("expected_range_high")
        if low is not None or high is not None:
            expectations.append(
                ExpectColumnValuesToBeBetween(
                    column="value",
                    min_value=low,
                    max_value=high,
                    condition_parser="pandas",
                    row_condition=f'metric_id == "{metric_id}" and value.notnull()',
                )
            )

    # 8. value not null (conditional — non-nullable metrics only)
    non_nullable = [
        mid for mid, meta in metric_catalog.items()
        if not meta.get("is_nullable", False)
    ]
    if non_nullable:
        condition_parts = " or ".join(f'metric_id == "{mid}"' for mid in non_nullable)
        expectations.append(
            ExpectColumnValuesToNotBeNull(
                column="value",
                condition_parser="pandas",
                row_condition=f"({condition_parts})",
            )
        )

    # 6. (metric_id, instrument_id, observed_at) uniqueness
    expectations.append(
        ExpectCompoundColumnsToBeUnique(
            column_list=["metric_id", "instrument_id", "observed_at"]
        )
    )

    suite = gx.ExpectationSuite(name="bronze_core", expectations=expectations)
    return suite


def _observations_to_dataframe(observations: list[Observation]) -> pd.DataFrame:
    """Convert observations to a pandas DataFrame for GE validation."""
    if not observations:
        return pd.DataFrame(
            columns=["metric_id", "instrument_id", "source_id", "observed_at", "value"]
        )
    records = [
        {
            "metric_id": obs.metric_id,
            "instrument_id": obs.instrument_id,
            "source_id": obs.source_id,
            "observed_at": obs.observed_at,
            "value": obs.value,
        }
        for obs in observations
    ]
    return pd.DataFrame(records)


def run_validation(
    df: pd.DataFrame,
    suite: gx.ExpectationSuite,
) -> gx.core.expectation_validation_result.ExpectationSuiteValidationResult:
    """Run GE validation on a DataFrame using EphemeralDataContext."""
    context = gx.get_context(mode="ephemeral")
    data_source = context.data_sources.add_pandas(name="sync_batch")
    data_asset = data_source.add_dataframe_asset(name="observations")
    batch_definition = data_asset.add_batch_definition_whole_dataframe(
        name="full_batch"
    )
    context.suites.add(suite)
    validation_definition = context.validation_definitions.add(
        gx.ValidationDefinition(
            name="bronze_core_validation",
            data=batch_definition,
            suite=suite,
        )
    )
    result = validation_definition.run(
        batch_parameters={"dataframe": df},
        result_format={"result_format": "COMPLETE"},
    )
    return result


def _classify_failure(
    expectation_type: str,
    expectation_kwargs: dict,
) -> str:
    """Map a GE expectation failure to a rejection code.

    expectation_type is snake_case (e.g., 'expect_column_values_to_not_be_null').
    """
    column = expectation_kwargs.get("column", "")

    if expectation_type == "expect_column_values_to_not_be_null":
        if column == "metric_id":
            return "NULL_METRIC"
        if column == "observed_at":
            return "NULL_TIMESTAMP"
        if column == "value":
            return "NULL_VIOLATION"
    if expectation_type == "expect_column_values_to_be_in_set":
        if column == "metric_id":
            return "UNKNOWN_METRIC"
        if column == "instrument_id":
            return "UNKNOWN_INSTRUMENT"
    if expectation_type == "expect_column_values_to_be_between":
        return "RANGE_VIOLATION"
    if expectation_type == "expect_compound_columns_to_be_unique":
        return "DUPLICATE_OBSERVATION"

    return "VALIDATION_ERROR"


def _build_dead_letter(obs: Observation, rejection_code: str) -> DeadLetterRow:
    """Build a DeadLetterRow from a failed observation."""
    return DeadLetterRow(
        source_id=obs.source_id,
        metric_id=obs.metric_id,
        instrument_id=obs.instrument_id,
        raw_payload=json.dumps({
            "metric_id": obs.metric_id,
            "instrument_id": obs.instrument_id,
            "value": obs.value,
            "observed_at": obs.observed_at.isoformat() if obs.observed_at else None,
        }),
        rejection_reason=f"GE validation failed: {rejection_code}",
        rejection_code=rejection_code,
    )


def _dedupe_keep_first(df: pd.DataFrame, flagged_indices: list[int]) -> list[int]:
    """For duplicate groups, keep the first occurrence and reject the rest.

    GE's ExpectCompoundColumnsToBeUnique flags ALL rows in a duplicate group.
    We want to keep the first row of each group (lowest index) and only
    reject subsequent duplicates.
    """
    key_cols = ["metric_id", "instrument_id", "observed_at"]
    seen_keys: set[tuple] = set()
    reject = []
    # Process in index order so lowest index is "first"
    for idx in sorted(flagged_indices):
        row = df.iloc[idx]
        key = tuple(row[c] for c in key_cols)
        if key in seen_keys:
            reject.append(idx)
        else:
            seen_keys.add(key)
    return reject


def validate_with_ge(
    observations: list[Observation],
    metric_catalog: dict[str, dict],
    instrument_set: set[str],
) -> tuple[list[Observation], list[DeadLetterRow], dict]:
    """Validate observations using Great Expectations.

    Returns (valid_observations, dead_letter_rows, checkpoint_summary).
    """
    # Empty batch — trivial checkpoint
    if not observations:
        return [], [], {
            "suite": "bronze_core",
            "passed": True,
            "expectations_total": 0,
            "expectations_failed": 0,
            "rows_validated": 0,
            "rows_rejected": 0,
            "rejection_breakdown": {},
        }

    suite = build_bronze_core_suite(metric_catalog, instrument_set)
    df = _observations_to_dataframe(observations)
    result = run_validation(df, suite)

    # Collect failed row indices → rejection code (first match wins per priority)
    failed_rows: dict[int, str] = {}  # index → rejection_code

    for exp_result in result.results:
        if exp_result.success:
            continue

        exp_type = exp_result.expectation_config.type
        exp_kwargs = dict(exp_result.expectation_config.kwargs)
        rejection_code = _classify_failure(exp_type, exp_kwargs)

        # Get unexpected indices from the result
        unexpected_indices = (
            exp_result.result.get("unexpected_index_list", [])
            if exp_result.result
            else []
        )

        if not unexpected_indices:
            continue

        # For duplicates, GE flags ALL copies. Keep first occurrence per group.
        if rejection_code == "DUPLICATE_OBSERVATION":
            unexpected_indices = _dedupe_keep_first(df, unexpected_indices)

        for idx in unexpected_indices:
            if idx not in failed_rows:
                failed_rows[idx] = rejection_code
            else:
                # Keep higher-priority rejection code
                cur = failed_rows[idx]
                cur_pri = _REJECTION_PRIORITY.index(cur) if cur in _REJECTION_PRIORITY else 99
                new_pri = _REJECTION_PRIORITY.index(rejection_code) if rejection_code in _REJECTION_PRIORITY else 99
                if new_pri < cur_pri:
                    failed_rows[idx] = rejection_code

    # Split into valid and dead letter
    valid = []
    dead = []
    rejection_counts: Counter = Counter()

    for i, obs in enumerate(observations):
        if i in failed_rows:
            code = failed_rows[i]
            dead.append(_build_dead_letter(obs, code))
            rejection_counts[code] += 1
        else:
            valid.append(obs)

    # Build checkpoint summary
    expectations_failed = sum(1 for r in result.results if not r.success)
    checkpoint = {
        "suite": "bronze_core",
        "passed": len(dead) == 0,
        "expectations_total": len(result.results),
        "expectations_failed": expectations_failed,
        "rows_validated": len(observations),
        "rows_rejected": len(dead),
        "rejection_breakdown": dict(rejection_counts),
    }

    return valid, dead, checkpoint
