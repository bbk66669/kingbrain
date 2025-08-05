#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
evaluate_qa.py – 批跑标准问答集并收集评测结果

CHANGELOG
- 通过 ask_code.run_query() 复用查询逻辑。
- 支持非交互 (--non-interactive) 与交互两种模式；非交互时根据简单规则打分：
  - 若答案不包含“信息不足”，且返回了 chunks，则判定相关。
- 输出 qa_eval.csv；同时保留 search_log.csv 由 ask_code 写入。
"""

import json
import pathlib
import asyncio
import argparse

HERE = pathlib.Path(__file__).resolve()
ROOT = HERE.parent.parent

from ask_code import run_query  # 复用

QA_SET = ROOT / "qa_set.json"
EVAL_CSV = ROOT / "qa_eval.csv"

QA_EXAMPLES = [
    {"q": "load_balance function purpose", "tags": ["loadbalance"]},
    {"q": "agent_decide_and_execute parameters", "tags": ["parameters"]},
]

async def eval_one(qa, non_interactive: bool):
    res = await run_query(qa["q"], json_out=True)
    ans = res.get("answer", "")
    chunks = res.get("chunks", [])
    if non_interactive:
        ok = bool(chunks) and ("信息不足" not in ans)
    else:
        print(f"Q: {qa['q']}\nA: {ans[:500]}\n相关(y/n)? ", end="", flush=True)
        ok = input().strip().lower() == "y"
    return qa["q"], ok

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--non-interactive", action="store_true")
    args = ap.parse_args()

    try:
        qa_set = json.loads(QA_SET.read_text(encoding="utf-8"))
    except Exception:
        qa_set = QA_EXAMPLES

    EVAL_CSV.write_text("question,relevant\n", encoding="utf-8")

    for qa in qa_set:
        q, ok = await eval_one(qa, args.non_interactive)
        with EVAL_CSV.open("a", encoding="utf-8") as f:
            f.write(f"\"{q}\",{str(ok).lower()}\n")

if __name__ == "__main__":
    asyncio.run(main())
