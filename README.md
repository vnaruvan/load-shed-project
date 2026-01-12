# Load Shed API

A Kubernetes-deployed demonstration API showcasing production-grade reliability patterns: **timeouts**, **circuit breakers**, **load shedding**, **autoscaling**, and **observability** around an unreliable upstream dependency.

**Stack:** FastAPI · Prometheus · Grafana · kube-prometheus-stack · Kind · Terraform

---

## Quick Start

**Prerequisites:** Docker, Kind, kubectl, Helm, Terraform ≥1.5

```bash
# 1. Create cluster
kind create cluster --name load-shed

# 2. Install monitoring
cd infra/terraform && terraform init && terraform apply -auto-approve && cd ../..

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
- Username: `admin`
- Password: `kubectl -n monitoring get secret kube-prometheus-stack-grafana -o jsonpath="{.data.admin-password}" | base64 -d`

---

## Architecture

### Components

| Component | Purpose |
|-----------|---------|
| **load-shed-api** | FastAPI service with reliability controls |
| **load-shed-upstream** | Simulated dependency (configurable latency/failures) |
| **Prometheus** | Metrics collection (kube-prometheus-stack) |
| **Grafana** | Dashboard visualization |
| **HPA + metrics-server** | Pod autoscaling based on CPU |
| **Terraform** | Infrastructure provisioning |

### Service Endpoints

- **`/client`** - Main endpoint that calls upstream (demonstrates timeout, circuit breaker, load shedding)
- **`/upstream`** - Simulated dependency with configurable behavior
- **`/metrics`** - Prometheus metrics endpoint
- **`/healthz`** - Liveness/readiness probe

### Data Flow

```
User/Load Generator
        ↓
 load-shed-api (/client)
        ↓
   httpx + timeout
        ↓
 load-shed-upstream (/upstream)
        ↓
   Prometheus scrapes /metrics
        ↓
   Grafana visualizes
