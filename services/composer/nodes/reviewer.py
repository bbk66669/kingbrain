# services/composer/nodes/reviewer.py
# -*- coding: utf-8 -*-

import os, re, time, json, textwrap, yaml
from typing import Dict, Any, List, Tuple
from ..artifacts import make_text

ALLOW_GLOBS = [
    "/apps/**","/services/**","/scripts/**","/ml/**","/observability/**",
    "/orchestrator/**","/strategy/ci/**","/.collab/**","/docs/**","/reports/**","/data/**"
]
READONLY_GLOBS = ["/k8s/manifests/**/*.yaml","/docs/kingbrain/**","/grafana/dashboards/**/*.json"]

def _repo_root(ctx: Dict[str, Any]) -> str:
    return ctx.get("repo_root") or os.getenv("REPO_ROOT","/workspace")

def _load_paths_policy(repo_root: str) -> Dict[str, Any]:
    p = os.path.join(repo_root,".collab","paths.allowlist.yaml")
    try:
        with open(p,"r",encoding="utf-8") as f:
            y = yaml.safe_load(f) or {}
    except Exception:
        y = {}
    return {
        "allow": y.get("allow", ALLOW_GLOBS),
        "readonly": y.get("readonly", READONLY_GLOBS),
        "deny": y.get("deny", []),
        "writable": y.get("writable", []),
    }

def _parse_paths_from_patch(patch_text: str) -> List[str]:
    # 抓取  "+++ b/<path>" 或 "--- a/<path>"
    paths = []
    for line in patch_text.splitlines():
        if line.startswith("+++ b/"):
            paths.append(line[len("+++ b/"):].strip())
        elif line.startswith("--- a/"):
            paths.append(line[len("--- a/"):].strip())
    # 去重
    uniq = []
    for p in paths:
        if p not in uniq:
            uniq.append(p)
    return uniq

def _glob_match(path: str, globs: List[str]) -> bool:
    # 简化：把 ** -> .*   * -> [^/]*  然后用正则匹配
    def g2re(g: str) -> str:
        s = re.escape(g).replace("\\*\\*", ".*").replace("\\*", "[^/]*")
        if not s.startswith("\\/"):  # 规则里是以 / 开头
            s = "\\/" + s
        return "^" + s + "$"
    p = "/" + path.lstrip("/")
    return any(re.match(g2re(g), p) for g in globs or [])

def _load_checklist(repo_root: str) -> List[Dict[str, Any]]:
    ck = os.path.join(repo_root, ".aoc-checklist.md")
    if not os.path.exists(ck):
        return []
    # 极简解析：把 markdown 中以 "1."/"- [ ]"/"- [x]" 开头的条目抓出来
    items = []
    with open(ck,"r",encoding="utf-8") as f:
        for ln in f:
            s = ln.strip()
            if s.startswith(("- [", "1.", "2.", "3.", "4.", "5.", "6.", "7.")):
                items.append({"raw": s})
    return items

def _find_latest_patch(repo_root: str) -> Tuple[str,str]:
    d = os.path.join(repo_root, "docs/kingbrain/DIFF")
    if not os.path.isdir(d):
        return "", ""
    cands = sorted([x for x in os.listdir(d) if x.endswith(".patch")])
    if not cands:
        return "", ""
    fp = os.path.join(d, cands[-1])
    with open(fp,"r",encoding="utf-8",errors="replace") as f:
        return fp, f.read()

def _check_allowlist(paths: List[str], policy: Dict[str, Any]) -> List[str]:
    errors = []
    for p in paths:
        ok_allow = _glob_match(p, policy.get("allow", []))
        if not ok_allow:
            errors.append(f"Path '{p}' not in allowlist")
        ro_hit = _glob_match(p, policy.get("readonly", []))
        if ro_hit:
            errors.append(f"Path '{p}' hits readonly glob")
        deny_hit = _glob_match(p, policy.get("deny", []))
        if deny_hit:
            errors.append(f"Path '{p}' hits deny glob")
    return errors

