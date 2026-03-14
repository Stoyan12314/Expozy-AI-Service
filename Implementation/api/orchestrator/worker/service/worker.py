"""
Worker
Top-level entrypoint. Owns the RabbitMQ consume loop, signal handling, and retry scheduling.
"""

import asyncio
import signal
import sys

from api.orchestrator.db.service.queue import get_message_queue

from .job_processor import JobProcessor

from shared.config import get_settings
from shared.utils import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)
settings = get_settings()


class Worker:
    def __init__(self):
        self.processor = JobProcessor()
        self.shutdown_event = asyncio.Event()

    # ── Signal handling ───────────────────────────────────────────────────────

    def setup_signals(self):
        def signal_handler(signum, frame):
            logger.info("Received shutdown signal", signal=signum)
            self.shutdown_event.set()

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

    # ── Message handling ──────────────────────────────────────────────────────

    async def handle_message(self, message):
        completed = await self.processor.process(message.job_id, message.attempt)

        if not completed and message.attempt < settings.max_retries:
            delay = min(
                settings.retry_base_delay * (2 ** (message.attempt - 1)),
                settings.retry_max_delay,
            )
            logger.info(
                "Scheduling retry",
                job_id=str(message.job_id),
                next_attempt=message.attempt + 1,
                delay=delay,
            )
            async with get_message_queue() as mq:
                await mq.publish_job_delayed(message.job_id, message.attempt + 1, delay)

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self):
        logger.info("Worker starting (RAG-powered, parallel HTML generation)")

        async with get_message_queue() as mq:
            consumer_task = asyncio.create_task(mq.consume(self.handle_message))
            await self.shutdown_event.wait()

            logger.info("Shutting down worker")
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass

        logger.info("Worker stopped")


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main():
    worker = Worker()
    worker.setup_signals()
    try:
        asyncio.run(worker.run())
    except KeyboardInterrupt:
        logger.info("Worker interrupted")
    except Exception as e:
        logger.error("Worker crashed", error=str(e), exc_info=e)
        sys.exit(1)


if __name__ == "__main__":
    main()