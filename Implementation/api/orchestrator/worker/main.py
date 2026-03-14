from __future__ import annotations

import asyncio
import signal
import sys

from shared.utils import setup_logging, get_logger
from api.orchestrator.worker.service.worker import Worker

setup_logging()
logger = get_logger(__name__)

worker = Worker()


def handle_signals() -> None:
    def _handler(signum, frame):
        logger.info("Received signal", signal=signum)
        worker.shutdown_event.set()

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def main() -> None:
    handle_signals()
    try:
        asyncio.run(worker.run())
    except KeyboardInterrupt:
        logger.info("Worker interrupted")
    except Exception as e:
        logger.error("Worker crashed", error=str(e), exc_info=e)
        sys.exit(1)


if __name__ == "__main__":
    main()