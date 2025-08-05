#!/usr/bin/env python3
"""
一键生成 KingBrain 入口候选脚本（去除顶层包，只保留具体模块）。

流程：
1. 为每个 repo 根目录打 __init__.py（消除 namespace 包）
2. 把 repo 父目录加到 sys.path（让 Python 能 import 顶层包）
3. full_files.json → all .py 路径 → 映射模块名
4. importlib.find_spec 过滤不可 import
5. grimp.build_graph 构建依赖图
6. roots = 无任何上游依赖的模块（排除顶层包名）
7. mains = 含 __main__ 的脚本
8. cands = sorted(roots ∪ mains)
9. 输出 entry_candidates.txt
"""
import sys, json, pathlib, re, importlib.util
import grimp

# ─── 准备 ──────────────────────────────
ROOT   = pathlib.Path(__file__).resolve().parent.parent
REPOS  = [pathlib.Path(p.strip())
          for p in (ROOT/"repos.txt").read_text().splitlines() if p.strip()]
FULL   = json.loads((ROOT/"full_files.json").read_text(encoding="utf-8"))

# 1. 在每个 repo 根目录写 __init__.py
for repo in REPOS:
    init = repo/"__init__.py"
    if not init.exists():
        init.write_text("# package marker\n", encoding="utf-8")
        print(f"[+] Created __init__.py in {repo}")

# 2. 把每个 repo 的父目录插入 sys.path
for repo in REPOS:
    parent = str(repo.parent.resolve())
    if parent not in sys.path:
        sys.path.insert(0, parent)

# 3. 路径 → 模块名 函数
def path_to_mod(fp: str):
    p = pathlib.Path(fp).resolve()
    # 过滤 venv、site-packages
    if any(seg in p.parts for seg in ("venv", ".venv", "site-packages")):
        return None
    for repo in REPOS:
        try:
            rel = p.relative_to(repo.resolve())
            parts = list(rel.with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            return repo.name + ("" if not parts else "." + ".".join(parts))
        except Exception:
            continue
    return None

mods = set(filter(None, (path_to_mod(f) for f in FULL)))
print(f"[*] 原始模块数：{len(mods)}")

# 4. 过滤：仅保留能 import 的
valid = []
skipped = []
for m in sorted(mods):
    try:
        if importlib.util.find_spec(m):
            valid.append(m)
        else:
            skipped.append(m)
    except Exception:
        skipped.append(m)
print(f"[*] 可 import 模块：{len(valid)}，跳过：{len(skipped)}")
if skipped:
    print("  示例跳过：", skipped[:5])

# 5. 调用 grimp 构建依赖图
print("[*] building import-graph …")
graph = grimp.build_graph(*sorted({m.split('.',1)[0] for m in valid}))

# 6. 找无上游依赖的“根模块”，并**排除**顶层包本身
roots = {
    m for m in graph.modules
    if not graph.find_modules_that_directly_import(m)
       and "." in m
}

# 7. 查 __main__ 的脚本
mains = set()
for f in FULL:
    txt = pathlib.Path(f).read_text(errors="ignore")
    if re.search(r'if\s+__name__\s*==\s*["\']__main__["\']', txt):
        if (m := path_to_mod(f)) in valid:
            mains.add(m)

# 8. 合并 & 输出
cands = sorted(roots | mains)
out = ROOT/"entry_candidates.txt"
out.write_text("\n".join(cands), encoding="utf-8")
print(f"[✓] 生成 {out} (共 {len(cands)} 条)")
