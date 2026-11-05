"""Compare CAMSAC with the baselines on all exported runs (leave-one-run-out CV).

Methods
  B1  Prometheus static threshold alerts (real ALERTS from prometheus/rules/baseline.yml)
  B2  metric-only robust z-score detector
  B3  log-only Drain3 detector
  B4  Isolation Forest on the raw metric vector
  CAMSAC (full) and ablations: -change, -logs

Usage: python evaluate.py data/run01 data/run02 ... [--out results]
"""
import argparse
import json
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
from scipy.stats import wilcoxon
from sklearn.ensemble import IsolationForest

import camsac as cs

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

TOLERANCE_S = 120   # an alarm up to 2 min after the fault ends still counts as detection
MERGE_GAP = 4       # alarm windows less than 1 min apart form one episode
K = 2               # consecutive windows required for an alarm (all learned detectors)
B1_CLASS = {"HighErrorRate": "app", "HighLatencyP95": "app", "TrafficDrop": "app",
            "HighCPUUsage": "cpu", "HighMemoryUsage": "memory",
            "PodRestarting": "availability", "DeploymentReplicasMismatch": "availability"}
# Modelled repair: diagnosis 60 s if the top-1 suspect is right, else 900 s of manual triage;
# then the fix for the true root cause (rollback, restart, scale-up, ...).
T_FIX = {"change": 45, "app": 60, "cpu": 30, "memory": 30, "availability": 30, "dependency": 30}
T_DIAG_OK, T_DIAG_MISS = 60, 900


# ---------------------------------------------------------------- data
def load_run(path):
    p = Path(path)
    meta = json.loads((p / "meta.json").read_text())
    changes = [json.loads(x) for x in (p / "changes.jsonl").read_text().splitlines() if x] \
        if (p / "changes.jsonl").exists() else []
    logs = pd.read_json(p / "logs.jsonl", lines=True)
    run = {"id": meta["run_id"], "meta": meta, "changes": changes,
           "metrics": pd.read_csv(p / "metrics.csv"), "alerts": pd.read_csv(p / "alerts.csv"),
           "labels": pd.read_csv(p / "labels.csv"), "logs": logs}
    run["table"] = cs.signal_table(run)
    ts = run["table"].index.to_numpy(dtype=float)
    y = np.zeros(len(ts), dtype=int)
    for _, f in run["labels"].iterrows():
        y[(ts > f.end) & (ts <= f.end + TOLERANCE_S)] = -1
    for _, f in run["labels"].iterrows():
        y[(ts >= f.start) & (ts <= f.end)] = 1
    run["y"] = y
    run["eval"] = ts >= meta["start"] + meta["warmup_s"]  # score only after warm-up
    return run


# ---------------------------------------------------------------- event-level metrics
def episodes(alarm):
    idx = np.flatnonzero(alarm)
    if not len(idx):
        return []
    eps, s, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - prev > MERGE_GAP:
            eps.append((s, prev))
            s = i
        prev = i
    eps.append((s, prev))
    return eps


def score_events(run, alarm, attribution=None):
    """attribution(onset_index) -> ranked list of root-cause classes (optional)."""
    ts = run["table"].index.to_numpy(dtype=float)
    alarm = alarm & run["eval"]
    eps = episodes(alarm)
    faults = run["labels"]
    rows, matched = [], set()
    for _, f in faults.iterrows():
        lo, hi = f.start, f.end + TOLERANCE_S
        # only a NEW alarm counts: an episode that was already firing before the fault
        # (e.g. a still-active earlier alert) does not detect it
        hits = [e for e in eps if lo - cs.STEP <= ts[e[0]] <= hi]
        row = {"run": run["id"], "fault": f.fault, "detected": bool(hits), "mttd": np.nan,
               "top1": np.nan, "top3": np.nan, "mttr": np.nan}
        if hits:
            matched.update(hits)
            first = min(e[0] for e in hits)
            row["mttd"] = max(0.0, ts[first] - f.start)
            if attribution is not None:
                ranked = attribution(first)
                row["top1"] = ranked[0] == f.root_cause
                row["top3"] = f.root_cause in ranked[:3]
                diag = T_DIAG_OK if row["top1"] else T_DIAG_MISS
                row["mttr"] = row["mttd"] + diag + T_FIX[f.root_cause]
        rows.append(row)
    fp = len([e for e in eps if e not in matched])
    normal = (run["y"] == 0) & run["eval"]
    return pd.DataFrame(rows), {"tp_eps": len(matched), "fp_eps": fp,
                                "normal_h": normal.sum() * cs.STEP / 3600,
                                "normal_w": int(normal.sum()), "alarm_normal_w": int((alarm & normal).sum())}


