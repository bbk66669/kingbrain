# ===== KingBrain bootstrap Makefile =====
# 使用方式：
#   make kb-preflight   # 基础连通性检查
#   make kb-deploy      # 部署 orchestrator FAKE 骨架（kustomize overlay）
#   make kb-contracts   # 合约探活：/kb-api/health
#   make kb-smoke       # 冒烟：POST /kb-api/plan 返回统一 envelope
#   make kb-diag        # 诊断信息
#   make kb-rollback    # 回滚到上一个 ReplicaSet
#   make kb-ack         # trigger /kb-api/ack
#   make kb-plan        # trigger /kb-api/plan
#   make kb-borrow      # trigger /kb-api/borrow
#   make kb-diff        # trigger /kb-api/diff
#   make kb-cr          # trigger /kb-api/cr
#   make kb-clean       # 清理可能遗留的 tmp-* 调试 Pod
#   make kb-status      # 查看 rollout 状态与 Pod 详情

SHELL := /bin/bash
KNS ?= orchestrator
K8S_OVERLAY ?= k8s/orchestrator/overlays/fake

.DEFAULT_GOAL := kb-help

.PHONY: kb-help
kb-help:
	@echo "Targets:"
	@echo "  kb-preflight   - verify kube context and namespace"
	@echo "  kb-deploy      - apply kustomize overlay: $(K8S_OVERLAY)"
	@echo "  kb-contracts   - contract ping against /kb-api/health"
	@echo "  kb-smoke       - smoke test: POST /kb-api/plan"
	@echo "  kb-diag        - print diagnostics (deploy/svc/ingress/pods + logs)"
	@echo "  kb-rollback    - rollout undo kb-orchestrator"
	@echo "  kb-ack         - trigger /kb-api/ack"
	@echo "  kb-plan        - trigger /kb-api/plan"
	@echo "  kb-borrow      - trigger /kb-api/borrow"
	@echo "  kb-diff        - trigger /kb-api/diff"
	@echo "  kb-cr          - trigger /kb-api/cr"
	@echo "  kb-clean       - clean leftover tmp-* pods"
	@echo "  kb-status      - show rollout status & pod details"

.PHONY: kb-preflight
kb-preflight:
	@echo ">> Preflight: kube context & ns"
	@kubectl version --client=true >/dev/null
	@kubectl get ns $(KNS) >/dev/null

.PHONY: kb-deploy
kb-deploy:
	@echo ">> Deploy overlay: $(K8S_OVERLAY)"
	kubectl apply -k $(K8S_OVERLAY)

.PHONY: kb-contracts
kb-contracts:
	@echo ">> Contracts: ping /kb-api/health via ClusterIP (with retries)"
	kubectl -n $(KNS) run tmp-curl-$$(date +%s) --rm -i --restart=Never --image=alpine:3.20 -- \
	  sh -lc 'set -e; apk add --no-cache curl jq >/dev/null; \
	    i=0; until [ $$i -ge 5 ]; do \
	      if curl -fsS --max-time 10 http://kb-orchestrator.$(KNS).svc.cluster.local:8000/kb-api/health \
	        | jq -e ".status==\"ok\" and .mode!=null" >/dev/null; then \
	        echo OK; exit 0; \
	      fi; \
	      echo "retry $$i"; i=$$((i+1)); sleep 2; \
	    done; \
	    echo "health check failed"; exit 1'

.PHONY: kb-smoke
kb-smoke:
	@echo ">> Smoke: POST /kb-api/plan (with retries)"
	kubectl -n $(KNS) run tmp-curl2-$$(date +%s) --rm -i --restart=Never --image=alpine:3.20 -- \
	  sh -lc 'set -e; apk add --no-cache curl jq >/dev/null; \
	    i=0; until [ $$i -ge 5 ]; do \
	      if curl -fsS --max-time 20 -X POST -H "Content-Type: application/json" \
	        -d "{\"task\":\"smoke\",\"notes\":\"fake\"}" \
	        http://kb-orchestrator.$(KNS).svc.cluster.local:8000/kb-api/plan \
	        | jq -e "(.result.phase // .phase) == \"PLAN\"" >/dev/null; then \
	        echo OK; exit 0; \
	      fi; \
	      echo "retry $$i"; i=$$((i+1)); sleep 2; \
	    done; \
	    echo "smoke failed"; exit 1'

.PHONY: kb-rollback
kb-rollback:
	@echo ">> Rollback to previous ReplicaSet (if any)"
	- kubectl -n $(KNS) rollout undo deploy/kb-orchestrator

.PHONY: kb-diag
kb-diag:
	@echo ">> Diagnostics"
	@kubectl -n $(KNS) get deploy,svc,ingress,pods -o wide
	@kubectl -n $(KNS) logs deploy/kb-orchestrator --tail=200 || true
	@echo ">> Recent events"
	@kubectl -n $(KNS) get events --sort-by=.lastTimestamp | tail -n 20 || true
	@echo ">> Deploy describe (tail)"
	@kubectl -n $(KNS) describe deploy/kb-orchestrator | tail -n 80 || true

# 通用 API 调用模板 (支持 t / n / extra 参数)
define KB_RUN_TEMPLATE
	@echo ">> $(1)"
	kubectl -n $(KNS) run tmp-$(1)-$$(date +%s) --rm -i --restart=Never --image=alpine:3.20 -- \
	  sh -lc 'set -e; apk add --no-cache curl jq >/dev/null; \
	    JSON=$$(jq -n --arg task "$${t:-scaffold}" --arg notes "$${n:-$(1)}" \
	      --arg extra "$${extra:-}" \
	      "$$extra|try fromjson catch {} as $$E | {task:$$task, notes:$$notes} + $$E"); \
	    curl -fsS --max-time 20 -X POST -H "Content-Type: application/json" \
	      -d "$$JSON" \
	      http://kb-orchestrator.$(KNS).svc.cluster.local:8000/kb-api/$(1) | jq'
endef

.PHONY: kb-ack kb-plan kb-borrow kb-diff kb-cr
kb-ack:    ; $(call KB_RUN_TEMPLATE,ack)
kb-plan:   ; $(call KB_RUN_TEMPLATE,plan)
kb-borrow: ; $(call KB_RUN_TEMPLATE,borrow)
kb-diff:   ; $(call KB_RUN_TEMPLATE,diff)
kb-cr:     ; $(call KB_RUN_TEMPLATE,cr)

# ========= 清理与状态 =========
.PHONY: kb-clean
kb-clean:
	@echo ">> Clean leftover tmp-* pods (if any)"
	- kubectl -n $(KNS) get pods -o name | grep '^pod/tmp-' | xargs -r kubectl -n $(KNS) delete
	@kubectl -n $(KNS) get pods | grep tmp- || echo "no tmp pods"

.PHONY: kb-status
kb-status:
	@echo ">> Status: kb-orchestrator rollout & pods"
	@kubectl -n $(KNS) rollout status deploy/kb-orchestrator
	@kubectl -n $(KNS) get deploy/kb-orchestrator -o wide
	@kubectl -n $(KNS) get rs -l app=kb-orchestrator
	@kubectl -n $(KNS) get pods -l app=kb-orchestrator -o wide
	@echo ">> Pod describe (last 50 lines)"
	@kubectl -n $(KNS) describe pod -l app=kb-orchestrator | tail -n 50 || true
