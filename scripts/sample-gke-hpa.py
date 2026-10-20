"""Record HPA decisions during the bounded CPU-load experiment."""
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

path = Path("load-tests/results/gke-hpa-samples.jsonl")
path.parent.mkdir(parents=True, exist_ok=True)
with path.open("w", encoding="utf-8") as output:
    for _ in range(12):
        hpa = json.loads(subprocess.check_output(
            ["kubectl", "-n", "load-shed", "get", "hpa", "load-shed-api", "-o", "json"], text=True))
        sample = {"time": datetime.now(timezone.utc).isoformat(), "status": hpa["status"]}
        output.write(json.dumps(sample) + "\n")
        output.flush()
        print(json.dumps(sample), flush=True)
        time.sleep(15)
