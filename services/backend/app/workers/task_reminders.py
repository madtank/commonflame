"""Task reminder worker entrypoint.

Runs reminder polling as a dedicated process/container, not from FastAPI app
startup.  This keeps API replicas from duplicating reminder delivery and gives
Docker Compose/ECS an explicit background worker command to scale/manage.

Usage:
    python -m app.workers.task_reminders
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path

# Match other worker entrypoints when invoked as ``python -m`` from varied CWDs.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services.task_reminder_delivery import TaskReminderDeliveryService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("task_reminder_worker")


async def main() -> None:
    """Run task reminder delivery until SIGTERM/SIGINT."""
    service = TaskReminderDeliveryService()
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def request_shutdown() -> None:
        logger.info("Received shutdown signal")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, request_shutdown)
        except NotImplementedError:
            # Windows/event-loop fallback; harmless in Linux containers.
            signal.signal(sig, lambda *_: request_shutdown())

    logger.info(
        "Starting task reminder worker poll_seconds=%s batch_size=%s",
        service.poll_seconds,
        service.batch_size,
    )
    service.start()
    try:
        await stop_event.wait()
    finally:
        await service.stop()
        logger.info("Task reminder worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
