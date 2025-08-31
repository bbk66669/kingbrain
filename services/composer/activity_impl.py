# services/composer/activity_impl.py
# -*- coding: utf-8 -*-

import os, json, hashlib, base64
from typing import Dict, Any, List, Tuple
from temporalio import activity

# 这些 import 原来在 Workflow 里，现在可以放在 Activity
from services.composer.graph import run_graph
from services.composer.artifacts import bundle as _bundle  # 按需导入
from services.composer.audit import emit_event  # 统一入口（本地+NATS+Neo4j）

try:
    from orchestrator import safeio
except Exception:
    import sys
    raise RuntimeError("orchestrator.safeio not found") from None

REPO_ROOT = os.getenv("REPO_ROOT", "/workspace")


def _digest(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


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
            # 静默跳过不可写文件（保持原逻辑）
            continue
    return written, evid


@activity.defn
async def compose_and_write_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Activity：真正的重逻辑都在这里"""
    phase = payload.get("phase", "").upper()
    task = payload.get("task", "")
    notes = payload.get("notes", "")
    ctx = payload.get("context", {})

    # started
    started_ev = await emit_event(f"kb.composer.{phase}.started.v1", {"task": task, "notes": notes})
    try:
        result = run_graph(phase=phase, task=task, notes=notes, context=ctx) or {}
        arts = result.get("artifacts", [])
        written, evid = _apply_artifacts(arts)

        # completed
        completed_ev = await emit_event(
            f"kb.composer.{phase}.completed.v1",
            {"task": task, "notes": notes, "written": written, "evidence": evid, "count": len(arts)},
        )

        return {
            "phase": phase,
            "written": written,
            "evidence": evid,
            "events": [started_ev.get("id"), completed_ev.get("id")],
            "count": len(arts),
        }
    except Exception as e:
        # failed
        failed_ev = await emit_event(
            f"kb.composer.{phase}.failed.v1",
            {"task": task, "error": str(e)},
        )
        return {
            "phase": phase,
            "written": [],
            "evidence": [],
            "events": [started_ev.get("id"), failed_ev.get("id")],
            "error": str(e),
        }
