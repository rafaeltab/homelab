cd cluster/homelab

sops --encrypt --in-place workloads/cert_manager/godaddy_secret.yaml
sops --encrypt --in-place workloads/minio/root_credentials.yaml