def summarise(per_fault, counts):
    det = per_fault["detected"].sum()
    tp, fp = sum(c["tp_eps"] for c in counts), sum(c["fp_eps"] for c in counts)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = det / len(per_fault)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1,
            "fp_per_h": fp / normal_h if (normal_h := sum(c["normal_h"] for c in counts)) else np.nan,
            "fpr_window": sum(c["alarm_normal_w"] for c in counts) / max(1, sum(c["normal_w"] for c in counts)),
            "mttd_s": per_fault["mttd"].mean(), "mttr_s": per_fault["mttr"].mean(),
            "top1": per_fault["top1"].astype(float).mean(), "top3": per_fault["top3"].astype(float).mean()}


# ---------------------------------------------------------------- detectors
def tune(runs, alarm_fn, grid):
    """Pick the threshold with the best event-level F1 on the training runs."""
    best = None
    for tau in grid:
        res = [score_events(r, alarm_fn(r, tau)) for r in runs]
        s = summarise(pd.concat([x[0] for x in res]), [x[1] for x in res])
        key = (s["f1"], -s["fp_per_h"])
        if best is None or key > best[0]:
            best = (key, tau)
    return best[1]


def b1_alarm(run):
    firing = set(run["alerts"]["ts"].round().astype(int))
    ts = run["table"].index.to_numpy(dtype=float).round().astype(int)
    return np.array([t in firing for t in ts])


def b1_attribution(run):
    ts = run["table"].index.to_numpy(dtype=float)
    alerts = run["alerts"]

    def rank(onset):
        a = alerts[(alerts.ts >= ts[onset] - cs.STEP) & (alerts.ts <= ts[onset] + 60)]
        order = list(dict.fromkeys(B1_CLASS.get(n, "app") for n in a.sort_values("ts").alertname))
        return order + [c for c in T_FIX if c not in order]
    return rank


TAU_GRID = np.round(np.arange(0.1, 0.96, 0.05), 2)


def threshold_detector(column):
    return lambda run, tau: cs.consecutive((run["table"][column] > tau).to_numpy(), K)


def run_fold(train, test):
    out = {}
    # B1: fixed rules, nothing to train
    out["B1 Prometheus thresholds"] = (lambda r: b1_alarm(r), b1_attribution)

    for name, col in [("B2 metric-only z-score", "m_max"), ("B3 log-only Drain3", "l_max")]:
        det = threshold_detector(col)
        tau = tune(train, det, TAU_GRID)
        out[name] = (lambda r, d=det, t=tau: d(r, t), None)

    # B4: Isolation Forest on raw metrics, trained on normal windows of the training runs
    feats = list(cs.METRICS)
    Xtr = np.vstack([r["metrics"][feats].to_numpy()[(r["y"] == 0) & r["eval"]] for r in train])
    if len(Xtr) < 20:
        raise SystemExit("not enough normal (fault-free) windows in the training runs")
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
    iso = IsolationForest(n_estimators=200, random_state=0).fit((Xtr - mu) / sd)
    for r in train + test:
        r["iso"] = -iso.score_samples((r["metrics"][feats].to_numpy() - mu) / sd)
    norm_scores = np.concatenate([r["iso"][(r["y"] == 0) & r["eval"]] for r in train])
    iso_grid = np.percentile(norm_scores, [90, 95, 97.5, 99, 99.5, 99.9])
    iso_det = lambda r, tau: cs.consecutive(r["iso"] > tau, K)  # noqa: E731
    tau = tune(train, iso_det, iso_grid)
    out["B4 Isolation Forest"] = (lambda r, t=tau: iso_det(r, t), None)

    weights = {}
    for name, kw in [("CAMSAC (full)", {}), ("CAMSAC − change prior", {"use_change": False}),
                     ("CAMSAC − logs", {"use_logs": False})]:
        model = cs.Camsac(**kw).fit([r["table"] for r in train], [np.where(r["eval"], r["y"], -1)
                                                                  for r in train])
        for r in train + test:
            r[f"p:{name}"] = model.proba(r["table"])
        det = lambda r, tau, n=name: cs.consecutive(r[f"p:{n}"] > tau, K)  # noqa: E731
        tau = tune(train, det, TAU_GRID)
        attr = lambda r: (lambda onset: cs.attribute(r["table"], onset))  # noqa: E731
        out[name] = (lambda r, d=det, t=tau: d(r, t), attr)
        weights[name] = {**model.weights, "tau": float(tau)}

    results = {}
    for name, (alarm_fn, attr_fn) in out.items():
        res = [score_events(r, alarm_fn(r), attr_fn(r) if attr_fn else None) for r in test]
        results[name] = (pd.concat([x[0] for x in res]), [x[1] for x in res])
    return results, weights


