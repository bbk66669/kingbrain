# services/composer/temporal_worker.py
import os, asyncio
from datetime import timedelta
from typing import Dict, Any

from temporalio import workflow, activity
from temporalio.client import Client
from temporalio.worker import Worker

# Activity 现在放在独立文件，Workflow 里只 import 白名单
from services.composer.activity_impl import compose_and_write_activity  # 白名单模块

@workflow.defn(name="KBComposer.run")
class KBComposerWorkflow:
    @workflow.run
    async def run(self, phase: str, task: str, notes: str, context: Dict[str, Any]) -> Dict[str, Any]:
        return await workflow.execute_activity(
            compose_and_write_activity,
            {"phase": phase, "task": task, "notes": notes, "context": context},
            start_to_close_timeout=timedelta(seconds=120),
            retry_policy=workflow.RetryPolicy(maximum_attempts=3),
        )

async def _main():
    target = os.getenv("KB_TEMPORAL_ADDR", "temporal-frontend.orchestrator.svc.cluster.local:7233")
    ns = os.getenv("KB_TEMPORAL_NAMESPACE", "default")
    tq = os.getenv("KB_TASK_QUEUE", "kb-composer")
    client = await Client.connect(target, namespace=ns)
    worker = Worker(
        client,
        task_queue=tq,
        workflows=[KBComposerWorkflow],
        activities=[compose_and_write_activity],
    )
    print(f"[composer-worker] connected ns={ns} target={target} tq={tq}")
    await worker.run()

if __name__ == "__main__":
    asyncio.run(_main())
