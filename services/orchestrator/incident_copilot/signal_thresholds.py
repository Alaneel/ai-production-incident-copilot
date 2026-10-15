"""Fault-lab thresholds, not general production SLOs or calibrated causality."""

THRESHOLDS = {
    "request_latency_p95": 1000.0,
    "request_error_rate": 0.05,
    "db_pool_active_ratio": 0.9,
    "cpu_utilization": 0.8,
    "kafka_consumer_lag": 100.0,
    "retry_rate": 1.0,
    "cache_miss_rate": 1.0,
}

# Missing units remain compatible with explicitly bounded legacy fixtures;
# an explicit different unit must never be interpreted as this metric.
METRIC_UNITS = {
    "request_latency_p95": "milliseconds",
    "request_error_rate": "ratio",
    "db_pool_active_ratio": "ratio",
    "cpu_utilization": "ratio",
    "memory_working_set": "bytes",
    "kafka_consumer_lag": "messages",
    "retry_rate": "retries_per_second",
    "cache_miss_rate": "misses_per_second",
}
RATIO_METRICS = {"request_error_rate", "db_pool_active_ratio", "cpu_utilization"}
