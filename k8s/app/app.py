"""Sample order service used as the monitored workload.

Exposes Prometheus metrics on /metrics and writes one JSON log line per request
to stdout so Promtail can ship it to Loki.

ROLE=orders (default) serves /api/orders and calls the payments service.
ROLE=payments serves /api/pay (the downstream dependency).

Fault injection (used by experiments/run_experiment.py):
  GET /admin/fault?type=error&value=0.4    extra error probability
  GET /admin/fault?type=latency&value=800  extra latency in ms
  GET /admin/fault?type=cpu                busy-loop threads (CPU saturation)
  GET /admin/fault?type=leak               leak memory until OOMKilled (persists across restarts)
  GET /admin/fault?type=crash              crash now and on every restart (crash loop)
  GET /admin/clear                         clear in-process faults
  env BUGGY=1                              "bad release": unhandled exception on some requests
  env PAYMENT_TIMEOUT_MS                   client timeout for the payments call (config faults)
"""
import json
import logging
import os
import random
import sys
import threading
import time
import urllib.request

from flask import Flask, Response, g, jsonify, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

ROLE = os.getenv("ROLE", "orders")
POD = os.getenv("POD_NAME", "local")
VERSION = os.getenv("VERSION", "1.0.0")
ERROR_RATE = float(os.getenv("ERROR_RATE", "0.02"))
BUGGY = os.getenv("BUGGY", "0") == "1"
PAYMENTS_URL = os.getenv("PAYMENTS_URL", "")
PAYMENT_TIMEOUT = float(os.getenv("PAYMENT_TIMEOUT_MS", "1000")) / 1000
STATE_DIR = os.getenv("STATE_DIR", "/state")

log = logging.getLogger("sample-app")
log.setLevel(logging.INFO)
log.addHandler(logging.StreamHandler(sys.stdout))
logging.getLogger("werkzeug").setLevel(logging.ERROR)


def emit(level, msg, **fields):
    log.info(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "level": level, "pod": POD,
                         "role": ROLE, "version": VERSION, "msg": msg, **fields}))


def flag(name):
    return os.path.join(STATE_DIR, name)


# A crash-loop fault survives container restarts through a flag file on an emptyDir volume.
if os.path.exists(flag("crash")):
    emit("fatal", "failed to load order state: corrupted checkpoint, exiting")
    sys.exit(3)

app = Flask(__name__)
REQUESTS = Counter("app_http_requests_total", "HTTP requests", ["method", "path", "status"])
LATENCY = Histogram(
    "app_http_request_duration_seconds", "HTTP request latency", ["path"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
ORDERS = Counter("app_orders_created_total", "Orders created")
INFO = Gauge("app_build_info", "Build information", ["version", "role"])
INFO.labels(VERSION, ROLE).set(1)

faults = {"error": 0.0, "latency": 0.0}
_leak, _stop = [], threading.Event()


def burn_cpu():
    while not _stop.is_set():
        sum(i * i for i in range(20000))


def leak_memory():
    while os.path.exists(flag("leak")):
        _leak.append(bytearray(3 * 1024 * 1024))
        time.sleep(1)


if os.path.exists(flag("leak")):
    threading.Thread(target=leak_memory, daemon=True).start()


@app.before_request
def start_timer():
    g.start = time.perf_counter()


@app.after_request
def record(resp):
    if request.path in ("/metrics", "/healthz") or request.path.startswith("/admin"):
        return resp
    elapsed = time.perf_counter() - g.start
    REQUESTS.labels(request.method, request.path, resp.status_code).inc()
    LATENCY.labels(request.path).observe(elapsed)
    level = "error" if resp.status_code >= 500 else "info"
    emit(level, g.get("msg", "request handled"), method=request.method, path=request.path,
         status=resp.status_code, latency_ms=round(elapsed * 1000, 2))
    return resp


@app.get("/")
def index():
    return jsonify(service=ROLE, pod=POD, version=VERSION, status="ok")


@app.get("/healthz")
def healthz():
    return "ok"


def fail(msg, status=500):
    g.msg = msg
    return jsonify(error=msg), status


@app.get("/api/orders")
def orders():
    time.sleep(random.uniform(0.005, 0.2) + faults["latency"] / 1000)
    if random.random() < ERROR_RATE + faults["error"]:
        return fail("payment gateway timeout")
    if BUGGY and random.random() < 0.35:
        return fail("unhandled exception: KeyError 'customer_id' in build_invoice()")
    if PAYMENTS_URL:
        try:
            urllib.request.urlopen(f"{PAYMENTS_URL}/api/pay", timeout=PAYMENT_TIMEOUT).read()
        except Exception as exc:  # noqa: BLE001 - any failure of the dependency
            return fail(f"payments call failed: {type(exc).__name__}", 502)
    ORDERS.inc()
    return jsonify(order_id=random.randint(10000, 99999), pod=POD)


@app.get("/api/pay")
def pay():
    time.sleep(random.uniform(0.005, 0.06))
    return jsonify(status="captured")


@app.get("/admin/fault")
def set_fault():
    kind, value = request.args.get("type"), request.args.get("value", "0")
    if kind in faults:
        faults[kind] = float(value)
    elif kind == "cpu":
        _stop.clear()
        for _ in range(2):
            threading.Thread(target=burn_cpu, daemon=True).start()
    elif kind in ("leak", "crash"):
        os.makedirs(STATE_DIR, exist_ok=True)
        open(flag(kind), "w").close()
        if kind == "leak":
            threading.Thread(target=leak_memory, daemon=True).start()
        else:
            threading.Timer(0.2, lambda: os._exit(3)).start()
    else:
        return jsonify(error=f"unknown fault {kind}"), 400
    # Deliberately not logged: the detectors must not see ground truth in the logs.
    return jsonify(pod=POD, fault=kind, value=value)


@app.get("/admin/clear")
def clear():
    faults.update(error=0.0, latency=0.0)
    _stop.set()
    _leak.clear()
    for name in ("leak", "crash"):
        if os.path.exists(flag(name)):
            os.remove(flag(name))
    return jsonify(pod=POD, cleared=True)


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
