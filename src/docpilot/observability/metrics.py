"""Prometheus metrics. Names follow the `<namespace>_<subsystem>_<name>_<unit>` convention."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)
_STAGE_BUCKETS = (0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120)

# --- HTTP ------------------------------------------------------------------------------
HTTP_REQUESTS = Counter(
    "docpilot_http_requests_total", "HTTP requests", ["method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "docpilot_http_request_duration_seconds",
    "HTTP request latency (time to response headers for streams)",
    ["method", "route"],
    buckets=_LATENCY_BUCKETS,
)
HTTP_IN_FLIGHT = Gauge("docpilot_http_requests_in_flight", "In-flight HTTP requests")
RATE_LIMITED = Counter("docpilot_rate_limited_total", "Requests rejected by the rate limiter")
RATE_LIMITER_ERRORS = Counter(
    "docpilot_rate_limiter_errors_total", "Rate limiter backend errors (failed open)"
)

# --- ingestion -------------------------------------------------------------------------
DOCUMENTS_UPLOADED = Counter(
    "docpilot_documents_uploaded_total", "Accepted uploads", ["kind", "duplicate"]
)
INGEST_OUTCOMES = Counter("docpilot_ingest_documents_total", "Ingestion outcomes", ["outcome"])
INGEST_STAGE_SECONDS = Histogram(
    "docpilot_ingest_stage_duration_seconds",
    "Duration of each ingestion stage",
    ["stage"],
    buckets=_STAGE_BUCKETS,
)
INGEST_END_TO_END_SECONDS = Histogram(
    "docpilot_ingest_end_to_end_seconds",
    "Upload accepted -> document ready (includes queueing)",
    buckets=_STAGE_BUCKETS,
)
INGEST_RETRIES = Counter("docpilot_ingest_retries_total", "Ingestion attempts retried")
INGEST_DEAD_LETTERS = Counter("docpilot_ingest_dead_letters_total", "Events sent to the DLQ")
INGEST_CHUNKS = Counter("docpilot_ingest_chunks_total", "Chunks written")
INGEST_REPUBLISHED = Counter(
    "docpilot_ingest_republished_total", "Stale documents re-published by the sweeper"
)
CONSUMER_ERRORS = Counter(
    "docpilot_consumer_errors_total", "Unhandled errors in the event consumer loop"
)

# --- query -----------------------------------------------------------------------------
QUERY_STAGE_SECONDS = Histogram(
    "docpilot_query_stage_duration_seconds",
    "Duration of each query stage",
    ["stage"],
    buckets=_LATENCY_BUCKETS,
)
QUERY_TIME_TO_FIRST_TOKEN = Histogram(
    "docpilot_query_time_to_first_token_seconds",
    "Time from request to first streamed answer token",
    buckets=_LATENCY_BUCKETS,
)
QUERIES = Counter("docpilot_queries_total", "Answered queries", ["outcome", "cached"])
CACHE_LOOKUPS = Counter("docpilot_cache_lookups_total", "Answer cache lookups", ["result"])
LLM_TOKENS = Counter("docpilot_llm_output_chunks_total", "Streamed LLM output chunks", ["provider"])
