"""
RabbitMQ queue wrapper (simple, but includes):
- Main queue with TTL
- DLX/DLQ for failed messages
- Delay queues (TTL -> dead-letter -> main queue) for retries/backoff
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator, Awaitable, Callable, Dict, Optional
from uuid import UUID

import aio_pika
from aio_pika import DeliveryMode, ExchangeType, Message
import orjson

from shared.config import get_settings
from api.orchestrator.models.dto import JobQueueMessage
from shared.utils.logging import get_logger

logger = get_logger(__name__)  # create a logger for this module (for debugging/info logs)


class MessageQueue:
    def __init__(self) -> None:
        self.s = get_settings()  # load app settings (rabbitmq_url, job_queue_name, etc.)

        self._conn: Optional[aio_pika.RobustConnection] = None  # RabbitMQ connection (network)
        self._ch: Optional[aio_pika.abc.AbstractChannel] = None  # RabbitMQ channel (logical session)
        self._main: Optional[aio_pika.abc.AbstractQueue] = None  # main job queue object

        self._delay: Dict[int, aio_pika.abc.AbstractQueue] = {}  # cache delay queues by delay_ms

    async def connect(self) -> None:
        """Connect and declare queues/exchanges once."""
        # If we already have a working connection + channel + main queue, do nothing.
        if self._conn and not self._conn.is_closed and self._ch and self._main:
            return

        # Log that we are connecting (useful for debugging deployments)
        logger.info("Connecting to RabbitMQ", url=str(self.s.rabbitmq_url))

        # Connect to RabbitMQ with auto-reconnect behavior 
        self._conn = await aio_pika.connect_robust(str(self.s.rabbitmq_url))

        # Open a channel over the connection (AMQP concept)
        self._ch = await self._conn.channel()

        # Prefetch=1 means: give this consumer only 1 unacked message at a time (fair dispatch)
        await self._ch.set_qos(prefetch_count=1)

        # Ensure the main queue, DLX, and DLQ exist (idempotent declarations)
        await self._declare_main_and_dlq()

        # Log that we are ready
        logger.info("RabbitMQ ready", queue=self.s.job_queue_name)

    async def disconnect(self) -> None:
        # If connected, close the connection (also closes channels)
        if self._conn and not self._conn.is_closed:
            await self._conn.close()

        # Reset everything so this instance can reconnect later
        self._conn = None
        self._ch = None
        self._main = None

        # Clear delay queue cache (those queue objects are tied to the old channel)
        self._delay.clear()

    async def _declare_main_and_dlq(self) -> None:


         # DLX = Dead Letter Exchange:
        #   a special exchange RabbitMQ uses to reroute "dead" messages.
        #   A message becomes "dead" if it is rejected/nacked with requeue=False,
        #   or if it expires (TTL), or if the queue hits limits.
        
        # DLQ = Dead Letter Queue:
        #   a normal queue that receives those dead messages (via the DLX),
        #   so you can inspect failures, debug, and optionally replay them later.

        """Main queue + DLX/DLQ."""
        # We cannot declare anything without a channel
        assert self._ch is not None

        q = self.s.job_queue_name          # main queue name (e.g., "jobs")
        dlx_name = f"{q}.dlx"              # dead-letter exchange name (e.g., "jobs.dlx")
        dlq_name = f"{q}.dlq"              # dead-letter queue name (e.g., "jobs.dlq")

        # 1) Create a dead-letter exchange (DLX).
        # DIRECT exchange means routing is based on exact routing_key match.
        dlx = await self._ch.declare_exchange(dlx_name, ExchangeType.DIRECT, durable=True)

        # 2) Create a dead-letter queue (DLQ) that will store failed/expired messages.
        dlq = await self._ch.declare_queue(dlq_name, durable=True)

        # 3) Bind the DLQ to the DLX with routing_key=q.
        # So when a message is dead-lettered to DLX with routing_key=q, it ends up in DLQ.
        await dlq.bind(dlx, routing_key=q)

        # 4) Declare the main queue, and configure:
        # - TTL for messages (how long messages can sit before expiring)
        # - where to send expired/rejected messages (DLX)
        # - what routing key to use when dead-lettering (q)
        self._main = await self._ch.declare_queue(
            q,
            durable=True,  # survive broker restart
            arguments={
                "x-message-ttl": 86_400_000,          # messages expire after 24 hours (milliseconds)
                "x-dead-letter-exchange": dlx_name,   # send dead messages to this exchange
                "x-dead-letter-routing-key": q,       # use routing key q so DLQ binding catches it
            },
        )

    async def _publish(self, routing_key: str, payload: JobQueueMessage) -> None:
        """Serialize + publish one message."""
        # Ensure connection/channel/queues exist before publishing
        await self.connect()
        assert self._ch is not None

        # Convert the Pydantic DTO to JSON bytes
        body = orjson.dumps(payload.model_dump(mode="json"))

        # Build the RabbitMQ message object
        msg = Message(
            body=body,                                # message body is bytes
            delivery_mode=DeliveryMode.PERSISTENT,     # try to persist to disk (works with durable queues)
            content_type="application/json",           # metadata (helps debugging)
        )

        # Publish to the default exchange.
        # With default exchange, routing_key == queue name delivers directly to that queue.
        await self._ch.default_exchange.publish(msg, routing_key=routing_key)

    async def publish_job(self, job_id: UUID, attempt: int = 1) -> None:
        """Send job straight to main queue."""
        # Create DTO and publish it to the main queue
        await self._publish(
            self.s.job_queue_name,
            JobQueueMessage(job_id=job_id, attempt=attempt),
        )

    async def publish_job_delayed(self, job_id: UUID, attempt: int, delay_seconds: float) -> None:
        """
        Send job to a delay queue.
        Delay queue has TTL; when TTL expires, it dead-letters into the main queue.
        """
        # Ensure connected (we'll declare/reuse a delay queue)
        await self.connect()
        assert self._ch is not None

        # Convert seconds to milliseconds (RabbitMQ TTL uses ms)
        delay_ms = max(0, int(delay_seconds * 1000))

        # Get or create a delay queue that waits delay_ms before forwarding to main queue
        delay_queue = await self._get_delay_queue(delay_ms)

        # Publish message into the delay queue (not the main queue)
        await self._publish(
            delay_queue.name,
            JobQueueMessage(job_id=job_id, attempt=attempt),
        )

    async def _get_delay_queue(self, delay_ms: int) -> aio_pika.abc.AbstractQueue:
        """Create (or reuse) a delay queue for this delay_ms."""
        # Need channel to declare queues
        assert self._ch is not None

        # If we already created this delay queue, reuse it
        if delay_ms in self._delay:
            return self._delay[delay_ms]

        main = self.s.job_queue_name             # main queue name
        name = f"{main}.delay.{delay_ms}"        # delay queue name (unique per delay)

        # Declare a delay queue with:
        # - x-message-ttl: messages live here for delay_ms
        # - when TTL expires, message is dead-lettered to the default exchange ("")
        # - default exchange uses routing key == queue name, so it goes to the main queue
        # - x-expires: delete the delay queue itself after it's unused for a bit
        q = await self._ch.declare_queue(
            name,
            durable=True,
            arguments={
                "x-message-ttl": delay_ms,            # message waits here for delay_ms
                "x-dead-letter-exchange": "",         # default exchange
                "x-dead-letter-routing-key": main,    # after TTL -> route into main queue
                "x-expires": delay_ms + 60_000,       # delete this delay queue later to avoid clutter
            },
        )

        # Cache it so we don't redeclare next time
        self._delay[delay_ms] = q
        return q

    async def consume(self, handler: Callable[[JobQueueMessage], Awaitable[None]]) -> None:
        """
        Consume forever.
        - success -> ACK
        - exception -> reject (requeue=False) -> goes to DLQ via DLX
        """
        # Ensure queue exists
        await self.connect()
        assert self._main is not None

        logger.info("Consuming jobs", queue=self.s.job_queue_name)

        # Iterator yields messages as they arrive
        async with self._main.iterator() as it:
            async for incoming in it:
                # process() context manager auto-ACKs on success.
                # If an exception happens, it rejects the message.
                # requeue=False means it will NOT go back to the main queue.
                # Because the main queue has DLX configured, rejected messages go to DLQ.
                async with incoming.process(requeue=False):
                    # Parse JSON bytes into dict
                    data = orjson.loads(incoming.body)

                    # Build the DTO object (validates fields/types)
                    job_msg = JobQueueMessage(**data)

                    # Call your worker logic
                    await handler(job_msg)


@asynccontextmanager
async def get_message_queue() -> AsyncGenerator[MessageQueue, None]:
    # Create a new queue client for a "with" block
    mq = MessageQueue()
    try:
        # Connect before yielding
        await mq.connect()
        yield mq
    finally:
        # Always disconnect on exit (even on exception)
        await mq.disconnect()


# Optional: global singleton (nice for FastAPI dependency)
_mq: Optional[MessageQueue] = None  # holds one shared instance for the whole API process


async def get_mq() -> MessageQueue:
    # Return the shared instance (create it on first call)
    global _mq
    if _mq is None:
        _mq = MessageQueue()
        await _mq.connect()
    return _mq


async def close_mq() -> None:
    # Close the shared instance on app shutdown
    global _mq
    if _mq:
        await _mq.disconnect()
        _mq = None
