"""Tests for the STMO (Redash) client."""

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
import requests

from herald_scraper.exceptions import RateLimitError
from herald_scraper.rate_limit import MAX_RATE_LIMIT_RETRIES
from herald_scraper.stmo_client import (
    DEFAULT_DATA_SOURCE,
    DEFAULT_STMO_URL,
    JOB_CANCELLED,
    JOB_FAILURE,
    JOB_PENDING,
    JOB_STARTED,
    JOB_SUCCESS,
    StmoClient,
    StmoError,
)


def make_response(
    status_code: int = 200,
    json_body: Any = None,
    headers: Optional[Dict[str, str]] = None,
) -> MagicMock:
    """Build a mock requests.Response returning json_body."""
    response = MagicMock(spec=requests.Response)
    response.status_code = status_code
    response.json.return_value = json_body
    response.raise_for_status.return_value = None
    # Real responses always carry headers; the rate-limit check reads them.
    response.headers = dict(headers or {})
    response.text = ""
    return response


def job(status: int, **fields: Any) -> Dict[str, Any]:
    """Build a Redash job payload."""
    return {"job": {"id": "job-1", "status": status, **fields}}


def query_result(columns: List[str], rows: List[Any]) -> Dict[str, Any]:
    """Build a Redash query_result payload."""
    return {
        "query_result": {
            "id": 99,
            "data": {"columns": [{"name": c} for c in columns], "rows": rows},
        }
    }


@pytest.fixture
def client() -> StmoClient:
    """An StmoClient that polls without sleeping."""
    return StmoClient(
        api_key="test-key",
        data_source=63,
        poll_interval=0,
        poll_timeout=10,
    )


class TestStmoClientInit:
    """Tests for StmoClient initialization."""

    def test_sets_authorization_header(self, client: StmoClient) -> None:
        assert client._session.headers["Authorization"] == "Key test-key"

    def test_defaults_to_stmo_url(self, client: StmoClient) -> None:
        assert client.base_url == DEFAULT_STMO_URL

    def test_strips_trailing_slash(self) -> None:
        c = StmoClient(api_key="k", data_source=1, base_url="https://redash.example.com/")
        assert c.base_url == "https://redash.example.com"

    def test_empty_api_key_raises(self) -> None:
        with pytest.raises(ValueError, match="api_key is required"):
            StmoClient(api_key="", data_source=1)

    def test_invalid_base_url_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid base_url"):
            StmoClient(api_key="k", data_source=1, base_url="not-a-url")


