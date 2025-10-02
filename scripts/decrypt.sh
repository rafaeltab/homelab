cd cluster/homelab

sops --decrypt --in-place workloads/cert_manager/godaddy_secret.yaml
sops --decrypt --in-place workloads/minio/root_credentials.yaml
