def scaffold_plan_md(task: str, notes: str, ts: int, wf: str, run: str) -> str:
    return (
        "# PLAN (scaffold)\n\n"
        f"- task: {task}\n"
        f"- notes: {notes}\n"
        f"- workflow_id: {wf}\n"
        f"- run_id: {run}\n"
        f"- ts: {ts}\n\n"
        "## What / Why / How\n"
        "- What: Outline the exact deliverables and acceptance.\n"
        "- Why: Align with PoR & spec.lock.\n"
        "- How: Minimal diffs, evidence packs, rollback strategy.\n"
    )
