"""Tests for Gold Arrow table building and merge logic."""

from datetime import UTC, datetime

import pyarrow as pa

from ftb.export.gold_export import build_gold_arrow_table, merge_partition


class TestMergePartition:
    """v4.0: Merge by data_version — keep higher version for duplicate keys."""

    def _ts(self, day=10):
        return datetime(2026, 3, day, tzinfo=UTC)

    def _ing(self):
        return datetime(2026, 3, 10, 0, 5, tzinfo=UTC)

    def _table(self, metric_ids, instrument_ids, observed_ats, values, versions):
        return pa.table({
            "metric_id": metric_ids,
            "instrument_id": instrument_ids,
            "observed_at": observed_ats,
            "value": values,
            "data_version": versions,
            "ingested_at": [self._ing()] * len(metric_ids),
            "metric_domain": ["macro"] * len(metric_ids),
            "year_month": ["2026-03"] * len(metric_ids),
        })

    def test_no_existing_data(self):
        new = self._table(["m1", "m2"], ["BTC-USD", "ETH-USD"],
                          [self._ts(), self._ts()], [1.0, 2.0], [1, 1])
        result = merge_partition(None, new)
        assert result.num_rows == 2

    def test_higher_version_wins(self):
        existing = self._table(["m1"], ["BTC-USD"], [self._ts()], [100.0], [1])
        new = self._table(["m1"], ["BTC-USD"], [self._ts()], [101.0], [2])
        result = merge_partition(existing, new)
        assert result.num_rows == 1
        assert result.column("value")[0].as_py() == 101.0

    def test_lower_version_ignored(self):
        existing = self._table(["m1"], ["BTC-USD"], [self._ts()], [100.0], [2])
        new = self._table(["m1"], ["BTC-USD"], [self._ts()], [99.0], [1])
        result = merge_partition(existing, new)
        assert result.num_rows == 1
        assert result.column("value")[0].as_py() == 100.0

    def test_disjoint_rows_concatenated(self):
        existing = self._table(["m1"], ["BTC-USD"], [self._ts(10)], [100.0], [1])
        new = self._table(["m1"], ["BTC-USD"], [self._ts(11)], [200.0], [1])
        result = merge_partition(existing, new)
        assert result.num_rows == 2

    def test_null_instrument_id_handled(self):
        existing = self._table(["m1"], [None], [self._ts()], [100.0], [1])
        new = self._table(["m1"], [None], [self._ts()], [101.0], [2])
        result = merge_partition(existing, new)
        assert result.num_rows == 1
        assert result.column("value")[0].as_py() == 101.0

    def test_empty_existing_returns_new(self):
        from ftb.export.gold_export import GOLD_ARROW_SCHEMA
        empty = pa.table(
            {f.name: pa.array([], type=f.type) for f in GOLD_ARROW_SCHEMA},
            schema=GOLD_ARROW_SCHEMA,
        )
        new = self._table(["m1"], ["BTC-USD"], [self._ts()], [1.0], [1])
        result = merge_partition(empty, new)
        assert result.num_rows == 1


class TestBuildGoldArrowTable:
    """Transform CH result rows into Arrow table with Gold schema + domain mapping."""

    def test_basic_row_mapping(self):
        rows = [{
            "metric_id": "macro.rates.fed_funds",
            "instrument_id": None,
            "observed_at": datetime(2026, 3, 10, tzinfo=UTC),
            "value": 4.5,
            "data_version": 1,
            "ingested_at": datetime(2026, 3, 10, 0, 5, tzinfo=UTC),
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
            "observed_at": datetime(2026, 3, 10, tzinfo=UTC),
            "value": 50000.0,
            "data_version": 1,
            "ingested_at": datetime(2026, 3, 10, 0, 5, tzinfo=UTC),
        }]
        domain_lookup = {"price.spot.close_usd": "price"}
        table = build_gold_arrow_table(rows, domain_lookup)
        assert table.num_rows == 0

    def test_chain_mapped_to_onchain(self):
        rows = [{
            "metric_id": "chain.tx_count",
            "instrument_id": "BTC-USD",
            "observed_at": datetime(2026, 3, 10, tzinfo=UTC),
            "value": 300000,
            "data_version": 1,
            "ingested_at": datetime(2026, 3, 10, 0, 5, tzinfo=UTC),
        }]
        domain_lookup = {"chain.tx_count": "chain"}
        table = build_gold_arrow_table(rows, domain_lookup)
        assert table.column("metric_domain")[0].as_py() == "onchain"

    def test_unknown_metric_skipped(self):
        rows = [{
            "metric_id": "unknown.metric",
            "instrument_id": None,
            "observed_at": datetime(2026, 3, 10, tzinfo=UTC),
            "value": 1.0,
            "data_version": 1,
            "ingested_at": datetime(2026, 3, 10, 0, 5, tzinfo=UTC),
        }]
        domain_lookup = {}  # metric not in catalog
        table = build_gold_arrow_table(rows, domain_lookup)
        assert table.num_rows == 0

    def test_multiple_domains(self):
        rows = [
            {
                "metric_id": "macro.rates.fed_funds",
                "instrument_id": None,
                "observed_at": datetime(2026, 3, 10, tzinfo=UTC),
                "value": 4.5,
                "data_version": 1,
                "ingested_at": datetime(2026, 3, 10, 0, 5, tzinfo=UTC),
            },
            {
                "metric_id": "defi.tvl.total",
                "instrument_id": None,
                "observed_at": datetime(2026, 3, 10, tzinfo=UTC),
                "value": 100e9,
                "data_version": 1,
                "ingested_at": datetime(2026, 3, 10, 0, 5, tzinfo=UTC),
            },
        ]
        domain_lookup = {"macro.rates.fed_funds": "macro", "defi.tvl.total": "defi"}
        table = build_gold_arrow_table(rows, domain_lookup)
        assert table.num_rows == 2
        domains = set(table.column("metric_domain").to_pylist())
        assert domains == {"macro", "defi"}
