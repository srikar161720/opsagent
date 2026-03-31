"""Kafka log consumer using confluent-kafka.

Consumes structured log entries from the ``opsagent-logs`` Kafka topic
and yields them as Python dicts for downstream processing by the
LogParser and WindowAggregator.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

from confluent_kafka import Consumer

logger = logging.getLogger(__name__)


class LogConsumer:
    """Consume log entries from a Kafka topic using confluent-kafka."""

    def __init__(
        self,
        bootstrap_servers: str = "localhost:9092",
        topic: str = "opsagent-logs",
        group_id: str = "opsagent-consumer",
    ) -> None:
        self.topic = topic
        self.consumer = Consumer(
            {
                "bootstrap.servers": bootstrap_servers,
                "group.id": group_id,
                "auto.offset.reset": "earliest",
                "enable.auto.commit": True,
            }
        )
        self.consumer.subscribe([topic])

    def consume(self) -> Iterator[dict[str, Any]]:
        """Yield log entries from Kafka indefinitely.

        Each yielded dict contains:
            timestamp:  int   — Unix epoch milliseconds (Kafka message timestamp)
            partition:  int   — Kafka partition number
            offset:     int   — Message offset within partition
            value:      dict  — Deserialized JSON log payload
        """
        while True:
            msg = self.consumer.poll(1.0)

            if msg is None:
                continue
            if msg.error():
                logger.warning("Consumer error: %s", msg.error())
                continue

            try:
                raw = msg.value()
                if raw is None:
                    continue
                value = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.warning(
                    "Failed to decode message at offset %d: %s",
                    msg.offset(),
                    exc,
                )
                continue

            # msg.timestamp() returns (timestamp_type, timestamp_ms) tuple
            _, timestamp_ms = msg.timestamp()

            yield {
                "timestamp": timestamp_ms,
                "partition": msg.partition(),
                "offset": msg.offset(),
                "value": value,
            }

    def close(self) -> None:
        """Commit final offsets and close the consumer."""
        self.consumer.close()
