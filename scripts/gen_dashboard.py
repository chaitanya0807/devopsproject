"""Generates grafana/provisioning/dashboards/system-monitoring.json."""
import json
import sys

PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
panels, y, pid = [], 0, 0


def nid():
    global pid
    pid += 1
    return pid


def row(title):
    global y
    panels.append({"type": "row", "title": title, "id": nid(), "collapsed": False,
                   "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": []})
    y += 1


def panel(kind, title, targets, x, w, h=7, unit=None, ds=PROM, opts=None, field=None):
    p = {"type": kind, "title": title, "id": nid(), "datasource": ds,
         "gridPos": {"h": h, "w": w, "x": x, "y": y},
         "targets": [dict(refId=chr(65 + i), datasource=ds, **t) for i, t in enumerate(targets)],
         "fieldConfig": {"defaults": dict(field or {}), "overrides": []},
         "options": opts or {}}
    if unit:
        p["fieldConfig"]["defaults"]["unit"] = unit
    panels.append(p)


def q(expr, legend=""):
    return {"expr": expr, "legendFormat": legend}


def thresholds(*steps):
    s = [{"color": "green", "value": None}]
    s += [{"color": c, "value": v} for v, c in steps]
    return {"thresholds": {"mode": "absolute", "steps": s}, "color": {"mode": "thresholds"}}


def stat(title, expr, x, w=4, unit=None, field=None, legend=""):
    panel("stat", title, [q(expr, legend)], x, w, 4, unit, field=field,
          opts={"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "background",
                "graphMode": "area", "textMode": "value_and_name" if legend else "auto"})


# ---------------- Host ----------------
row("Host (Node Exporter)")
stat("CPU usage", '100 - avg(rate(node_cpu_seconds_total{mode="idle"}[2m])) * 100', 0, 6, "percent",
     thresholds((70, "orange"), (90, "red")))
stat("Memory usage", "(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100", 6, 6,
     "percent", thresholds((75, "orange"), (90, "red")))
stat("Root disk usage",
     '(1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}) * 100',
     12, 6, "percent", thresholds((80, "orange"), (90, "red")))
stat("Uptime", "time() - node_boot_time_seconds", 18, 6, "s")
y += 4
panel("timeseries", "CPU by mode", [q('sum by (mode) (rate(node_cpu_seconds_total{mode!="idle"}[2m]))', "{{mode}}")],
      0, 12, unit="short")
panel("timeseries", "Network traffic",
      [q('sum(rate(node_network_receive_bytes_total{device!~"lo|veth.*|br-.*|docker.*"}[2m]))', "receive"),
       q('-sum(rate(node_network_transmit_bytes_total{device!~"lo|veth.*|br-.*|docker.*"}[2m]))', "transmit")],
      12, 12, unit="Bps")
y += 7

# ---------------- Cluster ----------------
row("Kubernetes cluster (kube-state-metrics)")
stat("Ready nodes", 'sum(kube_node_status_condition{condition="Ready",status="true"})', 0)
stat("Running pods", 'sum(kube_pod_status_phase{phase="Running"})', 4)
stat("Pending / failed pods", 'sum(kube_pod_status_phase{phase=~"Pending|Failed"})', 8,
     field=thresholds((1, "red")))
stat("Container restarts (1h)", "sum(increase(kube_pod_container_status_restarts_total[1h]))", 12,
     field=thresholds((1, "orange"), (5, "red")))
stat("Deployments unavailable",
     "count(kube_deployment_status_replicas_available < kube_deployment_spec_replicas) or vector(0)", 16,
     field=thresholds((1, "red")))
stat("Namespaces", "count(kube_namespace_created)", 20)
y += 4
panel("timeseries", "Running pods by namespace",
      [q('sum by (namespace) (kube_pod_status_phase{phase="Running"})', "{{namespace}}")], 0, 12)
panel("table", "Deployment replicas",
      [{"expr": "kube_deployment_spec_replicas", "format": "table", "instant": True},
       {"expr": "kube_deployment_status_replicas_available", "format": "table", "instant": True}],
      12, 12, opts={"showHeader": True})
panels[-1]["transformations"] = [
    {"id": "merge"},
    {"id": "organize", "options": {
        "excludeByName": {"Time": True, "__name__": True, "instance": True, "job": True},
        "renameByName": {"Value #A": "desired", "Value #B": "available"}}}]
y += 7

