"""Redash client for sql.telemetry.mozilla.org (STMO).

Runs ad-hoc SQL against an STMO data source and returns the result rows.
Authentication matches ``stmo-cli`` (https://github.com/mozilla/stmo-cli):
an ``Authorization: Key <api_key>`` header, with the key taken from
``REDASH_API_KEY`` and the instance from ``REDASH_URL``.
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse

import requests

from herald_scraper.rate_limit import raise_for_rate_limit, retry_on_rate_limit

logger = logging.getLogger(__name__)

DEFAULT_STMO_URL = "https://sql.telemetry.mozilla.org"
# The data source hosting phabricator_metrics on STMO.
DEFAULT_DATA_SOURCE = "Telemetry (BigQuery)"

# Redash job status codes (see stmo-cli's models::JobStatus).
JOB_PENDING = 1
JOB_STARTED = 2
JOB_SUCCESS = 3
JOB_FAILURE = 4
JOB_CANCELLED = 5


class StmoError(Exception):
    """Raised when an STMO query fails to run or return results."""


class StmoClient:
    """Client for running ad-hoc queries against STMO's Redash API.

    A query goes through three steps, mirroring ``stmo-cli execute``:

    1. ``POST /api/query_results`` starts a job for the SQL.
    2. ``GET /api/jobs/{job_id}`` is polled until the job finishes.
    3. ``GET /api/query_results/{result_id}.json`` returns the rows.

    Example:
        client = StmoClient(api_key="...", data_source="Telemetry (BigQuery)")
        rows = client.run_query("SELECT 1 AS n")
    """

    def __init__(
        self,
        api_key: str,
        data_source: Union[int, str] = DEFAULT_DATA_SOURCE,
        base_url: str = DEFAULT_STMO_URL,
        timeout: float = 30.0,
        poll_interval: float = 2.0,
        poll_timeout: float = 300.0,
        user_agent: str = "HeraldScraper/0.1",
    ) -> None:
        """
        Initialize the STMO client.

        Args:
            api_key: Redash API key, from https://sql.telemetry.mozilla.org/users/me
            data_source: Data source to query, either its numeric ID or its
                name as shown on the instance (matched case-insensitively).
                Names are resolved to IDs on first use.
            base_url: Base URL of the Redash instance
            timeout: Per-request timeout in seconds
            poll_interval: Seconds to wait between job status polls
            poll_timeout: Give up on a running query after this many seconds
            user_agent: User-Agent string for requests

        Raises:
            ValueError: If api_key is empty, base_url is not a complete URL,
                or data_source is blank
        """
        if not api_key:
            raise ValueError("api_key is required")

        parsed = urlparse(base_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(
                f"Invalid base_url: '{base_url}'. "
                f"Must be a complete URL (e.g., {DEFAULT_STMO_URL})"
            )

        if isinstance(data_source, str) and not data_source.strip():
            raise ValueError("data_source is required")

        self.base_url = base_url.rstrip("/")
        self.data_source = data_source
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self._data_source_id: Optional[int] = None
        self._session = requests.Session()
        self._session.headers["User-Agent"] = user_agent
        self._session.headers["Authorization"] = f"Key {api_key}"

    @classmethod
    def from_environment(
        cls,
        api_key: Optional[str] = None,
        data_source: Optional[Union[int, str]] = None,
        base_url: Optional[str] = None,
        **kwargs: Any,
    ) -> "StmoClient":
        """Build a client from explicit values, falling back to the environment.

        Reads ``REDASH_API_KEY`` and ``REDASH_URL`` using the same variable
        names as ``stmo-cli``, plus ``STMO_DATA_SOURCE`` for the data source
        (an ID or a name).

        Raises:
            ValueError: If no API key can be determined
        """
        api_key = api_key or os.environ.get("REDASH_API_KEY")
        if not api_key:
            raise ValueError(
                "No STMO API key. Pass --stmo-api-key or set REDASH_API_KEY "
                f"(get a key from {DEFAULT_STMO_URL}/users/me)"
            )

        if data_source is None:
            data_source = os.environ.get("STMO_DATA_SOURCE") or DEFAULT_DATA_SOURCE

        return cls(
            api_key=api_key,
            data_source=data_source,
            base_url=base_url or os.environ.get("REDASH_URL") or DEFAULT_STMO_URL,
            **kwargs,
        )

    def resolve_data_source_id(self) -> int:
        """Return the numeric ID of the configured data source.

        A numeric ``data_source`` is used as-is. A name is looked up against
        ``/api/data_sources`` and cached, so the extra request happens at most
        once per client.

        Raises:
            StmoError: If the name matches no data source on the instance
        """
        if self._data_source_id is not None:
            return self._data_source_id

        if isinstance(self.data_source, int) or str(self.data_source).isdigit():
            self._data_source_id = int(self.data_source)
            return self._data_source_id

        wanted = str(self.data_source).strip().casefold()
        available = self.list_data_sources()
        for source in available:
            if str(source.get("name", "")).strip().casefold() == wanted:
                self._data_source_id = int(source["id"])
                logger.info(
                    f"Resolved STMO data source {source['name']!r} to ID {self._data_source_id}"
                )
                return self._data_source_id

        names = ", ".join(sorted(repr(str(s.get("name"))) for s in available)) or "none"
        raise StmoError(
            f"No STMO data source named {self.data_source!r}. Available: {names}"
        )

    def list_data_sources(self) -> List[Dict[str, Any]]:
        """List the data sources the API key can see."""
        response = self._session.get(
            f"{self.base_url}/api/data_sources", timeout=self.timeout
        )
        body = self._decode_body(response, "/api/data_sources")
        if not isinstance(body, list):
            raise StmoError(f"STMO returned unexpected data source list: {type(body).__name__}")
        return [source for source in body if isinstance(source, dict)]

    def run_query(
        self, sql: str, parameters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Run ad-hoc SQL and return its rows.

        Args:
            sql: SQL to execute against the configured data source
            parameters: Optional Redash query parameters

        Returns:
            List of rows, each a dict keyed by column name

        Raises:
            StmoError: If the query fails, is cancelled, or times out
            requests.RequestException: On network failure
        """
        job = self._start_query(sql, parameters)
        result_id = self._await_result_id(job)
        return self._fetch_rows(result_id)

    def _start_query(
        self, sql: str, parameters: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Submit the SQL and return the job Redash created for it."""
        payload: Dict[str, Any] = {
            "query": sql,
            "data_source_id": self.resolve_data_source_id(),
            "max_age": 0,
        }
        if parameters:
            payload["parameters"] = parameters

        logger.info(f"Running STMO query on data source {self.data_source!r}")
        logger.debug(f"STMO SQL: {sql}")
        response = self._post("/api/query_results", payload)
        job = response.get("job")
        if not isinstance(job, dict):
            raise StmoError(f"STMO did not return a job for the query: {response}")
        return job

    def _await_result_id(self, job: Dict[str, Any]) -> int:
        """Poll a job until it succeeds, returning its query result ID.

        Cancels the job on timeout so it doesn't keep occupying a slot on
        the shared instance.

        Raises:
            StmoError: If the job fails, is cancelled, or exceeds poll_timeout
        """
        job_id = job.get("id")
        if not job_id:
            raise StmoError(f"STMO job has no ID: {job}")

        deadline = time.monotonic() + self.poll_timeout
        while True:
            status = job.get("status")

            if status == JOB_SUCCESS:
                result_id = job.get("query_result_id")
                if result_id is None:
                    raise StmoError(f"STMO job {job_id} succeeded but returned no result ID")
                return int(result_id)

            if status == JOB_FAILURE:
                raise StmoError(f"STMO query failed: {job.get('error') or 'unknown error'}")

            if status == JOB_CANCELLED:
                raise StmoError(f"STMO query {job_id} was cancelled")

            if status not in (JOB_PENDING, JOB_STARTED):
                raise StmoError(f"STMO job {job_id} has unexpected status {status!r}")

            if time.monotonic() >= deadline:
                self._cancel_job(job_id)
                raise StmoError(f"STMO query timed out after {self.poll_timeout}s")

            time.sleep(self.poll_interval)
            job = self._poll_job(job_id)

    def _poll_job(self, job_id: str) -> Dict[str, Any]:
        """Fetch the current state of a job."""
        response = self._get(f"/api/jobs/{job_id}")
        job = response.get("job")
        if not isinstance(job, dict):
            raise StmoError(f"STMO returned no job state for {job_id}: {response}")
        return job

    def _cancel_job(self, job_id: str) -> None:
        """Best-effort cancellation; a failure here must not mask the real error."""
        try:
            self._session.delete(f"{self.base_url}/api/jobs/{job_id}", timeout=self.timeout)
        except requests.RequestException as e:
            logger.warning(f"Failed to cancel STMO job {job_id}: {e}")

    def _fetch_rows(self, result_id: int) -> List[Dict[str, Any]]:
        """Fetch a completed query result and normalize its rows to dicts."""
        response = self._get(f"/api/query_results/{result_id}.json")
        data = response.get("query_result", {}).get("data", {})
        column_names = [col.get("name") for col in data.get("columns", [])]
        rows = [_row_as_dict(row, column_names) for row in data.get("rows", [])]
        logger.info(f"STMO query returned {len(rows)} rows")
        return rows

    def _get(self, path: str) -> Dict[str, Any]:
        """GET a Redash endpoint and return the decoded JSON body."""
        return retry_on_rate_limit(f"getting {path}", lambda: self._get_once(path))

    def _get_once(self, path: str) -> Dict[str, Any]:
        """Make a single GET attempt against a Redash endpoint."""
        response = self._session.get(f"{self.base_url}{path}", timeout=self.timeout)
        return self._decode(response, path)

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST JSON to a Redash endpoint and return the decoded JSON body."""
        return retry_on_rate_limit(f"posting to {path}", lambda: self._post_once(path, payload))

    def _post_once(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Make a single POST attempt against a Redash endpoint."""
        response = self._session.post(
            f"{self.base_url}{path}", json=payload, timeout=self.timeout
        )
        return self._decode(response, path)

    def _decode(self, response: requests.Response, path: str) -> Dict[str, Any]:
        """Turn a Redash response into a dict, raising StmoError on anything else."""
        body = self._decode_body(response, path)
        if not isinstance(body, dict):
            raise StmoError(f"STMO returned unexpected JSON for {path}: {type(body).__name__}")
        return body

    def _decode_body(self, response: requests.Response, path: str) -> Any:
        """Decode a Redash JSON response, mapping auth failures to StmoError.

        Rate limits are split out first: a 403 that carries rate-limit
        signals is transient and worth retrying, unlike a rejected API key.
        """
        raise_for_rate_limit(response, f"requesting {path}")
        if response.status_code in (401, 403):
            raise StmoError(
                f"STMO rejected the API key for {path} (HTTP {response.status_code}). "
                f"Check REDASH_API_KEY."
            )
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as e:
            raise StmoError(f"STMO returned a non-JSON response for {path}: {e}") from e


def _row_as_dict(row: Any, column_names: List[Optional[str]]) -> Dict[str, Any]:
    """Normalize one Redash result row to a column-keyed dict.

    Redash usually returns rows as objects already; positional lists are
    accepted too so we don't depend on that detail of the data source.
    """
    if isinstance(row, dict):
        return row
    if isinstance(row, (list, tuple)):
        return {name: value for name, value in zip(column_names, row) if name is not None}
    raise StmoError(f"Unexpected STMO row type: {type(row).__name__}")