class TestStmoClientFromEnvironment:
    """Tests for StmoClient.from_environment()."""

    def test_reads_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REDASH_API_KEY", "env-key")
        monkeypatch.setenv("STMO_DATA_SOURCE", "42")
        monkeypatch.setenv("REDASH_URL", "https://redash.example.com")

        c = StmoClient.from_environment()

        assert c._session.headers["Authorization"] == "Key env-key"
        assert c.resolve_data_source_id() == 42
        assert c.base_url == "https://redash.example.com"

    def test_arguments_win_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REDASH_API_KEY", "env-key")
        monkeypatch.setenv("STMO_DATA_SOURCE", "42")

        c = StmoClient.from_environment(api_key="arg-key", data_source=7)

        assert c._session.headers["Authorization"] == "Key arg-key"
        assert c.resolve_data_source_id() == 7

    def test_missing_api_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REDASH_API_KEY", raising=False)
        with pytest.raises(ValueError, match="REDASH_API_KEY"):
            StmoClient.from_environment(data_source=1)

    def test_defaults_to_telemetry_bigquery(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("STMO_DATA_SOURCE", raising=False)

        c = StmoClient.from_environment(api_key="k")

        assert c.data_source == DEFAULT_DATA_SOURCE


class TestStmoClientResolveDataSource:
    """Tests for resolving a data source name to its numeric ID."""

    @staticmethod
    def _client(data_source: Any) -> StmoClient:
        return StmoClient(api_key="k", data_source=data_source, poll_interval=0)

    def test_numeric_id_needs_no_lookup(self) -> None:
        client = self._client(63)
        with patch.object(client._session, "get") as get:
            assert client.resolve_data_source_id() == 63
        get.assert_not_called()

    def test_numeric_string_needs_no_lookup(self) -> None:
        client = self._client("63")
        with patch.object(client._session, "get") as get:
            assert client.resolve_data_source_id() == 63
        get.assert_not_called()

    def test_resolves_name_via_api(self) -> None:
        client = self._client("Telemetry (BigQuery)")
        with patch.object(client._session, "get") as get:
            get.return_value = make_response(
                json_body=[
                    {"id": 5, "name": "Crash DB"},
                    {"id": 63, "name": "Telemetry (BigQuery)"},
                ]
            )

            assert client.resolve_data_source_id() == 63

        assert get.call_args[0][0] == f"{DEFAULT_STMO_URL}/api/data_sources"

    def test_name_match_is_case_and_space_insensitive(self) -> None:
        client = self._client("  telemetry (bigquery)  ")
        with patch.object(client._session, "get") as get:
            get.return_value = make_response(
                json_body=[{"id": 63, "name": "Telemetry (BigQuery)"}]
            )

            assert client.resolve_data_source_id() == 63

    def test_resolution_is_cached(self) -> None:
        client = self._client("Telemetry (BigQuery)")
        with patch.object(client._session, "get") as get:
            get.return_value = make_response(
                json_body=[{"id": 63, "name": "Telemetry (BigQuery)"}]
            )

            client.resolve_data_source_id()
            client.resolve_data_source_id()

        assert get.call_count == 1

    def test_unknown_name_lists_available_sources(self) -> None:
        client = self._client("Nonexistent Source")
        with patch.object(client._session, "get") as get:
            get.return_value = make_response(
                json_body=[{"id": 5, "name": "Crash DB"}, {"id": 63, "name": "Telemetry"}]
            )

            with pytest.raises(StmoError, match="Crash DB.*Telemetry"):
                client.resolve_data_source_id()

    def test_blank_name_raises_at_construction(self) -> None:
        with pytest.raises(ValueError, match="data_source is required"):
            StmoClient(api_key="k", data_source="   ")

    def test_non_list_response_raises(self) -> None:
        client = self._client("Telemetry (BigQuery)")
        with patch.object(client._session, "get") as get:
            get.return_value = make_response(json_body={"unexpected": True})

            with pytest.raises(StmoError, match="unexpected data source list"):
                client.resolve_data_source_id()

    def test_query_resolves_name_before_posting(self) -> None:
        client = self._client("Telemetry (BigQuery)")
        with patch.object(client._session, "post") as post, patch.object(
            client._session, "get"
        ) as get:
            post.return_value = make_response(json_body=job(JOB_SUCCESS, query_result_id=99))
            get.side_effect = [
                make_response(json_body=[{"id": 63, "name": "Telemetry (BigQuery)"}]),
                make_response(json_body=query_result(["n"], [{"n": 1}])),
            ]

            rows = client.run_query("SELECT 1")

        assert rows == [{"n": 1}]
        assert post.call_args[1]["json"]["data_source_id"] == 63


class TestStmoClientRunQuery:
    """Tests for StmoClient.run_query()."""

    def test_returns_rows_from_successful_query(self, client: StmoClient) -> None:
        with patch.object(client._session, "post") as post, patch.object(
            client._session, "get"
        ) as get:
            post.return_value = make_response(json_body=job(JOB_SUCCESS, query_result_id=99))
            get.return_value = make_response(
                json_body=query_result(["n"], [{"n": 1}, {"n": 2}])
            )

            rows = client.run_query("SELECT n FROM t")

        assert rows == [{"n": 1}, {"n": 2}]

    def test_posts_adhoc_payload(self, client: StmoClient) -> None:
        with patch.object(client._session, "post") as post, patch.object(
            client._session, "get"
        ) as get:
            post.return_value = make_response(json_body=job(JOB_SUCCESS, query_result_id=99))
            get.return_value = make_response(json_body=query_result(["n"], []))

            client.run_query("SELECT 1", parameters={"day": "2026-08-07"})

        url, kwargs = post.call_args[0][0], post.call_args[1]
        assert url == f"{DEFAULT_STMO_URL}/api/query_results"
        assert kwargs["json"] == {
            "query": "SELECT 1",
            "data_source_id": 63,
            "max_age": 0,
            "parameters": {"day": "2026-08-07"},
        }

    def test_omits_parameters_when_absent(self, client: StmoClient) -> None:
        with patch.object(client._session, "post") as post, patch.object(
            client._session, "get"
        ) as get:
            post.return_value = make_response(json_body=job(JOB_SUCCESS, query_result_id=99))
            get.return_value = make_response(json_body=query_result(["n"], []))

            client.run_query("SELECT 1")

        assert "parameters" not in post.call_args[1]["json"]

    def test_polls_until_success(self, client: StmoClient) -> None:
        with patch.object(client._session, "post") as post, patch.object(
            client._session, "get"
        ) as get:
            post.return_value = make_response(json_body=job(JOB_PENDING))
            get.side_effect = [
                make_response(json_body=job(JOB_STARTED)),
                make_response(json_body=job(JOB_SUCCESS, query_result_id=99)),
                make_response(json_body=query_result(["n"], [{"n": 1}])),
            ]

            rows = client.run_query("SELECT 1")

        assert rows == [{"n": 1}]
        assert get.call_count == 3

    def test_positional_rows_are_keyed_by_column(self, client: StmoClient) -> None:
        with patch.object(client._session, "post") as post, patch.object(
            client._session, "get"
        ) as get:
            post.return_value = make_response(json_body=job(JOB_SUCCESS, query_result_id=99))
            get.return_value = make_response(
                json_body=query_result(["a", "b"], [["x", "y"]])
            )

            rows = client.run_query("SELECT a, b FROM t")

        assert rows == [{"a": "x", "b": "y"}]

    def test_failed_job_raises(self, client: StmoClient) -> None:
        with patch.object(client._session, "post") as post:
            post.return_value = make_response(json_body=job(JOB_FAILURE, error="bad table"))

            with pytest.raises(StmoError, match="bad table"):
                client.run_query("SELECT 1")

    def test_cancelled_job_raises(self, client: StmoClient) -> None:
        with patch.object(client._session, "post") as post:
            post.return_value = make_response(json_body=job(JOB_CANCELLED))

            with pytest.raises(StmoError, match="cancelled"):
                client.run_query("SELECT 1")

    def test_success_without_result_id_raises(self, client: StmoClient) -> None:
        with patch.object(client._session, "post") as post:
            post.return_value = make_response(json_body=job(JOB_SUCCESS))

            with pytest.raises(StmoError, match="no result ID"):
                client.run_query("SELECT 1")

    def test_unknown_status_raises(self, client: StmoClient) -> None:
        with patch.object(client._session, "post") as post:
            post.return_value = make_response(json_body=job(99))

            with pytest.raises(StmoError, match="unexpected status"):
                client.run_query("SELECT 1")

    def test_response_without_job_raises(self, client: StmoClient) -> None:
        with patch.object(client._session, "post") as post:
            post.return_value = make_response(json_body={})

            with pytest.raises(StmoError, match="did not return a job"):
                client.run_query("SELECT 1")

    def test_timeout_cancels_job_and_raises(self) -> None:
        client = StmoClient(
            api_key="k", data_source=1, poll_interval=0, poll_timeout=0
        )
        with patch.object(client._session, "post") as post, patch.object(
            client._session, "delete"
        ) as delete:
            post.return_value = make_response(json_body=job(JOB_PENDING))

            with pytest.raises(StmoError, match="timed out"):
                client.run_query("SELECT 1")

        delete.assert_called_once()
        assert delete.call_args[0][0].endswith("/api/jobs/job-1")

    def test_cancel_failure_does_not_mask_timeout(self) -> None:
        client = StmoClient(
            api_key="k", data_source=1, poll_interval=0, poll_timeout=0
        )
        with patch.object(client._session, "post") as post, patch.object(
            client._session, "delete"
        ) as delete:
            post.return_value = make_response(json_body=job(JOB_PENDING))
            delete.side_effect = requests.ConnectionError("boom")

            with pytest.raises(StmoError, match="timed out"):
                client.run_query("SELECT 1")

    def test_unauthorized_raises_with_key_hint(self, client: StmoClient) -> None:
        with patch.object(client._session, "post") as post:
            post.return_value = make_response(status_code=403)

            with pytest.raises(StmoError, match="REDASH_API_KEY"):
                client.run_query("SELECT 1")

    def test_non_json_response_raises(self, client: StmoClient) -> None:
        with patch.object(client._session, "post") as post:
            response = make_response()
            response.json.side_effect = ValueError("no json")
            post.return_value = response

            with pytest.raises(StmoError, match="non-JSON response"):
                client.run_query("SELECT 1")

    def test_http_error_propagates(self, client: StmoClient) -> None:
        with patch.object(client._session, "post") as post:
            response = make_response(status_code=500)
            response.raise_for_status.side_effect = requests.HTTPError("500")
            post.return_value = response

            with pytest.raises(requests.HTTPError):
                client.run_query("SELECT 1")


class TestStmoClientRateLimit:
    """STMO calls back off on a rate limit rather than failing outright."""

    def test_rate_limited_post_is_retried(self, client: StmoClient) -> None:
        with patch.object(client._session, "post") as post:
            post.side_effect = [
                make_response(status_code=429, headers={"retry-after": "5"}),
                make_response(json_body=job(JOB_SUCCESS, query_result_id=99)),
            ]
            with patch.object(client._session, "get") as get:
                get.return_value = make_response(
                    json_body=query_result(["reviewer_group"], [["alpha-reviewers"]])
                )

                with patch("herald_scraper.rate_limit.time.sleep") as sleep:
                    rows = client.run_query("SELECT 1")

        sleep.assert_called_once_with(5.0)
        assert rows == [{"reviewer_group": "alpha-reviewers"}]
        assert post.call_count == 2

    def test_persistent_rate_limit_raises(self, client: StmoClient) -> None:
        with patch.object(client._session, "post") as post:
            post.return_value = make_response(
                status_code=429, headers={"retry-after": "1"}
            )

            with patch("herald_scraper.rate_limit.time.sleep"):
                with pytest.raises(RateLimitError):
                    client.run_query("SELECT 1")

        assert post.call_count == MAX_RATE_LIMIT_RETRIES + 1

    def test_rate_limited_403_is_retried_not_reported_as_bad_key(
        self, client: StmoClient
    ) -> None:
        """A 403 with rate-limit headers must not be mistaken for a bad API key."""
        with patch.object(client._session, "post") as post:
            post.side_effect = [
                make_response(
                    status_code=403,
                    headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1"},
                ),
                make_response(json_body=job(JOB_SUCCESS, query_result_id=99)),
            ]
            with patch.object(client._session, "get") as get:
                get.return_value = make_response(
                    json_body=query_result(["reviewer_group"], [["alpha-reviewers"]])
                )

                with patch("herald_scraper.rate_limit.time.sleep"):
                    rows = client.run_query("SELECT 1")

        assert rows == [{"reviewer_group": "alpha-reviewers"}]
        assert post.call_count == 2
