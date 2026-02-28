import logging
import time

from worker.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("contextforge-worker")


def run() -> None:
    logger.info("worker started", extra={"app_env": settings.app_env})
    while True:
        logger.info("worker heartbeat", extra={"poll_seconds": settings.worker_poll_seconds})
        time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    run()
