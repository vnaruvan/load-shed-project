# Load Shed API

A Kubernetes-deployed demonstration service showing how to keep APIs responsive when upstream dependencies become slow or unreliable using **timeouts**, **circuit breakers**, **load shedding**, and **observability**.

**Stack:** FastAPI · Docker · Kubernetes (Kind) · Prometheus · Grafana · kube-prometheus-stack · HPA

---

## 60-Second Overview

| Aspect | Details |
|--------|---------|
| **What it is** | FastAPI service with `/client` endpoint that calls upstream using `httpx` with timeout |
| **What it demonstrates** | Timeouts, circuit breaker, load shedding (429), HPA autoscaling, Prometheus metrics, Grafana dashboards |
| **How to prove it works** | Run single in-cluster demo command, validate breaker state and metrics in Prometheus/Grafana |

---

## Quick Start

**Prerequisites:** Docker, Kind, kubectl, Helm, Terraform ≥1.5 (optional)

```bash
# 1. Create cluster
kind create cluster --name load-shed

# 2. Install monitoring stack
# Option A: Using Helm directly
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

kubectl apply -f deploy/k8s/namespace.yaml
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -

helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  -n monitoring \
  --set grafana.sidecar.dashboards.enabled=true \
  --set grafana.sidecar.dashboards.label=grafana_dashboard \
  --set grafana.defaultDashboardsEnabled=true

# Option B: Using Terraform (optional)
cd infra/terraform
terraform init
terraform apply -auto-approve
cd ../..

# 3. Build and load image
docker build -t load-shed-api:local .
kind load docker-image load-shed-api:local --name load-shed

# 4. Deploy application
kubectl apply -f deploy/k8s
kubectl -n load-shed rollout status deploy/load-shed-api

# 5. Port-forward services
kubectl -n load-shed port-forward svc/load-shed-api 8080:80 &
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090 &
kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80 &
```

**Grafana credentials:**
```bash
# Username: admin
# Password:
kubectl -n monitoring get secret kube-prometheus-stack-grafana \
  -o jsonpath="{.data.admin-password}" | base64 -d; echo
```

---

## Architecture

### Components

| Component | Purpose |
|-----------|---------|
| **load-shed-api** | FastAPI service with reliability controls + metrics |
| **Prometheus** | Scrapes `/metrics` via ServiceMonitor |
| **Grafana** | Visualizes dashboards from ConfigMaps |
| **metrics-server + HPA** | Autoscales load-shed-api based on CPU |
| **kube-prometheus-stack** | Helm-deployed monitoring stack |

### Data Flow

```
Load Generator
    ↓
load-shed-api Service (ClusterIP:80)
    ↓
FastAPI Pods
  /client → httpx (timeout) → Upstream (UPSTREAM_BASE_URL/upstream)
    ↓
  /metrics → Prometheus → Grafana Dashboards
```

---

## Reliability Controls

### Timeout
**Mitigates:** Tail latency blowups, cascading failures  
**Signal:** `upstream_requests_total{result="timeout"}` increases, `/client` p95 rises

### Circuit Breaker
**Mitigates:** Cascading failures, wasted work on broken dependencies  
**Signal:** `circuit_breaker_state=1`, `upstream_requests_total{result="breaker_open"}` increases

### Load Shedding (429)
**Mitigates:** Resource saturation, tail latency blowups  
**Signal:** `http_requests_total{path="/client",status="429"}` increases

### HPA (CPU-based)
**Mitigates:** CPU saturation by adding replicas  
**Signal:** `kubectl -n load-shed get hpa` shows scaling activity

---

## Failure Modes Explained

| Failure Mode | Description |
|--------------|-------------|
| **Retry storms** | Retries during failures multiply traffic and overwhelm dependencies |
| **Cascading failure** | Slow upstream ties up workers/sockets, degrading healthy endpoints |
| **Tail latency blowups** | Under load, queuing dominates—p95/p99 spikes even when averages look fine |
| **Resource saturation** | CPU/connection pools max out, throughput collapses, pods churn |
| **Load shedding vs backpressure** | Shedding (429) rejects work quickly; backpressure slows producers. Shedding is safer when queuing would amplify tail latency |

---

## Observability

### Key Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `http_requests_total` | Counter | Total HTTP requests (by method/path/status) |
| `http_request_duration_seconds_bucket` | Histogram | Request latency distribution |
| `upstream_requests_total` | Counter | Upstream outcomes (ok, error, timeout, breaker_open) |
| `upstream_request_duration_seconds_bucket` | Histogram | Upstream latency distribution |
| `circuit_breaker_state` | Gauge | Breaker state (0=closed, 1=open) |

### Key Dashboard Panels

