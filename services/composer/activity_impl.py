# services/composer/activity_impl.py
import os, json, hashlib, base64, time, uuid
from typing import Dict, Any, List, Tuple
from temporalio import activity

# 这些 import 原来在 Workflow 里，现在可以放在 Activity
from services.composer.graph import run_graph
from services.composer.artifacts import bundle as _bundle  # 按需导入
try:
    from orchestrator import safeio
except Exception:
    import sys
    raise RuntimeError("orchestrator.safeio not found") from None

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
    written, evid = [], []
    for a in arts or []:
        rel = (a.get("relpath") or "").lstrip("/")
        enc = (a.get("encoding") or "utf-8").lower()
        raw = a.get("content", "")
        b = base64.b64decode(raw) if enc == "base64" else str(raw).encode("utf-8")
        try:
            safeio.write_bytes_atomic(rel, b)
            written.append(rel)
            evid.append(_digest(b))
        except PermissionError:
            continue
    return written, evid

@activity.defn
async def compose_and_write_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Activity：真正的重逻辑都在这里"""
    phase = payload.get("phase", "").upper()
    task = payload.get("task", "")
    notes = payload.get("notes", "")
    ctx = payload.get("context", {})

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
