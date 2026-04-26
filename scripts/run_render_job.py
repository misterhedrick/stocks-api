from __future__ import annotations

import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


TRUTHY_VALUES = {"1", "true", "yes", "on"}
RETRYABLE_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}
DEFAULT_RETRY_DELAYS_SECONDS = (10, 30)


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
    retry_delays = _retry_delays_from_env()

    url = build_job_url(base_url, job_path)
    request = Request(
        url,
        method="POST",
        headers={
            "Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json",
        },
    )

    attempts = len(retry_delays) + 1
    for attempt in range(1, attempts + 1):
        result = _post_job(request, url, timeout_seconds)
        if result == 0:
            return 0

        status_code = result
        if (
            status_code not in RETRYABLE_HTTP_STATUS_CODES
            or attempt == attempts
        ):
            return 1

        retry_delay = retry_delays[attempt - 1]
        print(
            f"Job POST {url} will retry after HTTP {status_code} "
            f"in {retry_delay} seconds ({attempt}/{attempts})",
            file=sys.stderr,
        )
        time.sleep(retry_delay)

    return 1


def _post_job(request: Request, url: str, timeout_seconds: int) -> int:
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
        return exc.code
    except URLError as exc:
        print(f"Job POST {url} failed: {exc}", file=sys.stderr)
        return 1


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"{name} is required when scheduled jobs are enabled")
    return value.strip()


def _retry_delays_from_env() -> tuple[int, ...]:
    raw_value = os.getenv("JOB_RETRY_DELAYS_SECONDS")
    if raw_value is None:
        return DEFAULT_RETRY_DELAYS_SECONDS
    if not raw_value.strip():
        return ()

    retry_delays = []
    for item in raw_value.split(","):
        try:
            retry_delay = int(item.strip())
        except ValueError as exc:
            raise RuntimeError("JOB_RETRY_DELAYS_SECONDS must be comma-separated integers") from exc
        if retry_delay < 0:
            raise RuntimeError("JOB_RETRY_DELAYS_SECONDS values must be greater than or equal to 0")
        retry_delays.append(retry_delay)
    return tuple(retry_delays)


if __name__ == "__main__":
    raise SystemExit(run_job_from_env())
