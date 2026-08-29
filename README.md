# Priority-aware load shedding and dependency resilience

This lab demonstrates bounded, per-process admission before CPU or dependency work, plus HTTPX pooling/timeouts,
a closed/open/half-open circuit breaker, an independent failure-injection upstream, CPU HPA, and Prometheus/Grafana.
It makes no throughput or SLA claim until a dated result is captured from the supplied load scenarios.

## Architecture

```text
caller -> Service -> API pod
                    | /healthz, /metrics (exempt)
                    | /work -> atomic priority admission -> CPU spin -> HPA CPU signal
                    ` /client -> atomic priority admission -> circuit breaker -> pooled HTTPX
                                                                    -> upstream Service -> upstream pod
API /metrics -> ServiceMonitor -> Prometheus -> Grafana / threshold alerts
Metrics Server -> metrics.k8s.io -> HPA -> API Deployment (2..8 replicas)
```

`/work` is the CPU scaling experiment. Delayed `/client` calls demonstrate admission and dependency resilience;
CPU HPA does not directly observe I/O saturation and reacts much more slowly than local admission.

## Admission and priority

`MAX_INFLIGHT` (50) is the absolute per-Uvicorn-process limit shared by `/work` and `/client`.
`LOW_PRIORITY_MAX_INFLIGHT` (35) rejects low-priority work first, `NORMAL_PRIORITY_MAX_INFLIGHT` (45) then
rejects normal work, and only high work can use the final five permits before the absolute limit.
Every decision and count mutation occurs under one async lock and never waits in an admission queue. A rejection is
HTTP 429 with `Retry-After: 1`; an open breaker is HTTP 503. All priorities are rejected at the absolute limit.

The `priority=high|normal|low` query is **only a documented failure-testing input in this lab**. Production systems
must derive priority from authenticated tenant, route, or operation policy. Trusting arbitrary client input lets a
client self-promote. The enum prevents unknown values and bounds metric labels, but does not authenticate intent.

Reservation improves availability for important work, but it is not fair scheduling: continuous high/normal load
can starve low work, and the controller provides neither per-tenant fairness nor aging. Conversely, unused reserved
capacity cannot be consumed by low priority once the low threshold is reached. Production policy may need weighted
fair queues, tenant quotas, aging, or separate pools—while retaining a hard global cap.

Health and metrics are exempt. Metrics include offered/admitted/shed decisions and completed outcomes by bounded
route and priority, current admitted work, both limits, bounded HTTP status/route dimensions, upstream outcomes,
latency histograms, breaker state, and transitions.

## Dependency behavior

One lifespan-managed `httpx.AsyncClient` has a 100-connection pool, 20 keep-alive connections, and explicit connect
(0.2s), read (1s), write (1s), and pool (0.1s) defaults. Per-request `timeout_ms` is lab-only. Outcomes are 502 for
upstream HTTP/transport failure, 504 for timeout/pool timeout, 503 for breaker rejection, and 429 for admission.

The process-local breaker opens after five consecutive qualifying failures, using monotonic time. Open calls do not
invoke HTTPX. After 10 seconds it enters half-open and permits exactly one probe; other calls fail fast. Probe success
closes it, failure reopens it. Generation tokens stop stale completions from mutating a newer breaker state. With one
Uvicorn process per pod, every pod has independent admission and breaker state.

The independent upstream implements async `ms` delay and probabilistic `fail_rate`. Both are lab-only injection
parameters, validated (including non-finite float rejection), and must not be exposed in a production API.

## Configuration

| Variable | Default | Meaning |
|---|---:|---|
| `MAX_INFLIGHT` | 50 | Absolute admitted work per process |
| `LOW_PRIORITY_MAX_INFLIGHT` | 35 | Low-priority cutoff |
| `NORMAL_PRIORITY_MAX_INFLIGHT` | 45 | Normal-priority cutoff; must satisfy low < normal < absolute |
| `UPSTREAM_BASE_URL` | `http://127.0.0.1:8081` | Dependency base URL; Kubernetes sets the upstream Service |
| `BREAKER_FAILURE_THRESHOLD` | 5 | Consecutive failures to open |
| `BREAKER_COOLDOWN_SECONDS` | 10 | Open interval before one probe |
| `HTTP_MAX_CONNECTIONS` | 100 | Pool maximum |
| `HTTP_MAX_KEEPALIVE_CONNECTIONS` | 20 | Idle keep-alive maximum |
| `HTTP_{CONNECT,READ,WRITE,POOL}_TIMEOUT_SECONDS` | 0.2/1/1/0.1 | HTTPX phase limits |