**Request rate per pod:**
```promql
sum by (pod) (rate(http_requests_total{job="load-shed-api",path="/client"}[$__rate_interval]))
```

**p95 latency per pod:**
```promql
histogram_quantile(0.95,
  sum by (pod, le) (rate(http_request_duration_seconds_bucket{job="load-shed-api",path="/client"}[$__rate_interval]))
)
```

**Breaker state:**
```promql
max(circuit_breaker_state{job="load-shed-api"})
```

**Upstream results breakdown:**
```promql
sum by (result) (rate(upstream_requests_total{job="load-shed-api"}[$__rate_interval]))
```

**Load shed rate (429s):**
```promql
sum(rate(http_requests_total{job="load-shed-api",path="/client",status="429"}[$__rate_interval]))
```

**Why these panels:**
- **Request rate** - Confirms traffic and distribution across pods
- **p95 latency** - Shows tail latency protection (or failure)
- **Breaker state** - Proves open/close transitions work
- **Upstream results** - Proves short-circuiting vs real upstream calls
- **429 rate** - Proves shed behavior activates under saturation

---

## Demo (Under 2 Minutes)

### One-Command Demo

Run inside the cluster (no port-forward dependency):

```bash
kubectl -n load-shed run demo --rm -it --restart=Never --image=curlimages/curl -- sh -c '
SVC="http://load-shed-api/client";

echo "A) Baseline traffic";
seq 1 200 | xargs -n1 -P10 sh -c "curl -sS -o /dev/null \"$0\" || true" "$SVC";

echo "B) Trip breaker (fail_rate=1.0)";
seq 1 800 | xargs -n1 -P20 sh -c "curl -sS -o /dev/null \"$0?fail_rate=1.0\" || true" "$SVC";

echo "C) Recovery (fail_rate=0.0)";
seq 1 400 | xargs -n1 -P10 sh -c "curl -sS -o /dev/null \"$0?fail_rate=0.0\" || true" "$SVC";

echo "Done"
'
```

### Validate with Prometheus

**Breaker state:**
```promql
max(circuit_breaker_state{job="load-shed-api"})
```

**Upstream totals by result:**
```promql
sum by (result) (upstream_requests_total{job="load-shed-api"})
```

**Upstream result rate:**
```promql
sum by (result) (rate(upstream_requests_total{job="load-shed-api"}[2m]))
```

**Expected behavior:**
- **During trip phase:** Breaker goes to 1, `breaker_open` count increases
- **During recovery:** Breaker returns to 0, `ok` results increase again

---

## Alerting

Sample `PrometheusRule` included at `deploy/k8s/prometheus-rule-load-shed.yaml`

**Alert conditions:**
- Breaker open for sustained period
- Upstream p95 above threshold
- Elevated 5xx or 429 rates on `/client`

**Rationale:** These are user-visible degradations that warrant immediate attention.

---

## Troubleshooting

### Dashboard Missing in Grafana

**Check ConfigMap and labels:**
```bash
kubectl -n monitoring get cm | grep -i dashboard
kubectl -n monitoring get cm load-shed-dashboard -o yaml | head
```

**Fix:** Verify ConfigMap has label matching Grafana sidecar config (`grafana_dashboard`)

### Prometheus Not Scraping

**Check ServiceMonitor:**
```bash
kubectl -n load-shed get servicemonitor
kubectl -n load-shed describe servicemonitor load-shed-api
```

**Fix:** Ensure label selectors and namespace match Prometheus configuration

### HPA Shows `<unknown>`

**Check metrics-server:**
```bash
kubectl get apiservice v1beta1.metrics.k8s.io -o wide
kubectl -n kube-system get pods -l k8s-app=metrics-server
```

**Fix:** Install or restart metrics-server if unhealthy

### Kind Image Not Updating

**Reload image:**
```bash
docker build -t load-shed-api:local .
kind load docker-image load-shed-api:local --name load-shed
kubectl -n load-shed rollout restart deploy/load-shed-api
```

### Port-Forward Fails

**Check port usage and endpoints:**
```bash
ss -ltnp | grep ':8080' || true
kubectl -n load-shed get endpointslices -l kubernetes.io/service-name=load-shed-api -o wide
```

**Fix:** Kill process using port or verify Service has healthy endpoints

---

## Repository Structure

```
.
├── app/
│   ├── main.py              # FastAPI service, breaker logic, metrics
│   └── requirements.txt     # Python dependencies
├── deploy/
│   └── k8s/
│       ├── deployment.yaml
│       ├── service.yaml
│       ├── servicemonitor.yaml
│       ├── grafana-dashboard-configmap.yaml
│       ├── prometheus-rule-load-shed.yaml
│       └── hpa.yaml
├── scripts/                 # Helper demo scripts
├── dashboards/              # Grafana JSON sources
└── Dockerfile
```
