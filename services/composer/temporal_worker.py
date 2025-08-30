# services/composer/temporal_worker.py
import os, json, asyncio, hashlib, base64, time, uuid
from typing import Dict, Any, List, Tuple
from temporalio import workflow, activity
from temporalio.client import Client
from temporalio.worker import Worker

# 复用 Composer
from services.composer.graph import run_graph

# 直接复用安全落盘口子（和 orchestrator 同一套策略）
try:
    from orchestrator import safeio
except Exception:
    import sys
    raise RuntimeError("orchestrator.safeio not found in image; ensure same image is used for worker") from None

REPO_ROOT = os.getenv("REPO_ROOT", "/workspace")
AUDIT_DIR = os.path.join(REPO_ROOT, ".collab", "audit")
os.makedirs(AUDIT_DIR, exist_ok=True)

def _digest(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()

def _write_event(event_type: str, data: Dict[str, Any]) -> str:
    ev = {
        "id": str(uuid.uuid4()),
        "type": event_type,
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "kb-composer-worker",
        "specversion": "1.0",
        "datacontenttype": "application/json",
        "data": data,
    }
    path = os.path.join(AUDIT_DIR, f"events-{time.strftime('%Y%m%d')}.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev) + "\n")
    return ev["id"]

def _apply_artifacts(arts: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    written: List[str] = []
    evid: List[str] = []
    for i, a in enumerate(arts or []):
        rel = (a.get("relpath") or "").lstrip("/")
        enc = (a.get("encoding") or "utf-8").lower()
        raw = a.get("content", "")
        if enc == "base64":
            try:
                b = base64.b64decode(raw)
            except Exception:
                continue
        else:
            b = (raw if isinstance(raw, bytes) else str(raw).encode("utf-8"))
        # 走同一套 allowlist 原子写
        try:
            safeio.write_bytes_atomic(rel, b)
            written.append(rel)
            evid.append(_digest(b))
        except PermissionError:
            # 违反 allowlist 的产物将被丢弃（与线上 orchestrator 行为一致）
            continue
    return written, evid

# ================= Activities =================
@activity.defn
async def compose_and_write(payload: Dict[str, Any]) -> Dict[str, Any]:
    phase = (payload.get("phase") or "").upper()
    task  = payload.get("task")  or ""
    notes = payload.get("notes") or ""
    ctx   = payload.get("context") or {}

    started = _write_event(f"kb.composer.{phase}.started.v1", {"task": task, "notes": notes})
    try:
        result = run_graph(phase=phase, task=task, notes=notes, context=ctx) or {}
        arts = result.get("artifacts", [])
        written, evid = _apply_artifacts(arts)
        completed = _write_event(f"kb.composer.{phase}.completed.v1", {
            "task": task, "notes": notes, "written": written, "evidence": evid, "count": len(arts)
        })
        return {
            "phase": phase, "written": written, "evidence": evid,
            "events": [started, completed], "count": len(arts),
        }
    except Exception as e:
        failed = _write_event(f"kb.composer.{phase}.failed.v1", {"task": task, "error": str(e)})
        return {"phase": phase, "written": [], "evidence": [], "events": [started, failed], "error": str(e)}

# ================= Workflow ===================
@workflow.defn(name="KBComposer.run")
class KBComposerWorkflow:
    @workflow.run
    async def run(self, phase: str, task: str, notes: str, context: Dict[str, Any]) -> Dict[str, Any]:
        return await workflow.execute_activity(
            compose_and_write,
            {"phase": phase, "task": task, "notes": notes, "context": context},
            start_to_close_timeout=timedelta(seconds=120),
            retry_policy=workflow.RetryPolicy(maximum_attempts=3, non_retryable_error_types=[]),
        )

# ================= Entrypoint =================
from datetime import timedelta

async def _main():
    target = os.getenv("KB_TEMPORAL_ADDR", "temporal-frontend.orchestrator.svc.cluster.local:7233")
    ns     = os.getenv("KB_TEMPORAL_NAMESPACE", "default")
    tq     = os.getenv("KB_TASK_QUEUE", "kb-composer")
    client = await Client.connect(target, namespace=ns)
    worker = Worker(client, task_queue=tq, workflows=[KBComposerWorkflow], activities=[compose_and_write])
    print(f"[composer-worker] connected ns={ns} target={target} tq={tq}")
    await worker.run()

if __name__ == "__main__":
    asyncio.run(_main())
