#!/usr/bin/env bash
# Bring up the whole observability project. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

step "Checking tools"
for t in docker kubectl minikube helm; do
  command -v "$t" >/dev/null || { echo "missing: $t"; exit 1; }
done

step "Starting minikube"
minikube status >/dev/null 2>&1 || minikube start --memory=2500 --cpus=2
minikube addons enable metrics-server >/dev/null
MINIKUBE_IP=$(minikube ip)
echo "minikube IP: $MINIKUBE_IP"

step "Building sample-app image inside minikube"
minikube image build -t sample-app:1.0 k8s/app >/dev/null

step "Deploying sample app"
kubectl apply -f k8s/app/sample-app.yaml

step "Deploying cluster monitoring agents"
kubectl apply -f k8s/monitoring/cadvisor.yaml
kubectl apply -f k8s/monitoring/prometheus-rbac.yaml
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo add grafana https://grafana.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update >/dev/null
helm upgrade --install kube-state-metrics prometheus-community/kube-state-metrics \
  -n monitoring -f k8s/monitoring/kube-state-metrics-values.yaml >/dev/null
helm upgrade --install promtail grafana/promtail \
  -n monitoring -f k8s/monitoring/promtail-values.yaml >/dev/null

step "Exporting Prometheus API credentials"
mkdir -p prometheus/k8s
for i in $(seq 1 20); do
  TOKEN=$(kubectl -n demo-app get secret prometheus-external-token -o jsonpath='{.data.token}' 2>/dev/null || true)
  [ -n "$TOKEN" ] && break; sleep 1
done
echo "$TOKEN" | base64 -d > prometheus/k8s/token
kubectl -n demo-app get secret prometheus-external-token -o jsonpath='{.data.ca\.crt}' | base64 -d > prometheus/k8s/ca.crt
chmod 644 prometheus/k8s/token prometheus/k8s/ca.crt   # Prometheus runs as user "nobody"

step "Pointing Prometheus at minikube ($MINIKUBE_IP)"
sed -i -E "s/[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+:(30880|30881|8443)/$MINIKUBE_IP:\1/g" prometheus/prometheus.yml

step "Waiting for pods"
kubectl -n demo-app rollout status deploy/sample-app --timeout=180s
kubectl -n monitoring rollout status ds/cadvisor --timeout=300s
kubectl -n monitoring rollout status deploy/kube-state-metrics --timeout=180s
kubectl -n monitoring rollout status ds/promtail --timeout=180s

step "Starting host stack (Prometheus, Loki, Grafana, collector)"
docker compose up -d --build
curl -s -X POST localhost:9090/-/reload >/dev/null || true

cat <<EOF

Done.
  Grafana     http://localhost:3000   (dashboard: "System Monitoring")
  Prometheus  http://localhost:9090/targets
  Loki        http://localhost:3100/ready
  Collector   http://localhost:8000/metrics
  Sample app  http://$MINIKUBE_IP:30080/api/orders

Run scripts/verify.sh to check everything.
EOF
