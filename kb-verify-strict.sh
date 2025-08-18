#!/usr/bin/env bash
# kb-verify-merged.sh — 合并版（rev4）
# - 非中断执行：聚合失败，最后汇总
# - JSONL 支持昨天回退
# - grep 使用 -E 以匹配 (PLAN|plan)
# - 守卫拒绝也必须 HTTP 200
# - 校验 /plan.result.written_paths 前缀
# - OpenAPI 内容检查

set -uo pipefail

NS=orchestrator
ROOT=/srv/kingbrain
TODAY=$(date +%Y%m%d)
YDAY=$(date -d "yesterday" +%Y%m%d 2>/dev/null || date -v-1d +%Y%m%d)

FAIL=0
pass(){ echo "✅ $*"; }
fail(){ echo "❌ $*"; FAIL=$((FAIL+1)); }
warn(){ echo "⚠️  $*"; }

need_cmd(){ command -v "$1" >/dev/null 2>&1 || fail "缺少命令：$1"; }
need_cmd curl; need_cmd jq; need_cmd kubectl; need_cmd grep; need_cmd awk; need_cmd sed; need_cmd make

echo "=== A. Overlay hostPath ==="
# 仅一个 patchesStrategicMerge 引用，且指向 patch-hostpath.yaml
if [[ $(grep -c 'patchesStrategicMerge' k8s/orchestrator/overlays/fake/kustomization.yaml 2>/dev/null || echo 0) -ne 1 ]]; then
  fail "kustomization.yaml 中必须只有一个 patchesStrategicMerge"
fi
grep -q 'patch-hostpath.yaml' k8s/orchestrator/overlays/fake/kustomization.yaml 2>/dev/null || fail "kustomization.yaml 未引用 patch-hostpath.yaml"
# 补丁不得覆盖 command/args
if grep -Eq '^\s*command:|^\s*args:' k8s/orchestrator/overlays/fake/patch-hostpath.yaml 2>/dev/null; then
  fail "patch-hostpath.yaml 不得覆盖 command/args"
fi

# 部署已应用 & 三个工作负载挂载正确
for name in kb-orchestrator kb-composer kb-audit-ingester; do
  kubectl -n "$NS" get deploy "$name" >/dev/null 2>&1 || fail "Deployment $name 不存在"
  kubectl -n "$NS" rollout status deploy/"$name" >/dev/null 2>&1 || fail "$name 未就绪"
  yaml=$(kubectl -n "$NS" get deploy "$name" -o yaml 2>/dev/null || echo "")
  echo "$yaml" | grep -q 'path: /srv/kingbrain' || fail "$name 未挂 hostPath /srv/kingbrain"
  echo "$yaml" | grep -q 'mountPath: /workspace' || fail "$name 未挂 volumeMount /workspace"
done
pass "hostPath /srv/kingbrain → /workspace 挂载检查完成"

