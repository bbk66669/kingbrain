#!/usr/bin/env python3
import os
import sys
import json
import subprocess
import requests
import sqlite3
import textwrap
import argparse

ROOT      = "/srv/kingbrain/insight"
DB_PATH   = os.path.join(ROOT, "container_meta.db")
PRIMARY   = os.getenv("SG_URL", "http://localhost:7080")
FALLBACK  = os.getenv("LOCAL_SG_ENDPOINT", "http://localhost:7080")
TOKEN     = os.getenv("SG_TOKEN", "")

def find_cmd(keyword: str):
    """本地 ripgrep 索引 fallback"""
    result = subprocess.run(
        ["kb-insight-find", keyword],
        capture_output=True,
        text=True
    )
    print(result.stdout, end="")

def code_cmd(query: str, pattern: str = "literal"):
    """向 Sourcegraph 发 GraphQL 搜索，打印文件匹配结果"""
    gql = f"""
query ($q: String!) {{
  search(version: V3, query: $q, patternType: {pattern}) {{
    results {{
      matchCount
      results {{
        ... on FileMatch {{
          file {{ path }}
          lineMatches {{ preview lineNumber }}
        }}
      }}
    }}
  }}
}}
"""
    payload = {"query": gql, "variables": {"q": query}}
    headers = {"Authorization": f"token {TOKEN}"}
    for endpoint in (PRIMARY, FALLBACK):
        try:
            r = requests.post(
                endpoint + "/.api/graphql",
                json=payload,
                headers=headers,
                timeout=5
            )
            status, body = r.status_code, r.json()
            print(f"Status: {status}")
            print(json.dumps(body, indent=2, ensure_ascii=False))
            return
        except requests.exceptions.RequestException:
            continue
    print(f"Error: cannot reach {PRIMARY} or {FALLBACK}")

def svc_cmd(name: str):
    """从 containers 表里查容器 image/ports/last-updated"""
    if not os.path.isfile(DB_PATH):
        print(f"Error: DB not found at {DB_PATH}")
        sys.exit(1)

    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        "SELECT image, ports, updated_at FROM containers WHERE name = ?",
        (name,)
    )
    rows = cur.fetchall()
    if not rows:
        print(f"No metadata for container '{name}'")
    else:
        for image, ports, updated_at in rows:
            print(f"name:       {name}")
            print(f"image:      {image or '-'}")
            print(f"ports:      {ports or '-'}")
            print(f"updated_at: {updated_at}")
    con.close()

def callgraph_cmd():
    """打印依赖图 SVG 路径"""
    svg = os.path.join(ROOT, "graphs", "system.svg")
    if os.path.isfile(svg):
        print(f"Dependency graph: {svg}")
    else:
        print("Error: SVG graph not found, please run gen_graph_active.py first.")

def sync_neo4j_cmd():
    """手动触发 Neo4j 同步"""
    script = os.path.join(ROOT, "scripts", "sync_to_neo4j.py")
    if not os.path.isfile(script):
        print("Error: sync_to_neo4j.py not found")
        sys.exit(1)
    ret = subprocess.call([script])
    sys.exit(ret)

def usage():
    print(textwrap.dedent("""
      kb.py usage:
        kb find <keyword>                         # ripgrep fallback
        kb code [-p literal|regexp|structural] <sg-query>   # SG GraphQL 文本/正则/结构化搜索
        kb sg   [-p literal|regexp|structural] <sg-query>   # alias for code
        kb svc  <container>                       # show container image/ports/last-updated
        kb callgraph                              # 打印调用图 SVG 路径
        kb sync-neo4j                             # 手动触发 Neo4j 同步
    """).strip())

def main():
    if len(sys.argv) < 2:
        usage()
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "find":
        if len(sys.argv) != 3:
            usage(); sys.exit(1)
        find_cmd(sys.argv[2])

    elif cmd in ("code", "sg"):
        p = argparse.ArgumentParser(prog=f"kb {cmd}")
        p.add_argument("-p", "--pattern",
                       choices=["literal","regexp","structural"],
                       default="literal",
                       help="搜索模式：literal|regexp|structural")
        p.add_argument("query", help="Sourcegraph 查询字符串")
        args = p.parse_args(sys.argv[2:])
        code_cmd(args.query, args.pattern)

    elif cmd == "svc":
        if len(sys.argv) != 3:
            usage(); sys.exit(1)
        svc_cmd(sys.argv[2])

    elif cmd == "callgraph":
        callgraph_cmd()

    elif cmd == "sync-neo4j":
        sync_neo4j_cmd()

    else:
        print(f"Unknown cmd: {cmd}")
        usage()
        sys.exit(1)

if __name__ == "__main__":
    main()
