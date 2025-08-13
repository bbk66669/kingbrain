#!/usr/bin/env bash
# KingBrain preflight bootstrap (host + cluster)
# Safe to re-run. Ubuntu/Debian, amd64/arm64.

set -euo pipefail

### ---- Config (edit if needed) ---------------------------------------------
KUBECTL_VER="v1.32.0"
COSIGN_VER="v2.2.0"
YQ_VER="v4.40.5"
TKN_VER="v0.32.1"
KYVERNOCLI_VER="v1.12.0"
HELM_INSTALL_SCRIPT="https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3"

# TLS issuer mode: "selfsigned" (default) or "letsencrypt"
TLS_MODE="${TLS_MODE:-selfsigned}"
LE_EMAIL="${LE_EMAIL:-ops@example.com}"     # used when TLS_MODE=letsencrypt

# Namespaces used by our plan
NS_LIST=("kingbrain-portal" "kingbrain-lineage" "kingbrain-observability" "kingbrain-ml")

# Egress endpoints to probe
PROBE_URLS=(
  "https://github.com" 
  "https://ghcr.io/v2/" 
  "https://gcr.io" 
  "https://registry.sigstore.dev" 
  "https://rekor.sigstore.dev"
)

### ---- Helpers --------------------------------------------------------------
log() { printf "\n\033[1;36m[KB]\033[0m %s\n" "$*"; }
ok()  { printf "\033[1;32m✔\033[0m %s\n" "$*"; }
warn(){ printf "\033[1;33m!\033[0m %s\n" "$*"; }
fail(){ printf "\033[1;31m✖ %s\033[0m\n" "$*"; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"
}

arch() {
  case "$(uname -m)" in
    x86_64|amd64) echo "amd64" ;;
    aarch64|arm64) echo "arm64" ;;
    *) fail "unsupported arch $(uname -m)";;
  esac
}

install_pkg_debian() {
  sudo apt-get update -y
  sudo apt-get install -y "$@"
}

ensure_ns() {
  local ns="$1"
  if kubectl get ns "$ns" >/dev/null 2>&1; then ok "namespace $ns exists"
  else kubectl create ns "$ns"; ok "created namespace $ns"; fi
}

ensure_helm_release() {
  local rel="$1" chart="$2" ns="$3"; shift 3
  if helm status "$rel" -n "$ns" >/dev/null 2>&1; then
    ok "helm release $rel in $ns present"
  else
    helm install "$rel" "$chart" -n "$ns" --create-namespace "$@"
    ok "installed $rel ($chart) in $ns"
  fi
}

### ---- 0. Basic host packages & eBPF ---------------------------------------
log "Installing base packages & enabling eBPF (if available)"
install_pkg_debian git jq curl ca-certificates wget tar bpfcc-tools build-essential

# Best effort: load bpf (some kernels build-in; this will just no-op)
if lsmod | grep -q '^bpf'; then
  ok "bpf module already loaded"
else
  echo 'bpf' | sudo tee /etc/modules-load.d/bpf.conf >/dev/null || true
  sudo modprobe bpf 2>/dev/null && ok "bpf module loaded" || warn "bpf modprobe skipped (may be built-in)"
fi

### ---- 1. CLI toolchain (kubectl/helm/cosign/tkn/kyverno/yq) ----------------
ARCH="$(arch)"
log "Installing CLI toolchain for ${ARCH}"

# kubectl
if command -v kubectl >/dev/null 2>&1 && kubectl version --client --output=yaml | grep -q "$KUBECTL_VER"; then
  ok "kubectl $KUBECTL_VER present"
else
  curl -fsSLo kubectl "https://dl.k8s.io/release/${KUBECTL_VER}/bin/linux/${ARCH}/kubectl"
  sudo install -m 0755 kubectl /usr/local/bin/kubectl && rm -f kubectl
  ok "installed kubectl ${KUBECTL_VER}"
fi

# helm
if command -v helm >/dev/null 2>&1; then ok "helm present"
else curl -fsSL "$HELM_INSTALL_SCRIPT" | bash; ok "installed helm"; fi

# cosign
if command -v cosign >/dev/null 2>&1 && cosign version | grep -q "${COSIGN_VER#v}"; then
  ok "cosign ${COSIGN_VER} present"
else
  curl -fsSLo cosign "https://github.com/sigstore/cosign/releases/download/${COSIGN_VER}/cosign-linux-${ARCH}"
  sudo install -m 0755 cosign /usr/local/bin/cosign && rm -f cosign
  ok "installed cosign ${COSIGN_VER}"
fi

# tkn
if command -v tkn >/dev/null 2>&1 && tkn version | grep -qi "${TKN_VER#v}"; then
  ok "tkn ${TKN_VER} present"
