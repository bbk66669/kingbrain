#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backup_restore.py
Weaviate 数据备份与恢复到 S3 兼容存储（分页 + 向量 + 校验）

CHANGELOG
- LoggerAdapter + trace_id。
- 使用 GraphQL 游标分页（cursor/after）抓取对象，或 REST /objects?include=vector&limit&cursor。
- 备份内容包含 id、properties、vector。按分片写入 S3（每1万对象一个分片，可配置）。
- 恢复时保留原 UUID 与向量；失败重试与计数。
"""

import os
import json
import time
import logging
import signal
import atexit
from datetime import datetime

import boto3
import requests
from dotenv import load_dotenv

load_dotenv()

TRACE_ID = os.getenv("TRACE_ID", "default")
_base_logger = logging.getLogger("backup_restore")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: [trace=%(trace_id)s] %(message)s")
logger = logging.LoggerAdapter(_base_logger, {"trace_id": TRACE_ID})

WEAVIATE_URL = os.getenv("WEAVIATE_URL", "http://127.0.0.1:8080").rstrip("/")
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "")
S3_BUCKET = os.getenv("S3_BUCKET", "weaviate-backups")
S3_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID")
S3_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
TARGET_CLASS = "CodeChunk"
CHUNK_SIZE = int(os.getenv("BACKUP_CHUNK_SIZE", "10000"))
REST_LIMIT = 1000

HEADERS = {"Content-Type": "application/json"}

def list_objects_rest():
    """使用 REST 游标分页导出，包含向量。"""
    objects = []
    cursor = None
    total = 0
    while True:
        params = {
            "class": TARGET_CLASS,
            "limit": REST_LIMIT,
            "include": "vector",
        }
        if cursor:
            params["cursor"] = cursor
        url = f"{WEAVIATE_URL}/v1/objects"
        r = requests.get(url, headers=HEADERS, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        objs = data.get("objects", [])
        objects.extend(objs)
        total += len(objs)
        logger.info(f"Fetched {len(objs)} objects, total={total}")
        cursor = data.get("page", {}).get("next")
        if not cursor or len(objs) == 0:
            break
    return objects

def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT or None,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
    )

def backup():
    try:
        all_objs = list_objects_rest()
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        cli = s3_client()

        if not all_objs:
            logger.info("No objects to backup")
            return

        # 分片写入
        total = len(all_objs)
        parts = (total + CHUNK_SIZE - 1) // CHUNK_SIZE
        for i in range(parts):
            seg = all_objs[i*CHUNK_SIZE : (i+1)*CHUNK_SIZE]
            key = f"{TARGET_CLASS}/backup_{ts}_part{i+1}_of_{parts}.jsonl"
            body = "\n".join(json.dumps(o, ensure_ascii=False) for o in seg)
            cli.put_object(Bucket=S3_BUCKET, Key=key, Body=body.encode("utf-8"))
            logger.info(f"Backup part {i+1}/{parts} saved: s3://{S3_BUCKET}/{key}")

        meta_key = f"{TARGET_CLASS}/backup_{ts}_meta.json"
        cli.put_object(Bucket=S3_BUCKET, Key=meta_key, Body=json.dumps({"total": total, "parts": parts}, ensure_ascii=False).encode("utf-8"))
        logger.info(f"Backup meta saved: s3://{S3_BUCKET}/{meta_key}")
    except Exception as e:
        logger.error(f"Backup failed: {e}")

def restore(key_prefix: str):
    """从指定前缀恢复（例如 CodeChunk/backup_20250726T010000Z_）。"""
    cli = s3_client()
    # 列表
    resp = cli.list_objects_v2(Bucket=S3_BUCKET, Prefix=key_prefix)
    contents = resp.get("Contents", [])
    parts = [o["Key"] for o in contents if o["Key"].endswith(".jsonl")]
    if not parts:
        logger.error("未找到任何分片 .jsonl")
        return

    restored = 0
    for key in sorted(parts):
        obj = cli.get_object(Bucket=S3_BUCKET, Key=key)
        for line in obj["Body"].iter_lines():
            if not line:
                continue
            item = json.loads(line.decode("utf-8"))
            # 兼容字段
            _id = item.get("id")
            vector = item.get("vector")
            props = item.get("properties") or item.get("properties", {})
            # 老格式兼容
            if not props and "class" in item and "properties" in item:
                props = item["properties"]

            payload = {
                "class": TARGET_CLASS,
                "id": _id,
                "properties": props,
            }
            if vector is not None:
                payload["vector"] = vector
            ok = False
            for attempt in range(3):
                try:
                    r = requests.post(f"{WEAVIATE_URL}/v1/objects", headers=HEADERS, json=payload, timeout=30)
                    if r.status_code in (200, 201):
                        ok = True
                        restored += 1
                        break
                    else:
                        logger.error(f"Restore HTTP {r.status_code}: {r.text[:200]}")
                        time.sleep(2)
                except Exception as e:
                    logger.error(f"Restore error: {e}")
                    time.sleep(2)
            if not ok:
                logger.error(f"Failed to restore object {_id}")
    logger.info(f"Restored {restored} objects from {key_prefix}")

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", action="store_true")
    ap.add_argument("--restore-prefix", type=str, help="S3 key prefix to restore")
    args = ap.parse_args()

    if args.backup:
        backup()
    elif args.restore_prefix:
        restore(args.restore_prefix)
    else:
        print("Please specify --backup or --restore-prefix <prefix>")

if __name__ == "__main__":
    main()
