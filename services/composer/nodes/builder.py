# services/composer/nodes/builder.py
# -*- coding: utf-8 -*-

import os, json, time, shutil, tempfile, subprocess, hashlib
from typing import Dict, Any, List, Tuple, Optional
from ..artifacts import make_text, make_patch

# —— 最小“真实 Builder”实现：按 manifest 借模板 → 生成 unified diff → 跑 linters → 产出证据 —— #
# manifest 期望最小 schema：
# {
#   "task": "...",
#   "phase": "PLAN",
#   "borrow": [
#     {"type":"git","repo":"https://github.com/org/repo.git","ref":"v1.2.3","subpath":"templates/basic","target":"apps/demo"},
#     ...
#   ]
# }

def _repo_root(ctx: Dict[str, Any]) -> str:
    return ctx.get("repo_root") or os.getenv("REPO_ROOT", "/workspace")

def _safe_rel(p: str) -> str:
    return (p or "").lstrip("/").replace("..","")

def _read_manifest(repo_root: str) -> Dict[str, Any]:
    mf = os.path.join(repo_root, "docs/kingbrain/PLAN/manifest.json")
    try:
        with open(mf, "r", encoding="utf-8") as f:
            return json.loads(f.read() or "{}")
    except Exception:
        return {}

def _sha256(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()

def _run_cmd(cmd: List[str], cwd: Optional[str] = None, timeout: int = 120) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, timeout=timeout, capture_output=True, text=True)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:
        return 127, "", str(e)

def _borrow_git(repo: str, ref: str, subpath: str, tmpdir: str) -> Optional[str]:
    # 浅克隆指定 ref（tag/branch/commit），提取 subpath
    code, out, err = _run_cmd(["git", "clone", "--depth", "1", "--branch", ref, repo, "src"], cwd=tmpdir, timeout=300)
    if code != 0:
        # 尝试直接 clone 再 checkout
        code2, _, err2 = _run_cmd(["git", "clone", "--depth", "1", repo, "src"], cwd=tmpdir, timeout=300)
        if code2 != 0:
            return None
        _run_cmd(["git", "fetch", "--all", "--tags"], cwd=os.path.join(tmpdir, "src"))
        _run_cmd(["git", "checkout", ref], cwd=os.path.join(tmpdir, "src"))
    src = os.path.join(tmpdir, "src", subpath)
    return src if os.path.isdir(src) else None

def _gather_files(root: str) -> List[str]:
    files = []
    for d, _, fnames in os.walk(root):
        for n in fnames:
            files.append(os.path.join(d, n))
    return files

def _to_repo_rel(abs_src: str, src_root: str, target_root: str) -> str:
    rel = os.path.relpath(abs_src, src_root).replace("\\", "/")
    return os.path.join(target_root, rel).replace("\\", "/")

def _make_newfile_diff(target_rel: str, content: bytes) -> str:
    text = content.decode("utf-8", errors="replace")
    lines = text.splitlines()
    header = (
        f"diff --git a/{target_rel} b/{target_rel}\n"
        f"new file mode 100644\n"
        f"index 0000000..0000000\n"
        f"--- /dev/null\n"
        f"+++ b/{target_rel}\n"
    )
    body = ""
    body += f"@@ -0,0 +{len(lines)} @@\n"
    for line in lines:
        body += f"+{line}\n"
    return header + body

def _run_linters(temp_files: List[Tuple[str, bytes]]) -> str:
    # 逐工具“存在即用”，输出简要汇总
    summaries: List[str] = []

    def _maybe_tool(name: str) -> bool:
        return shutil.which(name) is not None

    # shellcheck
    if _maybe_tool("shellcheck"):
        issues = 0
        for path, _ in temp_files:
            if path.endswith(".sh"):
                code, out, err = _run_cmd(["shellcheck", "-S", "warning", path])
                if code != 0:
                    issues += 1
                    summaries.append(f"[shellcheck] {path}\n{out or err}")
        if issues == 0:
            summaries.append("[shellcheck] OK")
    else:
        summaries.append("[shellcheck] not installed; skipped")

    # hadolint
    if _maybe_tool("hadolint"):
        issues = 0
        for path, _ in temp_files:
            if os.path.basename(path).lower() == "dockerfile":
                code, out, err = _run_cmd(["hadolint", path])
                if code != 0:
                    issues += 1
                    summaries.append(f"[hadolint] {path}\n{out or err}")
        if issues == 0:
            summaries.append("[hadolint] OK")
    else:
        summaries.append("[hadolint] not installed; skipped")

    # kube-linter
    if _maybe_tool("kube-linter"):
        # 如果借来的有 yaml，就跑一下 kube-linter（临时写入一个目录）
        yaml_dir = None
        for path, _ in temp_files:
            if path.endswith((".yaml", ".yml")):
                yaml_dir = os.path.dirname(path)
                break
        if yaml_dir:
            code, out, err = _run_cmd(["kube-linter", "lint", yaml_dir])
            if code == 0:
                summaries.append("[kube-linter] OK")
            else:
                summaries.append(f"[kube-linter]\n{out or err}")
        else:
            summaries.append("[kube-linter] no YAML; skipped")
    else:
        summaries.append("[kube-linter] not installed; skipped")

    return "\n\n".join(summaries).strip() + "\n"

