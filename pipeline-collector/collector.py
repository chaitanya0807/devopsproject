"""GitLab CI/CD pipeline data collector.

Polls the GitLab REST API for recent pipelines of one project, then:
  * exposes them as Prometheus metrics on :8000/metrics
  * appends new finished pipelines to data/pipelines.csv for later analysis

Usage:
  python collector.py            # run as an exporter (polls every POLL_INTERVAL s)
  python collector.py --once     # fetch once, print a summary table, write CSV, exit

Environment:
  GITLAB_URL         default https://gitlab.com
  GITLAB_PROJECT_ID  numeric id or "group/project" path (default gitlab-org/gitlab-runner)
  GITLAB_TOKEN       optional personal access token (needed for private projects)
  POLL_INTERVAL      seconds between polls (default 300)
  PIPELINE_COUNT     how many recent pipelines to look at (default 50)
"""
import argparse
import csv
import logging
import os
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests
from prometheus_client import Gauge, start_http_server

GITLAB_URL = os.getenv("GITLAB_URL", "https://gitlab.com").rstrip("/")
PROJECT = os.getenv("GITLAB_PROJECT_ID", "gitlab-org/gitlab-runner")
TOKEN = os.getenv("GITLAB_TOKEN", "")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "300"))
PIPELINE_COUNT = int(os.getenv("PIPELINE_COUNT", "50"))
CSV_PATH = Path(__file__).parent / "data" / "pipelines.csv"
CSV_FIELDS = ["id", "project", "ref", "source", "status", "created_at",
              "started_at", "finished_at", "duration_s", "queued_duration_s", "web_url"]
FINISHED = {"success", "failed", "canceled", "skipped"}
STATUS_CODE = {"success": 1, "failed": 0, "canceled": -1, "running": 2, "pending": 3}

PIPELINES = Gauge("ci_pipeline_total", "Recent pipelines by status", ["project", "status"])
SUCCESS_RATIO = Gauge("ci_pipeline_success_ratio", "success / (success + failed)", ["project"])
DURATION = Gauge("ci_pipeline_duration_seconds", "Duration of recent finished pipelines",
                 ["project", "stat"])
QUEUED = Gauge("ci_pipeline_queued_seconds", "Average queue time of finished pipelines", ["project"])
LAST_STATUS = Gauge("ci_pipeline_last_status",
                    "Latest finished pipeline (1=success 0=failed -1=canceled)", ["project", "ref"])
LAST_SCRAPE = Gauge("ci_collector_last_success_timestamp", "Unix time of last successful poll")
SCRAPE_ERRORS = Gauge("ci_collector_errors", "1 if the last poll failed")

log = logging.getLogger("pipeline-collector")
session = requests.Session()
if TOKEN:
    session.headers["PRIVATE-TOKEN"] = TOKEN
_details_cache: dict[int, dict] = {}


def api(path, **params):
    url = f"{GITLAB_URL}/api/v4/projects/{quote(PROJECT, safe='')}{path}"
    resp = session.get(url, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def fetch_pipelines():
    """Return recent pipelines; finished ones are enriched with timing details."""
    pipelines = api("/pipelines", per_page=PIPELINE_COUNT, order_by="id", sort="desc")
    result = []
    for p in pipelines:
        if p["status"] in FINISHED:
            # Finished pipelines never change, so fetch their details only once.
            if p["id"] not in _details_cache:
                _details_cache[p["id"]] = api(f"/pipelines/{p['id']}")
            p = _details_cache[p["id"]]
        result.append(p)
    return result


def update_metrics(pipelines):
    counts = Counter(p["status"] for p in pipelines)
    PIPELINES.clear()
    for status, n in counts.items():
        PIPELINES.labels(PROJECT, status).set(n)

    decided = counts["success"] + counts["failed"]
    SUCCESS_RATIO.labels(PROJECT).set(counts["success"] / decided if decided else 0)

    durations = sorted(p["duration"] for p in pipelines if p.get("duration"))
    if durations:
        DURATION.labels(PROJECT, "avg").set(sum(durations) / len(durations))
        DURATION.labels(PROJECT, "max").set(durations[-1])
        DURATION.labels(PROJECT, "p50").set(durations[len(durations) // 2])
        DURATION.labels(PROJECT, "last").set(
            next(p["duration"] for p in pipelines if p.get("duration")))
    queued = [p["queued_duration"] for p in pipelines if p.get("queued_duration")]
    if queued:
        QUEUED.labels(PROJECT).set(sum(queued) / len(queued))

    LAST_STATUS.clear()
    for p in pipelines:  # newest first: report the latest finished default-branch run
        if p["ref"] in ("main", "master") and p["status"] in FINISHED:
            LAST_STATUS.labels(PROJECT, p["ref"]).set(STATUS_CODE.get(p["status"], -1))
            break


def write_csv(pipelines):
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    if CSV_PATH.exists():
        with CSV_PATH.open() as f:
            seen = {int(row["id"]) for row in csv.DictReader(f)}
    new = [p for p in pipelines if p["status"] in FINISHED and p["id"] not in seen]
    with CSV_PATH.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not seen:
            writer.writeheader()
        for p in sorted(new, key=lambda p: p["id"]):
            writer.writerow({
                "id": p["id"], "project": PROJECT, "ref": p["ref"], "source": p.get("source"),
                "status": p["status"], "created_at": p.get("created_at"),
                "started_at": p.get("started_at"), "finished_at": p.get("finished_at"),
                "duration_s": p.get("duration"), "queued_duration_s": p.get("queued_duration"),
                "web_url": p.get("web_url"),
            })
    return len(new)


def poll():
    try:
        pipelines = fetch_pipelines()
        update_metrics(pipelines)
        added = write_csv(pipelines)
        LAST_SCRAPE.set_to_current_time()
        SCRAPE_ERRORS.set(0)
        log.info("polled %d pipelines, %d new rows in %s", len(pipelines), added, CSV_PATH)
        return pipelines
    except requests.RequestException as exc:
        SCRAPE_ERRORS.set(1)
        log.error("GitLab API request failed: %s", exc)
        return []


def print_summary(pipelines):
    print(f"\nProject: {PROJECT}  ({GITLAB_URL})  fetched {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"{'ID':>11}  {'STATUS':<9} {'SOURCE':<20} {'DURATION':>9}  REF")
    print("-" * 80)
    for p in pipelines[:20]:
        dur = f"{p['duration']}s" if p.get("duration") else "-"
        print(f"{p['id']:>11}  {p['status']:<9} {(p.get('source') or '')[:20]:<20} {dur:>9}  "
              f"{p['ref'][:30]}")
    counts = Counter(p["status"] for p in pipelines)
    decided = counts["success"] + counts["failed"]
    print("-" * 80)
    print("Status counts:", dict(counts))
    if decided:
        print(f"Success ratio: {counts['success'] / decided:.1%}")
    print(f"CSV: {CSV_PATH}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="poll once, print summary, exit")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.once:
        print_summary(poll())
        return

    start_http_server(args.port)
    log.info("exporter on :%d, project=%s, interval=%ss", args.port, PROJECT, POLL_INTERVAL)
    while True:
        poll()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
