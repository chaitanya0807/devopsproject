#!/usr/bin/env bash
# Health checks for every component. Output is also saved to evidence/verify-output.txt
cd "$(dirname "$0")/.."
OUT=evidence/verify-output.txt
mkdir -p evidence
PASS=0; FAIL=0

check() {  # check "<test id> <description>" <command...>
  local name=$1; shift
  if out=$("$@" 2>&1); then
    printf '  [PASS] %-48s %s\n' "$name" "$out"; PASS=$((PASS + 1))
  else
    printf '  [FAIL] %-48s %s\n' "$name" "$out"; FAIL=$((FAIL + 1))
  fi
}

pods_running() {  # all pods in namespace $1 Running and ready
  local bad
  bad=$(kubectl get pods -n "$1" --no-headers | awk '$3!="Running" || split($2,a,"/") && a[1]!=a[2]')
  [ -z "$bad" ] && echo "$(kubectl get pods -n "$1" --no-headers | wc -l) pods running" || { echo "$bad"; return 1; }
}

targets_up() {
  curl -s localhost:9090/api/v1/targets | python3 -c '
import json,sys
t=json.load(sys.stdin)["data"]["activeTargets"]
down=[x["labels"]["job"] for x in t if x["health"]!="up"]
print(f"{len(t)-len(down)}/{len(t)} targets up", ("down: "+",".join(down)) if down else "")
sys.exit(1 if down or not t else 0)'
}

prom_has() {  # metric exists in Prometheus
  curl -sG localhost:9090/api/v1/query --data-urlencode "query=$1" | python3 -c '
import json,sys; r=json.load(sys.stdin)["data"]["result"]; print(f"{len(r)} series"); sys.exit(0 if r else 1)'
}

loki_has_logs() {
  local start=$(( $(date +%s) - 600 ))000000000
  curl -sG localhost:3100/loki/api/v1/query_range --data-urlencode 'query={namespace="demo-app",app="sample-app"}' \
    --data-urlencode "start=$start" --data-urlencode limit=100 | python3 -c '
import json,sys; r=json.load(sys.stdin)["data"]["result"]; n=sum(len(s["values"]) for s in r)
print(f"{n} log lines in last 10m"); sys.exit(0 if n else 1)'
}

http_ok() { curl -sf -m 5 -o /dev/null -w '%{http_code}' "$1"; }

{
echo "Verification run: $(date '+%Y-%m-%d %H:%M:%S')"
echo
echo "Kubernetes"
check "T01 minikube node Ready"            kubectl get nodes --no-headers -o custom-columns=S:.status.conditions[-1].type
check "T02 demo-app pods running"          pods_running demo-app
check "T03 monitoring pods running"        pods_running monitoring
check "T04 sample-app reachable (NodePort)" http_ok "http://$(minikube ip):30080/api/orders"
echo
echo "Metrics (Prometheus)"
check "T05 all scrape targets up"           targets_up
check "T06 node-exporter metrics"           prom_has node_load1
check "T07 kube-state-metrics"              prom_has kube_pod_status_phase
check "T08 cAdvisor per-pod metrics"        prom_has 'container_memory_working_set_bytes{namespace="demo-app"}'
check "T09 sample-app metrics per pod"      prom_has 'sum by (pod) (app_http_requests_total)'
check "T10 CI pipeline metrics"             prom_has ci_pipeline_total
echo
echo "Logs (Loki)"
check "T11 Loki ready"                      http_ok localhost:3100/ready
check "T12 sample-app logs in Loki"         loki_has_logs
echo
echo "Visualisation and CI collector"
check "T13 Grafana healthy"                 http_ok localhost:3000/api/health
check "T14 collector /metrics"              http_ok localhost:8000/metrics
check "T15 pipeline CSV dataset written"    bash -c 'f=pipeline-collector/data/pipelines.csv; [ -s $f ] && echo "$(($(wc -l <$f)-1)) rows"'
echo
echo "Result: $PASS passed, $FAIL failed"
} | tee "$OUT"
[ "$FAIL" -eq 0 ]
