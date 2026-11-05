"""CAMSAC: Change-Aware Multi-Signal Anomaly Correlation.

Pipeline (one 15 s window at a time, using only past data, so it can run online):
  1. metric scorer  - robust z-score of each service metric against a trailing
                      median/MAD baseline -> anomaly score in [0, 1]
  2. log scorer     - Drain3 log templates; scores error-line ratio, rare/new
                      templates and the Jensen-Shannon shift of the template mix
  3. change prior   - exponential decay after each CD change event
  4. fusion         - logistic model over cross-signal agreement features
                      (metric x log, change x anomaly, number of agreeing metrics)
  5. alarm          - probability above tau for K consecutive windows
  6. attribution    - ranks root-cause classes by score and onset order
"""
import json
import re

import numpy as np
import pandas as pd
from drain3 import TemplateMiner
from drain3.masking import MaskingInstruction
from drain3.template_miner_config import TemplateMinerConfig
from sklearn.linear_model import LogisticRegression

STEP = 15
BASELINE_WIN = 80        # 20 min trailing baseline
BASELINE_GAP = 4         # skip the most recent 1 min so the baseline doesn't absorb an anomaly at once
MIN_HISTORY = 20         # 5 min of history before scoring
CHANGE_DECAY_S = 180     # change prior half-life scale (lambda)

# metric: (direction, absolute floor for the spread). direction +1 = only increases are bad,
# -1 = only decreases, 0 = both.
METRICS = {
    "rps": (0, 0.3),
    "err_ratio": (1, 0.01),
    "p95": (1, 0.03),
    "cpu_max": (1, 0.02),
    "mem_max": (1, 4e6),
    "restarts": (1, 0.25),
    "unavailable": (1, 0.25),
    "payments_rps": (-1, 0.3),
}
GROUPS = {  # root-cause classes used for attribution
    "app": ["err_ratio", "p95", "rps"],
    "cpu": ["cpu_max"],
    "memory": ["mem_max"],
    "availability": ["restarts", "unavailable"],
    "dependency": ["payments_rps"],
}
CAUSE_GROUPS = {"cpu", "memory", "availability", "dependency"}


def squash(z, k=2.5, scale=2.0):
    """Map a robust z-score to [0, 1]: 0 below k, approaching 1 as z grows."""
    return 1 - np.exp(-np.clip(z - k, 0, None) / scale)


def robust_z(x: pd.Series, direction: int, floor: float) -> pd.Series:
    hist = x.shift(BASELINE_GAP).rolling(BASELINE_WIN, min_periods=MIN_HISTORY)
    med = hist.median()
    mad = (x.shift(BASELINE_GAP) - med).abs().rolling(BASELINE_WIN, min_periods=MIN_HISTORY).median()
    z = (x - med) / np.maximum(1.4826 * mad, floor)
    if direction > 0:
        return z.clip(lower=0)
    if direction < 0:
        return (-z).clip(lower=0)
    return z.abs()


def metric_scores(metrics: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=metrics.index)
    for name, (direction, floor) in METRICS.items():
        out[name] = squash(robust_z(metrics[name], direction, floor)).fillna(0)
    return out


# ---------------------------------------------------------------- logs
GUNICORN = re.compile(r"^\[[^\]]+\] \[\d+\] ")


def normalise(line: str) -> tuple[str, bool]:
    """Turn a raw log line into text for Drain3 plus an is-error flag."""
    try:
        rec = json.loads(line)
        level = rec.get("level", "info")
        text = f"{level} {rec.get('role', '')} {rec.get('method', '')} {rec.get('path', '')} " \
               f"{rec.get('status', '')} {rec.get('msg', '')}"
    except (json.JSONDecodeError, AttributeError):
        text = GUNICORN.sub("", line)
        level = "error" if re.search(r"\[(ERROR|CRITICAL)\]|Traceback|Error", line) else "info"
    return text, level in ("error", "fatal", "critical")


def make_miner() -> TemplateMiner:
    cfg = TemplateMinerConfig()
    cfg.drain_sim_th = 0.5
    cfg.drain_depth = 4
    cfg.masking_instructions = [
        MaskingInstruction(r"\b[0-9a-f]{8,}\b", "HEX"),
        MaskingInstruction(r"sample-app-[\w-]+|payments-[\w-]+", "POD"),
        MaskingInstruction(r"\d+(\.\d+)?", "NUM"),
    ]
    cfg.profiling_enabled = False
    return TemplateMiner(config=cfg)


