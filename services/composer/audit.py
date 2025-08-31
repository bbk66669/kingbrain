# -*- coding: utf-8 -*-
"""
Audit 事件发射器：
- 本地 JSONL 作为权威落盘
- 可选：NATS 发布（尽力而为）
- 可选：Neo4j 记录（尽力而为）

环境变量：
  REPO_ROOT                      默认为 /workspace
  KB_AUDIT_ENABLE_NATS           "1"/"true"/"yes" 开启（默认关闭）
  KB_AUDIT_NATS_URL              例：nats://nats:4222
  KB_AUDIT_NATS_SUBJECT_PREFIX   默认为 "kb.audit."

  KB_AUDIT_ENABLE_NEO4J          "1"/"true"/"yes" 开启（默认关闭）
  KB_AUDIT_NEO4J_URI             例：bolt://neo4j.neo4j.svc.cluster.local:7687
  KB_AUDIT_NEO4J_USER
  KB_AUDIT_NEO4J_PASS
"""

import os, json, time, uuid, asyncio, pathlib
from typing import Dict, Any, Optional

# 可选依赖：NATS / Neo4j 都是“尽力而为”
try:
    import nats  # nats-py
except Exception:
    nats = None
try:
    from neo4j import AsyncGraphDatabase
except Exception:
    AsyncGraphDatabase = None

_REPO_ROOT = os.getenv("REPO_ROOT", "/workspace")
_AUDIT_DIR = os.path.join(_REPO_ROOT, ".collab", "audit")
pathlib.Path(_AUDIT_DIR).mkdir(parents=True, exist_ok=True)

# 环境变量（优先）
_EN_NATS   = os.getenv("KB_AUDIT_ENABLE_NATS", "0") in ("1", "true", "TRUE", "yes", "YES")
_NATS_URL  = os.getenv("KB_AUDIT_NATS_URL") or ""          # 例：nats://nats:4222
_NATS_PREF = os.getenv("KB_AUDIT_NATS_SUBJECT_PREFIX", "kb.audit.")

_EN_NEO    = os.getenv("KB_AUDIT_ENABLE_NEO4J", "0") in ("1", "true", "TRUE", "yes", "YES")
_NEO_URI   = os.getenv("KB_AUDIT_NEO4J_URI") or ""         # 例：bolt://neo4j.neo4j.svc.cluster.local:7687
_NEO_USER  = os.getenv("KB_AUDIT_NEO4J_USER") or ""
_NEO_PASS  = os.getenv("KB_AUDIT_NEO4J_PASS") or ""

_nats_client = None
_neo_driver  = None


async def _get_nats():
    """获取/复用 NATS 连接；失败返回 None。"""
    global _nats_client
    if not _EN_NATS or not _NATS_URL or nats is None:
        return None
    if _nats_client is None:
        _nats_client = await nats.connect(_NATS_URL, connect_timeout=1)
    return _nats_client


async def _get_neo():
    """获取/复用 Neo4j 驱动；失败返回 None。"""
    global _neo_driver
    if not _EN_NEO or not _NEO_URI or AsyncGraphDatabase is None:
        return None
    if _neo_driver is None:
        _neo_driver = AsyncGraphDatabase.driver(_NEO_URI, auth=(_NEO_USER, _NEO_PASS))
    return _neo_driver


def _make_event(event_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """构造 CloudEvents 风格的事件对象。"""
    return {
        "id": str(uuid.uuid4()),
        "type": event_type,
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "kb-composer-worker",
        "specversion": "1.0",
        "datacontenttype": "application/json",
        "data": data or {},
    }


def _write_jsonl(ev: Dict[str, Any]) -> None:
    """将事件以 JSONL 形式落盘（本地权威）。"""
    p = os.path.join(_AUDIT_DIR, f"events-{time.strftime('%Y%m%d')}.jsonl")
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")


async def _emit_nats(ev: Dict[str, Any]) -> Optional[str]:
    """尽力将事件发布到 NATS；成功返回 subject，失败返回 None。"""
    try:
        cli = await _get_nats()
        if not cli:
            return None
        subject = f"{_NATS_PREF}{ev['type']}"
        await cli.publish(subject, json.dumps(ev).encode("utf-8"))
        return subject
    except Exception:
        return None


async def _emit_neo4j(ev: Dict[str, Any]) -> Optional[str]:
    """尽力将事件写入 Neo4j；成功返回事件 id，失败返回 None。"""
    try:
        drv = await _get_neo()
        if not drv:
            return None
        async with drv.session() as sess:
            await sess.run(
                """
                MERGE (e:AUDIT_EVENT {id:$id})
                SET e.type=$type, e.time=$time, e.source=$source,
                    e.specversion=$spec, e.data=$data
                """,
                id=ev["id"], type=ev["type"], time=ev["time"], source=ev["source"],
                spec=ev["specversion"], data=ev["data"],
            )
        return ev["id"]
    except Exception:
        return None


async def emit_event(event_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    对外主入口：
    - 写 JSONL（本地权威）
    - 尝试发到 NATS / Neo4j
    - 永不抛异常；失败以 None 标记

    返回：
    {
        "id": "<uuid>",
        "nats_subject": Optional[str],
        "neo4j_id": Optional[str],
    }
    """
    ev = _make_event(event_type, data)
    _write_jsonl(ev)
    nats_subject = await _emit_nats(ev)
    neo_id = await _emit_neo4j(ev)
    return {
        "id": ev["id"],
        "nats_subject": nats_subject,
        "neo4j_id": neo_id,
    }


# 可选：模块级简单自测
if __name__ == "__main__":
    async def _demo():
        res = await emit_event("demo.test", {"hello": "world"})
        print(res)

    asyncio.run(_demo())
