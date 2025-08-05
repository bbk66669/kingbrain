#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ask_code.py — 代码语义问答

适配 emb_ingest 新多版本字段，支持中文/英文，支持 tags 检索、预算控制、Prometheus 监控。
依赖：openai>=1.3.0、prometheus-client、aiohttp、spacy、jieba、rapidfuzz、dotenv
"""

import os
import sys
import re
import json
import textwrap
import argparse
import logging
import asyncio
import aiohttp
import time
import uuid
import atexit
import signal
import threading
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
import jieba
from rapidfuzz import fuzz

load_dotenv()

TRACE_ID = os.getenv("TRACE_ID", uuid.uuid4().hex)

_base_logger = logging.getLogger("ask_code")
logging.basicConfig(
    stream=sys.stderr, level=logging.INFO,
    format="%(levelname)s: %(message)s"
)
logger = logging.LoggerAdapter(_base_logger, {"trace_id": TRACE_ID})

try:
    from openai import AsyncOpenAI
except ImportError:
    sys.exit("❌ 需要 openai>=1.3.0，请先运行：pip install -U openai")
ai = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

import spacy
_nlp = spacy.load("en_core_web_sm")

WEAVIATE_URL        = os.getenv("WEAVIATE_URL", "http://127.0.0.1:8080").rstrip("/")
EMBED_MODEL         = os.getenv("EMBED_MODEL", "text-embedding-3-large")
QA_MODEL            = os.getenv("QA_MODEL", "gpt-4-turbo")
FALLBACK_MODEL      = os.getenv("FALLBACK_MODEL", "gpt-4o-mini")
CONF_THRESH         = float(os.getenv("CONF_THRESHOLD", "0.25"))
TOPK                = int(os.getenv("VECTOR_LIMIT", "15"))
AUTO_CONFIRM        = os.getenv("AUTO_CONFIRM", "y").lower().startswith("y")
MAX_SNIPPET         = int(os.getenv("MAX_SNIPPET_CHARS", "600"))
MIN_LINES           = int(os.getenv("MIN_LINES", "10"))
FEEDBACK_TIMEOUT    = int(os.getenv("FEEDBACK_TIMEOUT", "30"))
PROM_PORT           = int(os.getenv("PROM_PORT_ASK", "9000"))
EMBED_CONCURRENCY   = int(os.getenv("EMBED_CONCURRENCY", "5"))
WEIGHTS_CONFIG_FILE = os.getenv("WEIGHTS_CONFIG_FILE", "./query_weights.json")
PUSHGATEWAY_URL     = os.getenv("PUSHGATEWAY_URL", "")
PUSHGATEWAY_JOB     = os.getenv("PUSHGATEWAY_JOB", "ask_code")
MAX_BUDGET_USD      = float(os.getenv("MAX_BUDGET_USD", "100.0"))
EMBED_VERSION       = os.getenv("EMBED_VERSION", "v1")

# Pricing (USD per 1K tokens)
PRICING = {
    "text-embedding-3-large": {"prompt": 0.13, "completion": 0.13},
    "text-embedding-3-small": {"prompt": 0.02, "completion": 0.02},
    "gpt-4-turbo":   {"prompt": 0.01, "completion": 0.03},
    "gpt-4o-mini":   {"prompt": 0.003, "completion": 0.006},
}

from prometheus_client import Counter, Histogram, Gauge, start_http_server, CollectorRegistry, push_to_gateway

_registry = CollectorRegistry()
if os.getenv("METRICS_EMBEDDED", "true").lower().startswith("t"):
    start_http_server(PROM_PORT, registry=_registry)

search_counter     = Counter("code_search_total", "Total number of code searches", registry=_registry)
search_latency     = Histogram("code_search_latency_seconds", "Code search latency", registry=_registry)
query_type_counter = Counter("code_query_type_total", "Query type", ["type"], registry=_registry)
recall_precision   = Gauge("recall_precision", "Search recall precision", registry=_registry)
false_positives    = Gauge("false_positives", "False positive rate", registry=_registry)
token_usage_prompt = Counter("openai_tokens_prompt", "Prompt tokens", ["model"], registry=_registry)
token_usage_comp   = Counter("openai_tokens_completion", "Completion tokens", ["model"], registry=_registry)
api_errors         = Counter("openai_api_errors", "OpenAI API errors", ["type"], registry=_registry)
budget_spent_usd   = Gauge("budget_spent_usd", "Estimated budget spent (USD)", registry=_registry)

_stop_event = threading.Event()

def _price(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    p = PRICING.get(model) or PRICING.get(FALLBACK_MODEL, {"prompt": 0.0, "completion": 0.0})
    return (prompt_tokens / 1000.0) * p["prompt"] + (completion_tokens / 1000.0) * p["completion"]

def _accumulate_usage(model: str, usage):
    pt = getattr(usage, "prompt_tokens", 0) or usage.get("prompt_tokens", 0)
    ct = getattr(usage, "completion_tokens", 0) or usage.get("completion_tokens", 0)
    token_usage_prompt.labels(model=model).inc(pt)
    token_usage_comp.labels(model=model).inc(ct)
    cost = _price(model, pt, ct)
    try:
        current = budget_spent_usd._value.get()
        budget_spent_usd.set(current + cost)
    except Exception:
        budget_spent_usd.set(cost)

def _check_budget_raise():
    val = budget_spent_usd._value.get()
    if val > MAX_BUDGET_USD:
        raise RuntimeError(f"预算超限: ${val:.2f} > ${MAX_BUDGET_USD}")

def _push_metrics_daemon():
    if not PUSHGATEWAY_URL:
        return
    while not _stop_event.wait(60):
        try:
            push_to_gateway(PUSHGATEWAY_URL, job=PUSHGATEWAY_JOB, registry=_registry)
            logger.info("Metrics pushed to Pushgateway")
        except Exception as e:
            logger.error(f"Pushgateway failed: {e}")

_push_thread = threading.Thread(target=_push_metrics_daemon, name="metrics-push", daemon=True)
_push_thread.start()

def _on_exit(*_):
    _stop_event.set()
    if PUSHGATEWAY_URL:
        try:
            push_to_gateway(PUSHGATEWAY_URL, job=PUSHGATEWAY_JOB, registry=_registry)
        except Exception:
            pass
atexit.register(_on_exit)
signal.signal(signal.SIGTERM, _on_exit)
signal.signal(signal.SIGINT, _on_exit)

_STOPWORDS = {"the","and","of","to","in","for","a","is","on","return","函数","类","返回","如果","循环","导入"}
_kw_re = re.compile(r"[A-Za-z]{3,}|[\u4e00-\u9fa5]+")
def extract_keywords(text: str, limit: int = 8) -> List[str]:
    words = []
    if any('\u4e00' <= c <= '\u9fa5' for c in text):
        words.extend(jieba.cut(text.lower(), cut_all=False))
    else:
        words.extend(_kw_re.findall(text.lower()))
    freq: Dict[str, float] = {}
    for w in words:
        if w in _STOPWORDS:
            continue
        freq[w] = freq.get(w, 0) + 1
    domain_terms = ["loadbalance", "retry", "network", "trailing_mgr", "update_logic", "error_handling", "config", "api"]
    corrected: Dict[str, float] = {}
    for w, f in freq.items():
        if any('\u4e00' <= c <= '\u9fa5' for c in w):
            corrected[w] = f
        else:
            best = max(domain_terms, key=lambda t: fuzz.ratio(w, t))
            if fuzz.ratio(w, best) > 80:
                corrected[best] = corrected.get(best, 0) + f
            else:
                corrected[w] = corrected.get(w, 0) + f
    domain_weights = {
        "loadbalance": 2, "retry": 1.5, "network": 1.5,
        "trailing_mgr": 2, "update_logic": 1.5, "error_handling": 1.5,
        "config": 1.5, "api": 1.5
    }
    scored = {w: f * domain_weights.get(w, 1.0) for w, f in corrected.items()}
    special = [w for w in scored if "_" in w or (w and w[0].isupper())]
    ordered = special + sorted((w for w in scored if w not in special),
                               key=lambda x: scored[x], reverse=True)
    return ordered[:limit]

def has_function_pattern(text: str) -> bool:
    return bool(re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\(", text))

def extract_function_names_from_query(query: str) -> List[str]:
    doc = _nlp(query)
    patterns = [r'\b([a-z][a-z0-9_]+)\b', r'\b([A-Z][a-zA-Z0-9_]+)\b']
    stop = {'function','method','class','code','implementation','details','purpose','parameters','logic','update'}
    names = [m for p in patterns for m in re.findall(p, query)]
    names += [ent.text for ent in doc.ents if ent.label_ in ("ORG","PRODUCT")]
    return [n for n in names if n not in stop and len(n) > 2]

def generate_query_variants(question: str) -> List[str]:
    variants = [question]
    tech_syn = {
        'function': ['method','procedure','routine'],
        'parameters': ['arguments','args','inputs'],
        'purpose': ['goal','objective','functionality'],
        'implementation': ['code','logic','execution'],
        'update': ['modify','change','set'],
        'logic': ['algorithm','process','flow'],
    }
    ql = question.lower()
    for orig, syns in tech_syn.items():
        if orig in ql:
            for s in syns:
                variants.append(re.sub(rf'\b{orig}\b', s, question, flags=re.IGNORECASE))
    for ent in _nlp(question).ents:
        if ent.label_ in ("ORG","PRODUCT"):
            variants += [f"{ent.text} docstring", f"{ent.text} summary",
                         f"{ent.text} parameters", f"{ent.text} update logic"]
    if has_function_pattern(question):
        for n in extract_function_names_from_query(question):
            variants += [f"{n} parameters", f"{n} parent"]
    seen = set(); out = []
    for v in variants:
        if v not in seen:
            seen.add(v); out.append(v)
        if len(out) >= 8: break
    return out

def get_weights(question: str) -> Dict[str, float]:
    try:
        with open(WEIGHTS_CONFIG_FILE, "r") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {
            "purpose": {"exact": 1.2, "def": 1.3, "content": 0.8, "context": 0.6, "keywords": 0.4, "tags": 0.5},
            "implementation": {"exact": 1.0, "def": 0.8, "content": 1.3, "context": 0.8, "keywords": 0.5, "tags": 0.4},
            "parameter": {"exact": 1.1, "def": 1.2, "content": 0.7, "context": 0.5, "keywords": 0.6, "tags": 0.3},
            "default": {"exact": 1.0, "def": 0.9, "content": 0.9, "context": 0.6, "keywords": 0.3, "tags": 0.4},
        }
    ql = question.lower()
    for k, v in cfg.items():
        if k in ql:
            return v
    return cfg["default"]

_sema = asyncio.Semaphore(EMBED_CONCURRENCY)

async def a_chat(model: str, prompt: str, timeout: int = 30) -> str:
    async with _sema:
        try:
            resp = await asyncio.wait_for(
                ai.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2
                ),
                timeout=timeout
            )
            usage = resp.usage
            _accumulate_usage(model, usage)
            _check_budget_raise()
            return resp.choices[0].message.content
        except asyncio.TimeoutError as e:
            api_errors.labels(type="timeout").inc()
            raise
        except Exception as e:
            api_errors.labels(type=type(e).__name__).inc()
            msg = str(e)
            if "429" in msg or "Rate" in msg or "overloaded" in msg:
                await asyncio.sleep(10)
                return await a_chat(model, prompt, timeout)
            raise

async def a_embed(text: str, etype: str = "content", timeout: int = 30) -> List[float]:
    async with _sema:
        try:
            resp = await asyncio.wait_for(
                ai.embeddings.create(
                    model=EMBED_MODEL,
                    input=text,
                    user=etype
                ),
                timeout=timeout
            )
            usage = resp.usage
            _accumulate_usage(EMBED_MODEL, {"prompt_tokens": usage.total_tokens, "completion_tokens": 0})
            _check_budget_raise()
            return resp.data[0].embedding
        except asyncio.TimeoutError:
            api_errors.labels(type="timeout").inc()
            raise
        except Exception as e:
            api_errors.labels(type=type(e).__name__).inc()
            msg = str(e)
            if "429" in msg or "Rate" in msg or "overloaded" in msg:
                await asyncio.sleep(10)
                return await a_embed(text, etype, timeout)
            raise

async def _gql(query: Dict[str, Any], timeout: int = 15) -> Dict[str, Any]:
    async with aiohttp.ClientSession() as sess:
        try:
            async with sess.post(f"{WEAVIATE_URL}/v1/graphql",
                                 headers={"Content-Type": "application/json"},
                                 json=query, timeout=timeout) as resp:
                resp.raise_for_status()
                data = await resp.json()
                if "errors" in data:
                    msgs = "; ".join(e.get("message", "") for e in data["errors"])
                    raise RuntimeError(f"GraphQL error(s): {msgs}")
                return data
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(f"GraphQL request failed: {e}")
            raise

_FIELDS = """filePath startLine endLine signature content calls called_by imports
docstring tags parentSignature moduleName importPath embedType embedVersion
_additional { distance }"""

def _where_embed_version():
    return f'{{ operator: Equal, path: ["embedVersion"], valueString: "{EMBED_VERSION}" }}'

async def gql_vec(vec: List[float], k: int, embed_type: str) -> List[dict]:
    q = {"query": f"""
{{
  Get {{
    CodeChunk(
      nearVector: {{ vector: {json.dumps(vec)} }},
      limit: {k},
      where: {{
        operator: And, operands: [
          {{ path: ["embedType"], operator: Equal, valueString: "{embed_type}" }},
          {_where_embed_version()}
        ]
      }}
    ) {{ {_FIELDS} }}
  }}
}}"""}
    return (await _gql(q, timeout=12))["data"]["Get"]["CodeChunk"]

async def gql_keyword(term: str, k: int = 20) -> List[dict]:
    q = {"query": f"""
{{
  Get {{
    CodeChunk(
      where: {{
        operator: And, operands: [
          {{ operator: Or, operands: [
            {{ path: ["content"], operator: Like, valueString: "{term}" }},
            {{ path: ["tags"], operator: ContainsAny, valueStringArray: ["{term}"] }},
            {{ path: ["docstring"], operator: Like, valueString: "{term}" }}
          ] }},
          {_where_embed_version()}
        ]
      }},
      limit: {k}
    ) {{ {_FIELDS} }}
  }}
}}"""}
    try:
        return (await _gql(q, timeout=10))["data"]["Get"]["CodeChunk"]
    except Exception:
        return []

async def gql_tags(tags: List[str], k: int = 20) -> List[dict]:
    q = {"query": f"""
{{
  Get {{
    CodeChunk(
      where: {{
        operator: And, operands: [
          {{ path: ["tags"], operator: ContainsAny, valueStringArray: {json.dumps(tags)} }},
          {_where_embed_version()}
        ]
      }},
      limit: {k}
    ) {{ {_FIELDS} }}
  }}
}}"""}
    try:
        return (await _gql(q, timeout=10))["data"]["Get"]["CodeChunk"]
    except Exception:
        return []

async def search_exact_function(fname: str, k: int = 10) -> List[dict]:
    q = {"query": f"""
{{
  Get {{
    CodeChunk(
      where: {{
        operator: And, operands: [
          {{ path: ["signature"], operator: Like, valueString: "{fname}" }},
          {_where_embed_version()}
        ]
      }},
      limit: {k}
    ) {{ {_FIELDS} }}
  }}
}}"""}
    try:
        return (await _gql(q, timeout=10))["data"]["Get"]["CodeChunk"]
    except Exception:
        return []

async def search_by_keywords(keywords: List[str]) -> List[dict]:
    tasks = [gql_keyword(kw) for kw in keywords]
    res = await asyncio.gather(*tasks, return_exceptions=True)
    merged: List[dict] = []
    for r in res:
        if isinstance(r, list):
            merged.extend(r)
    return [d for d in merged if d["endLine"] - d["startLine"] + 1 >= MIN_LINES]

async def search_by_parent_chain(parent_sigs: List[str]) -> List[dict]:
    if not parent_sigs:
        return []
    q = {"query": f"""
{{
  Get {{
    CodeChunk(
      where: {{
        operator: And, operands: [
          {{ path: ["parentSignature"], operator: ContainsAny, valueStringArray: {json.dumps(parent_sigs)} }},
          {_where_embed_version()}
        ]
      }},
      limit: 20
    ) {{ {_FIELDS} }}
  }}
}}"""}
    try:
        return (await _gql(q, timeout=10))["data"]["Get"]["CodeChunk"]
    except Exception:
        return []

async def search_file_chunks(fname: str, limit: int = 500) -> List[dict]:
    q = {"query": f"""
{{
  Get {{
    CodeChunk(
      where: {{
        operator: And, operands: [
          {{ path: ["filePath"], operator: Like, valueString: "{fname}" }},
          {_where_embed_version()}
        ]
      }},
      limit: {limit}
    ) {{
      filePath startLine endLine content
    }}
  }}
}}"""}
    data = (await _gql(q, timeout=15))["data"]["Get"]["CodeChunk"]
    return sorted(data, key=lambda x: x["startLine"])

def rerank(question: str, **sources) -> List[dict]:
    weights = get_weights(question)
    all_res: Dict[tuple, dict] = {}
    seen = set()
    for key, lst in sources.items():
        w = weights.get(key, 1.0)
        for r in lst:
            tup = (r["filePath"], r["startLine"], r["endLine"], r.get("embedType", ""))
            if tup in seen:
                continue
            seen.add(tup)
            dist = r.get("_additional", {}).get("distance", 1.0)
            score = max(0, 1 - dist) * w
            qk = set(extract_keywords(question))
            tags = set(r.get("tags", []) or [])
            calls = set(r.get("calls", []) or [])
            overlap = len(qk & (tags | calls)) / max(len(qk), 1)
            bonus = overlap * 0.4
            if r.get("docstring"):
                bonus += 0.15
            if r.get("embedType") == "def":
                bonus += 0.1
            r["_final_score"] = score + bonus
            all_res[tup] = r
    return sorted(all_res.values(), key=lambda x: x["_final_score"], reverse=True)

def query_category(q: str) -> str:
    ql = q.lower()
    if any(k in ql for k in ("purpose", "summary", "功能说明")):
        return "purpose"
    if any(k in ql for k in ("implementation", "逻辑", "实现")):
        return "implementation"
    if any(k in ql for k in ("parameter", "参数")):
        return "parameter"
    return "default"

def get_adaptive_threshold(question: str, results: List[dict]) -> float:
    base_map = {"purpose": 0.3, "implementation": 0.25, "parameter": 0.2, "default": 0.25}
    base = base_map[query_category(question)]
    dists = [r["_additional"]["distance"] for r in results if r.get("_additional")]
    scores = [r.get("_final_score", 0) for r in results]
    if not dists or not scores:
        return base
    mn, avg = min(dists), sum(dists) / len(dists)
    avg_score = sum(scores) / len(scores)
    if mn > 0.6:
        base = min(base + 0.15, 0.7)
    if avg > 0.5:
        base = min(base + 0.1, 0.6)
    if avg_score < 0.6:
        base = min(base + 0.05, 0.7)
    if avg_score > 0.8:
        base = max(base - 0.05, 0.15)
    return base

def truncate_snippet(content: str) -> str:
    if len(content) <= MAX_SNIPPET:
        return content
    sn = content[:MAX_SNIPPET]
    ld = sn.rfind('.')
    ln = sn.rfind('\n')
    cut = max(ld, ln)
    if cut >= int(MAX_SNIPPET * 0.5):
        sn = sn[:cut]
    return sn + "..."

async def multi_stage_search(question: str) -> List[dict]:
    start = time.time()
    search_counter.inc()
    query_type_counter.labels(type=query_category(question)).inc()

    funcs = extract_function_names_from_query(question)
    variants = generate_query_variants(question)

    def_tasks = [a_embed(v, "def") for v in variants]
    con_tasks = [a_embed(v, "content") for v in variants]
    vec_def, vec_cont = await asyncio.gather(
        asyncio.gather(*def_tasks, return_exceptions=True),
        asyncio.gather(*con_tasks, return_exceptions=True),
    )
    vec_def = [v for v in vec_def if isinstance(v, list)]
    vec_cont = [v for v in vec_cont if isinstance(v, list)]

    tasks: List[asyncio.Task] = []
    tasks += [search_exact_function(fn) for fn in funcs]
    tasks += [gql_vec(v, k=TOPK, embed_type="def") for v in vec_def]
    tasks += [gql_vec(v, k=TOPK, embed_type="content") for v in vec_cont]
    tasks += [search_by_keywords(extract_keywords(question))]
    tasks += [search_by_parent_chain(funcs)]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    i = 0
    exact, sem_def, sem_cont = [], [], []
    for _ in funcs:
        if isinstance(results[i], list): exact.extend(results[i])
        i += 1
    for _ in vec_def:
        if isinstance(results[i], list): sem_def.extend(results[i])
        i += 1
    for _ in vec_cont:
        if isinstance(results[i], list): sem_cont.extend(results[i])
        i += 1
    kw_res = results[i] if isinstance(results[i], list) else []; i += 1
    pc_res = results[i] if isinstance(results[i], list) else []

    merged = rerank(
        question,
        **{
            "exact":   exact,
            "def":     sem_def,
            "content": sem_cont,
            "keywords": kw_res,
            "context":  pc_res,
        }
    )
    if merged:
        relevant = sum(1 for r in merged if r.get("_final_score", 0) > 0.5)
        recall_precision.set(relevant / len(merged))
        false_positives.set((len(merged) - relevant) / len(merged))

    try:
        with open("search_log.csv", "a", encoding="utf-8") as f:
            for r in merged[:TOPK]:
                f.write(
                    f'"{question}","{r.get("filePath","")}","{r.get("embedType","")}","{r.get("embedVersion","")}",'
                    f'{r.get("startLine",0)},{r.get("endLine",0)},{r.get("_additional",{}).get("distance",1.0)}\n'
                )
    except Exception as e:
        logger.error(f"写入 search_log.csv 失败: {e}")

    search_latency.observe(time.time() - start)
    return merged

async def run_query(question: str, json_out: bool = False) -> Dict[str, Any]:
    if not question:
        raise ValueError("question is empty")
    m = re.search(r'([\w\-.]+\.py)', question)
    if m:
        fname = m.group(1)
        try:
            chunks = await search_file_chunks(fname)
            if not chunks:
                return {"answer": f"未找到文件 {fname} 的代码片段。", "chunks": []}
            whole = "\n".join(c["content"] for c in chunks)
            ans = await a_chat(
                QA_MODEL,
                f"下面是脚本 {fname} 的全部代码，请用简洁要点总结 3 大核心功能：\n```python\n{whole}\n```"
            )
            return {"answer": ans, "chunks": chunks}
        except Exception as e:
            logger.error(f"File overview failed: {e}")
            return {"answer": f"文件总览失败: {e}", "chunks": []}

    try:
        docs = await multi_stage_search(question)
    except Exception as e:
        logger.error(f"检索失败: {e}，尝试关键字 fallback")
        try:
            kws = extract_keywords(question)
            fallback = await search_by_keywords(kws)
            if not fallback:
                return {"answer": "未检索到相关片段。", "chunks": []}
            docs = fallback
        except Exception as e2:
            return {"answer": f"关键字搜索失败: {e2}", "chunks": []}

    docs = [d for d in docs if isinstance(d, dict) and d.get("_additional")]

    if not docs:
        return {"answer": "未检索到相关片段。", "chunks": []}

    thresh = get_adaptive_threshold(question, docs)
    best = min(d["_additional"]["distance"] for d in docs)
    if best > thresh + 0.05 and not AUTO_CONFIRM:
        return {"answer": f"距离 {best:.3f} 超过阈值 {thresh:.3f}，已取消。", "chunks": docs}

    ctxs = []
    for d in docs[:TOPK]:
        hdr = f'【{d["filePath"]}:{d["startLine"]}-{d["endLine"]} · {d.get("signature","")} · dist={d["_additional"]["distance"]:.3f} · embedType={d.get("embedType","")}】'
        ctxs.append(hdr + "\n" + textwrap.indent(truncate_snippet(d.get("content","")), "    "))

    prompt = "你是一名资深 Python 工程师，仅凭下列代码片段回答提问；如信息不足请回答“信息不足”。\n"
    prompt += "\n".join(ctxs) + f"\n\n问题：{question}\n回答："
    try:
        answer = await a_chat(QA_MODEL, prompt)
        with open("search_log.csv", "a", encoding="utf-8") as f:
            f.write(f'"{question}","ANSWER","{answer.replace(chr(10)," ")}"\n')
        return {"answer": answer, "chunks": docs}
    except Exception as e:
        logger.error(f"生成回答失败: {e}")
        return {"answer": f"生成回答失败: {e}", "chunks": docs}

async def _selftest():
    cases = [
        {"q": "load_balance function purpose", "check": lambda r: any("loadbalance" in (d.get("tags") or []) for d in r.get("chunks", []))},
        {"q": "agent_decide_and_execute parameters", "check": lambda r: "参数" in r.get("answer", "") or "parameter" in r.get("answer","").lower()},
    ]
    for c in cases:
        try:
            res = await run_query(c["q"], True)
            ok = c["check"](res)
            logger.info(f"Selftest {'PASS' if ok else 'FAIL'}: {c['q']}")
        except Exception as e:
            logger.error(f"Selftest error: {e}")

def _cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args, rest = ap.parse_known_args()
    return args, " ".join(rest).strip()

async def main():
    args, question = _cli()
    if args.selftest:
        await _selftest()
        return
    if not question:
        logger.error("缺少问题")
        return
    res = await run_query(question, json_out=args.json)
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        chunks = res.get("chunks", [])
        if chunks:
            ctxs = []
            for d in chunks[:TOPK]:
                if "_additional" in d:
                    hdr = f'【{d["filePath"]}:{d["startLine"]}-{d["endLine"]} · {d.get("signature","")} · dist={d["_additional"]["distance"]:.3f} · embedType={d.get("embedType","")}】'
                    ctxs.append(hdr + "\n" + textwrap.indent(truncate_snippet(d.get("content","")), "    "))
            if ctxs:
                print("\n".join(ctxs))
                print("\n---\n")
        print(res.get("answer", ""))

if __name__ == "__main__":
    asyncio.run(main())
