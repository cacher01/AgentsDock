from __future__ import annotations

import asyncio
import os
import signal
import socket
import uuid

from app.application.platform_runner import PlatformTaskRunner
from app.application.worker import TaskWorker
from app.core.config import Settings
from app.persistence.provider import create_platform_repository_provider


async def run_worker() -> None:
    settings = Settings.from_env_file()
    provider = create_platform_repository_provider(settings)
    if not provider.enabled:
        raise RuntimeError("PLATFORM_REPOSITORY_BACKEND=postgres is required")
    repositories = provider.get()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop(*_args) -> None:
        loop.call_soon_threadsafe(stop.set)

    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, request_stop)

    worker_id = os.getenv(
        "PLATFORM_WORKER_ID",
        f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}",
    )
    worker = TaskWorker(
        scheduler=repositories.scheduler,
        runner=PlatformTaskRunner(
            settings=settings,
            repositories=repositories,
        ),
        worker_id=worker_id,
        poll_interval=float(os.getenv("PLATFORM_WORKER_POLL_SECONDS", "1")),
        heartbeat_interval=float(os.getenv("PLATFORM_WORKER_HEARTBEAT_SECONDS", "20")),
    )
    try:
        await worker.run_forever(stop)
    finally:
        await provider.close()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