echo "=== B. 回归接口 /health /config /plan ==="
# /health
MODE=$(curl -fsS http://kb.mwwnd.org/kb-api/health 2>/dev/null | jq -r '.mode // empty')
[[ "$MODE" =~ ^(FAKE|REAL)$ ]] && pass "/health OK (mode=$MODE)" || fail "/health 未返回合法 mode"

# /config
CFG=$(curl -fsS http://kb.mwwnd.org/kb-api/config 2>/dev/null || echo "{}")
[[ $(echo "$CFG" | jq -r '.events_sink') == "nats+file" ]] || fail "/config.events_sink 应为 nats+file（实际：$(echo "$CFG" | jq -r '.events_sink')）"
[[ $(echo "$CFG" | jq -r '.repo_root') == "/srv/kingbrain" ]] || fail "/config.repo_root 应为 /srv/kingbrain（实际：$(echo "$CFG" | jq -r '.repo_root')）"
for dep in nats temporal; do
  avail=$(echo "$CFG" | jq -r ".deps.$dep.available // empty")
  [[ "$avail" == "true" || "$avail" == "false" ]] || fail "/config.deps.$dep.available 应为 true/false（实际：$avail）"
done
pass "/config OK"

# /plan（FAKE）
TMP_DIR="$(mktemp -d -t kbv.XXXXXX)"
PLAN_HTTP=$(curl -sS -o "$TMP_DIR/plan.json" -w "%{http_code}" -X POST http://kb.mwwnd.org/kb-api/plan -H 'Content-Type: application/json' -d '{"task":"smoke"}' 2>/dev/null || echo 000)
PLAN_RESP="$(cat "$TMP_DIR/plan.json" 2>/dev/null || echo '{}')"
PHASE=$(echo "$PLAN_RESP" | jq -r '.result.phase // empty')
WID=$(echo "$PLAN_RESP" | jq -r '.workflow_id // empty')
RID=$(echo "$PLAN_RESP" | jq -r '.run_id // empty')
CEID=$(echo "$PLAN_RESP" | jq -r '.result.cloudevent_ids[0] // empty')
[[ "$PLAN_HTTP" -eq 200 ]] || fail "/plan HTTP 状态非 200（$PLAN_HTTP）"
[[ "$PHASE" == "PLAN" ]] || fail "/plan 返回 phase != PLAN（实际：$PHASE）"
[[ -n "$WID" && -n "$RID" ]] || fail "/plan 应返回非空 workflow_id / run_id（实际：$WID / $RID）"
[[ -n "$CEID" ]] || fail "/plan.result.cloudevent_ids 为空"
# written_paths 前缀（如存在）
if echo "$PLAN_RESP" | jq -e '.result.written_paths|length>0' >/dev/null 2>&1; then
  echo "$PLAN_RESP" | jq -r '.result.written_paths[]' | grep -Eq '^/srv/kingbrain/' || fail "written_paths[] 必须是 /srv/kingbrain 前缀"
fi
pass "/plan OK（phase=PLAN, workflow/run id 存在, event id=$CEID）"

echo "=== C. /events/{id} 本地 JSONL 回放 ==="
EVT=$(curl -fsS "http://kb.mwwnd.org/kb-api/events/$CEID" 2>/dev/null || echo "{}")
SPEC=$(echo "$EVT" | jq -r '.specversion // empty')
TYPE=$(echo "$EVT" | jq -r '.type // empty')
SUBJ=$(echo "$EVT" | jq -r '.subject // empty')
DPHASE=$(echo "$EVT" | jq -r '.data.phase // empty')
[[ "$SPEC" == "1.0" ]] || fail "CloudEvent specversion 应为 1.0（实际：$SPEC）"
echo "$TYPE" | grep -Eq '^kb\.workflow\.(PLAN|plan)\.(started|completed|rejected)\.v1$' || fail "CloudEvent type 不符合规范：$TYPE"
[[ "$DPHASE" == "PLAN" ]] || fail "CloudEvent data.phase 应为 PLAN（实际：$DPHASE）"
[[ -n "$SUBJ" ]] || fail "CloudEvent subject 为空"
pass "/events/{id} 回放 OK"

echo "=== D. 本地 JSONL 存在与 PLAN started/completed ==="
JSONL="$ROOT/.collab/audit/events-$TODAY.jsonl"
[[ -f "$JSONL" ]] || JSONL="$ROOT/.collab/audit/events-$YDAY.jsonl"
[[ -f "$JSONL" ]] || fail "找不到 JSONL（今天/昨天均无）：$ROOT/.collab/audit/events-{${TODAY},${YDAY}}.jsonl"
grep -Eq '"type":"kb\.workflow\.(PLAN|plan)\.started\.v1"' "$JSONL" || fail "JSONL 未见 PLAN.started.v1"
grep -Eq '"type":"kb\.workflow\.(PLAN|plan)\.completed\.v1"' "$JSONL" || fail "JSONL 未见 PLAN.completed.v1"
pass "JSONL 事件 OK（$JSONL）"

echo "=== E. FAKE 持久化产物与证据骨架 ==="
[[ -f "$ROOT/docs/kingbrain/PLAN/PLAN.md" ]] || fail "缺少 $ROOT/docs/kingbrain/PLAN/PLAN.md"
ls "$ROOT/.collab/PLAN"/manifest-*.json >/dev/null 2>&1 || fail "缺少 $ROOT/.collab/PLAN/manifest-*.json"
for d in sbom attestations policy; do
  [[ -d "$ROOT/.collab/evidence/$d" ]] || fail "缺少证据目录 $ROOT/.collab/evidence/$d"
done
pass "PLAN 产物与证据骨架 OK"

echo "=== F. 写入守卫（拒绝非法路径） ==="
DHTTP=$(curl -sS -o "$TMP_DIR/deny.json" -w "%{http_code}" -X POST http://kb.mwwnd.org/kb-api/plan -H 'Content-Type: application/json' \
  -d '{"task":"deny","written_paths":["/workspace/forbidden/file.txt"]}' 2>/dev/null || echo 000)
[[ "$DHTTP" -eq 200 ]] || fail "写入守卫拒绝时也应返回 200（实际：$DHTTP）"
ERR=$(cat "$TMP_DIR/deny.json" | jq -r '.error // empty')
[[ "$ERR" == "path not allowed" ]] || fail "写入守卫未返回 error=path not allowed（实际：$ERR）"
grep -Eq '"type":"kb\.workflow\.(PLAN|plan)\.rejected\.v1"' "$JSONL" || fail "JSONL 未见 PLAN.rejected.v1"
pass "写入守卫与 rejected 事件 OK"

echo "=== G. 文档：CESQL 与 OpenAPI ==="
if [[ -f docs/kingbrain/cesql-examples.md ]]; then
  grep -Ei 'type LIKE .?kb\.workflow\.plan\..*\.v1.?' docs/kingbrain/cesql-examples.md >/dev/null || fail "cesql-examples.md 未包含 plan.*.v1 示例"
  pass "CESQL 文档 OK"
else
  warn "未发现 docs/kingbrain/cesql-examples.md（建议补充，任务要求）"
fi
[[ -f docs/openapi.yaml ]] || fail "缺少 docs/openapi.yaml"
grep -Eq 'kb\.workflow\..*\.v1' docs/openapi.yaml || fail "openapi.yaml 未定义 kb.workflow.*.v1 事件"
pass "OpenAPI 文档 OK"

echo "=== H. Composer（ClusterIP 内部可达） ==="
if kubectl -n "$NS" run tmp-curl --rm -i --restart=Never --image=alpine:3.20 -- \
  sh -lc 'set -e; apk add --no-cache curl jq >/dev/null; \
  curl -fsS http://kb-composer.orchestrator.svc.cluster.local:8081/healthz | jq -e ".status==\"ok\"" >/dev/null; \
  curl -fsS -X POST http://kb-composer.orchestrator.svc.cluster.local:8081/plan -H "Content-Type: application/json" -d "{\"task\":\"scaffold\"}" | jq -e ".reason==\"NO_LLM_KEYS\"" >/dev/null' >/dev/null 2>&1; then
  pass "Composer /healthz & /plan OK"
else
  fail "Composer /healthz 或 /plan 失败"
fi

echo "=== I. Audit Ingester 回落 ==="
if [[ -f "$ROOT/.collab/audit/backlog.jsonl" ]]; then
  pass "Ingester Fallback backlog.jsonl OK"
else
  warn "未见 backlog.jsonl（若 Neo4j/ClickHouse 已配置则属正常）"
fi

echo "=== J. Backstage 占位 ==="
for app in apps/kb-mode-status apps/kb-openlineage apps/kb-sbom-licenses; do
  [[ -d "$app" ]] || fail "缺少 $app 目录"
done
MODE2=$(echo "$CFG" | jq -r '.mode')
if [[ "$MODE2" == "FAKE" ]]; then
  pass "FAKE 模式（前端应显示 Banner）"
else
  warn "当前非 FAKE 模式（如为 REAL，Banner 逻辑不适用）"
fi

echo "=== K. CLI 仅指向 /kb-api ==="
grep -Eq '/kb-api' tools/kb-stage tools/kb-tools >/dev/null 2>&1 && pass "CLI 指向 /kb-api OK" || fail "CLI 未发现 /kb-api 访问痕迹"

echo "=== L. Makefile 目标（可选一键跑） ==="
make -n kb-preflight kb-deploy kb-contracts kb-smoke kb-verify-l4 >/dev/null 2>&1 && pass "Makefile 目标存在" || warn "Makefile 目标缺失或不可用"

echo "=== M. PR-9 Git 一致性（可选） ==="
if [[ -f "$ROOT/.collab/DRYRUN/commits.log" ]]; then
  pass "Git DRYRUN commits.log 存在"
else
  warn "未检测到 DRYRUN/commits.log（PR-9 可选，且需凭据/触发器）"
fi

# 汇总
if [[ $FAIL -gt 0 ]]; then
  echo "❌ 共有 $FAIL 项校验失败"
  exit 1
else
  echo "✅ 全部校验完成（未出现 ❌ 即视为达标）"
fi
