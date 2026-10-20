# Cloud-Native Load-Shedding Service

A Kubernetes-native FastAPI service that protects application availability under overload and downstream dependency failures using **priority-aware admission control, circuit breaking, bounded HTTP timeouts, autoscaling, and observability**.

The service implements a hard per-process concurrency ceiling with reserved capacity for higher-priority traffic, rejects excess work before expensive processing begins, and exposes detailed Prometheus metrics for admission decisions, latency, dependency outcomes, and circuit-breaker state. Kubernetes monitoring supplies HPA and resource metrics.

Deployed and verified on **Google Kubernetes Engine (GKE)** behind Cloud Load Balancing, with images in Artifact Registry, HPA scaling from **2 to 8 pods**, and Cloud Monitoring dashboards for latency and HTTP 429 rate. See the [deployment guide](docs/gke.md) and [screenshots and verification evidence](docs/deployment-proof.md).

## Architecture

Both local Kind and GCP deployments use the same FastAPI service and CPU-based HPA with 2–8 replicas. On GCP, Cloud Load Balancing routes traffic through GKE Ingress to the API Service; images are stored in Artifact Registry, and Managed Prometheus sends application metrics to Cloud Monitoring.

### Local Kind architecture

```text
                         Kubernetes Service
                                |
                                v
                          load-shed-api
                    +-------------------------+
                    |      FastAPI Pod        |
                    |                         |
                    |  /work        /client   |
                    |    |             |      |
                    | Admission     Admission |
                    |    |             |      |
                    | CPU Work      Breaker   |
                    |                  |      |
                    |               HTTPX     |
                    +------------------|------+
                                       |
                                       v
                              Upstream Service
                                       |
                                       v
                                 Upstream Pod


load-shed-api /metrics
        |
        v
   ServiceMonitor
        |
        v
    Prometheus
        |
        v
      Grafana


Pod CPU
   |
   v
Metrics Server
   |
   v
Kubernetes HPA
   |
   v
API Deployment: 2-8 replicas
```

The project separates two reliability experiments:

* `/work` performs bounded CPU work and provides a measurable signal for CPU-based Horizontal Pod Autoscaling.
* `/client` exercises downstream dependency protection through admission control, HTTP timeouts, connection pooling, and a circuit breaker.

## Reliability Controls

### Priority-Aware Load Shedding

Before expensive work begins, every `/work` and `/client` request passes through an atomic admission controller.

The default per-process thresholds are:

| Priority         | Admission Ceiling |
| ---------------- | ----------------: |
| Low              |                35 |
| Normal           |                45 |
| High             |                50 |
| Absolute maximum |                50 |

This creates reserved capacity for higher-priority traffic.

At 35 concurrent requests, additional low-priority requests are rejected while normal and high traffic may continue.

At 45, additional normal traffic is also rejected.

Only high-priority traffic can consume the final five permits before the absolute concurrency ceiling of 50 is reached.

Admission decisions are performed under a single asynchronous lock so the threshold check and counter mutation occur atomically.

Requests that cannot be admitted fail immediately with:

```text
HTTP 429 Too Many Requests
Retry-After: 1
```

The service deliberately does not queue rejected work. The goal is to prevent excess requests from consuming additional resources while the process is already saturated.

### Circuit Breaker

The `/client` path protects calls to the simulated downstream service with a process-local circuit breaker.

The breaker follows the standard lifecycle:

```text
CLOSED
   |
   | repeated qualifying failures
   v
OPEN
   |
   | cooldown expires
   v
HALF-OPEN
   |
   +---- successful probe ----> CLOSED
   |
   +---- failed probe --------> OPEN
```

Default behavior:

* Opens after 5 consecutive qualifying failures
* Remains open for 10 seconds
* Allows exactly one half-open probe
* Short-circuits additional calls while open
* Uses generation tokens to prevent stale request completions from modifying newer breaker state

An open breaker returns HTTP 503 without invoking the downstream dependency.

### HTTP Connection Management and Timeouts

The service creates one lifespan-managed `httpx.AsyncClient` instead of creating a new HTTP client for every request.

Client-level connection settings (before the per-request timeout override):

