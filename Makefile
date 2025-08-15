# ===== KingBrain bootstrap Makefile =====
# 使用方式：
#   make kb-preflight   # 基础连通性检查
#   make kb-deploy      # 部署 orchestrator FAKE 骨架（kustomize overlay）
#   make kb-contracts   # 合约探活：/kb-api/health
#   make kb-smoke       # 冒烟：POST /kb-api/plan 返回统一 envelope
#   make kb-diag        # 诊断信息
#   make kb-rollback    # 回滚到上一个 ReplicaSet

SHELL := /bin/bash
KNS ?= orchestrator
K8S_OVERLAY ?= k8s/orchestrator/overlays/fake

.DEFAULT_GOAL := kb-help

.PHONY: kb-help
kb-help:
	@echo "Targets:"
	@echo "  kb-preflight   - verify kube context and namespace"
	@echo "  kb-deploy      - apply orchestrator + composer + audit-ingester"
	@echo "  kb-contracts   - contract ping against /kb-api/health"
	@echo "  kb-smoke       - smoke test: POST /kb-api/plan"
	@echo "  kb-diag        - print diagnostics (deploy/svc/ingress/pods + logs)"
	@echo "  kb-rollback    - rollout undo kb-orchestrator"

.PHONY: kb-preflight
kb-preflight:
	@echo ">> Preflight: kube context & ns"
	@kubectl version --client=true >/dev/null
	@kubectl get ns $(KNS) >/dev/null || (echo "namespace '$(KNS)' not found"; exit 1)

.PHONY: kb-deploy
kb-deploy:
	@echo ">> Deploy overlay: $(K8S_OVERLAY)"
	kubectl -n $(KNS) apply -k $(K8S_OVERLAY)
	@echo ">> Deploy composer (ClusterIP)"
	kubectl -n $(KNS) apply -k k8s/services/composer
	@echo ">> Deploy audit-ingester (ClusterIP)"
	kubectl -n $(KNS) apply -k k8s/services/audit-ingester

.PHONY: kb-contracts
kb-contracts:
	@echo ">> Contracts: ping /kb-api/health via ClusterIP (with retries)"
	kubectl -n $(KNS) run tmp-curl --rm -i --restart=Never --image=alpine:3.20 -- \
	  sh -lc 'set -e; apk add --no-cache curl jq >/dev/null; \
	    for i in 0 1 2 3 4; do \
	      if curl -fsS http://kb-orchestrator.$(KNS).svc.cluster.local:8000/kb-api/health \
	        | jq -e ".status==\"ok\" and .mode!=null" >/dev/null; then \
	        echo OK; exit 0; fi; echo retry $$i; sleep 2; done; exit 1'

.PHONY: kb-smoke
kb-smoke:
	@echo ">> Smoke: POST /kb-api/plan (with retries)"
	kubectl -n $(KNS) run tmp-curl2 --rm -i --restart=Never --image=alpine:3.20 -- \
	  sh -lc 'set -e; apk add --no-cache curl jq >/dev/null; \
	    for i in 0 1 2 3 4; do \
	      if curl -fsS -X POST -H "Content-Type: application/json" \
	        -d "{\"task\":\"smoke\",\"notes\":\"fake\"}" \
	        http://kb-orchestrator.$(KNS).svc.cluster.local:8000/kb-api/plan \
	        | jq -e ".result.phase==\"PLAN\"" >/dev/null; then \
	        echo OK; exit 0; fi; echo retry $$i; sleep 2; done; exit 1'

.PHONY: kb-rollback
kb-rollback:
	@echo ">> Rollback kb-orchestrator / composer / audit-ingester"
	-kubectl -n $(KNS) rollout undo deploy/kb-orchestrator
	-kubectl -n $(KNS) rollout undo deploy/kb-composer
	-kubectl -n $(KNS) rollout undo deploy/kb-audit-ingester

.PHONY: kb-diag
kb-diag:
	@echo ">> Diagnostics"
	@kubectl -n $(KNS) get deploy,svc,pods -o wide
	@kubectl -n $(KNS) logs deploy/kb-orchestrator --tail=200 || true
