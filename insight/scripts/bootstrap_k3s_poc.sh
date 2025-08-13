#!/usr/bin/env bash
set -euo pipefail

echo "[KB] PoC bootstrap: k3s + kubeconfig + cert-manager + namespaces"

# 0) 基础依赖
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -y
  sudo apt-get install -y curl jq ca-certificates git
fi

# 1) 安装/校验 k3s（优先 1.32 通道，失败则 stable）
if systemctl is-active --quiet k3s; then
  echo "✔️ k3s already running"
else
  echo "[KB] Installing k3s (channel v1.32, fallback stable)..."
  set +e
  curl -sfL https://get.k3s.io | INSTALL_K3S_CHANNEL="v1.32" sh -
  rc=$?
  if [ $rc -ne 0 ]; then
    echo "[KB] v1.32 channel failed (rc=$rc), falling back to stable..."
    curl -sfL https://get.k3s.io | INSTALL_K3S_CHANNEL="stable" sh -
  fi
  set -e
fi

# 2) kubeconfig
mkdir -p ~/.kube
sudo cp -f /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown "$(id -u)":"$(id -g)" ~/.kube/config
chmod 600 ~/.kube/config

# 如需远程访问，请把 server 改成本机可达 IP（自动替换第一块网卡 IP）
IP=$(hostname -I | awk '{print $1}')
if [ -n "${IP:-}" ]; then
  sed -i "s#https://127.0.0.1:6443#https://$IP:6443#g" ~/.kube/config
fi

echo "[KB] Verifying cluster reachability..."
kubectl version --client --output=yaml
kubectl get nodes -o wide

# 3) Helm（如未安装则快速装）
if ! command -v helm >/dev/null 2>&1; then
  echo "[KB] Installing helm 3..."
  curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
fi

# 4) cert-manager
echo "[KB] Installing cert-manager..."
helm repo add jetstack https://charts.jetstack.io >/dev/null 2>&1 || true
helm repo update >/dev/null 2>&1
helm upgrade --install cert-manager jetstack/cert-manager \
  --namespace cert-manager --create-namespace \
  --set installCRDs=true --wait --timeout 5m

# 5) 预创建命名空间
for ns in kingbrain-portal kingbrain-lineage kingbrain-observability kingbrain-ml; do
  kubectl get ns "$ns" >/dev/null 2>&1 || kubectl create ns "$ns"
done

# 6) 健康检查
echo "[KB] Cluster summary:"
kubectl get nodes -o wide
echo "--- cert-manager pods ---"
kubectl -n cert-manager get pods -o wide
echo "--- first 30 pods ---"
kubectl get pods -A | head -n 30
