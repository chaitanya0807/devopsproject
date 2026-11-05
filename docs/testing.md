# Initial Testing and Implementation Evidence

Run all automated checks with:

```bash
./scripts/verify.sh        # also writes evidence/verify-output.txt
```

## Test cases

Latest run: `evidence/verify-output.txt` (2026-11-05, **15/15 passed**).

| ID | Area | Test | How | Expected | Result |
|---|---|---|---|---|---|
| T01 | Kubernetes | minikube node is Ready | `kubectl get nodes` | `Ready` | PASS |
| T02 | Kubernetes | demo-app pods running | `kubectl get pods -n demo-app` | 2× sample-app + loadgen Running | PASS (3 pods) |
| T03 | Kubernetes | monitoring agents running | `kubectl get pods -n monitoring` | cAdvisor, kube-state-metrics, Promtail Running | PASS (3 pods) |
| T04 | Kubernetes | app reachable through NodePort | `curl http://$(minikube ip):30080/api/orders` | HTTP 200 | PASS |
| T05 | Prometheus | every scrape target healthy | `/api/v1/targets` | all `up` | PASS (7/7) |
| T06 | Prometheus | Node Exporter data | query `node_load1` | ≥1 series | PASS |
| T07 | Prometheus | kube-state-metrics data | query `kube_pod_status_phase` | ≥1 series | PASS (85) |
| T08 | Prometheus | cAdvisor per-pod data | `container_memory_working_set_bytes{namespace="demo-app"}` | series per demo-app pod | PASS |
| T09 | Prometheus | app metrics scraped **per pod** | `sum by (pod)(app_http_requests_total)` | 2 series (one per replica) | PASS (2) |
| T10 | Prometheus | CI pipeline metrics | query `ci_pipeline_total` | series per status | PASS (4) |
| T11 | Loki | Loki ready | `GET :3100/ready` | 200 | PASS |
| T12 | Loki | app logs ingested | LogQL `{namespace="demo-app",app="sample-app"}` | log lines in last 10 min | PASS |
| T13 | Grafana | Grafana healthy | `GET :3000/api/health` | 200 | PASS |
| T14 | Collector | exporter endpoint | `GET :8000/metrics` | 200 | PASS |
| T15 | Collector | dataset written | `pipeline-collector/data/pipelines.csv` | >0 rows | PASS (44 rows) |
| T16 | Dashboard | every panel query returns data | each PromQL/LogQL expression in `system-monitoring.json` | non-empty result | PASS (36/36 metric queries; log panel checked via T12) |

### Manual / functional tests

| ID | Test | Steps | Expected |
|---|---|---|---|
| F01 | Error injection shows on the dashboard | `kubectl -n demo-app set env deploy/sample-app ERROR_RATE=0.5` | Error-rate stat turns red within ~1 min, and 5xx lines appear in the logs panel. Revert with `ERROR_RATE=0.05`. |
| F02 | Pod failure is detected | `kubectl -n demo-app delete pod -l app=sample-app --wait=false` | Running pods dip, then recover. The new pod appears as a new `sample-app` target. |
| F03 | Scale-out is detected | `kubectl -n demo-app scale deploy/sample-app --replicas=3` | 3 targets, and the "Requests by pod" panel shows 3 lines |
| F04 | Collector works standalone | `docker compose run --rm pipeline-collector python collector.py --once` | Summary table (see `evidence/collector-once.txt`) |

## Evidence files

| File | Content |
|---|---|
| `evidence/verify-output.txt` | Automated test run (T01–T15) |
| `evidence/collector-once.txt` | Pipeline collector run: 50 pipelines, success ratio |
| `evidence/cluster-state.txt` | `kubectl get pods/svc -A`, `helm list`, `docker compose ps` |
| `pipeline-collector/data/pipelines.csv` | Collected CI/CD dataset |

## Screenshot checklist (`evidence/screenshots/`)

| # | File name | What to capture | URL / command |
|---|---|---|---|
| 1 | `01-kubectl-pods.png` | All pods Running | `kubectl get pods -A` |
| 2 | `02-prometheus-targets.png` | Every target green/UP | http://localhost:9090/targets |
| 3 | `03-grafana-host-cluster.png` | Dashboard top: Host + Kubernetes rows | http://localhost:3000 → Dashboards → M.Tech Project → System Monitoring |
| 4 | `04-grafana-pods-app.png` | cAdvisor + sample-app rows | same dashboard, scroll |
| 5 | `05-grafana-cicd-logs.png` | CI/CD + Logs rows | same dashboard, scroll |
| 6 | `06-loki-explore.png` | Explore → Loki → `{namespace="demo-app"} \|= "error"` | http://localhost:3000/explore |
| 7 | `07-collector-output.png` | Terminal running `collector.py --once` | see F04 |
| 8 | `08-verify-script.png` | Terminal running `./scripts/verify.sh` (15/15) | |
| 9 | `09-error-injection.png` | Error-rate panel spiking during F01 | |
| 10 | `10-helm-compose.png` | `helm list -A` and `docker compose ps` | |
