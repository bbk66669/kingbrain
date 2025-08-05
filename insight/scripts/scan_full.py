#!/usr/bin/env python3
"""遍历 repos.txt 列出的目录，把所有 *.py 列到 full_files.json"""
import pathlib, json, time, sys, os

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT  = ROOT / "full_files.json"
repos = [pathlib.Path(p.strip()) for p in (ROOT/"repos.txt").read_text().splitlines() if p.strip()]
all_files = []

t0 = time.time()
for repo in repos:
    for f in repo.rglob("*.py"):
        if f.is_file():
            all_files.append(str(f.resolve()))
with OUT.open("w") as fp:
    json.dump(all_files, fp, indent=2)
print(f"[✓] 共索引 {len(all_files)} 个 .py → {OUT}, 耗时 {time.time()-t0:.1f}s")
