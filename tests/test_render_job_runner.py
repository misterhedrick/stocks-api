from __future__ import annotations

import os
import unittest
from io import BytesIO, StringIO
from urllib.error import HTTPError
from unittest.mock import patch

from scripts.run_render_job import build_job_url, is_enabled, run_job_from_env


class FakeResponse:
    def __init__(self, *, status: int = 200, body: bytes = b"ok") -> None:
        self.status = status
        self.body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class RenderJobRunnerTests(unittest.TestCase):
    def test_is_enabled_accepts_truthy_values_only(self) -> None:
        self.assertTrue(is_enabled("true"))
        self.assertTrue(is_enabled("1"))
        self.assertTrue(is_enabled("YES"))
        self.assertFalse(is_enabled("false"))
        self.assertFalse(is_enabled(""))
        self.assertFalse(is_enabled(None))

    def test_build_job_url_preserves_query_string(self) -> None:
        self.assertEqual(
            build_job_url(
                "https://stocks-api-z11i.onrender.com/",
                "/api/v1/jobs/reconcile-broker?order_limit=100",
            ),
            "https://stocks-api-z11i.onrender.com/api/v1/jobs/reconcile-broker?order_limit=100",
        )

    def test_disabled_job_exits_successfully_without_required_secrets(self) -> None:
        with patch.dict(os.environ, {"SCHEDULED_JOBS_ENABLED": "false"}, clear=True), patch(
            "sys.stdout",
            new_callable=StringIO,
        ):
            self.assertEqual(run_job_from_env(), 0)

    def test_job_retries_retryable_http_errors(self) -> None:
        retryable_error = HTTPError(
            "https://example.test/api/v1/jobs/market-cycle",
            429,
            "Too Many Requests",
            hdrs=None,
            fp=BytesIO(b"Too Many Requests"),
        )

        with patch.dict(
            os.environ,
            {
                "SCHEDULED_JOBS_ENABLED": "true",
                "STOCKS_API_BASE_URL": "https://example.test",
                "ADMIN_API_TOKEN": "token",
                "JOB_PATH": "/api/v1/jobs/market-cycle",
                "JOB_RETRY_DELAYS_SECONDS": "0",
            },
            clear=True,
        ), patch(
            "scripts.run_render_job.urlopen",
            side_effect=[retryable_error, FakeResponse()],
        ) as urlopen, patch(
            "scripts.run_render_job.time.sleep",
        ) as sleep, patch(
            "sys.stdout",
            new_callable=StringIO,
        ), patch(
            "sys.stderr",
            new_callable=StringIO,
        ):
            self.assertEqual(run_job_from_env(), 0)

        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(0)

    def test_job_does_not_retry_non_retryable_http_errors(self) -> None:
        non_retryable_error = HTTPError(
            "https://example.test/api/v1/jobs/market-cycle",
            401,
            "Unauthorized",
            hdrs=None,
            fp=BytesIO(b"Unauthorized"),
        )

        with patch.dict(
            os.environ,
            {
                "SCHEDULED_JOBS_ENABLED": "true",
                "STOCKS_API_BASE_URL": "https://example.test",
                "ADMIN_API_TOKEN": "token",
                "JOB_PATH": "/api/v1/jobs/market-cycle",
                "JOB_RETRY_DELAYS_SECONDS": "0",
            },
            clear=True,
        ), patch(
            "scripts.run_render_job.urlopen",
            side_effect=non_retryable_error,
        ) as urlopen, patch(
            "scripts.run_render_job.time.sleep",
        ) as sleep, patch(
            "sys.stdout",
            new_callable=StringIO,
        ), patch(
            "sys.stderr",
            new_callable=StringIO,
        ):
            self.assertEqual(run_job_from_env(), 1)

        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
