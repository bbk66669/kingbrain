#!/usr/bin/env python3
"""
sync_to_neo4j.py

将容器元数据 (container_meta.db) 和调用图 (system.dot) 同步到 Neo4j。

MVP Schema:
  (:Container {name, image, ports, updated_at})
  (:File {path})
  (:Container)-[:RUNS]->(:File)   # 可选，如果有 entry
  (:File)-[:CALLS]->(:File)
"""

import os
import sys
import sqlite3
import re
import logging
from neo4j import GraphDatabase

# =============== 日志配置 ===============
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# =============== 常量 ===============
ENV_FILE  = "/etc/kingbrain/sync_to_neo4j.env"
DB_PATH   = "/srv/kingbrain/insight/container_meta.db"
DOT_PATH  = "/srv/kingbrain/insight/graphs/system.dot"

# =============== 预检 & 环境加载 ===============
def preflight():
    # 如果有 env 文件就先加载
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ[k] = v

    # 必要变量检查
    url = os.getenv("NEO4J_URL")
    user = os.getenv("NEO4J_USER")
    pwd = os.getenv("NEO4J_PASS")
    if not (url and user and pwd):
        sys.exit("❌ 请在 /etc/kingbrain/sync_to_neo4j.env 中设置 NEO4J_URL、NEO4J_USER、NEO4J_PASS")

    # 文件检查
    if not os.path.isfile(DB_PATH):
        sys.exit(f"❌ 找不到 SQLite DB: {DB_PATH}")
    if not os.path.isfile(DOT_PATH):
        sys.exit(f"❌ 找不到 DOT 文件: {DOT_PATH}")

    return url, user, pwd

# =============== 建立 Neo4j 驱动 ===============
url, user, pwd = preflight()
try:
    driver = GraphDatabase.driver(url, auth=(user, pwd))
except Exception as e:
    sys.exit(f"❌ 无法连接 Neo4j ({url}): {e}")

# =============== 事务函数 ===============
def clear_db(tx):
    tx.run("MATCH (n) DETACH DELETE n")

def sync_containers(tx):
    with sqlite3.connect(DB_PATH) as con:
        cols = [r[1] for r in con.execute("PRAGMA table_info(containers)")]
        has_entry = "entry" in cols
        sql = (
            "SELECT name, image, ports, updated_at, entry FROM containers"
            if has_entry
            else "SELECT name, image, ports, updated_at, NULL AS entry FROM containers"
        )
        rows = con.execute(sql).fetchall()

    if not rows:
        logging.warning("⚠️ 容器表为空")

    params = []
    for name, image, ports, updated_at, entry in rows:
        params.append({
            "name": name,
            "image": image or "",
            "ports": ports or "",
            "updated_at": updated_at,
            "entry": entry or None
        })

    # 创建/更新 Container 节点
    tx.run("""
        UNWIND $rows AS r
        MERGE (c:Container {name: r.name})
          SET c.image      = r.image,
              c.ports      = r.ports,
              c.updated_at = r.updated_at
    """, rows=params)

    # 如果有 entry 字段，再写 RUNS 关系
    tx.run("""
        UNWIND $rows AS r
        WITH r WHERE r.entry IS NOT NULL AND r.entry <> ''
        MERGE (c:Container {name: r.name})
        MERGE (f:File {path: r.entry})
        MERGE (c)-[:RUNS]->(f)
    """, rows=params)

def parse_dot_edges():
    edges = []
    pat = re.compile(r'^\s*"?(?P<src>[^"]+)"?\s*->\s*"?(?P<dst>[^"]+)"?')
    with open(DOT_PATH, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line or line.startswith("//") or "->" not in line:
                continue
            m = pat.match(line)
            if m:
                edges.append({"src": m.group("src"), "dst": m.group("dst")})
    return edges

def sync_calls(tx, edges):
    if not edges:
        logging.warning("⚠️ DOT 文件中没有 CALLS 关系")
        return
    tx.run("""
        UNWIND $edges AS e
        MERGE (f1:File {path: e.src})
        MERGE (f2:File {path: e.dst})
        MERGE (f1)-[:CALLS]->(f2)
    """, edges=edges)

# =============== 主流程 ===============
def main():
    edges = parse_dot_edges()
    logging.info("解析到 %d 条 CALLS 关系", len(edges))

    try:
        with driver.session() as sess:
            sess.write_transaction(clear_db)
            sess.write_transaction(sync_containers)
            sess.write_transaction(sync_calls, edges)
            cnt_c = sess.run("MATCH (c:Container) RETURN count(c) AS c").single()["c"]
            cnt_f = sess.run("MATCH (f:File) RETURN count(f) AS f").single()["f"]
    except Exception:
        logging.exception("❌ 同步到 Neo4j 失败")
        sys.exit(1)
    finally:
        driver.close()

    logging.info("✅ 同步完成")
    logging.info("  • 容器节点：%d 个", cnt_c)
    logging.info("  • 文件节点：%d 个", cnt_f)
    logging.info("  • 调用边：%d 条", len(edges))

if __name__ == "__main__":
    main()
