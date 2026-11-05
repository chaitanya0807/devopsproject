# System Architecture and Workflow

## 1. Architecture

The platform is split across two runtime environments to fit an 8 GB-class laptop (5 GB RAM usable):

* **Kubernetes (minikube, docker driver)** runs the workload under observation plus the lightweight in-cluster collectors.
* **Docker Compose on the host** runs the storage, query and visualisation layer.

```mermaid
flowchart TB
  subgraph K8S["Kubernetes cluster: minikube (192.168.49.2)"]
    direction LR
    subgraph NSAPP["namespace: demo-app"]
      APP1["sample-app pod 1<br/>Flask · /metrics · JSON logs"]
      APP2["sample-app pod 2"]
      LG["loadgen<br/>(synthetic traffic)"]
      LG -->|HTTP| APP1 & APP2
    end
    subgraph NSMON["namespace: monitoring"]
      KSM["kube-state-metrics<br/>NodePort 30880"]
      CADV["cAdvisor DaemonSet<br/>NodePort 30881"]
      PT["Promtail DaemonSet"]
    end
    API["kube-apiserver :8443"]
    LOGS[("/var/log/pods")]
    APP1 & APP2 -.stdout.-> LOGS
    PT -->|tail| LOGS
  end

  subgraph HOST["Host: Docker Compose (~/monitoring)"]
    direction LR
    PROM["Prometheus :9090<br/>TSDB, 15 d retention"]
    LOKI["Loki :3100<br/>log store, 7 d retention"]
    GRAF["Grafana :3000<br/>System Monitoring dashboard"]
    NE["Node Exporter :9100"]
    COL["pipeline-collector :8000<br/>Python"]
    CSV[("pipelines.csv<br/>dataset")]
  end

  GL["GitLab REST API<br/>/projects/:id/pipelines"]

  PROM -->|"pod discovery + scrape<br/>via API proxy"| API
  API --> APP1 & APP2
  PROM -->|scrape| KSM & CADV & NE & COL
  PT -->|push| LOKI
  COL -->|poll| GL
  COL --> CSV
  GRAF -->|PromQL| PROM
  GRAF -->|LogQL| LOKI
```

### Components

| Layer | Component | Runs in | Purpose |
|---|---|---|---|
| Workload | sample-app (2 replicas) | minikube, `demo-app` | Flask order service. Exposes RED metrics (rate, errors, duration) and writes JSON logs. Injects 5 % errors. |
| Workload | loadgen | minikube, `demo-app` | Constant synthetic traffic (~2 req/s) |
| Metrics | Node Exporter | host | Host CPU, memory, disk, network |
| Metrics | cAdvisor | minikube DaemonSet | Per-container/pod CPU, memory, network |
| Metrics | kube-state-metrics | minikube (Helm) | Kubernetes object state: pods, deployments, restarts, nodes |
| Metrics | Prometheus | host | Scrapes and stores all metrics |
| Logs | Promtail | minikube DaemonSet (Helm) | Tails every pod log and adds k8s labels |
| Logs | Loki | host | Stores and indexes logs by label |
| CI/CD | pipeline-collector | host | Polls GitLab pipelines. Exposes `ci_pipeline_*` metrics and writes CSV. |
| Visualisation | Grafana | host | Dashboards over Prometheus + Loki |

### Network paths and ports

| From → To | Path | Why it's done this way |
|---|---|---|
| Prometheus → sample-app pods | `https://192.168.49.2:8443/api/v1/namespaces/demo-app/pods/<pod>:8080/proxy/metrics` | Pod IPs are not routable from the host. Scraping through the Service would mix counters of different pods. Uses a read-only ServiceAccount limited to `demo-app`. |
| Prometheus → kube-state-metrics / cAdvisor | NodePort 30880 / 30881 | Single-node cluster, so a NodePort reaches exactly one instance |
| Prometheus container → minikube network | Prometheus is attached to the `minikube` docker network | Docker isolates bridge networks from each other by default |
| Promtail → Loki | `http://host.minikube.internal:3100` | minikube's alias for the host gateway |

| Port | Service |
|---|---|
| 3000 | Grafana |
| 9090 | Prometheus |
| 3100 | Loki |
| 9100 | Node Exporter |
| 8000 | pipeline-collector |
| 30080 | sample-app (NodePort) |
| 30880 | kube-state-metrics (NodePort) |
| 30881 | cAdvisor (NodePort) |

![Architecture](architecture.png)

## 2. Workflow (data flow)

![Workflow](workflow-sequence.png)

```mermaid
sequenceDiagram
  autonumber
  participant LG as loadgen
  participant APP as sample-app
  participant PT as Promtail
  participant L as Loki
  participant P as Prometheus
  participant C as pipeline-collector
  participant GL as GitLab API
  participant G as Grafana
  participant U as Engineer

  loop every 0.5 s
    LG->>APP: GET /api/orders
    APP-->>APP: update counters/histograms, write JSON log line
  end
  loop continuously
    PT->>PT: tail /var/log/pods/*
    PT->>L: push log batch (namespace, pod, app labels)
  end
  loop every 15 s
    P->>APP: scrape /metrics (each pod via API proxy)
    P->>P: scrape node-exporter, cAdvisor, kube-state-metrics, collector
  end
  loop every 300 s
    C->>GL: GET /projects/:id/pipelines (+ details of finished runs)
    C->>C: update ci_pipeline_* gauges, append new rows to pipelines.csv
  end
  U->>G: open "System Monitoring"
  G->>P: PromQL (host, cluster, pod, app, CI panels)
  G->>L: LogQL (log rate, log lines)
  G-->>U: unified dashboard
```

## 3. Deployment workflow

```mermaid
flowchart LR
  A[scripts/setup.sh] --> B[minikube start + metrics-server]
  B --> C[build sample-app image in minikube]
  C --> D[kubectl apply app + cAdvisor + RBAC]
  D --> E[helm install kube-state-metrics, promtail]
  E --> F[export SA token + CA for Prometheus]
  F --> G[docker compose up -d]
  G --> H[scripts/verify.sh: 15 checks]
```
