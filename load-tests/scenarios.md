# Reproducible scenarios

Run after `scripts/start.sh`; record outputs rather than copying expected numbers.

- Healthy: `hey -z 60s -c 20 'http://127.0.0.1:8080/client?ms=20'`
- CPU/HPA: `hey -z 10m -c 50 -m POST 'http://127.0.0.1:8080/work?ms=500'`
- Priority smoke traffic: `./load-tests/priority.sh`
- Assertion/evidence run: port-forward one API pod to `18080`, ensure it is idle, then run
  `python load-tests/evidence.py`. The script fails on ceiling/reservation/header/metric violations and writes a
  timestamped configuration and result under the ignored `load-tests/results/` directory.
- Breaker: five sequential `/client?fail_rate=1`, verify 503, wait for cooldown, then send one healthy probe.
- HPA maximum: continue CPU load to eight replicas, then increase concurrency.

Capture the Git commit, Docker/Kind/Kubernetes versions, duration, status counts, offered/accepted/shed
rates, latency percentiles, upstream outcomes, and timestamped `kubectl top pods` and HPA replica samples.
