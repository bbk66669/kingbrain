#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ask_code.py — 代码语义问答（增强修复版）
- 兼容第三方 logger 的 trace_id，避免 KeyError: 'trace_id'
- 多通道检索：签名子串匹配、精确签名、向量(def/content)、关键词、父链、调用关系
- _additional 为 None 时的安全处理；距离统一用 _safe_distance()
- 关键词清洗：丢弃空白/纯符号/中文虚词，避免 Weaviate stopwords 报错
- 签名子串匹配使用 *frag*；0 命中自动去掉 embedVersion 再试
- 文件名模糊 *fname*；0 命中自动去掉 embedVersion 再试
- 打印上下文头（文件/行号/距离/embedType），便于核对召回
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
load_dotenv()

TRACE_ID = os.getenv("TRACE_ID", uuid.uuid4().hex)

# ---------- logging ----------
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(levelname)s: [trace=%(trace_id)s] %(message)s"
)
class _TraceIdFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, "trace_id"):
            record.trace_id = TRACE_ID
        return True
for _h in logging.getLogger().handlers:
    _h.addFilter(_TraceIdFilter())

_base_logger = logging.getLogger("ask_code")
logger = logging.LoggerAdapter(_base_logger, {"trace_id": TRACE_ID})

# 降低第三方库噪音
logging.getLogger('jieba').setLevel(logging.WARNING)

# ---------- OpenAI ----------
try:
    from openai import AsyncOpenAI
except ImportError:
    sys.exit("❌ 需要 openai>=1.3.0，请先运行：pip install -U openai")
ai = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ---------- spaCy ----------
try:
    import spacy
    try:
        nlp = spacy.load("en_core_web_sm")
    except Exception:
        nlp = spacy.blank("en")
except ImportError:
    nlp = None

# ---------- ENV ----------
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

# ---------- Pricing ----------
PRICING = {
    "text-embedding-3-large": {"prompt": 0.13, "completion": 0.13},
    "text-embedding-3-small": {"prompt": 0.02, "completion": 0.02},
    "gpt-4-turbo":            {"prompt": 0.01, "completion": 0.03},
    "gpt-4o-mini":            {"prompt": 0.003, "completion": 0.006},
}

# ---------- Prometheus ----------
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

def _price(model: str, pt: int, ct: int) -> float:
    p = PRICING.get(model) or PRICING.get(FALLBACK_MODEL, {"prompt": 0.0, "completion": 0.0})
    return (pt/1000.0)*p["prompt"] + (ct/1000.0)*p["completion"]

def _accumulate_usage(model: str, usage):
    pt = getattr(usage, "prompt_tokens", 0) or (usage.get("prompt_tokens", 0) if isinstance(usage, dict) else 0)
    ct = getattr(usage, "completion_tokens", 0) or (usage.get("completion_tokens", 0) if isinstance(usage, dict) else 0)
    token_usage_prompt.labels(model=model).inc(pt)
    token_usage_comp.labels(model=model).inc(ct)
    try:
        cur = budget_spent_usd._value.get()
        budget_spent_usd.set(cur + _price(model, pt, ct))
    except Exception:
        budget_spent_usd.set(_price(model, pt, ct))

def _check_budget_raise():
    try:
        val = budget_spent_usd._value.get()
    except Exception:
        val = 0.0
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

threading.Thread(target=_push_metrics_daemon, name="metrics-push", daemon=True).start()

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

# ---------- NLP helpers ----------
STOPWORDS = {"the","and","of","to","in","for","a","is","on","return","函数","类","返回","如果","循环","导入"}
CN_STOP = {"的","了","呢","啊","吧","吗","在","有","和","或","与","及","等","里","中","上","下"}
_kw_re = re.compile(r"[A-Za-z]{3,}|[\u4e00-\u9fa5]+")

def _valid_kw(w: str) -> bool:
    w = (w or "").strip()
    if not w: return False
    if w in CN_STOP or w in STOPWORDS: return False
    if len(w) < 2: return False
    if w in {"_", ".", "-", "/", " "}: return False
    return bool(re.search(r"[A-Za-z0-9\u4e00-\u9fa5]", w))

