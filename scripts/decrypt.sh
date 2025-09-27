cd cluster

sops --decrypt --in-place cert_manager/godaddy_secret.yaml
