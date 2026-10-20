#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PROJECT_ID=${PROJECT_ID:-campusgpt-457123}
export REGION=${REGION:-us-central1}
export CLUSTER=${CLUSTER:-load-shed}
TAG=${TAG:-$(date -u +%Y%m%d%H%M%S)}
IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/load-shed/load-shed-api:$TAG"
for tool in gcloud kubectl docker gke-gcloud-auth-plugin; do
  command -v "$tool" >/dev/null || { echo "Missing prerequisite: $tool" >&2; exit 1; }
done
bash scripts/provision-gke.sh
for attempt in $(seq 1 90); do
  status=$(gcloud container clusters describe "$CLUSTER" --region="$REGION" --project="$PROJECT_ID" --format='value(status)')
  [ "$status" = RUNNING ] && break
  [ "$status" = ERROR ] && { echo "Cluster creation failed" >&2; exit 1; }
  sleep 10
done
[ "$status" = RUNNING ] || { echo "Cluster not ready; inspect GKE operations" >&2; exit 1; }
gcloud container clusters get-credentials "$CLUSTER" --region="$REGION" --project="$PROJECT_ID"
gcloud auth configure-docker "$REGION-docker.pkg.dev" --quiet
docker build --platform=linux/amd64 -t "$IMAGE" .
docker push "$IMAGE"
manifest=$(mktemp)
trap 'rm -f "$manifest"' EXIT
kubectl kustomize deploy/gke | sed "s|REGION-docker.pkg.dev/PROJECT_ID/load-shed/load-shed-api:REPLACE_WITH_COMMIT|$IMAGE|g" > "$manifest"
kubectl apply -f "$manifest"
kubectl -n load-shed rollout status deployment/load-shed-api --timeout=900s
kubectl -n load-shed rollout status deployment/load-shed-upstream --timeout=900s
if [ -n "${DASHBOARD_ID:-}" ]; then
  gcloud monitoring dashboards update "$DASHBOARD_ID" --config-from-file=dashboards/cloud-monitoring.json --project="$PROJECT_ID"
else
  gcloud monitoring dashboards create --config-from-file=dashboards/cloud-monitoring.json --project="$PROJECT_ID"
fi
kubectl -n load-shed get ingress,hpa,pods
