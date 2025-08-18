#!/usr/bin/env bash
set -euo pipefail
NS=orchestrator
ROOT=/srv/kingbrain
TODAY=$(date +%Y%m%d)

# 1. 查看 orchestrator 日志里是否有 reject 相关信息
echo "===== 1. 守卫日志 ====="
kubectl -n $NS logs deploy/kb-orchestrator -c api --tail=200 \
  | grep -iE 'allowlist|forbidden|reject|written_paths' || echo "无守卫日志"

# 2. 查看本地 JSONL 是否已写入 rejected 事件
echo "===== 2. rejected 事件 ====="
grep -E '"type":"kb\.workflow\.PLAN\.rejected\.v1"' \
  "$ROOT/.collab/audit/events-$TODAY.jsonl" | tail -n 2 || echo "无 rejected 事件"

# 3. 本地回放 /events/{id} 排查
echo "===== 3. events/{id} 本地回放 ====="
EVENT_FILE="$ROOT/.collab/audit/events-$TODAY.jsonl"
if [[ -f "$EVENT_FILE" ]]; then
  FIRST_ID=$(tail -n 20 "$EVENT_FILE" \
    | jq -r 'select(.type | startswith("kb.workflow.PLAN")) | .id' \
    | head -n1)
  if [[ -n "$FIRST_ID" ]]; then
    echo "本地事件 ID: $FIRST_ID"
    jq -r --arg id "$FIRST_ID" 'select(.id == $id)' "$EVENT_FILE" | jq .
  else
    echo "本地 JSONL 无 PLAN 事件"
  fi
else
  echo "本地 JSONL 不存在"
fi

# 4. cesql 文档存在性
echo "===== 4. CESQL 文档 ====="
test -f "$ROOT/docs/kingbrain/cesql-examples.md" \
  && echo "✅ 已存在" \
  || echo "❌ 缺失，需补"

# 5. 手动用 kubectl exec 直接调 orchestrator（避开域名/ingress）
echo "===== 5. 集群内直连验证 ====="
kubectl -n $NS run tmp-check --rm -i --restart=Never --image=alpine:3.20 -- \
  sh -lc 'set -e; apk add --no-cache curl jq >/dev/null;
    curl -sS http://kb-orchestrator:8000/kb-api/health | jq .;
    curl -sS http://kb-orchestrator:8000/kb-api/config | jq .;
    curl -sS -X POST http://kb-orchestrator:8000/kb-api/plan \
      -H "Content-Type: application/json" \
      -d "{\"task\":\"deny\",\"written_paths\":[\"/workspace/forbidden/file.txt\"]}" | jq .'