# ---------------------------------------------------------------- reporting
def timeline_plot(run, path):
    t = run["table"]
    x = (t.index.to_numpy(dtype=float) - run["meta"]["start"]) / 60
    fig, axes = plt.subplots(4, 1, figsize=(13, 8), sharex=True)
    for _, f in run["labels"].iterrows():
        for ax in axes:
            ax.axvspan((f.start - run["meta"]["start"]) / 60, (f.end - run["meta"]["start"]) / 60,
                       color="#f4a582", alpha=0.35, lw=0)
        axes[0].text((f.start - run["meta"]["start"]) / 60, 1.02, f.fault, fontsize=7, rotation=20,
                     transform=axes[0].get_xaxis_transform())
    for ev in run["changes"]:
        for ax in axes:
            ax.axvline((ev["ts"] - run["meta"]["start"]) / 60, color="#2166ac", ls="--", lw=0.8)
    axes[0].plot(x, t["m_max"], lw=0.8, label="metric score (max)")
    axes[0].plot(x, t["l_max"], lw=0.8, label="log score (max)")
    axes[0].legend(loc="upper right", fontsize=8)
    axes[1].plot(x, t["change"], lw=0.8, color="#2166ac", label="change prior")
    axes[1].legend(loc="upper right", fontsize=8)
    p = run.get("p:CAMSAC (full)")
    if p is not None:
        axes[2].plot(x, p, lw=0.8, color="#1b7837", label="CAMSAC incident probability")
        axes[2].legend(loc="upper right", fontsize=8)
    axes[3].step(x, b1_alarm(run).astype(int), lw=0.8, color="#b2182b", label="B1 any alert firing")
    axes[3].legend(loc="upper right", fontsize=8)
    axes[3].set_xlabel("minutes since run start (shaded = injected fault, dashed = CD change)")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--out", default="results")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(exist_ok=True)
    runs = [load_run(p) for p in a.runs]
    print(f"loaded {len(runs)} runs, {sum(len(r['labels']) for r in runs)} faults")

    all_faults, all_counts, per_run_f1, fold_weights = {}, {}, {}, []
    for i, test in enumerate(runs):
        train = [r for j, r in enumerate(runs) if j != i] or [test]  # 1 run: train = test (smoke only)
        results, weights = run_fold(train, [test])
        fold_weights.append({"test_run": test["id"], **{k: v for k, v in weights.items()}})
        for name, (pf, counts) in results.items():
            all_faults.setdefault(name, []).append(pf)
            all_counts.setdefault(name, []).extend(counts)
            per_run_f1.setdefault(name, []).append(summarise(pf, counts)["f1"])
        timeline_plot(test, out / f"timeline_{test['id']}.png")

    summary = pd.DataFrame({n: summarise(pd.concat(all_faults[n]), all_counts[n])
                            for n in all_faults}).T
    summary["f1_std_over_runs"] = [np.std(per_run_f1[n]) for n in summary.index]
    per_fault = pd.concat([pd.concat(v).assign(method=k) for k, v in all_faults.items()])
    detect = per_fault.pivot_table(index="fault", columns="method", values="detected", aggfunc="mean")
    mttd = per_fault.pivot_table(index="fault", columns="method", values="mttd", aggfunc="mean")

    summary.to_csv(out / "summary.csv")
    per_fault.to_csv(out / "per_fault.csv", index=False)
    (out / "weights.json").write_text(json.dumps(fold_weights, indent=2, default=float))

    stat = ""
    if len(runs) >= 5:
        p = wilcoxon(per_run_f1["CAMSAC (full)"], per_run_f1["B1 Prometheus thresholds"]).pvalue
        stat = f"\nWilcoxon signed-rank on per-run F1, CAMSAC vs B1: p = {p:.4f}\n"
    else:
        stat = f"\nOnly {len(runs)} runs: significance testing needs >= 5 runs (planned).\n"

    fmt = summary.copy()
    for c in ["precision", "recall", "f1", "fpr_window", "top1", "top3", "f1_std_over_runs"]:
        fmt[c] = fmt[c].map(lambda v: "-" if pd.isna(v) else f"{v:.2f}")
    for c in ["fp_per_h", "mttd_s", "mttr_s"]:
        fmt[c] = fmt[c].map(lambda v: "-" if pd.isna(v) else f"{v:.1f}")
    md = ["# Initial comparative results", "",
          f"Runs: {', '.join(r['id'] for r in runs)} · faults: {len(per_fault) // len(all_faults)} · "
          "leave-one-run-out evaluation (thresholds/weights tuned only on the other runs)", "",
          fmt.to_markdown(), stat,
          "## Detection rate per fault type", "", detect.round(2).to_markdown(), "",
          "## Mean time to detect per fault type (s)", "", mttd.round(0).to_markdown(), "",
          "MTTR is modelled: MTTD + diagnosis (60 s if top-1 root cause correct, else 900 s) "
          "+ fix time for the true root cause."]
    (out / "summary.md").write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
