"""Prometheus metrics for Auto SRE."""

from prometheus_client import Counter, Gauge, Histogram, Info

# Histogram buckets for latency metrics (seconds)
LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)

# ============================================================
# SCAN METRICS
# ============================================================
scan_duration_seconds = Histogram(
    "auto_sre_scan_duration_seconds",
    "Duration of anomaly scan in seconds",
    ["result"],  # success, error
    buckets=LATENCY_BUCKETS,
)

scan_total = Counter(
    "auto_sre_scan_total",
    "Total number of anomaly scans",
    ["result"],  # success, error
)

scan_findings_created = Counter(
    "auto_sre_scan_findings_created_total",
    "Total findings created during scans",
    ["severity"],  # critical, high, medium, low
)

scan_streams_checked = Gauge(
    "auto_sre_scan_streams_checked",
    "Number of streams checked in last scan",
)

scan_candidates_found = Gauge(
    "auto_sre_scan_candidates_found",
    "Number of spike candidates found in last scan (before dedup)",
)

scan_deduped = Counter(
    "auto_sre_scan_deduped_total",
    "Total findings deduplicated",
)

scan_windows_queried = Counter(
    "auto_sre_scan_windows_queried_total",
    "Total VL time windows queried during scans",
)

last_scan_timestamp = Gauge(
    "auto_sre_last_scan_timestamp",
    "Unix timestamp of last successful scan",
)

last_scan_error = Gauge(
    "auto_sre_last_scan_error",
    "1 if last scan failed, 0 if successful",
)

# ============================================================
# FINDINGS METRICS
# ============================================================
findings_total = Gauge(
    "auto_sre_findings_total",
    "Current number of findings",
    ["severity", "service", "acknowledged"],  # acknowledged: "true"/"false"
)

findings_created_total = Counter(
    "auto_sre_findings_created_total",
    "Total findings created",
    ["severity", "service"],
)

findings_acknowledged_total = Counter(
    "auto_sre_findings_acknowledged_total",
    "Total findings acknowledged",
    ["service"],
)

finding_age_seconds = Gauge(
    "auto_sre_finding_age_seconds",
    "Age of each open finding in seconds",
    ["finding_id", "service", "severity"],
)

# ============================================================
# VICTORIA LOGS METRICS
# ============================================================
vl_query_duration_seconds = Histogram(
    "auto_sre_vl_query_duration_seconds",
    "Victoria Logs query latency in seconds",
    ["operation", "result"],  # operation: search_logs, count_logs, count_by_stream, get_streams; result: success, error
    buckets=LATENCY_BUCKETS,
)

vl_query_total = Counter(
    "auto_sre_vl_query_total",
    "Total Victoria Logs queries",
    ["operation", "result"],
)

vl_circuit_breaker_state = Gauge(
    "auto_sre_vl_circuit_breaker_state",
    "Victoria Logs circuit breaker state (0=closed, 1=half-open, 2=open)",
)

vl_retries_total = Counter(
    "auto_sre_vl_retries_total",
    "Total Victoria Logs retries",
    ["operation"],
)

# ============================================================
# LLM METRICS
# ============================================================
llm_request_duration_seconds = Histogram(
    "auto_sre_llm_request_duration_seconds",
    "LLM request latency in seconds",
    ["operation", "result"],  # operation: analyze_logs, write_blog_post; result: success, error
    buckets=LATENCY_BUCKETS,
)

llm_request_total = Counter(
    "auto_sre_llm_request_total",
    "Total LLM requests",
    ["operation", "result"],
)

llm_tokens_used = Counter(
    "auto_sre_llm_tokens_used_total",
    "Total LLM tokens used",
    ["operation", "type"],  # type: prompt, completion
)

llm_circuit_breaker_state = Gauge(
    "auto_sre_llm_circuit_breaker_state",
    "LLM circuit breaker state (0=closed, 1=half-open, 2=open)",
)

llm_retries_total = Counter(
    "auto_sre_llm_retries_total",
    "Total LLM retries",
    ["operation"],
)

llm_confidence = Histogram(
    "auto_sre_llm_confidence",
    "LLM confidence score from analysis",
    ["severity"],
    buckets=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
)

