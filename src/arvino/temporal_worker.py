"""Temporal driver for runs (optional extra: ``pip install arvino[temporal]``).

Temporal supplies durable *driving* (timers, retries of the advance activity, survival of
process restarts); the SQL ledger remains the source of truth (ADR 0003). One workflow
per run loops: ``advance_run`` activity -> wait for a ``poke`` signal or the next deadline.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Coroutine
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

from arvino.engine import Engine

log = logging.getLogger(__name__)

WORKFLOW_NAME = "ArvinoRunDriver"
ACTIVITY_NAME = "advance_run"
MAX_IDLE_POLL_SECONDS = 300
CONTINUE_AS_NEW_AFTER = 200
TERMINAL = {"succeeded", "failed", "cancelled"}


@dataclass
class AdvanceResult:
    status: str
    wake_in_seconds: float | None = None


def workflow_id(run_id: str) -> str:
    return f"arvino-run-{run_id}"


class Activities:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @activity.defn(name=ACTIVITY_NAME)
    def advance_run(self, run_id: str) -> AdvanceResult:
        self._engine.reap_timeouts()
        view = self._engine.advance(run_id)
        wake = None
        if view.next_wakeup_at is not None:
            wake = max(0.0, (view.next_wakeup_at - datetime.now(UTC)).total_seconds())
        return AdvanceResult(status=view.status, wake_in_seconds=wake)


@workflow.defn(name=WORKFLOW_NAME)
class RunDriver:
    def __init__(self) -> None:
        self._poked = False

    @workflow.signal
    def poke(self) -> None:
        self._poked = True

    @workflow.run
    async def run(self, run_id: str) -> str:
        for _ in range(CONTINUE_AS_NEW_AFTER):
            self._poked = False
            res: AdvanceResult = await workflow.execute_activity(
                ACTIVITY_NAME,
                run_id,
                result_type=AdvanceResult,
                start_to_close_timeout=timedelta(seconds=120),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=2), maximum_attempts=10
                ),
            )
            if res.status in TERMINAL:
                return res.status
            wait = MAX_IDLE_POLL_SECONDS if res.wake_in_seconds is None else res.wake_in_seconds
            wait = min(max(wait, 1.0), MAX_IDLE_POLL_SECONDS)
            with contextlib.suppress(TimeoutError):
                await workflow.wait_condition(lambda: self._poked, timeout=timedelta(seconds=wait))
        workflow.continue_as_new(run_id)
        return "continued"  # pragma: no cover - continue_as_new raises


class TemporalNotifier:
    """RunNotifier backed by signal-with-start. Failures are logged by the engine, never fatal."""

    def __init__(self, address: str, namespace: str, task_queue: str) -> None:
        self._address, self._namespace, self._task_queue = address, namespace, task_queue
        self._client: Client | None = None
        self._loop = asyncio.new_event_loop()
        threading.Thread(
            target=self._loop.run_forever, name="temporal-notifier", daemon=True
        ).start()

    def _submit(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Fire-and-forget: a Temporal outage must never block API requests (reconcile recovers)."""
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)

        def _log(f: Future[Any]) -> None:
            if exc := f.exception():
                log.warning("temporal poke failed: %s: %s", type(exc).__name__, exc)

        fut.add_done_callback(_log)

    async def _poke(self, run_id: str) -> None:
        if self._client is None:
            self._client = await Client.connect(self._address, namespace=self._namespace)
        try:
            await self._client.start_workflow(
                WORKFLOW_NAME,
                run_id,
                id=workflow_id(run_id),
                task_queue=self._task_queue,
                start_signal="poke",
            )
        except WorkflowAlreadyStartedError:
            await self._client.get_workflow_handle(workflow_id(run_id)).signal("poke")

    def poke(self, run_id: str) -> None:
        self._submit(self._poke(run_id))


async def run_worker(engine: Engine, address: str, namespace: str, task_queue: str) -> None:
    client = await Client.connect(address, namespace=namespace)
    acts = Activities(engine)
    with ThreadPoolExecutor(max_workers=8) as pool:
        worker = Worker(
            client,
            task_queue=task_queue,
            workflows=[RunDriver],
            activities=[acts.advance_run],
            activity_executor=pool,
        )
        log.info("temporal worker started on %s/%s queue=%s", address, namespace, task_queue)
        await worker.run()
