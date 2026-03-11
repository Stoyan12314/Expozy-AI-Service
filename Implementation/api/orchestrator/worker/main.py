from __future__ import annotations

import asyncio
import signal
import sys

from shared.utils import setup_logging, get_logger

# This should be the file where run_worker() + shutdown_event live
from api.orchestrator.worker.service.worker_service import run_worker, shutdown_event

setup_logging()
logger = get_logger(__name__)


def handle_signals() -> None:
    def _handler(signum, frame):
        logger.info("Received signal", signal=signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def main() -> None:
    handle_signals()
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("Worker interrupted")
    except Exception as e:
        logger.error("Worker crashed", error=str(e), exc_info=e)
        sys.exit(1)


if __name__ == "__main__":
    main()
