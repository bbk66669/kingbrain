#!/usr/bin/env python3
"""
reach_live.py — 生成可到达（活跃）文件列表 & 死代码清单

1. 读 full_files.json（所有 .py 绝对路径）
2. 读 repos.txt，并准备 pkg marker + sys.path
3. 建立文件→模块 与 模块→文件 映射
4. 读 entry_candidates.txt，过滤只保留在模块→文件映射中的入口模块，并排除含 demo 的条目
5. 只用能 import 的顶层包调用 build_graph
6. 从入口模块 BFS，收集可达模块
7. 可达模块→文件 写 live_files.json，其余写 dead_code.txt
"""
import json, pathlib, sys, importlib.util, re, time
from grimp import build_graph

ROOT       = pathlib.Path(__file__).resolve().parent.parent
FULL_JSON  = ROOT / "full_files.json"
REPOS_TXT  = ROOT / "repos.txt"
ENTRY_TXT  = ROOT / "entry_candidates.txt"
LIVE_JSON  = ROOT / "live_files.json"
DEAD_TXT   = ROOT / "dead_code.txt"

# 1. 读取所有 .py 文件
if not FULL_JSON.exists():
    sys.exit("❌ 请先运行 scan_full.py 生成 full_files.json")
full_files = json.loads(FULL_JSON.read_text(encoding="utf-8"))

# 2. 读取 repos.txt 并打包标记、添加 sys.path
repos = [pathlib.Path(p.strip()).resolve() for p in REPOS_TXT.read_text(encoding="utf-8").splitlines() if p.strip()]
if not repos:
    sys.exit("❌ repos.txt 为空，请填写你的仓库路径")
for repo in repos:
    init = repo / "__init__.py"
    if not init.exists():
        init.write_text("# package marker\n", encoding="utf-8")
        print(f"[+] 打了包标记：{init}")
    parent = str(repo.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)

# 3. 文件路径 ↔️ 模块名 映射
def path_to_mod(fp):
    p = pathlib.Path(fp).resolve()
    for repo in repos:
        try:
            rel = p.relative_to(repo)
            parts = list(rel.with_suffix("").parts)
            # 过滤 __init__.py
            if parts and parts[-1] == "__init__":
                return None
            return repo.name + ("." + ".".join(parts) if parts else "")
        except Exception:
            continue
    return None

file2mod = {fp: path_to_mod(fp) for fp in full_files}
file2mod = {fp: m for fp, m in file2mod.items() if m}
mod2file = {m: fp for fp, m in file2mod.items()}

# 4. 读取入口模块，排除含 demo 的项
patterns = [l.strip() for l in ENTRY_TXT.read_text(encoding="utf-8").splitlines() if l.strip()]
patterns = [p for p in patterns if not re.search(r"\bdemo\b", p)]
entries = [m for m in patterns if m in mod2file]
if not entries:
    sys.exit("❌ 没有在 full_files 中匹配到任何入口模块，请检查 entry_candidates.txt")
print(f"[*] 原始入口 ({len(entries)})：")
for m in entries:
    print("   ", m)

# 5. 筛选可 import 的顶层包
raw_pkgs = {repo.name for repo in repos}
valid_pkgs = [p for p in raw_pkgs if importlib.util.find_spec(p)]
if len(valid_pkgs) < len(raw_pkgs):
    print(f"⚠️ 剔除不可 import 包：{sorted(raw_pkgs - set(valid_pkgs))}")
raw_pkgs = valid_pkgs
if not raw_pkgs:
    sys.exit("❌ 找不到任何可 import 的顶层包")
print(f"[*] 顶层包：{raw_pkgs}")

# 6. 构建 import-graph & BFS 可达模块
print("[*] 构建 import-graph …")
t0 = time.time()
graph = build_graph(*raw_pkgs)
print(f"[*] 完成 build_graph，用时 {time.time()-t0:.1f}s")

reachable = set()
queue = entries.copy()
while queue:
    m = queue.pop()
    if m in reachable:
        continue
    reachable.add(m)
    try:
        children = graph.find_modules_directly_imported_by(m)
    except Exception:
        continue
    for c in children:
        if c not in reachable:
            queue.append(c)
print(f"[*] BFS 完成，可达模块数：{len(reachable)}")

# 7. 写入活跃 & 死代码文件列表，过滤 __init__.py
live_files = sorted({mod2file[m] for m in reachable if m in mod2file})
dead_files = sorted(set(full_files) - set(live_files))

LIVE_JSON.write_text(json.dumps(live_files, indent=2), encoding="utf-8")
with DEAD_TXT.open("w", encoding="utf-8") as f:
    for fp in dead_files:
        if not fp.endswith("__init__.py"):
            f.write(fp + "\n")

print(f"[✓] 活跃文件 {len(live_files)} → {LIVE_JSON}")
print(f"[✓] 死代码文件 {len(dead_files)} → {DEAD_TXT}")
