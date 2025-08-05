#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
perf_test.py – 批量切分性能基准
"""

import json, pathlib, time, logging
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

HERE = pathlib.Path(__file__).resolve()
ROOT = pathlib.Path(os.getenv("ROOT_DIR", str(HERE.parent.parent)))

LIVE_JSON = ROOT/"live_files.json"

def run(files: int, concur: int):
    live = json.loads(LIVE_JSON.read_text(encoding="utf-8"))[:files]
    corpus = [pathlib.Path(f).read_text(encoding="utf-8",errors="ignore") for f in live]
    from split_by_ast import chunks_from_file
    start = time.time(); chunks = []
    with ThreadPoolExecutor(max_workers=concur) as ex:
        results = list(ex.map(lambda f: chunks_from_file(pathlib.Path(f), corpus), live))
    split_t = time.time() - start
    for sub in results:
        chunks.extend(sub)
    logging.info(f"files={files} concurrency={concur} split_time={split_t:.2f}s chunks={len(chunks)}")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", type=int, default=30)
    ap.add_argument("--concurrency", type=int, default=5)
    args = ap.parse_args()
    run(args.files, args.concurrency)
