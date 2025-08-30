import json, time
from typing import Dict, Any, List
from . import common_text
from ..artifacts import make_text

def run(task: str, notes: str, context: Dict[str, Any], agent_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    产物：
      - docs/kingbrain/PLAN/PLAN.md
      - docs/kingbrain/PLAN/manifest.json
    """
    ts = int(time.time())
    wf  = context.get("workflow_id") or ""
    run = context.get("run_id") or ""

    # 先用占位文本，LLM 结果（若有）在 graph.py 里拼接覆盖/补充
    plan_md = common_text.scaffold_plan_md(task=task, notes=notes, ts=ts, wf=wf, run=run)
    manifest = {
        "task": task,
        "phase": "PLAN",
        "ts": ts,
        "workflow_id": wf,
        "run_id": run,
        "items": [
            {"path": "docs/kingbrain/PLAN/PLAN.md", "source": "architect"},
            {"path": "docs/kingbrain/PLAN/manifest.json", "source": "architect"},
        ],
    }

    return [
        make_text("docs/kingbrain/PLAN/PLAN.md", plan_md),
        make_text("docs/kingbrain/PLAN/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"),
    ]
