from __future__ import annotations

import json
import os
import socket
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
    skippable_http_status_codes = _http_status_codes_from_env(
        "JOB_SKIP_HTTP_STATUS_CODES"
    )

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
        if status_code in skippable_http_status_codes:
            print(
                f"Job POST {url} skipped after HTTP {status_code}; "
                "treating as success.",
                file=sys.stderr,
            )
            return 0

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
                print(format_response_body_for_logs(body))
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
    except TimeoutError as exc:
        print(f"Job POST {url} timed out: {exc}", file=sys.stderr)
        return 504
    except socket.timeout as exc:
        print(f"Job POST {url} timed out: {exc}", file=sys.stderr)
        return 504


def format_response_body_for_logs(body: str) -> str:
    if is_enabled(os.getenv("JOB_PRINT_RESPONSE_BODY")):
        return body

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return _truncate_body(body)

    if not isinstance(payload, dict):
        return _truncate_body(body)

    summary = _response_summary(payload)
    if summary:
        return "Job response summary: " + " ".join(summary)
    return _truncate_body(body)


def _response_summary(payload: dict[str, object]) -> list[str]:
    summary: list[str] = []

    job_run = payload.get("job_run")
    if isinstance(job_run, dict):
        job_name = job_run.get("job_name")
        status = job_run.get("status")
        job_id = job_run.get("id")
        if job_name is not None:
            summary.append(f"job={job_name}")
        if status is not None:
            summary.append(f"status={status}")
        if job_id is not None:
            summary.append(f"id={job_id}")

    phase = payload.get("phase")
    if phase is not None:
        summary.append(f"phase={phase}")

    cleanup = payload.get("cleanup")
    if isinstance(cleanup, dict):
        summary.extend(
            _compact_mapping(
                "cleanup",
                cleanup,
                ("signals_marked_stale", "order_intents_marked_stale"),
            )
        )

    reconcile = payload.get("reconcile")
    if isinstance(reconcile, dict):
        summary.extend(
            _compact_mapping(
                "reconcile",
                reconcile,
                ("orders_seen", "orders_updated", "fills_seen", "positions_seen"),
            )
        )

    news = payload.get("news")
    if isinstance(news, dict):
        risk = news.get("risk_assessment")
        if isinstance(risk, dict):
            summary.extend(
                _compact_mapping(
                    "news",
                    risk,
                    ("market_risk_level", "should_block_new_entries"),
                )
            )
            review_symbols = risk.get("manual_review_symbols")
            if isinstance(review_symbols, list):
                symbols = ",".join(map(str, review_symbols[:8]))
                summary.append(f"news.manual_review_symbols={symbols}")

    performance = payload.get("performance")
    if isinstance(performance, dict):
        summary.extend(
            _compact_mapping(
                "performance",
                performance,
                ("fills_seen", "matched_round_trips"),
            )
        )
        totals = performance.get("totals")
        if isinstance(totals, dict) and "realized_pnl" in totals:
            summary.append(f"performance.realized_pnl={totals['realized_pnl']}")

    for section_name in ("scan", "preview", "exits", "submit"):
        section = payload.get(section_name)
        if isinstance(section, dict):
            summary.extend(
                _compact_mapping(
                    section_name,
                    section,
                    (
                        "status",
                        "signals_created",
                        "order_intents_created",
                        "order_intents_submitted",
                        "exits_created",
                        "errors",
                    ),
                )
            )

    reset_counts = payload.get("counts_before")
    if isinstance(reset_counts, dict):
        for table_name, value in reset_counts.items():
            summary.append(f"counts_before.{table_name}={value}")

    reset_deleted = payload.get("deleted")
    if isinstance(reset_deleted, dict):
        for table_name, value in reset_deleted.items():
            summary.append(f"deleted.{table_name}={value}")

    return summary


def _compact_mapping(
    prefix: str,
    values: dict[str, object],
    keys: tuple[str, ...],
) -> list[str]:
    return [
        f"{prefix}.{key}={values[key]}"
        for key in keys
        if key in values and values[key] is not None
    ]


def _truncate_body(body: str, *, limit: int = 1000) -> str:
    if len(body) <= limit:
        return body
    return body[:limit] + "...[truncated]"


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


def _http_status_codes_from_env(name: str) -> set[int]:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return set()

    status_codes = set()
    for item in raw_value.split(","):
        try:
            status_code = int(item.strip())
        except ValueError as exc:
            raise RuntimeError(f"{name} must be comma-separated integers") from exc
        if status_code < 100 or status_code > 599:
            raise RuntimeError(f"{name} values must be valid HTTP status codes")
        status_codes.add(status_code)
    return status_codes


if __name__ == "__main__":
    raise SystemExit(run_job_from_env())
