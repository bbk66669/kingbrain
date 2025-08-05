#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
split_by_ast.py

A1-A7 完整实现 & 修复
- 修复未定义变量 __file__、中文裸文本、正则/属性名错误（type(node).__name__）。
- 同义词/权重读取自 scripts/tag_synonyms.json；若未找到则使用内置默认。
- A2: 超长逻辑块二次切分；A7: level 参数；对 Assign 等无 end_lineno 的保守合并策略。
- A3: moduleName/importPath；A4: 中文分词；A5: 同义词映射；碎片检测；输出 chunks.json。
- 新增：calls/called_by/imports/docstring 字段的占位（为空列表/空串），便于 schema 一致。
- HTML 可视化由 visualize_chunks.py 负责，这里只产出数据。
"""

import ast
import pathlib
import json
import argparse
import re
import logging
import os
from typing import List, Dict, Tuple

import jieba

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

HERE = pathlib.Path(__file__).resolve()
ROOT = pathlib.Path(os.getenv("ROOT_DIR", str(HERE.parent.parent)))

LIVE_JSON = ROOT / "live_files.json"
OUT = ROOT / "chunks.json"

MIN_LINES = 4
MAX_LOGIC_LINES = 100
LEVELS = ["function", "class", "block"]

STOP_WORDS = {
    "def", "class", "return", "if", "for", "while", "and", "or", "import",
    "函数", "类", "返回", "如果", "循环", "导入"
}
_kw_re = re.compile(r"[A-Za-z]{3,}|[\u4e00-\u9fa5]+")

# 加载同义词映射
def load_tag_synonyms() -> Dict[str, str]:
    candidates = [
        ROOT / "scripts" / "tag_synonyms.json",
        ROOT / "tag_synonyms.json",
    ]
    for p in candidates:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {
        "lb": "loadbalance",
        "balance": "loadbalance",
        "bal": "loadbalance",
        "retry": "retry",
        "backoff": "retry",
        "err": "error_handling",
        "exception": "error_handling",
    }

TAG_SYNONYMS = load_tag_synonyms()

TAG_RULES = {
    "load_balance": "loadbalance", "lb_": "loadbalance", "retry": "retry", "backoff": "retry",
    "socket": "network", "ws_send": "network", "trailing_mgr": "trailing_mgr",
    "update": "update_logic", "config": "config", "error": "error_handling", "api": "api"
}

CALL_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(")

def _normalize_token(w: str) -> str:
    w = w.strip()
    if w in TAG_SYNONYMS:
        return TAG_SYNONYMS[w]
    w = w.replace("load_balance", "loadbalance")
    if w == "lb":
        return "loadbalance"
    return w

def extract_keywords(text: str, limit: int = 15, corpus=None) -> List[str]:
    scored: Dict[str, float] = {}
    words: List[str] = []
    if any('\u4e00' <= c <= '\u9fa5' for c in text):
        words.extend([w for w in jieba.cut(text.lower()) if w not in STOP_WORDS])
    else:
        words.extend([w for w in _kw_re.findall(text.lower()) if w not in STOP_WORDS])
    for w in words:
        w2 = _normalize_token(w)
        scored[w2] = scored.get(w2, 0.0) + 1.0
    domain_weights = {
        "loadbalance": 2, "retry": 1.5, "network": 1.5,
        "trailing_mgr": 2, "update_logic": 1.5, "config": 1.5, "error_handling": 1.5, "api": 1.5
    }
    scored = {w: s * domain_weights.get(w, 1.0) for w, s in scored.items()}
    special = [w for w in scored if "_" in w or (w and w[0].isupper())]
    ordered = special + sorted([w for w in scored if w not in special], key=lambda x: scored[x], reverse=True)
    return ordered[:limit]

def split_large_logic_block(src_lines: List[str], start: int, end: int, max_lines: int) -> List[Tuple[int, int]]:
    if end - start + 1 <= max_lines:
        return [(start, end)]
    chunks = []
    cur = start
    while cur <= end:
        chunks.append((cur, min(cur + max_lines - 1, end)))
        cur += max_lines
    return chunks

def _safe_end_lineno(node, src_lines: List[str]) -> int:
    end = getattr(node, "end_lineno", None)
    if end is not None:
        return end
    # 保守策略：对 Assign 等无 end_lineno 的节点，向后合并到下一空行/缩进减少/最多 5 行
    start = node.lineno
    max_add = 5
    line_count = len(src_lines)
    cur = start
    base_indent = len(src_lines[start - 1]) - len(src_lines[start - 1].lstrip())
    while cur < line_count and max_add > 0:
        nxt = src_lines[cur]
        indent = len(nxt) - len(nxt.lstrip())
        if nxt.strip() == "":
            cur += 1
            break
        if indent < base_indent:
            break
        cur += 1
        max_add -= 1
    return max(cur, start)

def build_parent_map(tree: ast.AST) -> Dict[ast.AST, ast.AST]:
    pm: Dict[ast.AST, ast.AST] = {}
    for p in ast.walk(tree):
        for c in ast.iter_child_nodes(p):
            pm[c] = p
    return pm

def get_parent_signature(node: ast.AST, pm: Dict[ast.AST, ast.AST]) -> List[str]:
    lst = []
    cur = node
    while cur in pm:
        cur = pm[cur]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            lst.append(f"{type(cur).__name__}:{getattr(cur, 'name', '')}")
    return lst[::-1]

def _collect_calls(text: str) -> List[str]:
    return list(sorted(set(m.group(1) for m in CALL_RE.finditer(text))))

def extract_chunk(fp: pathlib.Path, start: int, end: int, sig: str,
                  parents: List[str], params: List[str], src_lines: List[str],
                  mod_name: str, imp_path: str) -> Dict:
    body = "\n".join(src_lines[start - 1:end])
    if len(body.splitlines()) < MIN_LINES:
        return None
    summary_cn = sig.replace("FunctionDef:", "函数 ").replace("AsyncFunctionDef:", "异步函数 ").replace("ClassDef:", "类 ")
    header = f"# Summary: {summary_cn}\n# Params: {', '.join(params) if params else '无'}\n"
    tags = extract_keywords(body)
    for kw, tag in [("update", "update_logic"), ("config", "config"),
                    ("error", "error_handling"), ("api", "api")]:
        if kw in body.lower():
            tags.append(tag)
    tags = list({TAG_RULES.get(t, t) for t in tags})

    docstring = ""
    # 简易提取：若开头存在三引号块
    if '"""' in body or "'''" in body:
        # 粗略提取第一段
        m = re.search(r'("""|\'\'\')(.*?)(\1)', body, flags=re.S)
        if m:
            docstring = m.group(2).strip()

    calls = _collect_calls(body)

    return {
        "filePath": fp.as_posix(),
        "startLine": start,
        "endLine": end,
        "signature": sig,
        "parentSignature": parents,
        "moduleName": mod_name,
        "importPath": imp_path,
        "content": header + body,
        "tags": tags,
        "calls": calls,
        "called_by": [],
        "imports": [],
        "docstring": docstring,
    }

