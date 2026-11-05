"""Export one experiment run from Prometheus and Loki into flat files.

All methods are evaluated offline on these identical files:
  metrics.csv   15 s time series of the service-level features
  alerts.csv    firing intervals of the B1 Prometheus threshold alerts (ALERTS series)
  logs.jsonl    raw log lines of sample-app and payments

Usage: python export.py data/<run-id>
"""
import csv
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

PROM = "http://localhost:9090"
LOKI = "http://localhost:3100"
STEP = 15

S = 'app="sample-app"'
FEATURES = {
    # name: (PromQL, fill value when the series is absent)
    "rps": (f"sum(rate(app_http_requests_total{{{S}}}[1m]))", 0),
    "err_ratio": (f'sum(rate(app_http_requests_total{{{S},status=~"5.."}}[1m])) '
                  f"/ sum(rate(app_http_requests_total{{{S}}}[1m]))", 0),
    "p95": (f"histogram_quantile(0.95, sum by (le) "
            f"(rate(app_http_request_duration_seconds_bucket{{{S}}}[1m])))", 0),
    "cpu_max": ('max(rate(container_cpu_usage_seconds_total{namespace="demo-app",'
                'container="sample-app"}[1m]))', 0),
    "mem_max": ('max(container_memory_working_set_bytes{namespace="demo-app",container="sample-app"})', 0),
    "restarts": ('sum(increase(kube_pod_container_status_restarts_total{namespace="demo-app"}[1m]))', 0),
    "unavailable": ('sum(kube_deployment_spec_replicas{namespace="demo-app"}) '
                    '- sum(kube_deployment_status_replicas_available{namespace="demo-app"})', 0),
    "payments_rps": ('sum(rate(app_http_requests_total{app="payments"}[1m]))', 0),
}


def get(url, **params):
    with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(params)}", timeout=60) as r:
        return json.load(r)


def query_range(expr, start, end):
    res = get(f"{PROM}/api/v1/query_range", query=expr, start=start, end=end, step=STEP)
    return res["data"]["result"]


def export_metrics(run, start, end):
    grid = [start + i * STEP for i in range(int((end - start) // STEP) + 1)]
    cols = {}
    for name, (expr, fill) in FEATURES.items():
        values = {}
        for series in query_range(expr, start, end):
            for ts, v in series["values"]:
                values[round(float(ts))] = float(v) if v not in ("NaN", "+Inf", "-Inf") else fill
        cols[name] = [values.get(round(t), fill) for t in grid]
    with (run / "metrics.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", *FEATURES])
        for i, t in enumerate(grid):
            w.writerow([round(t), *(round(cols[n][i], 6) for n in FEATURES)])
    return len(grid)


def export_alerts(run, start, end):
    rows = []
    for series in query_range('ALERTS{alertstate="firing",baseline="b1"}', start, end):
        for ts, _ in series["values"]:
            rows.append((round(float(ts)), series["metric"]["alertname"]))
    with (run / "alerts.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "alertname"])
        w.writerows(sorted(rows))
    return len(rows)


def export_logs(run, start, end):
    query = '{namespace="demo-app", app=~"sample-app|payments"}'
    cursor, n = int(start * 1e9), 0
    with (run / "logs.jsonl").open("w") as f:
        while True:
            res = get(f"{LOKI}/loki/api/v1/query_range", query=query, start=cursor,
                      end=int(end * 1e9), limit=5000, direction="forward")
            entries = [(int(ts), s["stream"].get("app"), s["stream"].get("pod"), line)
                       for s in res["data"]["result"] for ts, line in s["values"]]
            if not entries:
                break
            entries.sort()
            for ts, app, pod, line in entries:
                f.write(json.dumps({"ts": ts / 1e9, "app": app, "pod": pod, "line": line}) + "\n")
            n += len(entries)
            if len(entries) < 5000:
                break
            cursor = entries[-1][0] + 1
    return n


def main(run_dir):
    run = Path(run_dir)
    meta = json.loads((run / "meta.json").read_text())
    start, end = meta["start"], meta["end"] + 60
    print(f"{run.name}: metrics {export_metrics(run, start, end)} rows, "
          f"alerts {export_alerts(run, start, end)} samples, logs {export_logs(run, start, end)} lines")


if __name__ == "__main__":
    for d in sys.argv[1:]:
        main(d)