def extract_keywords(text: str, limit: int = 8) -> List[str]:
    words = []
    if any('\u4e00' <= c <= '\u9fa5' for c in text):
        import jieba
        words.extend(jieba.cut(text.lower(), cut_all=False))
    else:
        words.extend(_kw_re.findall(text.lower()))
    freq: Dict[str, float] = {}
    for w in words:
        if w in STOPWORDS:
            continue
        freq[w] = freq.get(w, 0) + 1
    # 同义/领域纠错
    from rapidfuzz import fuzz
    domain_terms = ["loadbalance","retry","network","trailing_mgr","update_logic","error_handling","config","api","ws_main"]
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
    domain_weights = {"loadbalance":2,"retry":1.5,"network":1.5,"trailing_mgr":2,"update_logic":1.5,"error_handling":1.5,"config":1.5,"api":1.5,"ws_main":2}
    scored = {w: f*domain_weights.get(w,1.0) for w,f in corrected.items()}
    special = [w for w in scored if (w and w[0].isupper())]
    ordered = special + sorted((w for w in scored if w not in special), key=lambda x: scored[x], reverse=True)
    ordered = [w for w in ordered if _valid_kw(w)]
    return ordered[:limit]

def has_function_pattern(text: str) -> bool:
    return bool(re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\(", text))

def extract_function_names_from_query(query: str) -> List[str]:
    names: List[str] = []
    patterns = [r'\b([a-z_][a-z0-9_]+)\b', r'\b([A-Z][a-zA-Z0-9_]+)\b']
    for p in patterns:
        names.extend(re.findall(p, query))
    if nlp:
        doc = nlp(query)
        names += [ent.text for ent in doc.ents if getattr(ent, "label_", "") in ("ORG","PRODUCT")]
    stop = {'function','method','class','code','implementation','details','purpose','parameters','logic','update'}
    return [n for n in names if n not in stop and len(n) > 2]

def generate_query_variants(question: str) -> List[str]:
    variants = [question]
    tech_syn = {'function':['method','procedure','routine'],'parameters':['arguments','args','inputs'],
                'purpose':['goal','objective','functionality'],'implementation':['code','logic','execution'],
                'update':['modify','change','set'],'logic':['algorithm','process','flow']}
    ql = question.lower()
    for orig, syns in tech_syn.items():
        if orig in ql:
            for s in syns:
                variants.append(re.sub(rf'\b{orig}\b', s, question, flags=re.IGNORECASE))
    if nlp:
        doc = nlp(question)
        for ent in doc.ents:
            if getattr(ent, "label_", "") in ("ORG","PRODUCT"):
                variants += [f"{ent.text} docstring", f"{ent.text} summary", f"{ent.text} parameters", f"{ent.text} update logic"]
    if has_function_pattern(question):
        for n in extract_function_names_from_query(question):
            variants += [f"{n} parameters", f"{n} parent"]
    seen=set(); out=[]
    for v in variants:
        if v not in seen:
            seen.add(v); out.append(v)
        if len(out)>=8: break
    return out

def get_weights(question: str) -> Dict[str, float]:
    try:
        with open(WEIGHTS_CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {
            "purpose": {"exact":1.2,"def":1.3,"content":0.8,"context":0.6,"keywords":0.4,"tags":0.5},
            "implementation": {"exact":1.0,"def":0.8,"content":1.3,"context":0.8,"keywords":0.5,"tags":0.4},
            "parameter": {"exact":1.1,"def":1.2,"content":0.7,"context":0.5,"keywords":0.6,"tags":0.3},
            "default": {"exact":1.0,"def":0.9,"content":0.9,"context":0.6,"keywords":0.3,"tags":0.4},
        }
    ql = question.lower()
    for k,v in cfg.items():
        if k in ql: return v
    return cfg["default"]

# ---------- OpenAI wrappers ----------
_sema = asyncio.Semaphore(EMBED_CONCURRENCY)

async def a_chat(model: str, prompt: str, timeout: int = 60) -> str:
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
            _accumulate_usage(model, {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens})
            _check_budget_raise()
            return resp.choices[0].message.content
        except asyncio.TimeoutError:
            api_errors.labels(type="timeout").inc()
            raise
        except Exception as e:
            api_errors.labels(type=getattr(type(e),"__name__","Unknown")).inc()
            if any(x in str(e) for x in ("429","Rate","overloaded")):
                await asyncio.sleep(10)
                return await a_chat(model, prompt, timeout)
            raise

async def a_embed(text: str, etype: str = "content", timeout: int = 60) -> List[float]:
    async with _sema:
        try:
            resp = await asyncio.wait_for(
                ai.embeddings.create(model=EMBED_MODEL, input=text, user=etype),
                timeout=timeout
            )
            usage = getattr(resp, "usage", None)
            total = usage.total_tokens if usage else 0
            _accumulate_usage(EMBED_MODEL, {"prompt_tokens": total, "completion_tokens": 0})
            _check_budget_raise()
            return resp.data[0].embedding
        except asyncio.TimeoutError:
            api_errors.labels(type="timeout").inc()
            raise
        except Exception as e:
            api_errors.labels(type=getattr(type(e),"__name__","Unknown")).inc()
            if any(x in str(e) for x in ("429","Rate","overloaded")):
                await asyncio.sleep(10)
                return await a_embed(text, etype, timeout)
            raise

# ---------- Weaviate ----------
async def _gql(query: Dict[str, Any], timeout: int = 15) -> Dict[str, Any]:
    async with aiohttp.ClientSession() as sess:
        try:
            async with sess.post(
                f"{WEAVIATE_URL}/v1/graphql",
                headers={"Content-Type": "application/json"},
                json=query, timeout=timeout
            ) as resp:
                txt = await resp.text()
                if resp.status >= 400:
                    logger.error(f"GQL HTTP {resp.status}: {txt[:500]}")
                    raise RuntimeError(f"http {resp.status}")
                try:
                    data = json.loads(txt)
                except Exception as e:
                    logger.error(f"GQL parse error: {e} | body-snippet={txt[:300]}")
                    raise
                if "errors" in data:
                    msgs = "; ".join(e.get("message","") for e in data["errors"])
                    logger.error(f"GQL errors: {msgs} | query={query.get('query','')[:300]}")
                    raise RuntimeError(msgs)
                return data
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(f"GraphQL request failed: {e} | query={query.get('query','')[:300]}")
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
    term = (term or "").strip()
    if not _valid_kw(term):
        return []
    q = {"query": f"""
{{
  Get {{
    CodeChunk(
      where: {{
        operator: And, operands: [
          {{ operator: Or, operands: [
            {{ path: ["content"], operator: Like, valueString: "{term}" }},
            {{ path: ["tags"], operator: ContainsAny, valueText: ["{term}"] }},
            {{ path: ["docstring"], operator: Like, valueString: "{term}" }}
          ] }},
          {_where_embed_version()}
        ]
      }},
      limit: {k}
    ) {{ {_FIELDS} }}
  }}
}}"""}
    data = (await _gql(q, timeout=10))["data"]["Get"]["CodeChunk"]
    logger.info(f"[keyword] term='{term}' -> {len(data)} hits")
    return data

async def gql_tags(tags: List[str], k: int = 20) -> List[dict]:
    q = {"query": f"""
{{
  Get {{
    CodeChunk(
      where: {{
        operator: And, operands: [
          {{ path: ["tags"], operator: ContainsAny, valueText: {json.dumps(tags)} }},
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

async def search_by_signature_fragment(frag: str, k: int = 10) -> List[dict]:
    """签名子串匹配：*frag*；先按 embedVersion 过滤，0 命中再降级去掉版本。"""
    if not frag or len(frag) < 3:
        return []
    # 第一次：带 embedVersion
    q1 = {"query": f"""
{{
  Get {{
    CodeChunk(
      where: {{
        operator: And, operands: [
          {{ path: ["signature"], operator: Like, valueString: "*{frag}*" }},
          {_where_embed_version()}
        ]
      }},
      limit: {k}
    ) {{ {_FIELDS} }}
  }}
}}"""}
    data = (await _gql(q1, timeout=10))["data"]["Get"]["CodeChunk"]
    if data:
        logger.info(f"[sig-like] frag='{frag}' -> {len(data)} hits")
        return data
    # 兜底：不带 embedVersion
    q2 = {"query": f"""
{{
  Get {{
    CodeChunk(
      where: {{
        operator: And, operands: [
          {{ path: ["signature"], operator: Like, valueString: "*{frag}*" }}
        ]
      }},
      limit: {k}
    ) {{ {_FIELDS} }}
  }}
}}"""}
    data = (await _gql(q2, timeout=10))["data"]["Get"]["CodeChunk"]
    logger.info(f"[sig-like/no-version] frag='{frag}' -> {len(data)} hits")
    return data

async def search_by_keywords(keywords: List[str]) -> List[dict]:
    tasks = [gql_keyword(kw) for kw in keywords]
    res = await asyncio.gather(*tasks, return_exceptions=True)
    merged: List[dict] = []
    for r in res:
        if isinstance(r, list):
            merged.extend(r)
    return [d for d in merged if (d.get("endLine",0) - d.get("startLine",0) + 1) >= MIN_LINES]

async def search_by_parent_chain(parent_sigs: List[str]) -> List[dict]:
    if not parent_sigs:
        return []
    q = {"query": f"""
{{
  Get {{
    CodeChunk(
      where: {{
        operator: And, operands: [
          {{ path: ["parentSignature"], operator: ContainsAny, valueText: {json.dumps(parent_sigs)} }},
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
    """按文件名模糊检索：先用 *fname* + embedVersion；0 命中则去掉版本再试。"""
    patt = f"*{fname.strip()}*"
    # 尝试 1：带 embedVersion
    q1 = {"query": f"""
{{
  Get {{
    CodeChunk(
      where: {{
        operator: And, operands: [
          {{ path: ["filePath"], operator: Like, valueString: "{patt}" }},
          {_where_embed_version()}
        ]
      }},
      limit: {limit}
    ) {{ filePath startLine endLine content }}
  }}
}}"""}
    data = (await _gql(q1, timeout=15))["data"]["Get"]["CodeChunk"]
    if data:
        logger.info(f"[file-like] fname='{fname}' pattern='{patt}' -> {len(data)} hits")
        return sorted(data, key=lambda x: x["startLine"])
    # 尝试 2：不带 embedVersion 兜底
    q2 = {"query": f"""
{{
  Get {{
    CodeChunk(
      where: {{
        operator: And, operands: [
          {{ path: ["filePath"], operator: Like, valueString: "{patt}" }}
        ]
      }},
      limit: {limit}
    ) {{ filePath startLine endLine content }}
  }}
}}"""}
    data = (await _gql(q2, timeout=15))["data"]["Get"]["CodeChunk"]
    logger.info(f"[file-like/no-version] fname='{fname}' pattern='{patt}' -> {len(data)} hits")
    return sorted(data, key=lambda x: x["startLine"])

async def search_by_calls(func_names: List[str], k: int = 20) -> List[dict]:
    """按调用关系检索：谁在调用这些函数名（如 _load_balance）"""
    if not func_names:
        return []
    q = {"query": f"""
{{
  Get {{
    CodeChunk(
      where: {{
        operator: And, operands: [
          {{ path: ["calls"], operator: ContainsAny, valueText: {json.dumps(func_names)} }},
          {_where_embed_version()}
        ]
      }},
      limit: {k}
    ) {{ {_FIELDS} }}
  }}
}}"""}
    try:
        data = (await _gql(q, timeout=10))["data"]["Get"]["CodeChunk"]
        logger.info(f"[calls] funcs={func_names} -> {len(data)} hits")
        return data
    except Exception:
        return []

# ---------- small utils ----------
def _safe_distance(d: Optional[dict]) -> Optional[float]:
    """安全读取 _additional.distance；_additional 可能为 None。"""
    try:
        return (((d or {}).get("_additional") or {}).get("distance"))
    except Exception:
        return None

# ---------- rerank ----------
def rerank(question: str, **sources) -> List[dict]:
    weights = get_weights(question)
    all_res: Dict[tuple, dict] = {}
    seen = set()
    for key, lst in sources.items():
        w = weights.get(key, 1.0)
        for r in lst:
            if not isinstance(r, dict):
                continue
            tup = (r.get("filePath"), r.get("startLine"), r.get("endLine"), r.get("embedType",""))
            if tup in seen:
                continue
            seen.add(tup)
            dist = _safe_distance(r)
            base = (dist if isinstance(dist,(int,float)) else 1.0)
            score = max(0.0, 1 - base) * w
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
    return sorted(all_res.values(), key=lambda x: x.get("_final_score", 0.0), reverse=True)

def query_category(q: str) -> str:
    ql = q.lower()
    if any(k in ql for k in ("purpose","summary","功能说明")): return "purpose"
    if any(k in ql for k in ("implementation","逻辑","实现")): return "implementation"
    if any(k in ql for k in ("parameter","参数")): return "parameter"
    return "default"

def get_adaptive_threshold(question: str, results: List[dict]) -> float:
    base_map = {"purpose": 0.3, "implementation": 0.25, "parameter": 0.2, "default": 0.25}
    base = base_map[query_category(question)]
    dists = [_safe_distance(r) for r in results]
    dists = [d for d in dists if isinstance(d, (int, float))]
    scores = [r.get("_final_score", 0.0) for r in results]
    if not dists or not scores:
        return base
    mn, avg = min(dists), sum(dists)/len(dists)
    avg_score = sum(scores)/len(scores)
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
    ld = sn.rfind('.'); ln = sn.rfind('\n'); cut = max(ld, ln)
    if cut >= int(MAX_SNIPPET * 0.5):
        sn = sn[:cut]
    return sn + "..."

# ---------- pipeline ----------
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
    # 片段签名检索（子串）
    sig_frags = re.findall(r'[A-Za-z_][A-Za-z0-9_]{2,}', question)
    sig_take = sig_frags[:4]
    tasks += [search_by_signature_fragment(f) for f in sig_take]
    # 其它通道
    tasks += [search_exact_function(fn) for fn in funcs]
    tasks += [gql_vec(v, k=TOPK, embed_type="def") for v in vec_def]
    tasks += [gql_vec(v, k=TOPK, embed_type="content") for v in vec_cont]
    tasks += [search_by_keywords(extract_keywords(question))]
    tasks += [search_by_parent_chain(funcs)]
    tasks += [search_by_calls(funcs)]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 汇总命中（按添加顺序解析）
    i = 0
    sig_hit, exact, sem_def, sem_cont = [], [], [], []

    for _ in sig_take:
        if isinstance(results[i], list): sig_hit.extend(results[i])
        i += 1
    for _ in funcs:
        if isinstance(results[i], list): exact.extend(results[i])
        i += 1
    for _ in vec_def:
        if isinstance(results[i], list): sem_def.extend(results[i])
        i += 1
    for _ in vec_cont:
        if isinstance(results[i], list): sem_cont.extend(results[i])
        i += 1
    kw_res    = results[i] if isinstance(results[i], list) else []; i += 1
    pc_res    = results[i] if isinstance(results[i], list) else []; i += 1
    calls_res = results[i] if isinstance(results[i], list) else []

    logger.info(
        f"channel-hits: sig={len(sig_hit)} exact={len(exact)} "
        f"def={len(sem_def)} cont={len(sem_cont)} kw={len(kw_res)} "
        f"ctx={len(pc_res)} calls={len(calls_res)}"
    )

    merged = rerank(
        question,
        **{
            "signature": sig_hit,
            "exact":     exact,
            "def":       sem_def,
            "content":   sem_cont,
            "keywords":  kw_res,
            "context":   pc_res,
            "calls":     calls_res,
        }
    )

    # 兜底：逐 token 再做一次签名检索
    if not merged:
        rescue_tokens = re.findall(r'[A-Za-z_][A-Za-z0-9_]{2,}', question)
        for tok in rescue_tokens:
            try:
                got = await search_by_signature_fragment(tok, k=20)
                if got:
                    logger.info(f"[rescue] token='{tok}' -> {len(got)} hits")
                    merged = rerank(question, signature=got)
                    break
            except Exception as e:
                logger.error(f"[rescue] error on token='{tok}': {e}")

    if merged:
        relevant = sum(1 for r in merged if r.get("_final_score", 0) > 0.5)
        try:
            recall_precision.set(relevant / len(merged))
            false_positives.set((len(merged) - relevant) / len(merged))
        except Exception:
            pass

        try:
            with open("search_log.csv", "a", encoding="utf-8") as f:
                for r in merged[:TOPK]:
                    f.write(
                        f'"{question}","{r.get("filePath","")}","{r.get("embedType","")}","{r.get("embedVersion","")}",'
                        f'{r.get("startLine",0)},{r.get("endLine",0)},{_safe_distance(r)}\n'
                    )
        except Exception as e:
            logger.error(f"写入 search_log.csv 失败: {e}")

    search_latency.observe(time.time() - start)
    return merged

async def run_query(question: str, json_out: bool = False) -> Dict[str, Any]:
    if not question:
        raise ValueError("question is empty")

    # 文件总览（按文件名模糊）
    m = re.search(r'([\w\-.]+\.py)', question)
    if m:
        fname = m.group(1)
        try:
            chunks = await search_file_chunks(fname)
            if not chunks:
                logger.info(f"[file-overview] fname='{fname}' -> 0 hits")
                return {"answer": f"未找到文件 {fname} 的代码片段。", "chunks": []}
            whole = "\n".join(c.get("content","") for c in chunks)
            logger.info(f"[file-overview] fname='{fname}' -> {len(chunks)} chunks, assembling for summarize")
            ans = await a_chat(
                QA_MODEL,
                f"下面是脚本 {fname} 的全部代码，请用简洁要点总结 3 大核心功能：\npython\n{whole}\n"
            )
            return {"answer": ans, "chunks": chunks}
        except Exception as e:
            logger.error(f"[file-overview] failed: {e}")
            return {"answer": f"文件总览失败: {e}", "chunks": []}

    # 多通道检索
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

    # 保留非向量结果；清洗 _additional
    docs = [d for d in docs if isinstance(d, dict)]
    if not docs:
        return {"answer": "未检索到相关片段。", "chunks": []}
    for d in docs:
        if d.get("_additional") is None:
            d["_additional"] = {}

    thresh = get_adaptive_threshold(question, docs)
    dists = [_safe_distance(d) for d in docs]
    dists = [x for x in dists if isinstance(x,(int,float))]
    best = min(dists) if dists else 0.0

    if dists and best > thresh + 0.05 and not AUTO_CONFIRM:
        return {"answer": f"距离 {best:.3f} 超过阈值 {thresh:.3f}，已取消。", "chunks": docs}

    ctxs = []
    for d in docs[:TOPK]:
        dist = _safe_distance(d)
        dist_s = f"{dist:.3f}" if isinstance(dist,(int,float)) else "N/A"
        hdr = f'【{d.get("filePath","?")}:{d.get("startLine","?")}-{d.get("endLine","?")} · dist={dist_s} · embedType={d.get("embedType","?")}】'
        ctxs.append(hdr + "\n" + textwrap.indent(truncate_snippet(d.get("content","")), "    "))

    prompt = "你是一名资深 Python 工程师，仅凭下列代码片段回答提问；如信息不足请回答“信息不足”。\n"
    prompt += "\n".join(ctxs) + f"\n\n问题：{question}\n回答："

    try:
        answer = await a_chat(QA_MODEL, prompt)
        try:
            with open("search_log.csv", "a", encoding="utf-8") as f:
                safe_ans = (answer or "").replace("\n", " ")
                f.write(f'"{question}","ANSWER","{safe_ans}"\n')
        except Exception as e:
            logger.error(f"写入 search_log.csv 失败: {e}")
        return {"answer": answer, "chunks": docs}
    except Exception as e:
        logger.error(f"生成回答失败: {e}")
        return {"answer": f"生成回答失败: {e}", "chunks": docs}

async def _selftest():
    cases = [
        {"q": "load_balance function purpose", "check": lambda r: any("loadbalance" in (d.get("tags") or []) for d in r.get("chunks", []))},
        {"q": "agent_decide_and_execute parameters", "check": lambda r: "参数" in r.get("answer", "") or "parameter" in (r.get("answer","") or "").lower()},
        {"q": "ws_main 里 _load_balance 的作用是什么", "check": lambda r: "_load_balance" in json.dumps(r.get("chunks", []), ensure_ascii=False)},
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
                dist = _safe_distance(d)
                dist_s = f"{dist:.3f}" if isinstance(dist,(int,float)) else "N/A"
                hdr = f'【{d.get("filePath","?")}:{d.get("startLine","?")}-{d.get("endLine","?")} · dist={dist_s} · embedType={d.get("embedType","?")}】'
                ctxs.append(hdr + "\n" + textwrap.indent(truncate_snippet(d.get("content","")), "    "))
            if ctxs:
                print("\n".join(ctxs))
                print("\n---\n")
        print(res.get("answer", ""))

if __name__ == "__main__":
    asyncio.run(main())
