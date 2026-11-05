"""Sample order service used as the monitored workload.

Exposes Prometheus metrics on /metrics and writes one JSON log line per request
to stdout so Promtail can ship it to Loki.
"""
import json
import logging
import os
import random
import sys
import time

from flask import Flask, Response, g, jsonify, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

app = Flask(__name__)
POD = os.getenv("POD_NAME", "local")
ERROR_RATE = float(os.getenv("ERROR_RATE", "0.05"))

REQUESTS = Counter("app_http_requests_total", "HTTP requests", ["method", "path", "status"])
LATENCY = Histogram(
    "app_http_request_duration_seconds", "HTTP request latency", ["path"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
)
ORDERS = Counter("app_orders_created_total", "Orders created")

log = logging.getLogger("sample-app")
log.setLevel(logging.INFO)
log.addHandler(logging.StreamHandler(sys.stdout))
logging.getLogger("werkzeug").setLevel(logging.ERROR)


@app.before_request
def start_timer():
    g.start = time.perf_counter()


@app.after_request
def record(resp):
    if request.path == "/metrics":
        return resp
    elapsed = time.perf_counter() - g.start
    REQUESTS.labels(request.method, request.path, resp.status_code).inc()
    LATENCY.labels(request.path).observe(elapsed)
    level = "error" if resp.status_code >= 500 else "info"
    log.info(json.dumps({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "level": level, "pod": POD,
        "method": request.method, "path": request.path,
        "status": resp.status_code, "latency_ms": round(elapsed * 1000, 2),
    }))
    return resp


@app.get("/")
def index():
    return jsonify(service="sample-app", pod=POD, status="ok")


@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/api/orders")
def orders():
    time.sleep(random.uniform(0.005, 0.3))
    if random.random() < ERROR_RATE:
        return jsonify(error="payment gateway timeout"), 500
    ORDERS.inc()
    return jsonify(order_id=random.randint(10000, 99999), pod=POD)


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