def _load_evidence(repo_root: str) -> Dict[str, Any]:
    # 汇总已有证据（最小实现：lint 与 sbom 的存在性）
    rep = os.path.join(repo_root, "reports")
    data = os.path.join(repo_root, "data")
    found = {
        "lint_reports": sorted([f for f in (os.listdir(rep) if os.path.isdir(rep) else []) if f.startswith("lint-") and f.endswith(".md")]),
        "sbom": [],
        "kyverno": [],
    }
    sbom_dir = os.path.join(data, "sbom")
    if os.path.isdir(sbom_dir):
        found["sbom"] = sorted([x for x in os.listdir(sbom_dir) if x.endswith(".json") or x.endswith(".spdx.json")])
    kyv_dir = os.path.join(rep, "kyverno")
    if os.path.isdir(kyv_dir):
        found["kyverno"] = sorted([x for x in os.listdir(kyv_dir) if x.endswith(".yaml") or x.endswith(".json")])
    return found

def run_cr(task: str, notes: str, context: Dict[str, Any], agent_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    产物：
      - reports/CR/CR-<sid>.yaml       （结构化审查报告）
      - reports/CR/CR-<sid>.md         （人读版摘要）
    行为：
      - 校验：allowlist / readonly / deny
      - 校验：存在 lint/sbom/（可选）kyverno 的证据
      - 绑定：最近一份 *.patch 作为被审对象
    """
    repo_root = _repo_root(context)
    sid = (context.get("run_id") or str(int(time.time())))[:8]
    policy = _load_paths_policy(repo_root)
    ck_items = _load_checklist(repo_root)
    patch_file, patch_text = _find_latest_patch(repo_root)
    touched = _parse_paths_from_patch(patch_text) if patch_text else []

    findings = {
        "allowlist": _check_allowlist(touched, policy) if touched else ["no diff patch found"],
        "checklist_count": len(ck_items),
        "evidence": _load_evidence(repo_root),
        "patch_file": os.path.relpath(patch_file, repo_root) if patch_file else "",
        "touched": touched,
    }
    # AOC 七项粗略机检（最小判定：有清单、有证据、有可写路径、无只读/deny 命中）
    aoc_ok = (
        findings["checklist_count"] >= 7 and
        findings["allowlist"] == [] and
        (findings["evidence"]["lint_reports"] or findings["evidence"]["sbom"])
    )

    cr_yaml = {
        "title": f"CR: {task}",
        "description": f"Automated review for task={task}; notes={notes}; sid={sid}",
        "diff": (findings["patch_file"] or "N/A"),
        "attestations": [
            {"type":"sbom","digest": "sha256:" + "0"*64} if findings["evidence"]["sbom"] else {"type":"sbom","digest":"sha256:" + "0"*64},
        ],
        "touched_paths": findings["touched"],
        "aoc_checklist_items": findings["checklist_count"],
        "violations": findings["allowlist"],
        "result": "accept" if aoc_ok else "reject",
    }
    md = []
    md.append(f"# Code Review (sid={sid})")
    md.append(f"- result: **{'ACCEPT' if aoc_ok else 'REJECT'}**")
    md.append(f"- patch: `{findings['patch_file'] or 'N/A'}`")
    md.append(f"- touched: {len(findings['touched'])} files")
    if findings["allowlist"]:
        md.append("## Violations")
        for v in findings["allowlist"]:
            md.append(f"- {v}")
    md.append("## Evidence")
    md.append(f"- lint: {', '.join(findings['evidence']['lint_reports']) or 'N/A'}")
    md.append(f"- sbom: {', '.join(findings['evidence']['sbom']) or 'N/A'}")
    if findings["evidence"]["kyverno"]:
        md.append(f"- kyverno: {', '.join(findings['evidence']['kyverno'])}")
    md.append("")

    return [
        make_text(f"reports/CR/CR-{sid}.yaml", yaml.safe_dump(cr_yaml, sort_keys=False, allow_unicode=True)),
        make_text(f"reports/CR/CR-{sid}.md", "\n".join(md) + "\n"),
    ]