# ============================================================
# KAFKA METRICS
# ============================================================
kafka_producer_send_duration_seconds = Histogram(
    "auto_sre_kafka_producer_send_duration_seconds",
    "Kafka producer send latency in seconds",
    ["topic", "result"],
    buckets=LATENCY_BUCKETS,
)

kafka_producer_send_total = Counter(
    "auto_sre_kafka_producer_send_total",
    "Total Kafka messages sent",
    ["topic", "result"],
)

kafka_consumer_process_duration_seconds = Histogram(
    "auto_sre_kafka_consumer_process_duration_seconds",
    "Kafka consumer message processing latency in seconds",
    ["topic", "result"],
    buckets=LATENCY_BUCKETS,
)

kafka_consumer_process_total = Counter(
    "auto_sre_kafka_consumer_process_total",
    "Total Kafka messages processed by consumer",
    ["topic", "result"],
)

kafka_consumer_lag = Gauge(
    "auto_sre_kafka_consumer_lag",
    "Kafka consumer lag per partition",
    ["topic", "partition"],
)

kafka_outbox_pending = Gauge(
    "auto_sre_kafka_outbox_pending",
    "Number of pending outbox events not yet sent to Kafka",
)

kafka_outbox_processed_total = Counter(
    "auto_sre_kafka_outbox_processed_total",
    "Total outbox events processed by poller",
    ["result"],  # success, error
)

# ============================================================
# DATABASE METRICS
# ============================================================
db_query_duration_seconds = Histogram(
    "auto_sre_db_query_duration_seconds",
    "Database query latency in seconds",
    ["operation", "result"],  # operation: add_finding, list_findings, etc.
    buckets=LATENCY_BUCKETS,
)

db_pool_size = Gauge(
    "auto_sre_db_pool_size",
    "Database connection pool size",
)

db_pool_checked_out = Gauge(
    "auto_sre_db_pool_checked_out",
    "Number of checked out connections from pool",
)

# ============================================================
# BLOG METRICS
# ============================================================
blog_generation_duration_seconds = Histogram(
    "auto_sre_blog_generation_duration_seconds",
    "Blog post generation latency in seconds",
    ["result"],
    buckets=LATENCY_BUCKETS,
)

blog_generation_total = Counter(
    "auto_sre_blog_generation_total",
    "Total blog generations",
    ["result"],
)

blog_posts_total = Gauge(
    "auto_sre_blog_posts_total",
    "Total blog posts in database",
)

blog_findings_included = Gauge(
    "auto_sre_blog_findings_included",
    "Number of findings included in last blog post",
)

# ============================================================
# ALERT METRICS
# ============================================================
alert_batch_size = Histogram(
    "auto_sre_alert_batch_size",
    "Number of alerts in each analysis batch",
    buckets=(1, 2, 5, 10, 20, 50, 100),
)

alert_batch_duration_seconds = Histogram(
    "auto_sre_alert_batch_duration_seconds",
    "Alert batching window duration in seconds",
    buckets=(1, 5, 10, 30, 60, 120, 300, 600),
)

alert_analysis_duration_seconds = Histogram(
    "auto_sre_alert_analysis_duration_seconds",
    "Alert LLM analysis latency in seconds",
    ["result"],  # success, error
    buckets=LATENCY_BUCKETS,
)

alert_analysis_total = Counter(
    "auto_sre_alert_analysis_total",
    "Total alert analyses performed",
    ["result"],
)

alert_deduped_total = Counter(
    "auto_sre_alert_deduped_total",
    "Total alerts deduplicated",
)

alert_webhook_received_total = Counter(
    "auto_sre_alert_webhook_received_total",
    "Total alerts received via webhook",
    ["status"],  # firing, resolved
)

alert_webhook_errors_total = Counter(
    "auto_sre_alert_webhook_errors_total",
    "Total webhook processing errors",
)

# ============================================================
# HTTP METRICS
# ============================================================
http_request_duration_seconds = Histogram(
    "auto_sre_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path", "status"],
    buckets=LATENCY_BUCKETS,
)

http_requests_total = Counter(
    "auto_sre_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)

http_auth_failures_total = Counter(
    "auto_sre_http_auth_failures_total",
    "Total Basic Auth failures",
)

# ============================================================
# SYSTEM METRICS
# ============================================================
up = Gauge(
    "auto_sre_up",
    "Service health (1=up, 0=down)",
)

info = Info(
    "auto_sre_info",
    "Auto SRE build information",
)