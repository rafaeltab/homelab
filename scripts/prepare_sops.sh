move_to_repo_root() {
  # Move to the repository root or exit with an error
  if ! repo_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
    echo "Error: not inside a Git repository." >&2
    exit 1
  fi
  cd "$repo_root"
}

get_gpg_key_fp_from_name() {
  local key_name="$1"

  gpg --with-colons --list-secret-keys --fingerprint "${key_name}" \
      | awk -F: 'BEGIN{want=0} $1=="sec"{want=1} want && $1=="fpr"{print $10; exit}'
}

create_kubectl_secret() {
  local fp="$1"
  local secret_name="$2"
  local namespace="$3"

   gpg --export-secret-keys --armor "${fp}" | \
   kubectl create secret generic "${secret_name}" \
   --namespace="${namespace}" \
   --from-file=sops.asc=/dev/stdin
}

move_to_repo_root

gpg_key_exists=0

GPG_KEY_NAME="gpg.homelab1.network.rafaeltab.com"
GPG_COMMENT="flux secrets"

CHECKMARK="✅"
CROSS="❌"

GPG_KEY_FP=$(get_gpg_key_fp_from_name $GPG_KEY_NAME)
if ! [[ -z "${GPG_KEY_FP}" ]]; then
  echo "$CHECKMARK GPG key exists"
  gpg_key_exists=1
fi

if !(( gpg_key_exists )); then
  echo "$CROSS GPG key not found"
    gum spin --title="Creating GPG key..." -- gpg --batch --full-generate-key <<eof
    %no-protection
    key-type: 1
    key-length: 4096
    subkey-type: 1
    subkey-length: 4096
    expire-date: 0
    name-comment: ${GPG_COMMENT}
    name-real: ${GPG_KEY_NAME}
eof


  GPG_KEY_FP=$(get_gpg_key_fp_from_name $GPG_KEY_NAME)
  if ![[ -z "${GPG_KEY_FP}" ]]; then
    echo "$CHECKMARK GPG key created"
    gpg_key_exists=1
  fi
fi

if !((gpg_key_exists)); then
  echo "$CROSS Something went wrong"
  exit 1
fi

KUBECTL_SECRET_NAME="sops-gpg"
KUBECTL_SECRET_NAMESPACE="flux-system"

namespace_exists=0
secret_exists=0

if kubectl get namespace $KUBECTL_SECRET_NAMESPACE >/dev/null 2>&1; then
  echo "$CHECKMARK k8s '$KUBECTL_SECRET_NAMESPACE' namespace exists"
  namespace_exists=1
fi

if !((namespace_exists)); then
  echo "$CROSS k8s '$KUBECTL_SECRET_NAMESPACE' namespace does not exists"
  gum spin --title="Creating k8s '$KUBECTL_SECRET_NAMESPACE' namespace..." -- kubectl create namespace $KUBECTL_SECRET_NAMESPACE 
  echo "$CHECKMARK k8s '$KUBECTL_SECRET_NAMESPACE' namespace created"
  namespace_exists=1
fi

if kubectl get secret "$KUBECTL_SECRET_NAME" -n "$KUBECTL_SECRET_NAMESPACE" >/dev/null 2>&1; then
  secret_exists=1
  echo "$CHECKMARK k8s '$KUBECTL_SECRET_NAME' secret exists"
fi

if !((secret_exists)); then
  export -f create_kubectl_secret
  gum spin --title="Creating k8s '$KUBECTL_SECRET_NAME' secret..." -- bash -lc "create_kubectl_secret $GPG_KEY_FP $KUBECTL_SECRET_NAME $KUBECTL_SECRET_NAMESPACE"
  echo "$CHECKMARK k8s '$KUBECTL_SECRET_NAME' secret created"
fi
