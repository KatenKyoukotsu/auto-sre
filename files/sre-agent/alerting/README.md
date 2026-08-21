# Auto SRE Alerting Rules

## Overview
PrometheusRule definitions for monitoring Auto SRE service health and performance.

## Files
- `auto-sre-rules.yaml` — Main alerting rules covering all components

## Rule Categories
1. **Service Health** — Up/down, scan status
2. **Victoria Logs** — Circuit breaker, error rate, latency
3. **LLM** — Circuit breaker, error rate, latency
4. **Kafka** — Producer/consumer errors, lag, outbox backlog
5. **Database** — Pool exhaustion, latency
6. **Findings** — Critical spikes, stale unacked findings
7. **HTTP/API** — Error rates, auth failures, latency

## Deployment

### Kubernetes (Prometheus Operator)
```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: auto-sre-rules
  namespace: monitoring
spec:
  groups:
  - name: auto-sre.rules
    rules:
      # ... copy rules from auto-sre-rules.yaml
```

### Plain Prometheus
Add to `prometheus.yml`:
```yaml
rule_files:
  - "auto-sre-rules.yaml"
```

## Severity Levels
- **critical** — Immediate paging (PagerDuty, OpsGenie, etc.)
- **warning** — Ticket creation, Slack notification during business hours

## Tuning Thresholds
Adjust `for:` durations and threshold values based on your environment:
- Scan interval: default 15min → adjust `AutoSRELastScanStale` threshold
- VL/LLM latency: adjust based on baseline
- Kafka lag: adjust based on topic throughput
- DB pool: adjust based on max connections