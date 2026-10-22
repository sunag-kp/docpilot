# Load test — 2026-10-22-sandbox

Commit `f45d235` · 4 vCPU (Intel(R) Xeon(R) Processor @ 2.80GHz) · 15.7 GB RAM · API, 2 workers, Postgres, Redis, Redpanda, MinIO, Prometheus, Grafana, Jaeger and Locust all share this one machine.

Configuration: local MiniLM embeddings (ONNX, CPU) with a 10k-entry embedding LRU cache, BM25 + pgvector hybrid retrieval, built-in extractive answerer (no LLM), 1 API process, 2 ingestion workers, rate limit raised for the test, 5% trace sampling.

## Query API — 32 concurrent users, 2 minutes

`query:miss` defeats the answer cache (full retrieval + answer); `query:hit` is served from Redis; `stream:*` time the SSE endpoint to its first token and to the final event.

| Endpoint | Requests | Failures | Req/s | p50 (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|---|---|---|
| query:hit | 741 | 0 | 6.2 | 320 | 550 | 630 |
| query:miss | 2325 | 0 | 19.5 | 650 | 1000 | 1200 |
| query:stream (headers) | 777 | 0 | 6.5 | 280 | 510 | 610 |
| stream:complete | 777 | 0 | 6.5 | 950 | 1400 | 1800 |
| stream:first-token | 777 | 0 | 6.5 | 610 | 980 | 1200 |

Throughput: **32.2 HTTP requests/s** in total (the `stream:*` rows time the same streams as `query:stream` and are not counted again).

Where the time goes (from the services' Prometheus histograms, 2m window; includes time waiting for the CPU / event loop):

| Stage | p50 (ms) | p95 (ms) |
|---|---|---|
| embed_query | 53.1 | 218.9 |
| keyword_search | 138.4 | 248.9 |
| vector_search | 148.9 | 375.2 |
| generate | 77.8 | 448.8 |

## Query API — 1 user (unloaded latency), 1 minute

| Endpoint | Requests | Failures | Req/s | p50 (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|---|---|---|
| query:hit | 39 | 0 | 0.7 | 13 | 28 | 29 |
| query:miss | 103 | 0 | 1.7 | 38 | 59 | 62 |
| query:stream (headers) | 27 | 0 | 0.5 | 11 | 16 | 20 |
| stream:complete | 27 | 0 | 0.5 | 43 | 61 | 65 |
| stream:first-token | 27 | 0 | 0.5 | 39 | 56 | 64 |

## Ingestion — 8 uploaders for 1 minute, then drain

**426.4 documents/minute** end to end (upload → parsed, chunked, embedded, indexed): 1492 documents ({'markdown': 498, 'pdf': 497, 'docx': 497}), 5968 chunks in 209.95 s; 0 failed. Upload → ready latency p50 91.051 s, p95 148.402 s.

| Endpoint | Requests | Failures | Req/s | p50 (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|---|---|---|
| upload | 1490 | 0 | 24.8 | 150 | 300 | 480 |

Per-document stages (from the services' Prometheus histograms, 6m window; includes time waiting for the CPU / event loop):

| Stage | p50 (ms) | p95 (ms) |
|---|---|---|
| parse | 9.7 | 66.6 |
| chunk | 0.6 | 4.2 |
| embed | 195.0 | 528.1 |
| store | 35.5 | 98.1 |
| fetch | 9.2 | 46.0 |

Peak Kafka consumer lag: 833 messages.