def run_borrow(task: str, notes: str, context: Dict[str, Any], agent_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    产物：
      - docs/kingbrain/BORROW/Sources-<sid>.md      （列出借用源+哈希）
      - reports/lint-<sid>.md                       （linters 汇总）
    """
    repo_root = _repo_root(context)
    sid = (context.get("run_id") or str(int(time.time())))[:8]
    manifest = _read_manifest(repo_root)
    borrow = manifest.get("borrow") or []

    # 记录 sources
    lines = [
        f"# Borrowed Materials (task={task})",
        f"- notes: {notes}",
        f"- sid: {sid}",
        "",
    ]
    temp_files: List[Tuple[str, bytes]] = []
    with tempfile.TemporaryDirectory() as td:
        for i, it in enumerate(borrow):
            if (it or {}).get("type") != "git":
                continue
            repo = it.get("repo", "")
            ref = it.get("ref", "")
            subp = _safe_rel(it.get("subpath", ""))
            target = _safe_rel(it.get("target", ""))
            if not (repo and ref and subp and target):
                continue
            src_dir = _borrow_git(repo, ref, subp, td)
            if not src_dir:
                lines.append(f"- ERR borrow[{i}]: clone/fetch failed: {repo}@{ref} subpath={subp}")
                continue
            files = _gather_files(src_dir)
            lines.append(f"- {repo}@{ref} subpath={subp}  → target={target}  files={len(files)}")
            for absf in files:
                with open(absf, "rb") as rf:
                    b = rf.read()
                temp_path = os.path.join(td, f"f{i}_{os.path.basename(absf)}")
                with open(temp_path, "wb") as wf:
                    wf.write(b)
                temp_files.append((temp_path, b))
                lines.append(f"  - {os.path.relpath(absf, src_dir)}  ({_sha256(b)})")

    lint_sum = _run_linters(temp_files)
    arts = [
        make_text(f"docs/kingbrain/BORROW/Sources-{sid}.md", "\n".join(lines) + "\n"),
        make_text(f"reports/lint-{sid}.md", lint_sum),
    ]
    return arts

def run_diff(task: str, notes: str, context: Dict[str, Any], agent_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    产物：
      - docs/kingbrain/DIFF/diff-<sid>.patch  （把借来的文件以“新文件”形式输出 unified diff）
    """
    repo_root = _repo_root(context)
    sid = (context.get("run_id") or str(int(time.time())))[:8]
    manifest = _read_manifest(repo_root)
    borrow = manifest.get("borrow") or []
    chunks: List[str] = []
    with tempfile.TemporaryDirectory() as td:
        for it in borrow:
            if (it or {}).get("type") != "git":
                continue
            repo, ref = it.get("repo", ""), it.get("ref", "")
            subp = _safe_rel(it.get("subpath", ""))
            target = _safe_rel(it.get("target", ""))
            if not (repo and ref and subp and target):
                continue
            src_dir = _borrow_git(repo, ref, subp, td)
            if not src_dir:
                continue
            for absf in _gather_files(src_dir):
                with open(absf, "rb") as rf:
                    b = rf.read()
                target_rel = _to_repo_rel(absf, src_dir, target)
                chunks.append(_make_newfile_diff(target_rel, b))
    patch = "\n".join(chunks).rstrip() + ("\n" if chunks else "")
    if not patch:
        # 没有 borrow 条目或找不到 → 输出解释
        patch = (
            "diff --git b/NOOP a/NOOP\n"
            "--- a/NOOP\n"
            "+++ b/NOOP\n"
            "@@ -0,0 +1 @@\n"
            "+No borrow entries found in manifest.json\n"
        )
    return [make_patch(f"docs/kingbrain/DIFF/diff-{sid}.patch", patch)]
