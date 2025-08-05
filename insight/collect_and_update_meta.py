#!/usr/bin/env python3
import os
import requests
import sqlite3
import time
import json
import subprocess

PROM_HOST = os.getenv("PROM", "http://localhost:9090")
DB_PATH   = "/srv/kingbrain/insight/container_meta.db"

def get_entrypoint(container_name: str) -> str:
    """
    只对已知的 container_name 调用 inspect，并把 stderr 丢掉。
    """
    try:
        raw = subprocess.check_output(
            ["docker", "inspect", "--format", "{{ json .Config.Entrypoint }}", container_name],
            text=True,
            stderr=subprocess.DEVNULL
        ).strip()
        arr = json.loads(raw or "[]") or []
        return arr[0] if isinstance(arr, list) and arr else ""
    except Exception:
        return ""

def main():
    # 1) docker ps 拿映射
    lines = subprocess.check_output(
        ["docker", "ps", "--format", "{{.Names}} {{.Image}}"]
    ).decode().splitlines()
    image_map = dict(line.split(" ", 1) for line in lines if " " in line)

    # 2) Prometheus 查询 last_seen
    r = requests.get(f"{PROM_HOST}/api/v1/query", params={"query": "container_last_seen"})
    last_seen_map = {
        m["metric"].get("name", ""): int(float(m["value"][1]))
        for m in r.json().get("data", {}).get("result", [])
    }

    # 3) Prometheus activeTargets
    r2 = requests.get(f"{PROM_HOST}/api/v1/targets")
    targets = r2.json().get("data", {}).get("activeTargets", [])

    rows_meta = []
    rows_containers = []
    now = int(time.time())

    for t in targets:
        L = t.get("labels", {})
        inst = L.get("instance", "")
        raw_ctr = L.get("container")
        raw_name = L.get("name")
        name = raw_ctr or raw_name or L.get("job") or ""
        if not name:
            continue

        source = "container" if raw_ctr else ("name" if raw_name else "job")
        port = inst.split(":", 1)[1] if ":" in inst else ""
        git_sha = L.get("container_label_org_git_sha", "")

        # 镜像匹配策略
        image = image_map.get(name, "")
        if not image:
            for dn, img in image_map.items():
                if dn.endswith(f"-{name}"):
                    image = img; break
        if not image and name == "prometheus" and "prom" in image_map:
            image = image_map["prom"]
        if not image and source in ("container","name"):
            for dn, img in image_map.items():
                if name in dn:
                    image = img; break

        updated_at = last_seen_map.get(name, now)

        # 只有当 name 在 image_map 里才 inspect，避免报错
        entry = get_entrypoint(name) if name in image_map else ""

        # meta 表行
        rows_meta.append((
            name,
            L.get("container_label_com_docker_compose_service",""),
            L.get("container_label_com_docker_compose_project",""),
            L.get("container_label_io_kubernetes_container_name",""),
            L.get("job",""),
            json.dumps(L),
            updated_at
        ))

        # containers 表行，多了 entry
        rows_containers.append((
            name,
            image or "",
            git_sha,
            updated_at,
            port,
            entry
        ))

    # 写 SQLite
    con = sqlite3.connect(DB_PATH)
    con.execute("DROP TABLE IF EXISTS meta")
    con.execute("""
      CREATE TABLE meta(
        name TEXT PRIMARY KEY,
        service TEXT,
        project TEXT,
        k8s TEXT,
        job TEXT,
        labels TEXT,
        updated_at INTEGER
      )
    """)
    con.executemany(
      "INSERT INTO meta(name,service,project,k8s,job,labels,updated_at) VALUES(?,?,?,?,?,?,?)",
      rows_meta
    )

    con.execute("DROP TABLE IF EXISTS containers")
    con.execute("""
      CREATE TABLE containers(
        name TEXT PRIMARY KEY,
        image TEXT,
        git_sha TEXT,
        updated_at INTEGER,
        ports TEXT,
        entry TEXT
      )
    """)
    con.executemany(
      "INSERT INTO containers(name,image,git_sha,updated_at,ports,entry) VALUES(?,?,?,?,?,?)",
      rows_containers
    )

    con.commit()
    con.close()

if __name__ == "__main__":
    main()
