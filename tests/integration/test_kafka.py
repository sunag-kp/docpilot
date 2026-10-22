from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from docpilot.config import Settings
from docpilot.domain.events import DeadLetter, DocumentUploaded
from docpilot.domain.models import DocumentKind
from docpilot.infra.bus.kafka import KafkaEventConsumer, KafkaEventPublisher
from tests.integration.conftest import RUN_ID, integration_settings


def event() -> DocumentUploaded:
    return DocumentUploaded(uuid4(), uuid4(), "k", DocumentKind.TEXT, "a.txt")


def isolated(suffix: str) -> Settings:
    return integration_settings(
        kafka_topic_ingest=f"test.{RUN_ID}.{suffix}",
        kafka_topic_dlq=f"test.{RUN_ID}.{suffix}.dlq",
        kafka_consumer_group=f"test-{RUN_ID}-{suffix}",
    )


async def read_dlq(settings: Settings, count: int) -> list[bytes]:
    consumer = AIOKafkaConsumer(
        settings.kafka_topic_dlq,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        auto_offset_reset="earliest",
        group_id=f"dlq-reader-{uuid4().hex}",
    )
    await consumer.start()
    try:
        values: list[bytes] = []
        while len(values) < count:
            batch = await consumer.getmany(timeout_ms=5000)
            if not batch:
                break
            values.extend(r.value for records in batch.values() for r in records if r.value)
        return values
    finally:
        await consumer.stop()


async def run_until(consumer: KafkaEventConsumer, handler, done: asyncio.Event) -> None:  # type: ignore[no-untyped-def]
    task = asyncio.create_task(consumer.run(handler))
    try:
        await asyncio.wait_for(done.wait(), timeout=30)
    finally:
        await consumer.stop()
        await task


async def test_publish_consume_round_trip_with_trace_propagation() -> None:
    settings = isolated("roundtrip")
    provider = TracerProvider()
    tracer = provider.get_tracer("test")
    publisher = KafkaEventPublisher(settings)
    await publisher.start()
    try:
        sent = [event() for _ in range(5)]
        with tracer.start_as_current_span("upload-request") as span:
            producer_trace_id = span.get_span_context().trace_id
            for item in sent:
                await publisher.publish_document_uploaded(item)
        await publisher.ping()

        received: list[DocumentUploaded] = []
        trace_ids: set[int] = set()
        done = asyncio.Event()

        async def handler(e: DocumentUploaded) -> None:
            received.append(e)
            trace_ids.add(trace.get_current_span().get_span_context().trace_id)
            if len(received) == len(sent):
                done.set()

        await run_until(KafkaEventConsumer(settings, publisher, concurrency=2), handler, done)
        assert {e.event_id for e in received} == {e.event_id for e in sent}
        # The consumer span continues the producer's trace (W3C traceparent header).
        assert trace_ids == {producer_trace_id}
    finally:
        await publisher.stop()


async def test_failed_handler_is_redelivered_not_lost() -> None:
    settings = isolated("redelivery")
    publisher = KafkaEventPublisher(settings)
    await publisher.start()
    try:
        item = event()
        await publisher.publish_document_uploaded(item)
        calls = 0
        done = asyncio.Event()

        async def flaky(e: DocumentUploaded) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ConnectionError("postgres is down")
            done.set()

        consumer = KafkaEventConsumer(settings, publisher, concurrency=1, retry_backoff_seconds=0.1)
        await run_until(consumer, flaky, done)
        assert calls == 2
    finally:
        await publisher.stop()


async def test_poison_message_and_dead_letters_land_on_dlq() -> None:
    settings = isolated("poison")
    publisher = KafkaEventPublisher(settings)
    await publisher.start()
    producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap_servers)
    await producer.start()
    try:
        await producer.send_and_wait(settings.kafka_topic_ingest, b"{not json")
        good = event()
        await publisher.publish_document_uploaded(good)
        await publisher.publish_dead_letter(DeadLetter(good, "PermanentIngestionError", "bad", 1))

        done = asyncio.Event()

        async def handler(e: DocumentUploaded) -> None:
            assert e.event_id == good.event_id  # the poison pill was skipped, not retried forever
            done.set()

        await run_until(KafkaEventConsumer(settings, publisher, concurrency=1), handler, done)
        dlq = await read_dlq(settings, 2)
        assert b"{not json" in dlq
        assert any(
            json.loads(v).get("error_type") == "PermanentIngestionError"
            for v in dlq
            if v.startswith(b'{"')
        )
    finally:
        await producer.stop()
        await publisher.stop()