# ---------------- Containers ----------------
row("Containers / pods (cAdvisor)")
panel("timeseries", "CPU by pod (cores)",
      [q('sum by (namespace, pod) (rate(container_cpu_usage_seconds_total{pod!=""}[2m]))', "{{namespace}}/{{pod}}")],
      0, 12, unit="short")
panel("timeseries", "Memory working set by pod",
      [q('sum by (namespace, pod) (container_memory_working_set_bytes{pod!="", container=""})',
         "{{namespace}}/{{pod}}")], 12, 12, unit="bytes")
y += 7

# ---------------- App ----------------
row("Sample application (demo-app)")
stat("Request rate", "sum(rate(app_http_requests_total[1m]))", 0, 6, "reqps")
stat("Error rate", 'sum(rate(app_http_requests_total{status=~"5.."}[5m])) / sum(rate(app_http_requests_total[5m])) * 100',
     6, 6, "percent", thresholds((5, "orange"), (10, "red")))
stat("p95 latency", "histogram_quantile(0.95, sum by (le) (rate(app_http_request_duration_seconds_bucket[5m])))",
     12, 6, "s", thresholds((0.3, "orange"), (0.5, "red")))
stat("Orders created (1h)", "sum(increase(app_orders_created_total[1h]))", 18, 6)
y += 4
panel("timeseries", "Requests by status",
      [q("sum by (status) (rate(app_http_requests_total[1m]))", "{{status}}")], 0, 8, unit="reqps")
panel("timeseries", "Requests by pod",
      [q("sum by (pod) (rate(app_http_requests_total[1m]))", "{{pod}}")], 8, 8, unit="reqps")
panel("timeseries", "Latency percentiles",
      [q("histogram_quantile(0.50, sum by (le) (rate(app_http_request_duration_seconds_bucket[5m])))", "p50"),
       q("histogram_quantile(0.95, sum by (le) (rate(app_http_request_duration_seconds_bucket[5m])))", "p95"),
       q("histogram_quantile(0.99, sum by (le) (rate(app_http_request_duration_seconds_bucket[5m])))", "p99")],
      16, 8, unit="s")
y += 7

# ---------------- CI/CD ----------------
row("CI/CD pipelines (GitLab collector)")
stat("Success ratio", "ci_pipeline_success_ratio * 100", 0, 6, "percent",
     {"thresholds": {"mode": "absolute", "steps": [
         {"color": "red", "value": None}, {"color": "orange", "value": 70}, {"color": "green", "value": 90}]},
      "color": {"mode": "thresholds"}})
stat("Avg duration", 'ci_pipeline_duration_seconds{stat="avg"}', 6, 6, "s")
stat("Avg queue time", "ci_pipeline_queued_seconds", 12, 6, "s")
stat("Last main-branch run", "ci_pipeline_last_status", 18, 6,
     field={"mappings": [{"type": "value", "options": {
         "1": {"text": "SUCCESS", "color": "green"}, "0": {"text": "FAILED", "color": "red"},
         "-1": {"text": "CANCELED", "color": "orange"}}}], "color": {"mode": "thresholds"},
         "thresholds": {"mode": "absolute", "steps": [{"color": "blue", "value": None}]}})
y += 4
panel("piechart", "Recent pipelines by status", [q("sum by (status) (ci_pipeline_total)", "{{status}}")],
      0, 8, 8, opts={"reduceOptions": {"calcs": ["lastNotNull"]}, "legend": {"displayMode": "table", "placement": "right"}})
panel("timeseries", "Pipeline duration", [q("ci_pipeline_duration_seconds", "{{stat}}")], 8, 16, 8, "s")
y += 8

# ---------------- Logs ----------------
row("Logs (Loki)")
panel("timeseries", "Log lines per second by level",
      [{"expr": 'sum by (detected_level) (rate({namespace="demo-app", app="sample-app"}[1m]))',
        "legendFormat": "{{detected_level}}"}], 0, 24, 6, ds=LOKI)
y += 6
panel("logs", "sample-app logs (errors first: filter with the search box)",
      [{"expr": '{namespace="demo-app", app="sample-app"}'}], 0, 24, 10, ds=LOKI,
      opts={"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True})

dash = {"uid": "mtech-system-monitoring", "title": "System Monitoring",
        "tags": ["mtech", "kubernetes", "observability"], "timezone": "browser",
        "schemaVersion": 41, "version": 1, "refresh": "30s",
        "time": {"from": "now-1h", "to": "now"}, "panels": panels}
json.dump(dash, open(sys.argv[1], "w"), indent=2)
print(f"{len(panels)} panels")