| Setting                        | Value |
| ------------------------------ | ----: |
| Maximum connections            |   100 |
| Maximum keep-alive connections |    20 |
| Connect timeout                | 0.2 s |
| Read timeout                   |   1 s |
| Write timeout                  |   1 s |
| Pool timeout                   | 0.1 s |

The `/client` endpoint overrides all four timeout phases for each upstream call using the lab query parameter `timeout_ms`, which defaults to **200ms**. The connect/read/write/pool values above are client-level defaults; they are not the effective timeouts for `/client` calls. HTTPX phase timeouts bound individual network operations rather than imposing a single end-to-end request deadline.

Connection pooling allows reuse while the effective timeouts bound waits for network operations and connection-pool capacity.

Downstream failures are mapped intentionally:

| Condition                          | Response |
| ----------------------------------- | -------: |
| Admission rejected                 |      429 |
| Circuit breaker open               |      503 |
| Upstream HTTP or transport failure |      502 |
| Upstream timeout or pool timeout   |      504 |
| Invalid request parameters         |      422 |

## Kubernetes Autoscaling

The API runs behind a Kubernetes Deployment with a CPU-based HorizontalPodAutoscaler.

```text
Minimum replicas: 2
Maximum replicas: 8
```

`/work` creates bounded CPU pressure so the scaling lifecycle can be observed through Metrics Server and the Kubernetes HPA controller.

Admission control and autoscaling operate at different timescales:

```text
Request arrives
      |
      +---- Admission control: immediate overload protection
      |
      +---- HPA: slower capacity adaptation through additional replicas
```

Load shedding therefore protects individual processes while Kubernetes reacts to sustained resource demand.

## Observability

The application exposes Prometheus metrics for:

* offered requests
* admitted requests
* shed requests
* completed requests
* current in-flight work
* configured admission limits
* HTTP status codes
* request latency
* upstream request outcomes
* upstream latency
* circuit-breaker state
* circuit-breaker transitions

The two deployment environments collect application metrics differently:

| Environment | Application metric collection | Visualization |
| --- | --- | --- |
| Local Kind | `ServiceMonitor` → Prometheus | Grafana |
| GCP / GKE | `PodMonitoring` → Managed Service for Prometheus | Cloud Monitoring |

The application exports the metrics listed above. Kubernetes monitoring provides the HPA replica counts and CPU/memory metrics.

For local Kind, Grafana is provisioned with dashboards covering:

* request and admission rates
* shedding rate and shed fraction
* p95 and p99 application latency
* p95 and p99 upstream latency
* upstream outcomes
* circuit-breaker state and transitions
* in-flight work and configured limits
* HPA current, desired, and maximum replicas
* CPU utilization
* memory utilization

The local Prometheus alert rules cover sustained breaker-open state, elevated downstream failures, high upstream latency, and sustained shedding.

The GKE Cloud Monitoring dashboard covers p95 application latency, HTTP 429 rate, 429 fraction, and admitted in-flight work. The GKE deployment does not install the local Grafana stack or its alert rules.

## Infrastructure

The project uses:

* **FastAPI** for the API
* **HTTPX** for asynchronous downstream calls
* **Docker** for containerization and **Artifact Registry** for GCP images
* **Cloud Load Balancing** with GKE Ingress for public traffic on GCP
* **Kind** for local Kubernetes orchestration; **GKE Autopilot** for the GCP deployment
* **Horizontal Pod Autoscaler** for CPU-based scaling
* **Metrics Server** for Kubernetes resource metrics
* **Prometheus** locally and **Managed Service for Prometheus** on GKE for application metrics
* **Grafana** locally and **Cloud Monitoring** on GCP for visualization
* **Terraform** and **Helm** for the local monitoring stack
* **Pytest** for automated verification
* **Ruff** for Python linting
* **GitHub Actions** for CI

## Run Locally

### Prerequisites

Install:

* Docker
* Kind
* kubectl
* Terraform
* curl
* Python 3
* Bash or WSL

Start the environment:

```bash
./scripts/start.sh
```

The script:

1. Creates or reuses the Kind cluster
2. Installs Metrics Server
3. Applies Terraform-managed monitoring infrastructure
4. Builds the API Docker image
5. Loads the image into Kind
6. Deploys the API and upstream service
7. Verifies Kubernetes workloads and metrics
8. Provisions the Grafana dashboard
9. Starts the required local port-forwards

Run the priority test:

```bash
./load-tests/priority.sh
```

Stop local port-forwards:

```bash
./scripts/stop.sh
```

## Local Verification

Install development dependencies:

```bash
python -m pip install -r requirements-dev.txt
python -m pip install -r app/requirements.txt
```

Run the test suite:

```bash
python -m pytest -q
```

Run linting:

```bash
python -m ruff check app tests
```

Validate Python compilation:

```bash
python -m compileall -q app tests
```

Validate Terraform:

```bash
terraform -chdir=infra/terraform fmt -check
terraform -chdir=infra/terraform init -backend=false
terraform -chdir=infra/terraform validate
```

Build the Docker image:

```bash
docker build -t load-shed-api:local .
```

## Deterministic Admission Verification

For single-process admission verification, forward directly to one API pod:

```bash
POD=$(kubectl -n load-shed get pod \
  -l app=load-shed-api \
  -o jsonpath='{.items[0].metadata.name}')

kubectl -n load-shed port-forward "pod/$POD" 18080:8080
```

In another terminal:

```bash
python load-tests/evidence.py
```

The evidence test verifies that:

* low-priority traffic does not exceed its ceiling of 35
* normal-priority traffic cannot consume capacity reserved for high priority
* total admitted concurrency never exceeds 50
* rejected traffic returns 429 with `Retry-After`
* admission and completion metrics reconcile with observed requests
* in-flight accounting returns to the expected value after the test

Additional scenarios are documented in:

```text
load-tests/scenarios.md
```

These include healthy traffic, CPU/HPA scaling, delayed dependency calls, priority reservation, breaker transitions, half-open recovery, and sustained maximum-replica behavior.

## Verified Behavior

Local verification demonstrated:

* Three-tier atomic admission at 35 / 45 / 50
* Absolute concurrency ceiling of 50 per process
* Correct 422 input validation
* Correct 429 overload responses
* Admission metric reconciliation
* Closed, open, and half-open circuit-breaker transitions
* Docker image build
* Clean Kind deployment
* Terraform convergence
* Prometheus target scraping
* Grafana dashboard provisioning and valid PromQL
* Numeric Kubernetes HPA metrics
* HPA scale-out from 2 to 8 Ready API pods
* Automated tests, linting, Terraform validation, and CI configuration

## Design Considerations

Admission and breaker state are intentionally process-local. This keeps overload decisions fast and independent of external coordination systems, but it does not provide a cluster-wide concurrency quota.

With multiple replicas, aggregate theoretical capacity grows with the number of API processes, while individual pods continue enforcing their own concurrency limits.

The priority model uses strict reserved capacity rather than fair scheduling. This guarantees headroom for higher-priority requests but can leave reserved capacity unused and can starve low-priority traffic under sustained higher-priority load.

The public `priority` query parameter exists to make the behavior easy to reproduce and test. A production implementation should derive priority from trusted application policy such as authenticated tenant identity, route classification, operation type, or service-level policy.

CPU-based HPA is used specifically for the `/work` scaling experiment. CPU utilization does not directly represent I/O saturation, so production workloads may benefit from additional scaling signals such as queue depth, request concurrency, latency, or application-specific Prometheus metrics.

## Production Extensions

Possible extensions include:

* authenticated priority classification
* per-tenant quotas
* weighted fair scheduling
* borrowable reserved capacity
* distributed admission control
* custom-metric Kubernetes autoscaling
* retry budgets and retry-aware admission
* production remote Terraform state
* multi-cluster or regional traffic management

## Project Goal

This project focuses on a practical reliability principle:

> **When capacity is constrained, explicitly control which work enters the system instead of allowing overload to determine system behavior.**

Autoscaling adds capacity over time. Admission control protects the service immediately. Circuit breaking prevents unhealthy dependencies from consuming resources repeatedly. Observability makes each decision measurable.