def chunks_from_file(fp: pathlib.Path, corpus=None, level: str = "function") -> List[Dict]:
    src = fp.read_text(encoding="utf-8", errors="ignore")
    src_lines = src.splitlines()
    try:
        tree = ast.parse(src, filename=str(fp))
    except Exception as e:
        logging.warning(f"AST parse failed {fp}: {e}")
        return []
    mod_name = fp.parent.name
    try:
        imp_path = fp.parent.relative_to(ROOT).as_posix() if fp.parent != ROOT else "root"
    except Exception:
        imp_path = fp.parent.as_posix()

    pm = build_parent_map(tree)

    if level == "function":
        nodes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                 ast.If, ast.For, ast.While, ast.Try, ast.With, ast.ExceptHandler, ast.Assign)
    elif level == "class":
        nodes = (ast.ClassDef,)
    else:  # block
        nodes = (ast.With, ast.ExceptHandler, ast.Assign, ast.If, ast.For, ast.While, ast.Try)

    chunks: List[Dict] = []
    for node in ast.walk(tree):
        if isinstance(node, nodes):
            start = getattr(node, "lineno", 1)
            end = _safe_end_lineno(node, src_lines)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = getattr(node, "name", "")
            else:
                name = type(node).__name__
            sig = f"{type(node).__name__}:{name}"

            params: List[str] = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = list(getattr(node.args, "args", []))
                defaults = list(getattr(node.args, "defaults", []))
                pad = len(args) - len(defaults)
                for i, a in enumerate(args):
                    ann = getattr(a, "annotation", None)
                    ptype = ast.unparse(ann) if ann is not None else "None"
                    if i < pad:
                        default = "None"
                    else:
                        default = ast.unparse(defaults[i - pad])
                    params.append(f"{a.arg}:{ptype}={default}")

            parents = get_parent_signature(node, pm)

            for s, e in split_large_logic_block(src_lines, start, end, MAX_LOGIC_LINES):
                c = extract_chunk(fp, s, e, sig, parents, params, src_lines, mod_name, imp_path)
                if c:
                    chunks.append(c)
    return chunks

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=4)
    ap.add_argument("--min-lines-range", type=str, default="4,8,12")
    ap.add_argument("--max-logic-lines", type=int, default=100)
    ap.add_argument("--level", choices=LEVELS, default="function")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    global MIN_LINES, MAX_LOGIC_LINES
    MIN_LINES = args.min
    MAX_LOGIC_LINES = args.max_logic_lines

    LIVE = []
    try:
        LIVE = json.loads(LIVE_JSON.read_text(encoding="utf-8"))
    except Exception:
        logging.error(f"无法读取 {LIVE_JSON}")

    if args.selftest:
        if not LIVE:
            logging.error("LIVE 列表为空，selftest 失败")
            return
        fp = pathlib.Path(LIVE[0])
        corpus = [pathlib.Path(f).read_text(encoding="utf-8", errors="ignore") for f in LIVE]
        chunks = chunks_from_file(fp, corpus, args.level)
        logging.info(f"Selftest: {len(chunks)} chunks generated" if chunks else "Selftest failed")
        return

    min_range = [int(x) for x in args.min_lines_range.split(",") if x.strip()]
    corpus = [pathlib.Path(f).read_text(encoding="utf-8", errors="ignore") for f in LIVE] if LIVE else []

    results = []
    for ml in min_range:
        MIN_LINES = ml
        chunks = [c for f in LIVE for c in chunks_from_file(pathlib.Path(f), corpus, args.level)]
        avg = sum(c["endLine"] - c["startLine"] + 1 for c in chunks) / len(chunks) if chunks else 0
        frags = len([c for c in chunks if c["endLine"] - c["startLine"] + 1 < avg * 0.5]) if chunks else 0
        prec = 1 - frags / len(chunks) if chunks else 0
        results.append({
            "min_lines": ml,
            "avg_lines": avg,
            "fragment_ratio": (frags / len(chunks)) if chunks else 0,
            "precision": prec
        })
    if results:
        best = max(results, key=lambda x: x["precision"])
        logging.info(f"推荐 MIN_LINES={best['min_lines']} 平均长度={best['avg_lines']:.1f} 碎片比={best['fragment_ratio']:.2%}")
        (ROOT / "min_lines_stats.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        MIN_LINES = best["min_lines"]

    chunks = [c for f in LIVE for c in chunks_from_file(pathlib.Path(f), corpus, args.level)]
    logging.info(f"[✓] split_by_ast → {len(chunks)} blocks")
    OUT.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
