# Login to github
GITHUB_USER="$(gh api user --jq .login)"
GITHUB_TOKEN="$(gh auth token)"

export GITHUB_USER GITHUB_TOKEN

echo "Bootstrapping flux..."
flux bootstrap github \
    --token-auth \
    --owner=$GITHUB_USER \
    --repository=homelab \
    --branch=main \
    --path=cluster/flux \
    --personal \
    --components source-controller,kustomize-controller,helm-controller

GPG_KEY_NAME="gpg.homelab1.network.rafaeltab.com"
GPG_COMMENT="flux secrets"

get_gpg_key_fp() {
    gpg --with-colons --list-secret-keys --fingerprint "${GPG_KEY_NAME}" \
        | awk -F: 'BEGIN{want=0} $1=="sec"{want=1} want && $1=="fpr"{print $10; exit}'
}

echo "Trying to find key..."
GPG_KEY_FP=$(get_gpg_key_fp)


if [[ -z "${GPG_KEY_FP}" ]]; then
    echo "Generating new key..."
    gpg --batch --full-generate-key <<EOF
    %no-protection
    Key-Type: 1
    Key-Length: 4096
    Subkey-Type: 1
    Subkey-Length: 4096
    Expire-Date: 0
    Name-Comment: ${GPG_COMMENT}
    Name-Real: ${GPG_KEY_NAME}
EOF

    GPG_KEY_FP=$(get_gpg_key_fp)

    echo "Creating secret..."
    gpg --export-secret-keys --armor "${GPG_KEY_FP}" |
    kubectl create secret generic sops-gpg \
    --namespace=flux-system \
    --from-file=sops.asc=/dev/stdin
fi