def log_windows(logs: pd.DataFrame, index: pd.Index) -> pd.DataFrame:
    """Per-window template counts + error counts (templates mined online, in time order)."""
    miner = make_miner()
    t0 = index[0]
    rows = []
    for ts, line in zip(logs["ts"], logs["line"]):
        text, is_err = normalise(line)
        cid = miner.add_log_message(text)["cluster_id"]
        rows.append((int((ts - t0) // STEP), cid, is_err))
    df = pd.DataFrame(rows, columns=["w", "tpl", "err"])
    counts = df.groupby(["w", "tpl"]).size().unstack(fill_value=0)
    counts = counts.reindex(range(len(index)), fill_value=0)
    errs = df.groupby("w")["err"].sum().reindex(range(len(index)), fill_value=0)
    counts.index = errs.index = index
    return counts, errs


def js_divergence(p, q):
    p, q = p / p.sum(), q / q.sum()
    m = (p + q) / 2
    with np.errstate(divide="ignore", invalid="ignore"):
        kl = lambda a, b: np.nansum(np.where(a > 0, a * np.log2(a / b), 0))  # noqa: E731
    return 0.5 * kl(p, m) + 0.5 * kl(q, m)


def log_scores(logs: pd.DataFrame, index: pd.Index) -> pd.DataFrame:
    counts, errs = log_windows(logs, index)
    totals = counts.sum(axis=1)
    c = counts.to_numpy(dtype=float)
    hist = np.zeros_like(c)  # trailing baseline template counts (same window rule as metrics)
    cum = np.vstack([np.zeros(c.shape[1]), np.cumsum(c, axis=0)])
    for i in range(len(c)):
        hi, lo = max(0, i - BASELINE_GAP), max(0, i - BASELINE_GAP - BASELINE_WIN)
        hist[i] = cum[hi] - cum[lo]

    rare, js = np.zeros(len(c)), np.zeros(len(c))
    for i in range(len(c)):
        if totals.iloc[i] == 0 or hist[i].sum() == 0:
            continue
        base_p = hist[i] / hist[i].sum()
        rare_mask = base_p < 0.002  # templates (almost) never seen in the baseline
        rare_lines = c[i][rare_mask].sum()
        rare[i] = rare_lines / totals.iloc[i] if rare_lines >= 2 else 0
        js[i] = js_divergence(c[i] + 0.1, hist[i] + 0.1)

    err_ratio = (errs / totals.replace(0, np.nan)).fillna(0)
    out = pd.DataFrame(index=index)
    out["log_err"] = squash(robust_z(err_ratio, 1, 0.02)).fillna(0)
    out["log_rare"] = np.clip(rare * 4, 0, 1)
    out["log_js"] = squash(robust_z(pd.Series(js, index=index), 1, 0.02)).fillna(0)
    # A log window with no lines at all while traffic is expected is also suspicious, but the
    # metric side already covers traffic drops, so it is not double counted here.
    return out


# ---------------------------------------------------------------- change prior
def change_prior(changes: list[dict], index: pd.Index) -> pd.Series:
    prior = np.zeros(len(index))
    t = index.to_numpy(dtype=float)
    for ev in changes:
        dt = t - ev["ts"]
        prior = np.maximum(prior, np.where(dt >= 0, np.exp(-dt / CHANGE_DECAY_S), 0))
    return pd.Series(prior, index=index)


# ---------------------------------------------------------------- fusion
def signal_table(run) -> pd.DataFrame:
    """All per-window scores for one run (run = experiments.data loader dict)."""
    idx = pd.Index(run["metrics"]["ts"].to_numpy(dtype=float), name="ts")
    m = metric_scores(run["metrics"].set_index(idx))
    lg = log_scores(run["logs"], idx)
    tbl = pd.concat([m, lg], axis=1)
    tbl["change"] = change_prior(run["changes"], idx)
    tbl["m_max"] = m[list(METRICS)].max(axis=1)
    tbl["m_count"] = (m[list(METRICS)] > 0.5).sum(axis=1)
    tbl["l_max"] = lg.max(axis=1)
    return tbl


def fusion_features(tbl: pd.DataFrame, use_logs=True, use_change=True) -> np.ndarray:
    m, l, c = tbl["m_max"], tbl["l_max"] * use_logs, tbl["change"] * use_change
    agree = np.minimum(tbl["m_count"], 3) / 3
    feats = [m, agree, l, m * l, c * np.maximum(m, l), c * l, c]
    return np.column_stack(feats)


class Camsac:
    def __init__(self, use_logs=True, use_change=True, k=2):
        self.use_logs, self.use_change, self.k = use_logs, use_change, k
        self.model = LogisticRegression(class_weight="balanced", C=1.0, max_iter=1000)
        self.tau = 0.5

    def fit(self, tables, labels):
        X = np.vstack([fusion_features(t, self.use_logs, self.use_change) for t in tables])
        y = np.concatenate(labels)
        keep = y >= 0  # -1 = tolerance zone right after a fault, excluded from training
        self.model.fit(X[keep], y[keep])
        return self

    def proba(self, tbl):
        return self.model.predict_proba(fusion_features(tbl, self.use_logs, self.use_change))[:, 1]

    @property
    def weights(self):
        names = ["metric", "agreement", "log", "metric*log", "change*anomaly", "change*log", "change"]
        return dict(zip(names, np.round(self.model.coef_[0], 3)))


def consecutive(flags: np.ndarray, k: int) -> np.ndarray:
    """True where the last k windows are all True (alarm debouncing)."""
    if k <= 1:
        return flags.astype(bool)
    run = np.zeros(len(flags), dtype=int)
    for i, f in enumerate(flags):
        run[i] = run[i - 1] + 1 if f and i else int(f)
    return run >= k


# ---------------------------------------------------------------- attribution
def attribute(tbl: pd.DataFrame, onset: int, horizon: int = 8) -> list[str]:
    """Rank root-cause classes for an alarm episode starting at window index `onset`.

    Score = mean group score over [onset-4, onset+horizon]; a cause group whose score
    crosses 0.5 no later than the first symptom gets a precedence bonus; a recent change
    with anomalous signals makes "change" the leading hypothesis.
    """
    lo, hi = max(0, onset - 4), min(len(tbl), onset + horizon)
    win = tbl.iloc[lo:hi]
    scores, first = {}, {}
    for g, cols in GROUPS.items():
        s = win[cols].max(axis=1)
        scores[g] = float(s.mean())
        above = np.flatnonzero(s.to_numpy() > 0.5)
        first[g] = above[0] if len(above) else np.inf
    symptom_onset = first["app"]
    for g in CAUSE_GROUPS:
        if first[g] <= symptom_onset and np.isfinite(first[g]):
            scores[g] += 0.5
    anomaly = win[["m_max", "l_max"]].max(axis=1)
    scores["change"] = float((win["change"] * anomaly).mean()) * 2
    return sorted(scores, key=scores.get, reverse=True)
