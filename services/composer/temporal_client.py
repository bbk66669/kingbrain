# services/composer/temporal_client.py
import os, uuid
from typing import Dict, Any
from temporalio.client import Client

async def _connect():
    target = os.getenv("KB_TEMPORAL_ADDR", "temporal-frontend.orchestrator.svc.cluster.local:7233")
    ns     = os.getenv("KB_TEMPORAL_NAMESPACE", "default")
    return await Client.connect(target, namespace=ns)

async def submit_async(phase: str, task: str, notes: str, context: Dict[str, Any]) -> Dict[str, str]:
    client = await _connect()
    wf_id = f"kb-{phase.lower()}-{uuid.uuid4().hex[:8]}"
    handle = await client.start_workflow(
        "KBComposer.run",
        phase, task, notes, context,
        id=wf_id,
        task_queue=os.getenv("KB_TASK_QUEUE", "kb-composer"),
    )
    return {"workflow_id": handle.id, "run_id": handle.first_execution_run_id}

async def describe_async(workflow_id: str) -> Dict[str, Any]:
    client = await _connect()
    h = client.get_workflow_handle(workflow_id=workflow_id)
    info = await h.describe()
    return {
        "workflow_id": info.workflow_execution.workflow_id,
        "run_id": info.workflow_execution.run_id,
        "status": str(info.status),
        "history_length": info.history_length,
    }