```

---

## Reliability Patterns

### 1. Timeouts
**Mitigates:** Tail latency blowups, cascading failures, resource saturation

Configured `httpx` timeout prevents hung requests from consuming workers indefinitely.

**Observable signal:** `upstream_requests_total{result="timeout"}` increases, `/client` p95 latency rises

### 2. Circuit Breaker
**Mitigates:** Cascading failures, wasted work against failing dependencies

Opens after sustained upstream failures and short-circuits calls to fail fast.

**Observable signal:** `circuit_breaker_state=1`, `upstream_requests_total{result="breaker_open"}` increases

### 3. Load Shedding (429)
**Mitigates:** Resource saturation, tail latency blowups

Rejects requests quickly when overloaded instead of queuing everything.

**Observable signal:** `http_requests_total{path="/client",status="429"}` increases

### 4. Horizontal Pod Autoscaling
**Mitigates:** Resource saturation

Automatically scales pods based on CPU utilization (requires metrics-server).

---

## Observability

### Key Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `http_requests_total` | Counter | HTTP requests by method, path, status |
| `http_request_duration_seconds` | Histogram | Request latency distribution |
| `upstream_requests_total` | Counter | Upstream outcomes (ok, error, timeout, breaker_open) |
| `upstream_request_duration_seconds` | Histogram | Upstream latency distribution |
| `circuit_breaker_state` | Gauge | Breaker state (0=closed, 1=open) |

### Dashboard Panels

**Request rate (overall):**
```promql
sum(rate(http_requests_total{job="load-shed-api",path="/client"}[$__rate_interval]))
```

**Request rate (per pod):**
```promql
sum by (pod) (rate(http_requests_total{job="load-shed-api",path="/client"}[$__rate_interval]))
```

**p95 latency:**
```promql
histogram_quantile(0.95, 
  sum by (le) (rate(http_request_duration_seconds_bucket{job="load-shed-api",path="/client"}[$__rate_interval]))
)
```

**Circuit breaker state:**
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

---

## Demo Walkthrough

### Option 1: Automated Demo Script

Run the complete demo sequence automatically:

```bash
./scripts/demo_breaker.sh
```

This script runs baseline traffic, trips the circuit breaker, and demonstrates recovery.

**Alternative - Target upstream directly:**
```bash
UPSTREAM_DEPLOY=load-shed-upstream ./scripts/demo_breaker.sh
```

This variant can be used to test the upstream service behavior in isolation.

### Option 2: Manual Demo Steps

Run these commands inside the cluster to generate realistic traffic patterns:

#### Step 1: Baseline Traffic

```bash
kubectl -n load-shed run demo --rm -it --restart=Never --image=curlimages/curl -- sh -c '
SVC="http://load-shed-api/client";
echo "Warmup - healthy traffic";
seq 1 200 | xargs -n1 -P10 sh -c "curl -sS -o /dev/null \"$0\" || true" "$SVC";
echo "Done"
'
```

**Expected:** HTTP 200s, request rate increases, traffic distributed across pods

#### Step 2: Trip Circuit Breaker

```bash
kubectl -n load-shed run demo --rm -it --restart=Never --image=curlimages/curl -- sh -c '
SVC="http://load-shed-api/client";
echo "Forcing failures - tripping breaker";
seq 1 800 | xargs -n1 -P20 sh -c "curl -sS -o /dev/null \"$0?fail_rate=1.0\" || true" "$SVC";
echo "Done"
'
```

**Expected:** Circuit breaker opens, `breaker_open` outcomes increase

**Verify:**
```promql
max(circuit_breaker_state{job="load-shed-api"})  # Should be 1
sum by (result) (rate(upstream_requests_total{job="load-shed-api"}[2m]))
```

#### Step 3: Recovery

```bash
kubectl -n load-shed run demo --rm -it --restart=Never --image=curlimages/curl -- sh -c '
SVC="http://load-shed-api/client";
echo "Recovery - healthy traffic";
seq 1 400 | xargs -n1 -P10 sh -c "curl -sS -o /dev/null \"$0?fail_rate=0.0\" || true" "$SVC";
echo "Done"
'
```

**Expected:** Circuit breaker closes, `circuit_breaker_state` returns to 0

---

## Failure Modes Explained

### Retry Storms
Retries amplify traffic during incidents. Even if this service doesn't retry, upstream callers can multiply load exponentially.

### Cascading Failures
Slow or failing dependencies consume workers and connection pools. Latency climbs, timeouts increase, failures spread to healthy endpoints.

### Tail Latency Blowups
As utilization rises, queuing dominates. p95/p99 latency increases sharply even when average latency appears stable.

### Resource Saturation
CPU, memory, or connection pools hit limits. Throughput collapses, pods restart repeatedly, response codes degrade.

### Load Shedding vs Backpressure
- **Load shedding:** Rejects work quickly (429) to protect latency and stability
- **Backpressure:** Slows producers or queues requests

Shedding is protective when you cannot safely queue everything.

---

## Alerting

A sample `PrometheusRule` is included at `deploy/k8s/prometheus-rule-load-shed.yaml`.

**Example alert conditions:**
- Circuit breaker open for sustained period
- Upstream p95 latency above threshold
- Elevated 5xx or 429 rates on `/client`

These catch user-visible degradation and dependency failures before complete outage.

---

## Troubleshooting

### Grafana dashboard not appearing
**Symptom:** Dashboard missing in Grafana UI

**Diagnosis:**
```bash
kubectl -n monitoring get cm | grep -i dashboard
kubectl -n monitoring logs deploy/kube-prometheus-stack-grafana --tail=200
```

**Fix:** Verify ConfigMap labels match Grafana's dashboard discovery configuration

### Prometheus not scraping
**Symptom:** No metrics in Prometheus targets

**Diagnosis:**
```bash
kubectl -n load-shed get servicemonitor
kubectl -n load-shed describe servicemonitor load-shed-api
```

**Fix:** Ensure ServiceMonitor namespace and label selectors match

### HPA shows "no metrics returned"
**Symptom:** HPA cannot scale pods

**Diagnosis:**
```bash
kubectl -n kube-system get pods | grep metrics-server
kubectl get apiservices | grep metrics
```

**Fix:** Install or restart metrics-server

### Kind image not updating
**Symptom:** Code changes not reflected in pods

**Fix:**
```bash
docker build -t load-shed-api:local .
kind load docker-image load-shed-api:local --name load-shed
kubectl -n load-shed rollout restart deploy/load-shed-api
```

### Port-forward fails
**Symptom:** Connection refused or address already in use

**Diagnosis:**
```bash
ss -ltnp | grep -E '(:8080|:3000|:9090)'
kubectl -n load-shed get endpointslices -l kubernetes.io/service-name=load-shed-api -o wide
```

**Fix:** Kill conflicting processes or verify service has endpoints

---

## Repository Structure

```
.
├── app/
│   ├── main.py              # FastAPI service, breaker logic, metrics
│   └── requirements.txt     # Python dependencies
├── deploy/
│   └── k8s/
│       ├── namespace.yaml
│       ├── app-deployment.yaml
│       ├── app-service.yaml
│       ├── app-hpa.yaml
│       ├── upstream.yaml
│       ├── servicemonitor.yaml
│       ├── grafana-dashboard-configmap.yaml
│       └── prometheus-rule-load-shed.yaml
├── infra/
│   └── terraform/           # kube-prometheus-stack installation
├── scripts/                 # Demo and load generators
├── dashboards/              # Dashboard JSON sources
└── Dockerfile
```
