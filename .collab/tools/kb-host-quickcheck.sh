#!/usr/bin/env bash
set -Eeuo pipefail

KCFG_DEFAULT="/etc/rancher/k3s/k3s.yaml"
export KUBECONFIG="${KUBECONFIG:-$KCFG_DEFAULT}"

if [ ! -r "$KUBECONFIG" ]; then
  echo "✖️ 找不到 kubeconfig: $KUBECONFIG（或 $KCFG_DEFAULT）"
  exit 1
fi

TMPCACHE="$(mktemp -d)"
trap 'rm -rf "$TMPCACHE"' EXIT

echo "[KB] 检查 K8s 连接…"
# 1) 客户端可用  2) API /readyz 可用（等价于连通并健康）
if kubectl --request-timeout=10s --cache-dir="$TMPCACHE" version --client -o=yaml >/dev/null 2>&1 \
  && kubectl --request-timeout=10s --cache-dir="$TMPCACHE" get --raw='/readyz?verbose' >/dev/null 2>&1; then
  echo "✅ kubectl 已连通：$(kubectl --cache-dir="$TMPCACHE" config current-context || echo default)"
  kubectl --cache-dir="$TMPCACHE" get nodes -o wide
else
  echo "✖️ kubectl 无法连到集群（请检查 KUBECONFIG=$KUBECONFIG）"
  exit 1
fi
