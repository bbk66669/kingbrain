import time
from typing import Dict, Any, List
from ..artifacts import make_text, make_patch

def run_borrow(task: str, notes: str, context: Dict[str, Any], agent_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    产物：
      - docs/kingbrain/BORROW/README.md
    """
    ts = int(time.time())
    md = (
        f"# Borrowed Materials\n\n"
        f"- task: {task}\n- notes: {notes}\n- ts: {ts}\n\n"
        f"List sources here (templates/repos/tags/licenses). This is a placeholder.\n"
    )
    return [make_text("docs/kingbrain/BORROW/README.md", md)]

def run_diff(task: str, notes: str, context: Dict[str, Any], agent_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    产物：
      - docs/kingbrain/DIFF/diff-<sid>.patch  （统一 diff，占位）
    注意：仅演示最小 diff；真实实现由 Builder-AI 生成。
    """
    sid = (context.get("run_id") or "")[:8]
    ts = int(time.time())
    # 统一 diff 样例（对 PLAN.md 做一行附加）
    patch = f"""diff --git a/docs/kingbrain/PLAN/PLAN.md b/docs/kingbrain/PLAN/PLAN.md
index 0000000..0000001 100644
--- a/docs/kingbrain/PLAN/PLAN.md
+++ b/docs/kingbrain/PLAN/PLAN.md
@@ -1,3 +1,5 @@
 # PLAN (scaffold)
+<!-- DIFF generated at {ts} -->
+This is a minimal placeholder diff for task: {task}
"""
    return [make_patch(f"docs/kingbrain/DIFF/diff-{sid or ts}.patch", patch)]
