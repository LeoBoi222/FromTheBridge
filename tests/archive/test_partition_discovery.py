"""Tests for DuckDB-based partition discovery."""

from unittest.mock import MagicMock, patch

import pytest

from ftb.archive.partition_discovery import (
    _configure_duckdb_s3,
    _resolve_iceberg_params,
    _sql_str,
)


class TestSqlStr:
    def test_simple_string(self):
        assert _sql_str("hello") == "'hello'"

    def test_escapes_quotes(self):
        assert _sql_str("it's") == "'it''s'"


class TestResolveIcebergParams:
    def test_extracts_root_version_and_format(self):
        mock_catalog = MagicMock()
        mock_table = MagicMock()
        mock_table.metadata_location = "s3://bronze-hot/bronze/observations_hot/metadata/00001-abc123.metadata.json"
        mock_catalog.load_table.return_value = mock_table

        root, version, name_format = _resolve_iceberg_params(mock_catalog, "bronze.observations_hot")
        assert root == "s3://bronze-hot/bronze/observations_hot"
        assert version == "00001"
        assert name_format == "00001-abc123%s.metadata.json"

    def test_higher_version_with_full_uuid(self):
        mock_catalog = MagicMock()
        mock_table = MagicMock()
        mock_table.metadata_location = "s3://bronze-hot/bronze/observations_hot/metadata/00042-ba6ab442-1ffe-4416-af03-00993cabdca5.metadata.json"
        mock_catalog.load_table.return_value = mock_table

        root, version, name_format = _resolve_iceberg_params(mock_catalog, "bronze.observations_hot")
        assert root == "s3://bronze-hot/bronze/observations_hot"
        assert version == "00042"
        assert "00042-ba6ab442-1ffe-4416-af03-00993cabdca5" in name_format
        assert name_format.endswith("%s.metadata.json")

    def test_raises_on_bad_format(self):
        mock_catalog = MagicMock()
        mock_table = MagicMock()
        mock_table.metadata_location = "s3://bronze-hot/some/weird/path.json"
        mock_catalog.load_table.return_value = mock_table

        with pytest.raises(ValueError, match="Unexpected metadata_location"):
            _resolve_iceberg_params(mock_catalog, "bronze.observations_hot")


class TestConfigureDuckdbS3:
    def test_sets_s3_properties_from_catalog(self):
        mock_con = MagicMock()
        mock_catalog = MagicMock()
        mock_catalog.properties = {
            "s3.endpoint": "http://minio:9001",
            "s3.access-key-id": "test_key",
            "s3.secret-access-key": "test_secret",
            "s3.region": "us-east-1",
        }

        _configure_duckdb_s3(mock_con, mock_catalog)

        # Verify S3 settings were configured
        calls = [str(c) for c in mock_con.execute.call_args_list]
        assert any("s3_endpoint" in c for c in calls)
        assert any("s3_access_key_id" in c for c in calls)
        assert any("s3_secret_access_key" in c for c in calls)
        assert any("s3_url_style" in c for c in calls)
        assert any("s3_use_ssl" in c for c in calls)

    def test_strips_http_prefix_from_endpoint(self):
        mock_con = MagicMock()
        mock_catalog = MagicMock()
        mock_catalog.properties = {
            "s3.endpoint": "http://minio:9001",
            "s3.access-key-id": "",
            "s3.secret-access-key": "",
            "s3.region": "us-east-1",
        }

        _configure_duckdb_s3(mock_con, mock_catalog)

        endpoint_call = mock_con.execute.call_args_list[0][0][0]
        assert "minio:9001" in endpoint_call
        assert "http://" not in endpoint_call
