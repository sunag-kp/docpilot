"""Kafka adapter (Redpanda locally; any Kafka-compatible broker).

Producer: idempotent, ``acks=all``, keyed by document id so one document's events stay
ordered on one partition. Trace context travels in message headers (W3C traceparent), so a
trace started by the upload request continues in the worker.

Consumer: auto-commit is off. An offset is committed only *after* the handler finished,
which gives at-least-once delivery; the ingestion pipeline is idempotent so redelivery is
harmless. Partitions are processed concurrently; messages within a partition in order.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, ConsumerRecord, TopicPartition
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError
from opentelemetry import context as otel_context
from opentelemetry import propagate, trace

from docpilot.config import Settings
from docpilot.domain.events import DeadLetter, DocumentUploaded
from docpilot.observability.logging import get_logger
from docpilot.observability.metrics import CONSUMER_ERRORS, CONSUMER_LAG
from docpilot.services.ports import EventHandler

_log = get_logger(__name__)
_tracer = trace.get_tracer("docpilot.kafka")
Headers = list[tuple[str, bytes]]


def _connection_kwargs(settings: Settings) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "bootstrap_servers": settings.kafka_bootstrap_servers,
        "security_protocol": settings.kafka_security_protocol,
    }
    if settings.kafka_sasl_mechanism:
        kwargs["sasl_mechanism"] = settings.kafka_sasl_mechanism
        kwargs["sasl_plain_username"] = settings.kafka_sasl_username
        if settings.kafka_sasl_password is not None:
            kwargs["sasl_plain_password"] = settings.kafka_sasl_password.get_secret_value()
    return kwargs


def _inject_trace_headers() -> Headers:
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return [(key, value.encode()) for key, value in carrier.items()]


def _extract_trace_context(headers: Any) -> otel_context.Context:
    carrier = {key: value.decode() for key, value in (headers or ()) if isinstance(value, bytes)}
    return propagate.extract(carrier)


async def ensure_topics(settings: Settings) -> None:
    admin = AIOKafkaAdminClient(**_connection_kwargs(settings))
    await admin.start()
    try:
        existing = set(await admin.list_topics())
        wanted = [
            NewTopic(settings.kafka_topic_ingest, settings.kafka_topic_partitions, 1),
            NewTopic(settings.kafka_topic_dlq, 1, 1),
        ]
        missing = [topic for topic in wanted if topic.name not in existing]
        if missing:
            with contextlib.suppress(TopicAlreadyExistsError):  # another process won the race
                await admin.create_topics(missing)
            _log.info("kafka_topics_created", topics=[t.name for t in missing])
    finally:
        await admin.close()


class KafkaEventPublisher:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._producer = AIOKafkaProducer(
            **_connection_kwargs(settings),
            acks="all",
            enable_idempotence=True,
            linger_ms=5,
            request_timeout_ms=10_000,
        )

    async def start(self) -> None:
        await ensure_topics(self._settings)
        await self._producer.start()

    async def stop(self) -> None:
        await self._producer.stop()

    async def ping(self) -> None:
        await self._producer.partitions_for(self._settings.kafka_topic_ingest)

    async def publish_document_uploaded(self, event: DocumentUploaded) -> None:
        with _tracer.start_as_current_span(
            f"{self._settings.kafka_topic_ingest} publish", kind=trace.SpanKind.PRODUCER
        ):
            await self._producer.send_and_wait(
                self._settings.kafka_topic_ingest,
                value=event.to_json(),
                key=event.partition_key,
                headers=_inject_trace_headers(),
            )

    async def publish_dead_letter(self, letter: DeadLetter) -> None:
        await self._producer.send_and_wait(
            self._settings.kafka_topic_dlq,
            value=letter.to_json(),
            key=letter.event.partition_key,
            headers=_inject_trace_headers(),
        )

    async def publish_raw_dead_letter(self, value: bytes, error: str) -> None:
        await self._producer.send_and_wait(
            self._settings.kafka_topic_dlq,
            value=value,
            headers=[("error", error.encode()[:1000])],
        )


class KafkaEventConsumer:
    def __init__(
        self,
        settings: Settings,
        publisher: KafkaEventPublisher,
        *,
        concurrency: int,
        retry_backoff_seconds: float = 2.0,
    ) -> None:
        self._settings = settings
        self._publisher = publisher
        self._semaphore = asyncio.Semaphore(concurrency)
        self._retry_backoff = retry_backoff_seconds
        self._stopping = asyncio.Event()
        self._positions: dict[TopicPartition, int] = {}
        self._consumer = AIOKafkaConsumer(
            settings.kafka_topic_ingest,
            **_connection_kwargs(settings),
            group_id=settings.kafka_consumer_group,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            max_poll_records=concurrency * 2,
            # Generous: parsing+embedding a large PDF on CPU can take a while.
            max_poll_interval_ms=600_000,
            session_timeout_ms=30_000,
        )

    async def stop(self) -> None:
        self._stopping.set()

    async def run(self, handler: EventHandler) -> None:
        await self._consumer.start()
        _log.info("kafka_consumer_started", group=self._settings.kafka_consumer_group)
        try:
            while not self._stopping.is_set():
                batches = await self._consumer.getmany(timeout_ms=1000)
                if batches:
                    await asyncio.gather(
                        *(
                            self._process_partition(tp, records, handler)
                            for tp, records in batches.items()
                        )
                    )
                await self._record_lag()
        finally:
            await self._consumer.stop()
            _log.info("kafka_consumer_stopped")

    async def _record_lag(self) -> None:
        """Lag = partition high-water mark minus our next offset (what scaling keys off)."""
        for tp in self._consumer.assignment():
            highwater = self._consumer.highwater(tp)
            if highwater is None:
                continue
            position = self._positions.get(tp)
            if position is None:
                position = await self._consumer.position(tp)
            CONSUMER_LAG.labels(tp.topic, str(tp.partition)).set(max(0, highwater - position))

    async def _process_partition(
        self, tp: TopicPartition, records: list[ConsumerRecord[bytes, bytes]], handler: EventHandler
    ) -> None:
        for record in records:
            if self._stopping.is_set():
                self._consumer.seek(tp, record.offset)  # resume here after restart/rebalance
                return
            try:
                event = DocumentUploaded.from_json(record.value or b"")
            except (ValueError, KeyError) as exc:
                # Poison message: it will never parse, so park it and move on.
                _log.error("kafka_poison_message", partition=tp.partition, offset=record.offset)
                await self._publisher.publish_raw_dead_letter(record.value or b"", repr(exc))
                await self._consumer.commit({tp: record.offset + 1})
                self._positions[tp] = record.offset + 1
                continue

            parent = _extract_trace_context(record.headers)
            try:
                async with self._semaphore:
                    with _tracer.start_as_current_span(
                        f"{tp.topic} process", context=parent, kind=trace.SpanKind.CONSUMER
                    ):
                        await handler(event)
            except Exception:
                # Infrastructure is unhealthy (e.g. Postgres down). Don't commit: rewind
                # and back off, so the event is retried rather than lost.
                CONSUMER_ERRORS.inc()
                _log.exception("kafka_handler_failed", partition=tp.partition, offset=record.offset)
                self._consumer.seek(tp, record.offset)
                await asyncio.sleep(self._retry_backoff * random.uniform(0.5, 1.5))  # noqa: S311
                return
            await self._consumer.commit({tp: record.offset + 1})
            self._positions[tp] = record.offset + 1
