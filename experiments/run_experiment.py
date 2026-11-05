#!/usr/bin/env python3
"""Failure-injection harness: produces one labelled experiment run.

Each run: warm-up, then the 8 fault types in a seeded random order, each injected
for --inject seconds and followed by --cooldown seconds of normal operation.
Two benign deployments are placed inside random cool-downs, so the change feed
also contains changes that do NOT cause failures (needed to measure false positives).

Outputs in experiments/data/<run-id>/:
  meta.json     run parameters and time range
  labels.csv    ground truth: fault, target, start, end, root-cause class
  changes.jsonl CD change feed (deploys / config changes) visible to the detectors

Only the standard library is used; kubectl must point at the cluster.
Usage: python3 experiments/run_experiment.py --run-id run01 --seed 1
"""
import argparse
import csv
import json
import random
import subprocess
import time
from pathlib import Path

NS = "demo-app"
DATA = Path(__file__).parent / "data"

# fault -> (root-cause class used for attribution scoring, description)
FAULTS = {
    "error_spike": ("app", "30 % extra 5xx responses on all sample-app pods"),
    "latency": ("app", "+700 ms latency on all sample-app pods"),
    "cpu_saturation": ("cpu", "CPU busy-loop on one sample-app pod"),
    "memory_leak": ("memory", "memory leak on one pod until OOMKilled (repeats)"),
    "crash_loop": ("availability", "one pod crash-loops on startup"),
    "bad_deploy": ("change", "release 1.1.0 with an unhandled-exception bug"),
    "dependency_down": ("dependency", "payments service scaled to 0"),
    "config_error": ("change", "config change PAYMENT_TIMEOUT_MS=25 (too small)"),
}


def sh(*args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check).stdout.strip()


def kubectl(*args, check=True):
    return sh("kubectl", "-n", NS, *args, check=check)


def ready_pods(app="sample-app"):
    out = kubectl("get", "pods", "-l", f"app={app}", "-o", "json")
    pods = []
    for p in json.loads(out)["items"]:
        conds = {c["type"]: c["status"] for c in p["status"].get("conditions", [])}
        if p["metadata"].get("deletionTimestamp") is None and conds.get("Ready") == "True":
            pods.append(p["metadata"]["name"])
    return sorted(pods)


def admin(pod, path, retries=10):
    for _ in range(retries):
        try:
            return sh("kubectl", "get", "--raw", f"/api/v1/namespaces/{NS}/pods/{pod}:8080/proxy{path}")
        except subprocess.CalledProcessError:
            time.sleep(3)  # pod may be restarting (OOM) - retry
    return None


def rollout(*env):
    kubectl("set", "env", "deploy/sample-app", *env)
    kubectl("rollout", "status", "deploy/sample-app", "--timeout=180s", check=False)


