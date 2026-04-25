from __future__ import annotations

import os
import unittest
from io import StringIO
from unittest.mock import patch

from scripts.run_render_job import build_job_url, is_enabled, run_job_from_env


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


if __name__ == "__main__":
    unittest.main()
