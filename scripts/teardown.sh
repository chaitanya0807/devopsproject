#!/usr/bin/env bash
# Stop everything but keep data (Prometheus/Loki/Grafana volumes, cluster state).
# Run scripts/setup.sh to bring it back.
cd "$(dirname "$0")/.."
docker compose stop
minikube stop
