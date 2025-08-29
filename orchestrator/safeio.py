# orchestrator/safeio.py
import os, io, tempfile, shutil, fnmatch, yaml
from typing import Dict, Any, Tuple

REPO_ROOT = os.getenv("REPO_ROOT", "/workspace")
ALLOWLIST_FILE = os.path.join(REPO_ROOT, ".collab", "paths.allowlist.yaml")

def _load_allowlist() -> Dict[str, list]:
    try:
        with open(ALLOWLIST_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            # 标准化字段
            for k in ("allow", "deny", "readonly", "writable"):
                data.setdefault(k, [])
            return data
    except FileNotFoundError:
        # 没有 allowlist 就拒绝写
        return {"allow": [], "deny": ["**"], "readonly": [], "writable": []}

def _match_any(path_norm: str, patterns: list) -> bool:
    # allowlist 里是以“/”开头的绝对样式，这里统一成以“/”开头再匹配
    if not path_norm.startswith("/"):
        path_norm = "/" + path_norm
    for pat in patterns or []:
        if fnmatch.fnmatch(path_norm, pat):
            return True
    return False

def _classify(path_norm: str, rules: Dict[str, list]) -> str:
    # 优先级：deny > writable > readonly > allow > default-deny
    if _match_any(path_norm, rules.get("deny", [])):
        return "deny"
    if _match_any(path_norm, rules.get("writable", [])):
        return "writable"
    if _match_any(path_norm, rules.get("readonly", [])):
        return "readonly"
    if _match_any(path_norm, rules.get("allow", [])):
        # allow 不代表可写，只代表可见；仍然默认只读
        return "allow"
    return "unknown"

def _normalize_rel(relpath: str) -> Tuple[str, str]:
    # 只接受相对路径
    if relpath.startswith("/") or relpath.startswith("~"):
        raise ValueError(f"absolute path not allowed: {relpath}")
    # 防目录穿越
    norm = os.path.normpath(relpath).replace("\\", "/")
    if norm.startswith("../"):
        raise ValueError(f"path escapes repo root: {relpath}")
    abspath = os.path.join(REPO_ROOT, norm)
    # 再次确保不越界
    if not os.path.realpath(abspath).startswith(os.path.realpath(REPO_ROOT) + os.sep):
        raise ValueError(f"path not within repo: {relpath}")
    return norm, abspath

def write_bytes_atomic(relpath: str, data: bytes) -> Dict[str, Any]:
    rules = _load_allowlist()
    norm, abspath = _normalize_rel(relpath)
    verdict = _classify(norm, rules)
    if verdict != "writable":
        raise PermissionError(f"path not writable by policy: {norm} (class={verdict})")

    os.makedirs(os.path.dirname(abspath), exist_ok=True)
    dir_ = os.path.dirname(abspath)
    fd, tmp = tempfile.mkstemp(prefix=".tmp.", dir=dir_)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, abspath)  # 原子替换
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
    return {"relpath": norm, "abspath": abspath}

def write_text_atomic(relpath: str, text: str, encoding="utf-8") -> Dict[str, Any]:
    return write_bytes_atomic(relpath, text.encode(encoding))
