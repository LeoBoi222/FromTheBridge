"""Tests for gold_export core logic — domain mapping, query, anomaly guard."""

from datetime import UTC, datetime

from ftb.export.gold_export import (
    build_export_query,
    catalog_to_gold_domain,
    check_anomaly_guard,
    derive_partitions,
)


class TestCatalogToGoldDomain:
    """v4.0: Gold domain 'onchain' maps to catalog domain 'chain'.
    Gold domain 'flows' maps to catalog domains 'flows', 'etf', 'stablecoin'.
    Remaining: derivatives, macro, defi stay as-is.
    price, metadata excluded from Phase 1 export.
    """

    def test_chain_maps_to_onchain(self):
        assert catalog_to_gold_domain("chain") == "onchain"

    def test_flows_stays_flows(self):
        assert catalog_to_gold_domain("flows") == "flows"

    def test_etf_maps_to_flows(self):
        assert catalog_to_gold_domain("etf") == "flows"

    def test_stablecoin_maps_to_flows(self):
        assert catalog_to_gold_domain("stablecoin") == "flows"

    def test_derivatives_stays(self):
        assert catalog_to_gold_domain("derivatives") == "derivatives"

    def test_macro_stays(self):
        assert catalog_to_gold_domain("macro") == "macro"

    def test_defi_stays(self):
        assert catalog_to_gold_domain("defi") == "defi"

    def test_price_returns_none(self):
        assert catalog_to_gold_domain("price") is None

    def test_metadata_returns_none(self):
        assert catalog_to_gold_domain("metadata") is None

    def test_unknown_returns_none(self):
        assert catalog_to_gold_domain("foobar") is None


class TestBuildExportQuery:
    """v4.0: SELECT ... FINAL with watermark delta + 3-minute lag floor."""

    def test_query_has_final_keyword(self):
        sql, params = build_export_query(
            datetime(2026, 3, 10, 0, 0, tzinfo=UTC),
            datetime(2026, 3, 10, 1, 0, tzinfo=UTC),
        )
        assert "FINAL" in sql

    def test_query_uses_watermark(self):
        wm = datetime(2026, 3, 10, 0, 0, tzinfo=UTC)
        run_ts = datetime(2026, 3, 10, 1, 0, tzinfo=UTC)
        sql, params = build_export_query(wm, run_ts)
        assert params["last_watermark"] == wm

    def test_query_applies_3min_lag(self):
        wm = datetime(2026, 3, 10, 0, 0, tzinfo=UTC)
        run_ts = datetime(2026, 3, 10, 1, 0, tzinfo=UTC)
        sql, params = build_export_query(wm, run_ts)
        assert "INTERVAL 3 MINUTE" in sql

    def test_first_run_no_watermark(self):
        """First run uses epoch as watermark."""
        sql, params = build_export_query(
            None,
            datetime(2026, 3, 10, 1, 0, tzinfo=UTC),
        )
        assert params["last_watermark"].year == 1970

    def test_query_has_correct_columns(self):
        sql, _ = build_export_query(
            datetime(2026, 3, 10, tzinfo=UTC),
            datetime(2026, 3, 10, 1, 0, tzinfo=UTC),
        )
        for col in ["metric_id", "instrument_id", "observed_at", "value", "ingested_at", "data_version"]:
            assert col in sql

    def test_query_orders_correctly(self):
        sql, _ = build_export_query(
            datetime(2026, 3, 10, tzinfo=UTC),
            datetime(2026, 3, 10, 1, 0, tzinfo=UTC),
        )
        assert "ORDER BY metric_id, instrument_id, observed_at" in sql


class TestDerivePartitions:
    def test_single_row(self):
        rows = [{"observed_at": datetime(2026, 3, 10), "metric_domain": "macro"}]
        assert derive_partitions(rows) == {("2026-03", "macro")}

    def test_multiple_months_and_domains(self):
        rows = [
            {"observed_at": datetime(2026, 2, 15), "metric_domain": "macro"},
            {"observed_at": datetime(2026, 3, 10), "metric_domain": "defi"},
            {"observed_at": datetime(2026, 3, 10), "metric_domain": "macro"},
        ]
        result = derive_partitions(rows)
        assert result == {("2026-02", "macro"), ("2026-03", "defi"), ("2026-03", "macro")}

    def test_deduplicates(self):
        rows = [
            {"observed_at": datetime(2026, 3, 10), "metric_domain": "macro"},
            {"observed_at": datetime(2026, 3, 15), "metric_domain": "macro"},
        ]
        assert derive_partitions(rows) == {("2026-03", "macro")}


class TestAnomalyGuard:
    """v4.0: Fail if delta exceeds 10x rolling 7-day avg or >2M rows."""

    def test_under_limit_passes(self):
        assert check_anomaly_guard(100, rolling_avg=50) is True

    def test_over_10x_fails(self):
        assert check_anomaly_guard(600, rolling_avg=50) is False

    def test_exactly_10x_passes(self):
        assert check_anomaly_guard(500, rolling_avg=50) is True

    def test_over_2m_hard_cap(self):
        assert check_anomaly_guard(2_000_001, rolling_avg=1_000_000) is False

    def test_2m_exactly_passes(self):
        assert check_anomaly_guard(2_000_000, rolling_avg=1_000_000) is True

    def test_zero_rolling_avg_allows_first_run(self):
        """First run has no history — allow up to 2M."""
        assert check_anomaly_guard(1000, rolling_avg=0) is True

    def test_force_backfill_bypasses(self):
        assert check_anomaly_guard(5_000_000, rolling_avg=50, force_backfill=True) is True
