#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

for command in helm kubectl python3; do
  command -v "$command" >/dev/null || { echo "missing required command: $command" >&2; exit 1; }
done

scripts/prepare_grafana_dashboards.py --check

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
export HELM_CONFIG_HOME="$tmp/helm/config"
export HELM_CACHE_HOME="$tmp/helm/cache"
export HELM_DATA_HOME="$tmp/helm/data"
helm repo add grafana https://grafana.github.io/helm-charts >/dev/null
helm repo add minio-operator https://operator.min.io >/dev/null
helm repo update >/dev/null

render() {
  local release=$1 file=$2 chart=$3 version=$4 namespace=$5
  python3 - "$file" "$tmp/$release-values.yaml" <<'PY'
import sys
from pathlib import Path
import yaml
source = yaml.safe_load(Path(sys.argv[1]).read_text())
Path(sys.argv[2]).write_text(yaml.safe_dump(source["spec"]["values"], sort_keys=False))
PY
  helm template "$release" "$chart" --version "$version" --namespace "$namespace" \
    -f "$tmp/$release-values.yaml" >"$tmp/$release.yaml"
}

render grafana cluster/workloads/grafana/grafana_release.yaml grafana/grafana 9.2.0 observability
render loki cluster/workloads/loki/loki_release.yaml grafana/loki 6.41.1 observability
render mimir cluster/workloads/mimir/mimir_release.yaml grafana/mimir-distributed 5.8.0 observability
render tempo cluster/workloads/tempo/tempo_release.yaml grafana/tempo-distributed 1.48.0 observability
render minio cluster/workloads/minio/tenant_helm.yaml minio-operator/tenant 7.1.1 minio
kubectl kustomize cluster/workloads/grafana >"$tmp/grafana-gitops.yaml"

python3 - "$tmp" <<'PY'
import json
import sys
from pathlib import Path
import yaml

tmp = Path(sys.argv[1])

def documents(name):
    return [doc for doc in yaml.safe_load_all((tmp / name).read_text()) if doc]

def text(name):
    return (tmp / name).read_text()

# Parsing every document proves the Helm and Kustomize output is valid YAML.
counts = {name: len(documents(name)) for name in (
    "grafana.yaml", "loki.yaml", "mimir.yaml", "tempo.yaml", "minio.yaml", "grafana-gitops.yaml"
)}
assert "reject_old_samples_max_age: 360h" in text("loki.yaml")
assert "reject_old_samples: true" in text("loki.yaml")
assert "retention_period: 43800h" in text("loki.yaml")
assert "retention_enabled: true" in text("loki.yaml")
assert "delete_request_store: s3" in text("loki.yaml")
assert "past_grace_period: 744h" in text("mimir.yaml")
assert "out_of_order_time_window: 744h" in text("mimir.yaml")
assert "compactor_blocks_retention_period: 43800h" in text("mimir.yaml")
assert "block_retention: 43800h" in text("tempo.yaml")
assert "storage: 100Gi" in text("minio.yaml")
assert "folderUid: hermes" in text("grafana.yaml")
assert "folder: Hermes" in text("grafana.yaml")
assert "grafana-dashboards-kubernetes" in text("grafana.yaml")
assert "kubernetes-views-pods" not in text("grafana.yaml")
assert "grafana-dashboards-default" not in text("grafana.yaml")
assert "dashboards/kubernetes" in text("grafana.yaml")
assert "dashboards/general" in text("grafana.yaml")
assert "dashboards/hermes" in text("grafana.yaml")

grafana_deployments = [d for d in documents("grafana.yaml") if d["kind"] == "Deployment"]
assert len(grafana_deployments) == 1
assert grafana_deployments[0]["spec"]["strategy"] == {"type": "Recreate"}

rendered = documents("grafana-gitops.yaml")
configmaps = {d["metadata"]["name"]: d for d in rendered if d["kind"] == "ConfigMap"}
assert set(configmaps) == {
    "grafana-dashboards-kubernetes", "grafana-dashboards-general", "grafana-dashboards-hermes"
}
assert set(configmaps["grafana-dashboards-kubernetes"]["data"]) == {"k8s_views_pods.json"}
assert set(configmaps["grafana-dashboards-general"]["data"]) == {"adrk6nd.json"}
assert set(configmaps["grafana-dashboards-hermes"]["data"]) == {
    "hermes-usage.json", "hermes-reliability.json"
}
for cm in configmaps.values():
    for value in cm["data"].values():
        json.loads(value)
print("rendered YAML documents:", counts)
print("retention/backfill/storage fields, Grafana Recreate strategy, and dashboard ConfigMaps: OK")
PY
