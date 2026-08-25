#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"
CLUSTER="${CLUSTER:-load-shed}"; PF_DIR="$ROOT/.pf"; mkdir -p "$PF_DIR"
need(){ command -v "$1" >/dev/null || { echo "missing prerequisite: $1" >&2; exit 1; }; }
for command in docker kind kubectl terraform curl; do need "$command"; done
kind get clusters | grep -qx "$CLUSTER" || kind create cluster --name "$CLUSTER"
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.7.2/components.yaml
kubectl -n kube-system patch deployment metrics-server --type=json -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]' || true
kubectl -n kube-system rollout status deployment/metrics-server --timeout=180s
terraform -chdir=infra/terraform init -reconfigure
terraform -chdir=infra/terraform apply -auto-approve
docker build -t load-shed-api:local .
kind load docker-image load-shed-api:local --name "$CLUSTER"
kubectl apply -f deploy/k8s/namespace.yaml
kubectl apply -f deploy/k8s/app-deployment.yaml -f deploy/k8s/app-service.yaml -f deploy/k8s/upstream.yaml -f deploy/k8s/app-hpa.yaml
kubectl -n load-shed rollout status deployment/load-shed-upstream --timeout=180s
kubectl -n load-shed rollout status deployment/load-shed-api --timeout=180s
kubectl wait --for=condition=Established crd/servicemonitors.monitoring.coreos.com crd/prometheusrules.monitoring.coreos.com --timeout=180s
kubectl apply -f deploy/k8s/servicemonitor.yaml -f deploy/k8s/prometheus-rule-load-shed.yaml
kubectl -n monitoring create configmap load-shed-dashboard --from-file=load-shed-dashboard.json=dashboards/load-shed-dashboard.json --dry-run=client -o yaml | kubectl apply -f -
kubectl -n monitoring label configmap load-shed-dashboard grafana_dashboard=1 --overwrite
kubectl get --raw /apis/metrics.k8s.io/v1beta1 >/dev/null; kubectl top pods -n load-shed
kubectl -n load-shed get endpointslice -l kubernetes.io/service-name=load-shed-api
for item in "api load-shed load-shed-api 8080:80" "grafana monitoring kube-prometheus-stack-grafana 3000:80" "prom monitoring kube-prometheus-stack-prometheus 9090:9090"; do
  read -r name namespace service ports <<<"$item"
  kubectl -n "$namespace" port-forward "svc/$service" "$ports" >"$PF_DIR/$name.log" 2>&1 & echo $! >"$PF_DIR/$name.pid"
done
for url in http://127.0.0.1:8080/healthz http://127.0.0.1:3000/api/health http://127.0.0.1:9090/-/ready; do
  for _ in {1..30}; do curl -fsS "$url" >/dev/null && break; sleep 1; done; curl -fsS "$url" >/dev/null
done
kubectl -n load-shed get hpa load-shed-api
echo "Ready: API :8080, Grafana :3000, Prometheus :9090"