class Run:
    def __init__(self, run_id, seed, warmup, inject, cooldown):
        self.dir = DATA / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.run_id, self.rng = run_id, random.Random(seed)
        self.warmup, self.inject, self.cooldown = warmup, inject, cooldown
        self.meta = {"run_id": run_id, "seed": seed, "warmup_s": warmup, "inject_s": inject,
                     "cooldown_s": cooldown}
        self.labels, self.changes = [], []
        self.version = 0
        self.target = None

    def log(self, msg):
        print(time.strftime("%H:%M:%S"), self.run_id, msg, flush=True)

    def change(self, kind, description, **extra):
        """Record a CD change event, as a GitLab deployment/pipeline feed would report it."""
        self.version += 1
        ev = {"ts": time.time(), "service": "sample-app", "kind": kind,
              "change_id": f"{self.run_id}-c{self.version}", "description": description, **extra}
        self.changes.append(ev)
        with (self.dir / "changes.jsonl").open("a") as f:
            f.write(json.dumps(ev) + "\n")

    # ---------------- faults ----------------
    def inject_fault(self, fault):
        pods = ready_pods()
        self.target = self.rng.choice(pods)
        if fault == "error_spike":
            self.target = "all"
            for p in pods:
                admin(p, "/admin/fault?type=error&value=0.3")
        elif fault == "latency":
            self.target = "all"
            for p in pods:
                admin(p, "/admin/fault?type=latency&value=700")
        elif fault in ("cpu_saturation", "memory_leak", "crash_loop"):
            kind = {"cpu_saturation": "cpu", "memory_leak": "leak", "crash_loop": "crash"}[fault]
            admin(self.target, f"/admin/fault?type={kind}")
        elif fault == "bad_deploy":
            self.target = "deploy/sample-app"
            self.change("deploy", "release sample-app 1.1.0", version="1.1.0")
            rollout("BUGGY=1", "VERSION=1.1.0")
        elif fault == "dependency_down":
            self.target = "deploy/payments"
            kubectl("scale", "deploy/payments", "--replicas=0")
        elif fault == "config_error":
            self.target = "deploy/sample-app"
            self.change("config", "set PAYMENT_TIMEOUT_MS=25")
            rollout("PAYMENT_TIMEOUT_MS=25")

    def clear_fault(self, fault):
        if fault in ("error_spike", "latency"):
            for p in ready_pods():
                admin(p, "/admin/clear")
        elif fault in ("cpu_saturation", "memory_leak"):
            admin(self.target, "/admin/clear", retries=30)
        elif fault == "crash_loop":
            kubectl("delete", "pod", self.target, "--wait=false", check=False)
        elif fault == "bad_deploy":
            self.change("rollback", "rollback sample-app to 1.0.0", version="1.0.0")
            rollout("BUGGY=0", "VERSION=1.0.0")
        elif fault == "dependency_down":
            kubectl("scale", "deploy/payments", "--replicas=1")
        elif fault == "config_error":
            self.change("config", "set PAYMENT_TIMEOUT_MS=1000")
            rollout("PAYMENT_TIMEOUT_MS=1000")

    def benign_deploy(self):
        v = f"1.0.{self.rng.randint(1, 99)}"
        self.change("deploy", f"release sample-app {v}", version=v)
        rollout(f"VERSION={v}")  # the new version is healthy, so it stays deployed

    def restore_baseline(self):
        """Always leave the system healthy, even if the run is interrupted."""
        for p in ready_pods():
            admin(p, "/admin/clear", retries=2)
        kubectl("scale", "deploy/payments", "--replicas=1", check=False)
        env = json.loads(kubectl("get", "deploy/sample-app", "-o",
                                 "jsonpath={.spec.template.spec.containers[0].env}") or "[]")
        env = {e["name"]: e.get("value") for e in env}
        if env.get("BUGGY") != "0" or env.get("PAYMENT_TIMEOUT_MS") != "1000":
            rollout("BUGGY=0", "PAYMENT_TIMEOUT_MS=1000")

    # ---------------- schedule ----------------
    def execute(self):
        order = list(FAULTS)
        self.rng.shuffle(order)
        benign_after = set(self.rng.sample(range(len(order)), 2))
        self.meta["start"] = time.time()
        self.meta["fault_order"] = order
        self.log(f"warm-up {self.warmup}s; order: {order}")
        time.sleep(self.warmup)
        try:
            for i, fault in enumerate(order):
                start = time.time()
                self.inject_fault(fault)
                self.log(f"INJECT {fault} target={self.target}")
                time.sleep(max(0, self.inject - (time.time() - start)))
                self.clear_fault(fault)
                end = time.time()
                self.log(f"CLEAR  {fault}")
                self.labels.append({"run_id": self.run_id, "fault_id": f"{self.run_id}-f{i + 1}",
                                    "fault": fault, "target": self.target, "start": round(start, 3),
                                    "end": round(end, 3), "root_cause": FAULTS[fault][0]})
                self.write_labels()
                cool_start = time.time()
                if i in benign_after:
                    # benign change in the middle of the cool-down
                    time.sleep(self.cooldown * self.rng.uniform(0.35, 0.55))
                    self.benign_deploy()
                    self.log("BENIGN deploy")
                time.sleep(max(0, self.cooldown - (time.time() - cool_start)))
        finally:
            self.restore_baseline()
            self.meta["end"] = time.time()
            (self.dir / "meta.json").write_text(json.dumps(self.meta, indent=2))
            self.write_labels()
            self.log("run finished")

    def write_labels(self):
        with (self.dir / "labels.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["run_id", "fault_id", "fault", "target", "start",
                                              "end", "root_cause"])
            w.writeheader()
            w.writerows(self.labels)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--warmup", type=int, default=480)
    ap.add_argument("--inject", type=int, default=120)
    ap.add_argument("--cooldown", type=int, default=210)
    a = ap.parse_args()
    if (DATA / a.run_id / "labels.csv").exists():
        raise SystemExit(f"run {a.run_id} already exists")
    Run(a.run_id, a.seed, a.warmup, a.inject, a.cooldown).execute()


if __name__ == "__main__":
    main()
