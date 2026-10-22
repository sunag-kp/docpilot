"""Load test for the question-answering API.

Setup (once): a fresh tenant indexes the evaluation corpus (8 docs, PDF/DOCX/MD).
Traffic mix per user:
  * 3x  POST /v1/query        cache MISS — a unique suffix defeats the answer cache, so the
                              full path runs: embed query, BM25 + pgvector, RRF, answer.
  * 1x  POST /v1/query        cache HIT  — one of a few fixed questions.
  * 1x  POST /v1/query/stream SSE; also records time-to-first-token as "stream:first-token".

    locust -f benchmarks/locustfile_query.py --host http://localhost:8000 \\
        --headless -u 32 -r 8 -t 2m --csv benchmarks/results/<run>/query
"""

from __future__ import annotations

import itertools
import random
import time

from locust import HttpUser, between, events, task
from locust.env import Environment

from benchmarks.common import create_tenant, load_questions

QUESTIONS = load_questions()
HOT = QUESTIONS[:5]
_counter = itertools.count()
_headers: dict[str, str] = {}


@events.test_start.add_listener
def setup(environment: Environment, **_: object) -> None:
    assert environment.host, "--host is required"
    _headers.update(create_tenant(environment.host, upload_corpus=True))


class QueryUser(HttpUser):
    wait_time = between(0.1, 0.5)

    @task(3)
    def ask_uncached(self) -> None:
        question = f"{random.choice(QUESTIONS)} (load {next(_counter)})"
        self.client.post(
            "/v1/query", json={"question": question}, headers=_headers, name="query:miss"
        )

    @task(1)
    def ask_cached(self) -> None:
        self.client.post(
            "/v1/query", json={"question": random.choice(HOT)}, headers=_headers, name="query:hit"
        )

    @task(1)
    def ask_streaming(self) -> None:
        question = f"{random.choice(QUESTIONS)} (stream {next(_counter)})"
        started = time.perf_counter()
        first_token: float | None = None
        with self.client.post(
            "/v1/query/stream",
            json={"question": question},
            headers=_headers,
            name="query:stream (headers)",
            stream=True,
            catch_response=True,
        ) as response:
            for line in response.iter_lines():
                if first_token is None and line.startswith(b"event: token"):
                    first_token = time.perf_counter()
                if line.startswith(b"event: error"):
                    response.failure("stream error event")
            finished = time.perf_counter()
            for name, at in (("stream:first-token", first_token), ("stream:complete", finished)):
                if at is not None:
                    self.environment.events.request.fire(
                        request_type="SSE",
                        name=name,
                        response_time=(at - started) * 1000,
                        response_length=0,
                        exception=None,
                        context={},
                    )
