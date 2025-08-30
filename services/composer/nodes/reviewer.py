import time, textwrap
from typing import Dict, Any, List
from ..artifacts import make_text

def run_cr(task: str, notes: str, context: Dict[str, Any], agent_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    产物（注意 allowlist，CR 放到 /reports/**）：
      - reports/CR/cr-draft.yaml
    """
    ts = int(time.time())
    yaml_txt = textwrap.dedent(f"""\
    title: "CR: {task}"
    description: |
      Auto-generated draft by Reviewer-AI (placeholder).
      notes: {notes}
      ts: {ts}
    diff: |
      (attach PR diff link here)
    attestations:
      - type: sbom
        digest: sha256:{"0"*64}
      - type: kyverno
        digest: sha256:{"f"*64}
    """)
    return [make_text("reports/CR/cr-draft.yaml", yaml_txt)]
