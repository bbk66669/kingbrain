#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ensure_weaviate_schema.py

CHANGELOG
- 修复 logging.basicConfig(extra=...) 误用；改用 LoggerAdapter 统一附加 trace_id。
- Prometheus: 统一使用自建 registry，并在 start_http_server(port, registry=_registry) 暴露；可通过 METRICS_EMBEDDED 开关。
- 新增安全的属性对比：仅 POST 新属性；对可能不可变的属性不执行 PUT，给出灰度迁移建议（C1）。
- 统一 schema 字段，确保包含全文索引与 embedType/embedVersion。
- 统计对象增量（G2）并定时 Push，带退出控制。
- GraphQL 计数使用 Aggregate 查询；失败回退为 0。
"""

import os
import sys
import json
import time
import signal
import atexit
import threading
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

TRACE_ID = os.getenv("TRACE_ID", "default")

_base_logger = logging.getLogger("ensure_weaviate_schema")
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: [trace=%(trace_id)s] %(message)s",
)
logger = logging.LoggerAdapter(_base_logger, {"trace_id": TRACE_ID})

# Prometheus
METRICS_AVAILABLE = True
try:
    from prometheus_client import Gauge, start_http_server, CollectorRegistry, push_to_gateway
except Exception:
    METRICS_AVAILABLE = False
    logger.warning("prometheus_client not installed, metrics disabled")

_registry = CollectorRegistry() if METRICS_AVAILABLE else None
_stop_event = threading.Event()

def _maybe_start_http(port: int):
    if METRICS_AVAILABLE and os.getenv("METRICS_EMBEDDED", "true").lower().startswith("t"):
        try:
            start_http_server(port, registry=_registry)
            logger.info(f"Prometheus HTTP started on {port}")
        except Exception as e:
            logger.error(f"start_http_server failed: {e}")

if METRICS_AVAILABLE:
    PROM_PORT = int(os.getenv("PROM_PORT_SCHEMA", "9002"))
    _maybe_start_http(PROM_PORT)

    PUSHGATEWAY_URL = os.getenv("PUSHGATEWAY_URL", "")
    PUSHGATEWAY_JOB = os.getenv("PUSHGATEWAY_JOB", "schema_check")

    schema_status = Gauge("weaviate_schema_status", "Schema creation status", ["class_name"], registry=_registry)
    schema_fields = Gauge("weaviate_schema_fields", "Number of fields", ["class_name"], registry=_registry)
    schema_objects = Gauge("weaviate_schema_objects", "Number of objects", ["class_name"], registry=_registry)
    object_delta = Gauge("weaviate_object_delta", "Object count change", ["class_name"], registry=_registry)

    def push_metrics():
        if not PUSHGATEWAY_URL:
            return
        try:
            push_to_gateway(PUSHGATEWAY_URL, job=PUSHGATEWAY_JOB, registry=_registry)
            logger.info("Metrics pushed to Pushgateway")
        except Exception as e:
            logger.error(f"Pushing metrics failed: {e}")

    def schedule_metrics():
        while not _stop_event.wait(60):
            push_metrics()

    _push_thread = threading.Thread(target=schedule_metrics, name="metrics-push", daemon=True)
    _push_thread.start()

    def _on_exit(*_):
        _stop_event.set()
        push_metrics()
    atexit.register(_on_exit)
    signal.signal(signal.SIGTERM, _on_exit)
    signal.signal(signal.SIGINT, _on_exit)

# Env
WEAVIATE_URL = os.getenv("WEAVIATE_URL", "http://127.0.0.1:8080").rstrip("/")
TARGET_CLASS = "CodeChunk"
SCHEMA_VERSION = os.getenv("SCHEMA_VERSION", "v1")

HEADERS = {"Content-Type": "application/json"}

PROPERTIES = [
    {"name": "filePath",        "dataType": ["string"], "description": "Path of the source file", "indexFilterable": True, "indexSearchable": True},
    {"name": "startLine",       "dataType": ["int"],    "description": "Starting line (inclusive)"},
    {"name": "endLine",         "dataType": ["int"],    "description": "Ending line (inclusive)"},
    {"name": "content",         "dataType": ["text"],   "description": "Source code text of this chunk", "indexSearchable": True},
    {"name": "signature",       "dataType": ["string"], "description": "AST signature", "indexFilterable": True, "indexSearchable": True},
    {"name": "parentSignature", "dataType": ["string[]"], "description": "Parent AST node signatures", "indexFilterable": True},
    {"name": "moduleName",      "dataType": ["string"], "description": "Module name", "indexFilterable": True, "indexSearchable": True},
    {"name": "importPath",      "dataType": ["string"], "description": "Import path", "indexFilterable": True, "indexSearchable": True},
    {"name": "tags",            "dataType": ["string[]"], "description": "Semantic tags", "indexFilterable": True, "indexSearchable": True},
    {"name": "calls",           "dataType": ["string[]"], "description": "Functions this chunk calls"},
    {"name": "called_by",       "dataType": ["string[]"], "description": "Callers of this chunk"},
    {"name": "imports",         "dataType": ["string[]"], "description": "Import statements inside this chunk"},
    {"name": "docstring",       "dataType": ["text"],   "description": "Docstring extracted from the chunk", "indexSearchable": True},
    {"name": "embedType",       "dataType": ["string"], "description": "Type of embedding (def/content)", "indexFilterable": True},
    {"name": "embedVersion",    "dataType": ["string"], "description": "Embedding version", "indexFilterable": True},
    # 实验/评测维度（B1/A6）
    {"name": "sigWeight",       "dataType": ["int"],    "description": "Signature duplication weight"},
    {"name": "withAnnotation",  "dataType": ["boolean"],"description": "Whether annotation lines kept"},
]

def count_objects() -> int:
    try:
        q = {"query": f"{{ Aggregate {{ {TARGET_CLASS} {{ meta {{ count }} }} }} }}"}
        resp = requests.post(f"{WEAVIATE_URL}/v1/graphql", headers=HEADERS, json=q, timeout=10)
        resp.raise_for_status()
        return resp.json()["data"]["Aggregate"][TARGET_CLASS][0]["meta"]["count"]
    except Exception as e:
        logger.error(f"Counting objects failed: {e}")
        return 0

def get_schema():
    resp = requests.get(f"{WEAVIATE_URL}/v1/schema", headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()

def post_property(prop):
    r = requests.post(f"{WEAVIATE_URL}/v1/schema/{TARGET_CLASS}/properties",
                      headers=HEADERS, json=prop, timeout=20)
    r.raise_for_status()

def main():
    initial = count_objects()
    try:
        sch = get_schema()
    except Exception as e:
        if METRICS_AVAILABLE: schema_status.labels(class_name=TARGET_CLASS).set(0)
        logger.error(f"获取 schema 失败: {e}")
        sys.exit(1)

    classes = {c["class"]: c for c in sch.get("classes", [])}
    if TARGET_CLASS in classes:
        logger.info(f"Class '{TARGET_CLASS}' 已存在，检查属性差异")
        cur_props = {p["name"]: p for p in classes[TARGET_CLASS].get("properties", [])}
        to_add = [p for p in PROPERTIES if p["name"] not in cur_props]

        if to_add:
            logger.warning("检测到缺失属性，将增量添加（建议在低峰期操作，并做好备份）")
            for p in to_add:
                try:
                    post_property(p)
                    logger.info(f"属性已添加: {p['name']}")
                except requests.RequestException as e:
                    logger.error(f"添加属性失败 {p['name']}: {e}")
                    sys.exit(1)
        else:
            logger.info("属性集合已满足需求（不修改已存在属性，避免不可变字段导致 422）。")

        # 版本提示
        desc = classes[TARGET_CLASS].get("description", "")
        curr_ver = ""
        if "version:" in desc:
            curr_ver = desc.split("version:", 1)[-1].strip()
        if curr_ver and curr_ver != SCHEMA_VERSION:
            logger.warning(f"Schema 版本不匹配：当前 {curr_ver}，期望 {SCHEMA_VERSION}。建议评估灰度迁移。")

        if METRICS_AVAILABLE:
            schema_status.labels(class_name=TARGET_CLASS).set(1)
    else:
        logger.info(f"Class '{TARGET_CLASS}' 不存在，创建中")
        payload = {
            "class": TARGET_CLASS,
            "description": f"A chunk of source code with manual embeddings and rich context, version: {SCHEMA_VERSION}",
            "vectorizer": "none",
            "properties": PROPERTIES,
        }
        try:
            r = requests.post(f"{WEAVIATE_URL}/v1/schema", headers=HEADERS, json=payload, timeout=30)
            r.raise_for_status()
            logger.info(f"Class '{TARGET_CLASS}' 创建成功")
            if METRICS_AVAILABLE:
                schema_status.labels(class_name=TARGET_CLASS).set(1)
        except requests.RequestException as e:
            if METRICS_AVAILABLE:
                schema_status.labels(class_name=TARGET_CLASS).set(0)
            logger.error(f"创建 Class 失败: {e}")
            sys.exit(1)

    if METRICS_AVAILABLE:
        schema_fields.labels(class_name=TARGET_CLASS).set(len(PROPERTIES))
        final = count_objects()
        schema_objects.labels(class_name=TARGET_CLASS).set(final)
        object_delta.labels(class_name=TARGET_CLASS).set(final - initial)

if __name__ == "__main__":
    main()