else
  curl -fsSLo tkn.tar.gz "https://github.com/tektoncd/cli/releases/download/${TKN_VER}/tkn_${TKN_VER#v}_Linux_${ARCH}.tar.gz"
  sudo tar -xzf tkn.tar.gz -C /usr/local/bin tkn && rm -f tkn.tar.gz
  ok "installed tkn ${TKN_VER}"
fi

# kyverno CLI
if command -v kyverno >/dev/null 2>&1 && kyverno version | grep -q "${KYVERNOCLI_VER#v}"; then
  ok "kyverno CLI ${KYVERNOCLI_VER} present"
else
  curl -fsSLo kyverno.tar.gz "https://github.com/kyverno/kyverno/releases/download/v${KYVERNOCLI_VER#v}/kyverno-cli_v${KYVERNOCLI_VER#v}_linux_x86_64.tar.gz"
  sudo tar -xzf kyverno.tar.gz -C /usr/local/bin kyverno && rm -f kyverno.tar.gz
  ok "installed kyverno CLI ${KYVERNOCLI_VER}"
fi

# yq
if command -v yq >/dev/null 2>&1 && yq --version | grep -q "${YQ_VER}"; then
  ok "yq ${YQ_VER} present"
else
  sudo wget -qO /usr/local/bin/yq "https://github.com/mikefarah/yq/releases/download/${YQ_VER}/yq_linux_${ARCH}"
  sudo chmod +x /usr/local/bin/yq
  ok "installed yq ${YQ_VER}"
fi

### ---- 2. Cluster reachability & quick info --------------------------------
log "Checking K8s cluster reachability"
kubectl cluster-info >/dev/null || fail "kubectl cannot reach cluster (check kubeconfig)"
kubectl get nodes -o wide
ok "cluster reachable"

### ---- 3. Egress probes -----------------------------------------------------
log "Probing outbound access for critical endpoints"
for u in "${PROBE_URLS[@]}"; do
  if curl -fsS --connect-timeout 5 -o /dev/null "$u"; then ok "egress ok: $u"; else warn "egress blocked: $u"; fi
done

### ---- 4. Install cert-manager & Ingress NGINX ------------------------------
log "Installing cert-manager & ingress-nginx (idempotent)"
helm repo add jetstack https://charts.jetstack.io >/dev/null 2>&1 || true
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx >/dev/null 2>&1 || true
helm repo update >/dev/null 2>&1 || true

ensure_helm_release "cert-manager" "jetstack/cert-manager" "cert-manager" --set installCRDs=true
ensure_helm_release "ingress-nginx" "ingress-nginx/ingress-nginx" "ingress-nginx"

# ClusterIssuer (self-signed by default)
log "Ensuring ClusterIssuer (${TLS_MODE})"
if [ "$TLS_MODE" = "selfsigned" ]; then
cat <<'YAML' | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata: { name: selfsigned-issuer }
spec:
  selfSigned: {}
---
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata: { name: kb-ca-issuer }
spec:
  ca:
    secretName: kb-ca-keypair
---
apiVersion: v1
kind: Secret
metadata: { name: kb-ca-keypair, namespace: cert-manager }
type: kubernetes.io/tls
data:
  tls.crt: ""
  tls.key: ""
YAML
ok "ClusterIssuers applied (selfsigned). You can replace kb-ca-keypair later."
else
cat <<YAML | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata: { name: letsencrypt-prod }
spec:
  acme:
    email: ${LE_EMAIL}
    server: https://acme-v02.api.letsencrypt.org/directory
    privateKeySecretRef: { name: le-account-key }
    solvers:
      - http01:
          ingress:
            class: nginx
YAML
ok "ClusterIssuer letsencrypt-prod applied"
fi

### ---- 5. Namespaces --------------------------------------------------------
log "Creating namespaces for KingBrain"
for ns in "${NS_LIST[@]}"; do ensure_ns "$ns"; done

### ---- 6. StorageClass sanity ----------------------------------------------
log "Checking StorageClasses (fast RWO / shared RWX)"
kubectl get sc || true
DEFAULT_SC="$(kubectl get sc -o jsonpath='{.items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class=="true")].metadata.name}' || true)"
[ -n "${DEFAULT_SC}" ] && ok "default StorageClass: ${DEFAULT_SC}" || warn "no default StorageClass detected"
warn "If you lack fast RWO or shared RWX classes, install your provider (e.g., local-path-provisioner / Longhorn / NFS-subdir)."

### ---- 7. Final report ------------------------------------------------------
log "Summary"
kubectl get pods -A --no-headers | awk '{c[$4]++} END{for (k in c) printf "  %s: %d\n", k, c[k]}'
ok "Preflight done. You can now hand over to AI pipeline (Phase 0 → make validate → PR)."

exit 0
