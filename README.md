# Load Shed API

A Kubernetes demonstration API showing how services behave under unreliable dependencies using **timeouts**, **circuit breakers**, **load shedding (429)**, **HPA autoscaling**, and **Prometheus/Grafana observability**.

**Stack:** FastAPI · Docker · Kubernetes (Kind) · Prometheus · Grafana · kube-prometheus-stack · HPA (metrics-server)

---

## 60-Second Overview

| Feature | Description |
|---------|-------------|
| **Client endpoint** | `/client` calls upstream via `httpx` with hard timeout |
| **Circuit breaker** | Opens on sustained failures, short-circuits calls |
| **Load shedding** | Returns **429** when queue depth too high (fail fast) |
| **Observability** | Prometheus scrapes `/metrics`, Grafana shows p95 latency, breaker state, outcomes |
| **Autoscaling** | HPA scales `load-shed-api` based on CPU |

---

## Quick Start

**Prerequisites:** Docker, Kind, kubectl, Helm

### One-Command Setup

```bash
./scripts/start.sh
```

This script:
- Creates Kind cluster
- Installs kube-prometheus-stack
- Builds and loads API image
- Applies all manifests (including dashboard ConfigMap)
- Starts port-forwards in background

**Access URLs:**
- API: http://localhost:8080
- Grafana: http://localhost:3000
- Prometheus: http://localhost:9090

**Grafana credentials:**
```bash
# Username: admin
# Password:
kubectl -n monitoring get secret kube-prometheus-stack-grafana \
  -o jsonpath="{.data.admin-password}" | base64 -d; echo
```

**Stop services:**
```bash
./scripts/stop.sh
```

---

## Architecture

### Components

| Component | Purpose |
|-----------|---------|
| **load-shed-api** | FastAPI service with `/client`, `/metrics`, `/healthz` |
| **load-shed-upstream** | Simulated dependency with configurable behavior |
| **Prometheus** | Scrapes metrics via ServiceMonitor |
| **Grafana** | Dashboards provisioned via ConfigMap sidecar |
| **HPA + metrics-server** | Scales pods based on CPU utilization |

### Data Flow

```
Load Generator
    ↓
load-shed-api (/client)
    ↓
httpx + timeout
    ↓
load-shed-upstream (/upstream)
    ↓
/metrics → Prometheus → Grafana
```

Circuit breaker can block upstream calls when open.

---

## Reliability Controls

### 1. Timeout
**What:** Hard `httpx` timeout for upstream calls  
**Mitigates:** Tail latency blowups, cascading failures, resource saturation  
**Observable signal:** `upstream_requests_total{result="timeout"}` rises, `/client` p95 increases

### 2. Circuit Breaker
**What:** Opens after sustained upstream failures, short-circuits to fail fast  
**Mitigates:** Cascading failures, wasted work against failing dependencies  
**Observable signal:** `circuit_breaker_state=1`, `upstream_requests_total{result="breaker_open"}` increases

### 3. Load Shedding (429)
**What:** Rejects requests when internal queue depth too high  
**Mitigates:** Resource saturation, tail latency blowups  
**Observable signal:** `/client` returns 429, `http_requests_total{path="/client",status="429"}` increases

### 4. HPA Autoscaling
**What:** Scales `load-shed-api` based on CPU utilization  
**Mitigates:** Saturation under rising load  
**Observable signal:** `kubectl -n load-shed get hpa` shows replicas increasing, Grafana CPU panel rises then flattens

---

## Failure Modes

### Retry Storms
Retries multiply load during outages. Even if this service doesn't retry, upstream callers might, amplifying traffic and exhausting capacity.

### Cascading Failure
Slow or failing dependencies consume workers and connection pools. Latency rises, timeouts spike, failures spread to healthy endpoints.

### Tail Latency Blowups
At high utilization, queueing dominates. p95/p99 can explode even when average latency looks stable.

### Resource Saturation
CPU, memory, or concurrency limits are hit. Throughput collapses, pods may restart, error rates climb.

### Load Shedding vs Backpressure
**Load shedding** rejects work quickly (429) to protect the system. **Backpressure** slows producers or queues work. Shedding is safer when you cannot reliably queue everything.

---

## Observability

### Key Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `http_requests_total` | Counter | HTTP requests by path/status |
| `http_request_duration_seconds` | Histogram | Request latency distribution by path |
| `upstream_requests_total` | Counter | Upstream outcomes (ok, error, timeout, breaker_open) |
| `upstream_request_duration_seconds` | Histogram | Upstream latency distribution |
| `circuit_breaker_state` | Gauge | Breaker state (0=closed, 1=open) |

### Dashboard Queries

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

**Upstream outcomes breakdown:**
```promql
sum by (result) (rate(upstream_requests_total{job="load-shed-api"}[$__rate_interval]))
```

**Load shed rate (429s):**
```promql
sum(rate(http_requests_total{job="load-shed-api",path="/client",status="429"}[$__rate_interval]))
```

