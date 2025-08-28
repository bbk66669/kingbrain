# Minimal Composer：不依赖 LangGraph，只返回统一 artifacts 列表
import time
from typing import Dict, Any

def run_graph(phase: str, task: str, notes: str, context: Dict[str, Any]) -> Dict[str, Any]:
    artifacts = []
    if phase == "PLAN":
        # 仅产出 docs/kingbrain/PLAN/PLAN.md
        artifacts.append({
            "relpath": "docs/kingbrain/PLAN/PLAN.md",
            "encoding": "utf-8",
            "content": (
                "# PLAN (scaffold)\n\n"
                f"- task: {task}\n"
                f"- notes: {notes}\n"
                f"- workflow_id: {context.get('workflow_id')}\n"
                f"- run_id: {context.get('run_id')}\n"
                f"- ts: {int(time.time())}\n"
            )
        })
    return {"artifacts": artifacts}
