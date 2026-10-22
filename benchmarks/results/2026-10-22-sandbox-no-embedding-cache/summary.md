# Load test — 2026-10-22-sandbox-no-embedding-cache

Commit `f45d235` · 4 vCPU (Intel(R) Xeon(R) Processor @ 2.80GHz) · 15.7 GB RAM · API, 2 workers, Postgres, Redis, Redpanda, MinIO, Prometheus, Grafana, Jaeger and Locust all share this one machine.

Configuration: identical to 2026-10-22-sandbox except EMBEDDING_CACHE_SIZE=0 (no embedding cache): the A/B baseline, run back to back on the same machine.

## Query API — 32 concurrent users, 2 minutes

`query:miss` defeats the answer cache (full retrieval + answer); `query:hit` is served from Redis; `stream:*` time the SSE endpoint to its first token and to the final event.

| Endpoint | Requests | Failures | Req/s | p50 (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|---|---|---|
| query:hit | 262 | 0 | 2.2 | 50 | 150 | 3800 |
| query:miss | 734 | 0 | 6.2 | 3300 | 3800 | 4000 |
| query:stream (headers) | 254 | 0 | 2.1 | 45 | 150 | 290 |
| stream:complete | 254 | 0 | 2.1 | 3400 | 3900 | 4100 |
| stream:first-token | 254 | 0 | 2.1 | 3400 | 3900 | 4000 |

Where the time goes (from the services' Prometheus histograms, 2m window; includes time waiting for the CPU / event loop):

| Stage | p50 (ms) | p95 (ms) |
|---|---|---|
| embed_query | 1722.1 | 2425.1 |
| keyword_search | 17.4 | 54.9 |
| vector_search | 24.7 | 91.1 |
| generate | 1752.1 | 2441.9 |
