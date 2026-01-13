#!/usr/bin/env bash
set -euo pipefail

CLUSTER="${CLUSTER:-load-shed}"
NS_APP="${NS_APP:-load-shed}"
NS_MON="${NS_MON:-monitoring}"
RELEASE="${RELEASE:-kube-prometheus-stack}"

need(){ command -v "$1" >/dev/null 2>&1 || { echo "missing: $1"; exit 1; }; }
need kind; need kubectl; need helm; need docker

if ! kind get clusters | grep -qx "$CLUSTER"; then
  kind create cluster --name "$CLUSTER"
fi

kubectl get ns "$NS_APP" >/dev/null 2>&1 || kubectl create ns "$NS_APP"
kubectl get ns "$NS_MON" >/dev/null 2>&1 || kubectl create ns "$NS_MON"

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update >/dev/null

helm upgrade --install "$RELEASE" prometheus-community/kube-prometheus-stack \
  -n "$NS_MON" \
  --set grafana.defaultDashboardsEnabled=true \
  --set grafana.sidecar.dashboards.enabled=true \
  --set grafana.sidecar.dashboards.label=grafana_dashboard \
  --set grafana.grafana\.ini.dashboards.default_home_dashboard_path=/tmp/dashboards/load-shed-dashboard.json >/dev/null

docker build -t load-shed-api:local .
kind load docker-image load-shed-api:local --name "$CLUSTER"

kubectl apply -f deploy/k8s/namespace.yaml >/dev/null
kubectl apply -f deploy/k8s/app-deployment.yaml >/dev/null
kubectl apply -f deploy/k8s/app-service.yaml >/dev/null
kubectl apply -f deploy/k8s/upstream.yaml >/dev/null
kubectl apply -f deploy/k8s/servicemonitor.yaml >/dev/null
kubectl apply -f deploy/k8s/grafana-dashboard-configmap.yaml >/dev/null
kubectl apply -f deploy/k8s/prometheus-rule-load-shed.yaml >/dev/null
kubectl apply -f deploy/k8s/app-hpa.yaml >/dev/null

kubectl -n "$NS_APP" rollout status deploy/load-shed-api --timeout=180s
kubectl -n "$NS_MON" rollout status deploy/kube-prometheus-stack-grafana --timeout=180s
kubectl -n "$NS_MON" rollout status deploy/kube-prometheus-stack-operator --timeout=180s

echo "Grafana:    http://localhost:3000"
echo "Prometheus: http://localhost:9090"
echo "API:        http://localhost:8080"
echo "Grafana password:"
kubectl -n "$NS_MON" get secret kube-prometheus-stack-grafana -o jsonpath='{.data.admin-password}' | base64 -d; echo

kubectl -n "$NS_APP" port-forward svc/load-shed-api 8080:80 >/tmp/pf-api.log 2>&1 &
kubectl -n "$NS_MON" port-forward svc/kube-prometheus-stack-grafana 3000:80 >/tmp/pf-grafana.log 2>&1 &
kubectl -n "$NS_MON" port-forward svc/kube-prometheus-stack-prometheus 9090:9090 >/tmp/pf-prom.log 2>&1 &

sleep 1
