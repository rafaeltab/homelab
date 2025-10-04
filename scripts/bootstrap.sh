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

