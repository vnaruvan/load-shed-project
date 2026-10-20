"""Read GKE application metrics from Cloud Monitoring without displaying credentials."""
import json
import os
import subprocess
import urllib.parse
import urllib.request

project = os.environ.get("PROJECT_ID", "campusgpt-457123")
token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
queries = {
    "scraped_request_counters": 'count(load_shed_http_requests_total{namespace="load-shed"})',
    "p95_latency_seconds": 'histogram_quantile(0.95, sum by (le) (rate(load_shed_http_request_duration_seconds_bucket{namespace="load-shed",route=~"/work|/client"}[5m])))',
    "rate_429": 'sum(rate(load_shed_http_requests_total{namespace="load-shed",status="429"}[5m]))',
}
for name, query in queries.items():
    url = f"https://monitoring.googleapis.com/v1/projects/{project}/location/global/prometheus/api/v1/query?" + urllib.parse.urlencode({"query": query})
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
    print(json.dumps({"query": name, "status": data.get("status"), "result": data.get("data", {}).get("result")}))
