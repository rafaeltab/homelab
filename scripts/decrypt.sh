cd cluster/secrets

sops --decrypt --in-place godaddy_secret.yaml
sops --decrypt --in-place root_credentials.yaml
sops --decrypt --in-place loki_minio_user.yaml