Invalid environment configuration fails startup.

## Run and verify

Prerequisites: Docker, Kind, kubectl, Terraform, curl, and a Bash environment. Terraform is the sole owner of the
pinned `kube-prometheus-stack`; the local backend writes ignored state under `infra/terraform/.state/`. Teams should use an encrypted, access-controlled
remote backend with locking rather than share local state.

```bash
./scripts/start.sh
./load-tests/priority.sh
./scripts/stop.sh
```

The start script creates/reuses Kind, installs pinned Metrics Server v0.7.2 with Kind's kubelet TLS accommodation,
applies Terraform monitoring, builds/loads the image, deploys and verifies workloads/metrics/EndpointSlices, provisions
the canonical dashboard, and records exact port-forward PIDs in `.pf`. Stop only terminates those recorded processes;
it does not delete the cluster.

Local checks:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check app tests
python -m compileall -q app tests
terraform -chdir=infra/terraform fmt -check
terraform -chdir=infra/terraform init -backend=false
terraform -chdir=infra/terraform validate
docker build -t load-shed-api:local .
```

### Go smoke checker

`cmd/load-shed-check` is a standard-library-only operational check for the deployed API. It validates the bounded lab
priority before sending traffic, verifies `/healthz`, classifies `/client` responses, and requires `Retry-After` on a
shed response. It exits nonzero when the observed outcome differs from `--expect`.

```bash
go run ./cmd/load-shed-check --base-url http://127.0.0.1:8080 --priority normal --expect admitted
go run ./cmd/load-shed-check --base-url http://127.0.0.1:8080 --priority low --expect shed
go test ./...
```

See `load-tests/scenarios.md` for healthy, CPU/HPA, delayed admission, priority reservation, breaker, half-open recovery,
and sustained maximum-replica scenarios. Store exact commit/tool versions, duration, rates, status counts, percentiles,
replica/CPU timeline, and upstream outcomes; do not report expected values as measured results.

For deterministic single-process admission evidence, port-forward one API pod and run:

```bash
POD=$(kubectl -n load-shed get pod -l app=load-shed-api -o jsonpath='{.items[0].metadata.name}')
kubectl -n load-shed port-forward "pod/$POD" 18080:8080
# In a second terminal:
python load-tests/evidence.py
```

This test fails if low exceeds 35, normal consumes the five permits reserved for high, total in-flight exceeds 50,
overflow responses lack `Retry-After`, or offered/admitted/shed/completed/in-flight metrics do not reconcile. It saves
the exact configuration, working-tree hash, versions, timestamps, and observed results under `load-tests/results/`.

## Dashboard and alerts

The canonical JSON in `dashboards/` shows offered, admitted, shed, and completed rates by priority; shed fraction;
client/upstream p95 and p99; upstream outcomes; breaker state and transitions; in-flight and all limits per pod; HPA CPU
utilization and current/desired/maximum replicas; and memory. Alerts are ordinary
threshold alerts: continuously open breaker, traffic-gated `/client` 5xx fraction, high upstream p95, and sustained shed
fraction. They are deliberately not called SLO alerts.

## Limitations

Kind is not production. Admission and breaker state are process-local; capacity is therefore approximately replicas
times the per-process limit and there is no distributed quota. CPU work is synthetic. HPA is slower than admission and
does not directly measure I/O saturation. Priority is unauthenticated lab input. There is no tenant fairness guarantee,
and low priority can starve. Historical removal of committed provider binaries would require a separately authorized Git
history rewrite; deleting them from the current tree does not shrink old commits.
