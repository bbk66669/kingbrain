#!/usr/bin/env python3
import json
import pathlib
import subprocess
import time
import sys
import tempfile
import os
import re
from collections import OrderedDict

# —— 配置路径 ——#
INSIGHT_ROOT = pathlib.Path(__file__).resolve().parent.parent
REMOTE_FILE  = INSIGHT_ROOT / "remote-repos.txt"
GRAPH_DIR    = INSIGHT_ROOT / "graphs"
GRAPH_DIR.mkdir(exist_ok=True)

DOT_F   = GRAPH_DIR / "system.dot"
SVG_F   = GRAPH_DIR / "system.svg"
MMD_F   = GRAPH_DIR / "system.mmd"
MER_SVG = GRAPH_DIR / "system.mmd.svg"   # mmdc 输出
PNG_F   = GRAPH_DIR / "system.mmd.png"   # cairosvg 输出

# 1. 读取仓库列表
if not REMOTE_FILE.exists():
    sys.exit(f"❌ 找不到 {REMOTE_FILE}，请先创建 remote-repos.txt")
repos = [
    pathlib.Path(line.strip().rsplit("/",1)[-1]).stem
    for line in REMOTE_FILE.read_text("utf-8").splitlines()
    if line.strip() and not line.startswith("#")
]
if not repos:
    sys.exit("❌ remote-repos.txt 中未解析到任何仓库")

print(f"🕘 开始生成依赖图，仓库列表：{repos}")
t0 = time.time()

# 2. 合并 pydeps 输出
with DOT_F.open("w", encoding="utf-8") as fp:
    fp.write('digraph KingBrain {\n  rankdir="LR"\n')
    for repo in repos:
        scan_dir = pathlib.Path("/root") / repo
        if not scan_dir.is_dir():
            print(f"⚠️ 未找到 /root/{repo}，跳过") 
            continue
        print(f"  ▶️ pydeps 扫描 {scan_dir} …", end="", flush=True)
        try:
            dot = subprocess.check_output([
                "pydeps","--noshow","--max-bacon","2","--show-dot",str(scan_dir)
            ], text=True, stderr=subprocess.DEVNULL)
            print(" done")
        except subprocess.CalledProcessError:
            print(" failed, skip")
            continue
        for ln in dot.splitlines():
            if ln.startswith("digraph") or ln.strip()=="}":
                continue
            fp.write(ln+"\n")
    fp.write("}\n")

# 3. Graphviz 渲染 SVG
print("🖼️  渲染 SVG …", end="", flush=True)
subprocess.run(["dot","-Tsvg","-o",str(SVG_F),str(DOT_F)], check=True)
print(" done")

# 4. DOT → Mermaid (.mmd)
print("📝  生成 Mermaid 文件 …", end="", flush=True)
edges=[]
for ln in DOT_F.read_text("utf-8").splitlines():
    ln=ln.strip()
    if "->" not in ln: continue
    clean=re.split(r"\s*\[", ln,1)[0]
    src,dst=clean.split("->",1)
    src=src.strip().strip('"'); dst=dst.strip().strip('";')
    edges.append((src,dst))
node_ids=OrderedDict()
for s,d in edges:
    if s not in node_ids: node_ids[s]=f"n{len(node_ids)+1}"
    if d not in node_ids: node_ids[d]=f"n{len(node_ids)+1}"
lines=["flowchart LR"]
for node,nid in node_ids.items():
    label=node.replace('"','\\"')
    lines.append(f'{nid}["{label}"]')
for s,d in edges:
    lines.append(f"{node_ids[s]} --> {node_ids[d]}")
MMD_F.write_text("\n".join(lines), "utf-8")
print(" done")

# 5. mmdc → Mermaid SVG
print("🎨  调用 mmdc 生成 Mermaid SVG …", end="", flush=True)
try:
    cfg={"args":["--no-sandbox","--disable-setuid-sandbox"]}
    with tempfile.NamedTemporaryFile("w+",suffix=".json",delete=False) as tf:
        json.dump(cfg,tf); tf.flush(); cfg_path=tf.name
    subprocess.run([
        "mmdc","-i",str(MMD_F),
        "-o",str(MER_SVG),
        "-p",cfg_path
    ], check=True)
    os.remove(cfg_path)
    print(" done")
except FileNotFoundError:
    print("\n❌ mmdc 未找到，请安装 mermaid-cli")
except subprocess.CalledProcessError as e:
    print(f"\n❌ mmdc 执行失败: {e}")

# 6. cairosvg → PNG
print("🖼️  用 cairosvg 生成 PNG …", end="", flush=True)
try:
    import cairosvg
    cairosvg.svg2png(url=str(MER_SVG), write_to=str(PNG_F))
    print(" done")
except ImportError:
    print("\n❌ cairosvg 未安装，请 pip install cairosvg")
except Exception as e:
    print(f"\n❌ cairosvg 转换失败: {e}")

print(f"[✓] 完成： DOT:{DOT_F.name} SVG:{SVG_F.name} MMD:{MMD_F.name} "
      f"MER_SVG:{MER_SVG.name} PNG:{PNG_F.name} 耗时{time.time()-t0:.1f}s")
