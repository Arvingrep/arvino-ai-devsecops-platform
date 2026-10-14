"""Real Temporal (local dev server) integration: durable driving, signals, worker restart.

Skipped when the dev server cannot be started (e.g. offline and not cached).
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

temporalio = pytest.importorskip("temporalio")

from temporalio.client import WorkflowExecutionStatus  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402
from temporalio.worker import Worker  # noqa: E402

from arvino.clock import SystemClock  # noqa: E402
from arvino.db import StepRow, transaction  # noqa: E402
from arvino.domain import Profile  # noqa: E402
from arvino.ports import StepOutcome  # noqa: E402
from arvino.temporal_worker import (  # noqa: E402
    Activities,
    RunDriver,
    TemporalNotifier,
    workflow_id,
)
from tests.conftest import BASE_CHECKS, SHA, Env, build_env  # noqa: E402

pytestmark = pytest.mark.temporal
QUEUE = "arvino-test-queue"


async def wait_for(pred, timeout: float = 30.0, what: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await asyncio.to_thread(pred):
            return
        await asyncio.sleep(0.2)
    raise AssertionError(f"timed out waiting for {what}")


def worker(client, env: Env, pool: ThreadPoolExecutor) -> Worker:
    return Worker(
        client,
        task_queue=QUEUE,
        workflows=[RunDriver],
        activities=[Activities(env.engine).advance_run],
        activity_executor=pool,
    )


async def scenario(tmp_path: Path) -> None:
    try:
        tenv = await WorkflowEnvironment.start_local()
    except Exception as exc:
        pytest.skip(f"temporal dev server unavailable: {type(exc).__name__}")
    async with tenv:
        client = tenv.client
        target = client.service_client.config.target_host
        env = build_env(tmp_path, clock=SystemClock())
        env.engine.ports.notifier = TemporalNotifier(target, "default", QUEUE)
        with ThreadPoolExecutor(max_workers=4) as pool:
            # --- 1. the driver advances a retry that only a timer can trigger (backoff)
            env.executor.script(
                "triage",
                StepOutcome(status="failed", error="flaky", retryable=True),
                StepOutcome(status="succeeded"),
            )
            env.executor.script("pull_request", env.pr_script())
            async with worker(client, env, pool):
                run, _ = await asyncio.to_thread(
                    env.engine.create_run, env.project_id, env.issue_id, Profile.FAST, "t1", "owner"
                )

                # shorten the backoff so the test is quick; the *driver* must fire the retry.
                def shorten() -> None:
                    with transaction(env.engine._sf) as s:
                        for st in s.query(StepRow).filter(StepRow.run_id == run.id):
                            st.backoff_seconds = 1

                await asyncio.to_thread(shorten)
                await asyncio.to_thread(
                    env.engine.start_run, env.project_id, env.issue_id, Profile.FAST, "t1", "owner"
                )
                handle = client.get_workflow_handle(workflow_id(run.id))
                await wait_for(
                    lambda: env.engine.get_run(run.id).step("triage").attempt == 2,
                    30,
                    "retry fired by driver",
                )
                await wait_for(
                    lambda: env.engine.get_run(run.id).step("ci").status == "running",
                    30,
                    "reach CI wait",
                )
                desc = await handle.describe()
                assert desc.status == WorkflowExecutionStatus.RUNNING

            # --- 2. worker is DOWN: external events keep the ledger correct and signals queue up
            await asyncio.to_thread(env.webhook_pr, run.id)
            await asyncio.to_thread(env.checks, run.id, BASE_CHECKS)
            await asyncio.to_thread(env.converge, "acme-preview", SHA)
            assert (await asyncio.to_thread(env.engine.get_run, run.id)).status == "succeeded"
            assert (
                await handle.describe()
            ).status == WorkflowExecutionStatus.RUNNING  # nobody to finish it

            # --- 3. a *new* worker picks the workflow up and it completes
            async with worker(client, env, pool):
                result = await asyncio.wait_for(handle.result(), 30)
            assert result == "succeeded"


def test_temporal_driver_fires_retries_and_survives_worker_restart(tmp_path: Path) -> None:
    asyncio.run(scenario(tmp_path))
