from __future__ import annotations

import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


TRUTHY_VALUES = {"1", "true", "yes", "on"}


def is_enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in TRUTHY_VALUES


def build_job_url(base_url: str, job_path: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", job_path.lstrip("/"))


def run_job_from_env() -> int:
    if not is_enabled(os.getenv("SCHEDULED_JOBS_ENABLED")):
        print("Scheduled jobs are disabled; set SCHEDULED_JOBS_ENABLED=true to run.")
        return 0

    base_url = _required_env("STOCKS_API_BASE_URL")
    admin_token = _required_env("ADMIN_API_TOKEN")
    job_path = os.getenv("JOB_PATH", "/api/v1/jobs/reconcile-broker")
    timeout_seconds = int(os.getenv("JOB_TIMEOUT_SECONDS", "90"))

    url = build_job_url(base_url, job_path)
    request = Request(
        url,
        method="POST",
        headers={
            "Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
            print(f"Job POST {url} returned {response.status}")
            if body:
                print(body)
            return 0
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        print(f"Job POST {url} failed with HTTP {exc.code}", file=sys.stderr)
        if body:
            print(body, file=sys.stderr)
        return 1
    except URLError as exc:
        print(f"Job POST {url} failed: {exc}", file=sys.stderr)
        return 1


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"{name} is required when scheduled jobs are enabled")
    return value.strip()


if __name__ == "__main__":
    raise SystemExit(run_job_from_env())
