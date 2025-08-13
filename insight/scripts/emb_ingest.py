#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
emb_ingest.py – 嵌入与写入 Weaviate

CHANGELOG
- UUID 编码 sigWeight/withAnnotation/embedVersion，新增属性落库，支持多版本共存（B1/A6）。
- 价格精算与预算判断；记录截断统计（TOK_LIMIT）。
- 统一 Prometheus registry，HTTP 暴露可选；定时 Push 守护线程（可干净退出）。
- 使用 REST /v1/objects 写入，避免 SDK 版本差异；id 使用合法 UUID（sha256 前 32 位转 uuid.UUID）。
"""

import os, json, pathlib, argparse, asyncio, hashlib, logging, sqlite3, threading, atexit, signal, uuid
from typing import List, Dict, Any, Tuple
import requests

# --- OpenAI / tiktoken ---
from openai import AsyncOpenAI
import tiktoken

# --- Prometheus ---
from prometheus_client import Counter, Gauge, start_http_server, CollectorRegistry, push_to_gateway

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

HERE = pathlib.Path(__file__).resolve()
ROOT = pathlib.Path(os.getenv("ROOT_DIR", str(HERE.parent.parent)))

LIVE_JSON  = ROOT / "live_files.json"
CHUNKS_JSON= ROOT / "chunks.json"
EMBED_CACHE= ROOT / "embed_cache.sqlite"

EMBED_MODEL   = os.getenv("EMBED_MODEL", "text-embedding-3-large")
TOK_LIMIT     = 8191
WEAVIATE_URL  = os.getenv("WEAVIATE_URL", "http://127.0.0.1:8080").rstrip("/")
OPENAI_API_KEY= os.getenv("OPENAI_API_KEY")
PUSHGATEWAY_URL = os.getenv("PUSHGATEWAY_URL", "")
PUSHGATEWAY_JOB = os.getenv("PUSHGATEWAY_JOB", "emb_ingest")
MAX_BUDGET_USD  = float(os.getenv("MAX_BUDGET_USD", "100"))
EMBED_VERSION   = os.getenv("EMBED_VERSION", "v1")
PROM_PORT       = int(os.getenv("PROM_PORT_INGEST", "9001"))

ai = AsyncOpenAI(api_key=OPENAI_API_KEY)
try:
    enc = tiktoken.encoding_for_model(EMBED_MODEL)
except Exception:
    enc = tiktoken.get_encoding("cl100k_base")

# Pricing (USD / 1K tokens) — 仅用于估算
PRICING = {
    "text-embedding-3-large": {"prompt": 0.13},
    "text-embedding-3-small": {"prompt": 0.02},
}

_registry = CollectorRegistry()
if os.getenv("METRICS_EMBEDDED", "true").lower().startswith("t"):
    start_http_server(PROM_PORT, registry=_registry)

ingest_counter = Counter('code_ingest_total','Total inserted objects', registry=_registry)
token_prompt   = Counter('openai_tokens_prompt','Prompt tokens', ['model'], registry=_registry)
api_errors     = Counter('openai_api_errors','OpenAI API errors', ['type'], registry=_registry)
truncated_cnt  = Counter('text_truncated_total','Texts truncated due to token limit', registry=_registry)
budget_spent   = Gauge('budget_spent_usd','Estimated budget spent (USD)', registry=_registry)

_stop_event = threading.Event()

def _price(model: str, pt: int) -> float:
    p = PRICING.get(model, {"prompt": 0.0})
    return (pt / 1000.0) * p["prompt"]

def _accumulate(pt: int):
    token_prompt.labels(model=EMBED_MODEL).inc(pt)
    cur = 0.0
    try:
        cur = float(budget_spent._value.get())
    except Exception:
        pass
    newv = cur + _price(EMBED_MODEL, pt)
    budget_spent.set(newv)
    if newv > MAX_BUDGET_USD:
        raise RuntimeError(f"预算超限: ${newv:.2f} > ${MAX_BUDGET_USD}")

def push_metrics_once():
    if not PUSHGATEWAY_URL:
        return
    try:
        push_to_gateway(PUSHGATEWAY_URL, job=PUSHGATEWAY_JOB, registry=_registry)
    except Exception:
        pass

def _daemon_push():
    while not _stop_event.wait(60):
        push_metrics_once()

threading.Thread(target=_daemon_push, name="metrics-push", daemon=True).start()

def _on_exit(*_):
    _stop_event.set()
    push_metrics_once()
atexit.register(_on_exit)
signal.signal(signal.SIGTERM, _on_exit)
signal.signal(signal.SIGINT,  _on_exit)

def prepare_text(chunk: Dict[str,Any], embed_type: str, sig_weight=3, with_annotation=True) -> str:
    content = chunk.get("content", "")
    if not with_annotation:
        lines = []
        for l in content.splitlines():
            if l.startswith("# Summary"):
                continue
            lines.append(l)
        content = "\n".join(lines)
    sig = chunk.get("signature","")
    if embed_type == "def":
        return ((sig + "\n") * sig_weight) + content
    return content

def _truncate_by_tokens(text: str) -> Tuple[str, int]:
    ids = enc.encode(text)
    if len(ids) <= TOK_LIMIT:
        return text, 0
    truncated_cnt.inc()
    ids = ids[:TOK_LIMIT]
    return enc.decode(ids), 1

async def embed_batch(texts: List[str], etype: str) -> List[List[float]]:
    try:
        resp = await ai.embeddings.create(model=EMBED_MODEL, input=texts, user=etype)
        total_tokens = getattr(resp, "usage", None)
        if total_tokens and getattr(resp.usage, "total_tokens", 0):
            _accumulate(resp.usage.total_tokens)
        return [d.embedding for d in resp.data]
    except Exception as e:
        api_errors.labels(type=type(e).__name__).inc()
        # 简单退避
        await asyncio.sleep(10)
        return []

def weaviate_insert(obj: Dict[str,Any]) -> bool:
    url = f"{WEAVIATE_URL}/v1/objects"
    try:
        r = requests.post(url, headers={"Content-Type":"application/json"}, json=obj, timeout=30)
        if r.status_code in (200, 201, 409):  # 409 冲突视作已存在
            return True
        logging.error(f"Weaviate insert HTTP {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        logging.error(f"Weaviate insert error: {e}")
        return False

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["func","chunk","file"], default="func")
    ap.add_argument("--sig-weight-test", default="2,3,5")
    ap.add_argument("--compare-annotation", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sig_weights = [int(x) for x in args.sig_weight_test.split(",")]
    chunks = json.loads(CHUNKS_JSON.read_text(encoding="utf-8"))

    # SQLite 缓存
    conn = sqlite3.connect(str(EMBED_CACHE))
    conn.execute("CREATE TABLE IF NOT EXISTS embeddings(key TEXT PRIMARY KEY, vector TEXT)")
    # 简单清理策略：最多 200k 条
    conn.execute("DELETE FROM embeddings WHERE rowid IN (SELECT rowid FROM embeddings ORDER BY rowid DESC LIMIT -1 OFFSET 200000)")
    conn.commit()

    written_stats = []

    for sw in sig_weights:
        for anno in ([True, False] if args.compare_annotation else [True]):
            written = 0
            batch_texts: List[str] = []
            batch_keys:  List[str] = []
            batch_chunks: List[Tuple[Dict[str,Any], str]] = []  # (chunk, etype)

            async def flush():
                nonlocal written, batch_texts, batch_keys, batch_chunks
                if not batch_texts:
                    return
                vecs = await embed_batch(batch_texts, "mixed")
                for key, vec, (chunk, etype) in zip(batch_keys, vecs, batch_chunks):
                    # 缓存写入
                    conn.execute(
                        "INSERT OR REPLACE INTO embeddings(key,vector) VALUES(?,?)",
                        (key, json.dumps(vec))
                    )
                    if args.dry_run:
                        continue
                    # 生成稳定 UUID（含实验维度）
                    digest = hashlib.sha256(
                        f"{chunk['filePath']}:{chunk['startLine']}:{chunk['endLine']}:{etype}:{sw}:{anno}:{EMBED_VERSION}".encode()
                    ).hexdigest()
                    uid = str(uuid.UUID(digest[:32]))
                    props = {
                        **chunk,
                        "embedType": etype,
                        "embedVersion": EMBED_VERSION,
                        "sigWeight": sw,
                        "withAnnotation": bool(anno),
                    }
                    obj = {"class": "CodeChunk", "id": uid, "properties": props, "vector": vec}
                    if weaviate_insert(obj):
                        written += 1
                        ingest_counter.inc()
                conn.commit()
                batch_texts, batch_keys, batch_chunks = [], [], []

            for chunk in chunks:
                texts = [
                    prepare_text(chunk, "def",    sig_weight=sw, with_annotation=anno),
                    prepare_text(chunk, "content",sig_weight=sw, with_annotation=anno),
                ]
                etypes = ["def", "content"]
                for t, et in zip(texts, etypes):
                    t2, _ = _truncate_by_tokens(t)
                    h = hashlib.sha256((chunk.get("content","") + str(chunk["startLine"]) + chunk["filePath"]).encode()).hexdigest()
                    key = f"{h}:{et}:{sw}:{int(anno)}:{EMBED_VERSION}"
                    cur = conn.execute("SELECT vector FROM embeddings WHERE key=?", (key,)).fetchone()
                    if cur:
                        vec = json.loads(cur[0])
                        if not args.dry_run:
                            digest = hashlib.sha256(
                                f"{chunk['filePath']}:{chunk['startLine']}:{chunk['endLine']}:{et}:{sw}:{anno}:{EMBED_VERSION}".encode()
                            ).hexdigest()
                            uid = str(uuid.UUID(digest[:32]))
                            props = {
                                **chunk,
                                "embedType": et,
                                "embedVersion": EMBED_VERSION,
                                "sigWeight": sw,
                                "withAnnotation": bool(anno),
                            }
                            obj = {"class": "CodeChunk", "id": uid, "properties": props, "vector": vec}
                            if weaviate_insert(obj):
                                written += 1
                                ingest_counter.inc()
                        continue
                    batch_texts.append(t2)
                    batch_keys.append(key)
                    batch_chunks.append((chunk, et))
                    if len(batch_texts) >= 64:
                        await flush()

            await flush()
            written_stats.append({"sig_weight": sw, "annotation": anno, "written": written})

    (ROOT / "ingest_stats.json").write_text(json.dumps(written_stats, indent=2, ensure_ascii=False), encoding="utf-8")
    conn.close()
    logging.info("ingest done")

if __name__ == "__main__":
    asyncio.run(main())