**Why these panels:**
- **Rate + p95:** User-visible load and tail latency
- **Breaker state + outcomes:** Dependency health and short-circuiting behavior
- **429 rate:** When service protects itself instead of collapsing

---

## Demo (Under 2 Minutes)

### A) Baseline Healthy Traffic

```bash
kubectl -n load-shed run demo-baseline --rm -i --restart=Never --image=curlimages/curl -- sh -lc '
SVC="http://load-shed-api/client";
seq 1 300 | xargs -n1 -P30 -I{} sh -c "curl -sS -o /dev/null \"$SVC?ms=80&fail_rate=0.0&timeout_ms=2000\" || true"
'
```

**Expected:** Mostly 200s, breaker state stays at 0

### B) Trip Circuit Breaker

```bash
./scripts/demo_breaker.sh
```

**Expected:** `circuit_breaker_state` becomes 1, `breaker_open` outcomes increase

**Verify in Prometheus:**
```promql
max(circuit_breaker_state{job="load-shed-api"})
```

```promql
sum by (result) (increase(upstream_requests_total{job="load-shed-api"}[10m]))
```

### C) Demonstrate Load Shedding (429)

**Note:** Load shedding depends on `MAX_INFLIGHT` configuration. If 429s don't appear, lower `MAX_INFLIGHT` in the deployment manifest and redeploy.

```bash
kubectl -n load-shed run shed-test --rm -i --restart=Never --image=curlimages/curl -- sh -lc '
SVC="http://load-shed-api/client";
seq 1 2000 | xargs -n1 -P300 -I{} sh -c "curl -s -o /dev/null -w \"%{http_code}\n\" \"$SVC?ms=600&fail_rate=0.0&timeout_ms=8000\" || true" \
| sort | uniq -c
'
```

**Expected:** Mix of 429s (shed) and 200s

**Confirm in Prometheus:**
```promql
sum(rate(http_requests_total{job="load-shed-api",path="/client",status="429"}[2m]))
```

### D) Recovery

Stop load generation and wait for breaker to close.

**Verify:**
```promql
max(circuit_breaker_state{job="load-shed-api"})  # Should return to 0
```

---

## Troubleshooting

### Grafana Dashboard Not Appearing

**Check ConfigMap exists and is labeled:**
```bash
kubectl -n monitoring get cm load-shed-dashboard -o yaml | head
```

**Check Grafana sidecar logs:**
```bash
kubectl -n monitoring logs deploy/kube-prometheus-stack-grafana -c grafana-sc-dashboard --tail=200
```

**Fix:** Verify ConfigMap label matches Grafana sidecar configuration

### Prometheus Not Scraping

**Check ServiceMonitor:**
```bash
kubectl -n load-shed get servicemonitor
kubectl -n load-shed describe servicemonitor load-shed-api
```

**Fix:** Ensure namespace and label selectors match Prometheus configuration

### HPA Shows "No Metrics Returned"

**Check metrics-server:**
```bash
kubectl get apiservice v1beta1.metrics.k8s.io -o wide
kubectl -n kube-system get pods -l k8s-app=metrics-server
```

**Fix:** Install or restart metrics-server

### Kind Image Not Updating

**Rebuild and reload:**
```bash
docker build -t load-shed-api:local .
kind load docker-image load-shed-api:local --name load-shed
kubectl -n load-shed rollout restart deploy/load-shed-api
```

### Port-Forward Fails (Address Already in Use)

**Check for conflicts:**
```bash
ss -ltnp | grep -E '(:8080|:3000|:9090)'
./scripts/stop.sh
```

**Fix:** Stop existing port-forwards using `stop.sh` script

---

## Repository Structure

```
.
├── app/
│   ├── main.py              # FastAPI service, reliability controls, metrics
│   └── requirements.txt     # Python dependencies
├── deploy/
│   └── k8s/
│       ├── namespace.yaml
│       ├── deployment.yaml
│       ├── service.yaml
│       ├── servicemonitor.yaml
│       ├── hpa.yaml
│       ├── upstream.yaml
│       ├── grafana-dashboard-configmap.yaml
│       └── prometheus-rule-load-shed.yaml
├── scripts/
│   ├── start.sh             # One-command setup
│   ├── stop.sh              # Stop port-forwards
│   └── demo_breaker.sh      # Circuit breaker demo
├── dashboards/              # Dashboard JSON sources (optional)
└── Dockerfile
```

---

## Advanced Configuration

### Adjusting Load Shedding Threshold

Edit `deploy/k8s/deployment.yaml`:

```yaml
env:
- name: MAX_INFLIGHT
  value: "50"  # Lower value = more aggressive shedding
```

Redeploy:
```bash
kubectl apply -f deploy/k8s/deployment.yaml
kubectl -n load-shed rollout restart deploy/load-shed-api
```

### Tuning Circuit Breaker

Circuit breaker parameters can be adjusted in `app/main.py`:
- Failure threshold
- Recovery timeout
- Half-open request count

---
