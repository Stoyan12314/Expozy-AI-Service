"""
RabbitMQ message queue service using aio-pika.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Callable, Optional
from uuid import UUID

import aio_pika
from aio_pika import Message, DeliveryMode, ExchangeType
from aio_pika.abc import AbstractChannel, AbstractConnection, AbstractQueue
import orjson

from shared.config import get_settings
from api.orchestrator.models.schemas import JobQueueMessage
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class MessageQueue:
    """RabbitMQ message queue client."""
    
    def __init__(self) -> None:
        self._connection: Optional[AbstractConnection] = None
        self._channel: Optional[AbstractChannel] = None
        self._queue: Optional[AbstractQueue] = None
        self._settings = get_settings()
    
    async def connect(self) -> None:
        """Establish connection to RabbitMQ."""
        if self._connection and not self._connection.is_closed:
            return
        
        logger.info("Connecting to RabbitMQ", url=str(self._settings.rabbitmq_url))
        
        self._connection = await aio_pika.connect_robust(
            str(self._settings.rabbitmq_url),
            timeout=30,
        )
        self._channel = await self._connection.channel()
        
        # Set prefetch for fair dispatch
        await self._channel.set_qos(prefetch_count=1)
        
        # Declare durable queue
        self._queue = await self._channel.declare_queue(
            self._settings.job_queue_name,
            durable=True,
            arguments={
                "x-message-ttl": 86400000,  # 24 hours
                "x-dead-letter-exchange": f"{self._settings.job_queue_name}.dlx",
            }
        )
        
        # Declare dead letter exchange and queue
        dlx = await self._channel.declare_exchange(
            f"{self._settings.job_queue_name}.dlx",
            ExchangeType.DIRECT,
            durable=True,
        )
        dlq = await self._channel.declare_queue(
            f"{self._settings.job_queue_name}.dlq",
            durable=True,
        )
        await dlq.bind(dlx, routing_key="")
        
        logger.info("RabbitMQ connected", queue=self._settings.job_queue_name)
    
    async def disconnect(self) -> None:
        """Close RabbitMQ connection."""
        if self._connection and not self._connection.is_closed:
            await self._connection.close()
            logger.info("RabbitMQ disconnected")
        self._connection = None
        self._channel = None
        self._queue = None
    
    async def publish_job(self, job_id: UUID, attempt: int = 1) -> None:
        """
        Publish a job to the queue.
        
        Args:
            job_id: UUID of the job to process
            attempt: Current attempt number (for retry tracking)
        """
        if not self._channel:
            await self.connect()
        
        message = JobQueueMessage(job_id=job_id, attempt=attempt)
        body = orjson.dumps(message.model_dump(mode="json"))
        
        await self._channel.default_exchange.publish(
            Message(
                body=body,
                delivery_mode=DeliveryMode.PERSISTENT,
                content_type="application/json",
            ),
            routing_key=self._settings.job_queue_name,
        )
        
        logger.debug("Job published to queue", job_id=str(job_id), attempt=attempt)
    
    async def publish_job_delayed(
        self, 
        job_id: UUID, 
        attempt: int, 
        delay_seconds: float
    ) -> None:
        """
        Publish a job with delay (for retries with backoff).
        Uses RabbitMQ message TTL + dead letter exchange pattern.
        """
        if not self._channel:
            await self.connect()
        
        message = JobQueueMessage(job_id=job_id, attempt=attempt)
        body = orjson.dumps(message.model_dump(mode="json"))
        
        # Create temporary delay queue
        delay_queue_name = f"{self._settings.job_queue_name}.delay.{int(delay_seconds * 1000)}"
        delay_queue = await self._channel.declare_queue(
            delay_queue_name,
            durable=True,
            arguments={
                "x-message-ttl": int(delay_seconds * 1000),
                "x-dead-letter-exchange": "",
                "x-dead-letter-routing-key": self._settings.job_queue_name,
                "x-expires": int(delay_seconds * 1000) + 60000,  # Queue expires after use
            }
        )
        
        await self._channel.default_exchange.publish(
            Message(
                body=body,
                delivery_mode=DeliveryMode.PERSISTENT,
                content_type="application/json",
            ),
            routing_key=delay_queue_name,
        )
        
        logger.debug(
            "Job published with delay", 
            job_id=str(job_id), 
            attempt=attempt,
            delay_seconds=delay_seconds
        )
    
    async def consume(
        self, 
        callback: Callable[[JobQueueMessage], asyncio.Future],
    ) -> None:
        """
        Start consuming messages from the queue.
        
        Args:
            callback: Async function to process each message
        """
        if not self._queue:
            await self.connect()
        
        logger.info("Starting queue consumer", queue=self._settings.job_queue_name)
        
        async with self._queue.iterator() as queue_iter:
            async for message in queue_iter:
                async with message.process(requeue=False):
                    try:
                        data = orjson.loads(message.body)
                        job_message = JobQueueMessage(**data)
                        
                        logger.debug(
                            "Processing message",
                            job_id=str(job_message.job_id),
                            attempt=job_message.attempt
                        )
                        
                        await callback(job_message)
                        
                    except Exception as e:
                        logger.error(
                            "Message processing failed",
                            error=str(e),
                            body=message.body.decode()[:200]
                        )
                        # Message will be dead-lettered


@asynccontextmanager
async def get_message_queue() -> AsyncGenerator[MessageQueue, None]:
    """Context manager for message queue."""
    mq = MessageQueue()
    try:
        await mq.connect()
        yield mq
    finally:
        await mq.disconnect()


# Global singleton for API service
_mq_instance: Optional[MessageQueue] = None


async def get_mq() -> MessageQueue:
    """Get global message queue instance (for FastAPI)."""
    global _mq_instance
    if _mq_instance is None:
        _mq_instance = MessageQueue()
        await _mq_instance.connect()
    return _mq_instance


async def close_mq() -> None:
    """Close global message queue instance."""
    global _mq_instance
    if _mq_instance:
        await _mq_instance.disconnect()
        _mq_instance = None
