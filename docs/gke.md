# Deploy to GKE

This deployment path adds regional GKE Autopilot, Artifact Registry, a GKE Ingress
(Google Cloud external HTTP Application Load Balancer), Managed Service for
Prometheus, and a Cloud Monitoring dashboard. The existing Kind workflow stays usable.
The API HPA targets 65% CPU with 2–8 replicas. Autopilot can adjust resource requests;
inspect the admitted Deployment when interpreting CPU utilization.

## Prerequisites

Use a Google Cloud project with billing enabled, permission to enable APIs, create
GKE clusters and Artifact Registry repositories, create service accounts and grant
IAM roles, and create Monitoring dashboards. Install Google Cloud CLI, the
`gke-gcloud-auth-plugin`, kubectl and Docker Desktop running Linux containers.
Authenticate with `gcloud auth login`. No service-account key is needed.

From the repository root in WSL/Linux:

```bash
PROJECT_ID=campusgpt-457123 REGION=us-central1 bash scripts/deploy-gke.sh
```

Install `gke-gcloud-auth-plugin` using Google's supported package instructions if
using the Snap CLI (its component manager cannot install it). Set `DASHBOARD_ID`
to the existing dashboard ID when rerunning to avoid duplicates.

Alternatively, from PowerShell:

```powershell
./scripts/deploy-gke.ps1 -ProjectId YOUR_PROJECT_ID -Region us-central1
```

The script creates billable resources immediately. It uses a dedicated node service
account with the GKE default node role and Artifact Registry reader. Existing clusters
must already have image-pull permissions. For existing clusters, review their network,
service account and capacity before deployment. Reruns reuse the cluster/repository,
publish a new timestamped image and create another dashboard. Use the dashboard's ID
with `gcloud monitoring dashboards update` to update an existing dashboard instead.

Only `/healthz`, `/work` and `/client` are routed to the API by the public Ingress.
Prometheus scrapes `/metrics` internally through PodMonitoring. This demo uses HTTP;
configure a domain and Google-managed certificate before sending sensitive traffic.
The upstream failure simulator remains internal.

## Verify the live deployment

```powershell
kubectl -n load-shed get ingress,pods,hpa
kubectl -n load-shed describe ingress load-shed-api
kubectl -n load-shed describe podmonitoring load-shed-api
$ip = kubectl -n load-shed get ingress load-shed-api -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
curl.exe -i "http://$ip/healthz"
curl.exe -i -X POST "http://$ip/work?ms=50"
curl.exe -i "http://$ip/client?ms=50"
```

Wait for the Ingress IP and healthy backends; provisioning can take several minutes.
In Cloud Monitoring, open **Load-shedding API — GKE** after generating application
traffic. Check p95 latency, 429 responses/second, 429 fraction, and admitted in-flight
work. Queries select application routes and exclude health checks from latency and
429-fraction denominators. Metrics may take a few minutes to appear.

Use `load-tests/evidence.py --help` for the existing experiment tooling. Generate
concurrent requests to `/client?ms=500&timeout_ms=1000&priority=low` to exercise
admission rejection and capture response headers showing `429` and `Retry-After: 1`.
Generate sustained CPU load against `/work` and watch
`kubectl -n load-shed get hpa,pods -w`. A maximum of 8 is a configured limit, not
proof that the experiment reached 8; retain actual HPA samples as evidence.
For breaker verification, send repeated `/client?fail_rate=1` requests to observe
502 followed by 503. Breakers and admission counters are per process, so use a
single-pod port-forward for deterministic breaker experiments.

## Cleanup

Delete the Ingress first, while the cluster still exists, and allow GKE to clean up
its load balancer before deleting the cluster:

```powershell
kubectl -n load-shed delete ingress load-shed-api
# Confirm the associated forwarding rule/backend resources have been removed.
gcloud container clusters delete load-shed --region=us-central1 --project=YOUR_PROJECT_ID
gcloud artifacts repositories delete load-shed --location=us-central1 --project=YOUR_PROJECT_ID
```

Delete the dashboard and dedicated node service account if no longer needed.
Keep your project if it contains other workloads. Do not describe this as deployed
until the public health check, image pulls, HPA and dashboard have been verified.

## Verified deployment

- Project: `campusgpt-457123`; regional Autopilot cluster: `load-shed`, `us-central1`.
- Health endpoint used for verification: `http://136.82.66.60/healthz` (historical HTTP demo endpoint; availability is not guaranteed).
- Artifact Registry image is deployed by digest; see `deployment-evidence.json`.
- All 10 container tests pass. Both API replicas and the upstream started successfully.
- Public 250-request burst: 73 successes and 177 HTTP 429 responses, with `Retry-After: 1` verified.
- Single-pod admission experiment observed the ceiling of 50 and reconciled priority counters.
- Dependency checks: five 502 responses, then 503 while open, 200 after cooldown, and 504 for timeout.
- CPU load caused HPA scaling from 2 to 4 to 8; all eight API pods were Ready.
- Cloud Monitoring returned nonempty latency and 429-rate data.
- Dashboard: https://console.cloud.google.com/monitoring/dashboards/builder/23dd7316-bdb5-4312-9758-d46e2e2e3a39?project=campusgpt-457123

Reproduce smoke checks with `python3 scripts/verify-gke.py http://INGRESS_IP`,
replacing `INGRESS_IP` with the current address returned by `kubectl get ingress`.
The CPU experiment is bounded to 150 seconds (`python3 scripts/load-gke.py`).
After load stops, HPA retains higher capacity for its 300-second scale-down window,
then returns toward two replicas. The endpoint/IP and running resource state can
change after redeployment or cleanup; this section records the verified run.

The bounded CPU run recorded 5,145 successful requests and 11 HTTP 502 responses
(about 0.21%) during the scaling period. Their cause was not isolated. The final
public health check returned 200. This deployment demonstrates scaling and load
shedding; the run does not establish zero-error behavior during scaling.
