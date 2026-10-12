from __future__ import annotations

from app.application.ports import TaskScheduler
from app.persistence.postgres.scheduler import PostgresTaskScheduler


def test_postgres_scheduler_satisfies_scheduler_protocol() -> None:
    assert isinstance(PostgresTaskScheduler(object()), TaskScheduler)


def test_scheduler_uses_skip_locked_and_leases() -> None:
    import inspect

    source = inspect.getsource(PostgresTaskScheduler).lower()
    assert "for update of t skip locked" in source
    assert "worker_leases" in source
    assert "leased_until" in source
    assert "heartbeat_at" in source
    claim_source = inspect.getsource(PostgresTaskScheduler.claim).lower()
    assert "delete from worker_leases" not in claim_source
