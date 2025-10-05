cd cluster/secrets

sops --encrypt --in-place godaddy_secret.yaml
sops --encrypt --in-place root_credentials.yaml
sops --encrypt --in-place loki_minio_user.yaml
sops --encrypt --in-place mimir_minio_user.yaml
