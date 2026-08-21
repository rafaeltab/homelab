# ExternalDNS fit assessment for the homelab

_Last verified: 2026-08-20_

## Decision summary

ExternalDNS is a very good architectural fit for this repository's Flux, Traefik, SOPS, and cert-manager setup. The existing Ingress status already contains the exact address that the current public DNS records use, so ExternalDNS could derive all four application records without duplicating `192.168.2.46` in Git.

The direct GoDaddy provider is only a **short-term fit**, however. ExternalDNS currently authenticates to GoDaddy with classic `sso-key <key>:<secret>` credentials. GoDaddy marks those credentials deprecated and says they are supported only through 2026; GoDaddy's replacement is a scoped Personal Access Token (PAT), which the current ExternalDNS GoDaddy provider does not support.

Recommended direction:

1. **Long term:** keep GoDaddy as registrar if desired, but move authoritative DNS to Cloudflare. ExternalDNS supports scoped Cloudflare API tokens, and cert-manager has a built-in Cloudflare DNS01 solver. This removes the current third-party GoDaddy webhook and the shared classic-key retirement risk.
2. **Short term / learning exercise:** a tightly scoped ExternalDNS-to-GoDaddy deployment is workable. Start with one opt-in canary, dry-run, `create-only`, TXT ownership, and no automatic deletions.

Do not roll the direct GoDaddy integration out unattended without first resolving the classic-key/PAT lifecycle.

## Existing homelab fit

### Repository architecture

The repository already has the pieces ExternalDNS needs:

- Flux reconciles namespaces, Helm repositories, encrypted secrets, and workloads through ordered `Kustomization` resources.
- Secrets are encrypted with SOPS and decrypted by Flux.
- k3s provides Traefik and writes Traefik's reachable address into Ingress status.
- cert-manager already performs DNS01 challenges against GoDaddy through `snowdrop/godaddy-webhook`.
- The authoritative nameservers for `rafaeltab.com` are currently GoDaddy's `ns71.domaincontrol.com` and `ns72.domaincontrol.com`.

Relevant repository locations:

- `cluster/flux/kustomizations/`
- `cluster/helm_repositories/`
- `cluster/namespaces/`
- `cluster/secrets/`
- `cluster/workloads/`
- `cluster/workloads/godaddy_webhook/godaddy_webhook.yaml`
- `cluster/workloads/certificates/issuer.yaml`
- `cluster/secrets/godaddy_secret.yaml`

### Live cluster and DNS state

The live `homelab1` context was checked during this research:

- Single k3s node: `homelab1`, Kubernetes `v1.36.3+k3s1`
- Node and Traefik `LoadBalancer` address: `192.168.2.46`
- No ExternalDNS deployment currently exists.
- No ExternalDNS annotations currently exist in the repository.
- The `letsencrypt-prod` ClusterIssuer is ready.
- All four application certificates are ready and were recently issued or renewed.

The current Ingress status and public A records agree exactly:

| Ingress hostname | Ingress address | Current public DNS |
| --- | --- | --- |
| `grafana.homelab1.local.rafaeltab.com` | `192.168.2.46` | `192.168.2.46` |
| `otlp.homelab1.local.rafaeltab.com` | `192.168.2.46` | `192.168.2.46` |
| `minio.network.rafaeltab.com` | `192.168.2.46` | `192.168.2.46` |
| `registry.network.rafaeltab.com` | `192.168.2.46` | `192.168.2.46` |

ExternalDNS's Ingress source reads names from Ingress rules/TLS hosts and targets from `status.loadBalancer.ingress`. It does not discard RFC1918 targets, so these Ingresses naturally produce the same A records that exist today.

Publishing `192.168.2.46` through public authoritative DNS is technically valid and is already the homelab's behavior. Clients still need LAN/VPN routing to reach it. This is not split-horizon DNS and publicly exposes the internal names and address.

## Credential fit

The existing cert-manager integration and ExternalDNS do not consume credentials in the same shape:

- The deployed cert-manager webhook references one Secret key named `token` in the `cert-manager` namespace.
- ExternalDNS requires two independent inputs: `--godaddy-api-key` and `--godaddy-api-secret`.
- Kubernetes cannot reference a Secret across namespaces.

The live Secret was inspected without printing its value. Its one-key shape does not directly satisfy ExternalDNS's two required inputs. Do not pass the combined/opaque value to either ExternalDNS flag and do not add a wrapper that tries to split it at pod startup.

For a direct GoDaddy rollout, create a separate SOPS-encrypted Secret in an `external-dns` namespace:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: godaddy-external-dns
  namespace: external-dns
type: Opaque
stringData:
  api-key: ENC[...]
  api-secret: ENC[...]
```

Inject those keys through chart `env` entries and reference `$(GODADDY_API_KEY)` / `$(GODADDY_API_SECRET)` from `extraArgs`. Do not commit literal credentials in Helm values.

### Important authentication risk

As of this review:

- GoDaddy recommends PAT Bearer authentication.
- Classic developer keys are deprecated and documented as supported only through 2026.
- ExternalDNS v0.22.0 still sends `Authorization: sso-key <key>:<secret>` to GoDaddy's v1 API.
- The current Snowdrop cert-manager webhook also uses classic `sso-key` authentication.

Therefore both DNS record automation and certificate renewal have the same upcoming provider-authentication risk. A GoDaddy PAT cannot simply be placed in either ExternalDNS classic-key flag.

Before implementing the direct provider, verify that the account can still create/use production classic credentials. Use a disposable record and inspect the response body on any `403`; GoDaddy notes that account eligibility can reject otherwise valid credentials.

## Proposed short-term Flux layout

A direct GoDaddy experiment would follow the repository's existing structure:

```text
cluster/
├── namespaces/external-dns.yaml
├── helm_repositories/external_dns.yaml
├── secrets/external_dns_godaddy.yaml
├── workloads/external_dns/external_dns_release.yaml
└── flux/kustomizations/003_external_dns.yaml
```

Also update the existing Kustomization resource lists. The Flux ExternalDNS Kustomization should depend on:

- `namespaces`
- `helm-repositories`
- `secrets`

The official chart repository is:

```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: HelmRepository
metadata:
  name: external-dns
  namespace: flux-system
spec:
  interval: 1h
  url: https://kubernetes-sigs.github.io/external-dns/
```

At the time of research, the chart index's newest chart was `1.21.1`, packaging ExternalDNS `0.21.0`, while ExternalDNS `v0.22.0` had been released the same day. Pin a chart/app combination deliberately; do not casually override the image to a newer same-day release. Version `v0.22.0` also changes the default annotation prefix and requires an explicit policy, so its release notes must be treated as migration instructions.

## Safe initial configuration

The exact values must be rendered against the chart version selected during implementation. The intended configuration is:

```yaml
provider:
  name: godaddy

sources:
  - ingress

# Start safe. Do not enable sync during adoption.
policy: create-only
registry: txt
txtOwnerId: homelab1
txtPrefix: "external-dns-%{record_type}."

domainFilters:
  - rafaeltab.com

dryRun: true

extraArgs:
  ingress-class: traefik
  godaddy-api-key: "$(GODADDY_API_KEY)"
  godaddy-api-secret: "$(GODADDY_API_SECRET)"

env:
  - name: GODADDY_API_KEY
    valueFrom:
      secretKeyRef:
        name: godaddy-external-dns
        key: api-key
  - name: GODADDY_API_SECRET
    valueFrom:
      secretKeyRef:
        name: godaddy-external-dns
        key: api-secret
```

Additional safeguards:

- Add an arbitrary opt-in annotation to selected Ingresses and configure ExternalDNS's annotation filter during the canary phase. Otherwise the Ingress source will discover all matching Ingress hosts automatically.
- Keep the source list to `ingress` initially. Do not enable `service` or `crd` until there is a specific use case.
- Keep `domainFilters` restricted to `rafaeltab.com`.
- Give every cluster a unique, stable TXT owner ID.
- Keep the TXT prefix stable after rollout.
- GoDaddy's ExternalDNS provider enforces a minimum TTL of 600 seconds.

## Existing-record adoption

The four desired A records already exist and currently have no ExternalDNS ownership metadata. TXT ownership prevents one ExternalDNS instance from overwriting records owned by another instance, but an existing unowned A record should not be assumed to become safely adopted just because its value already matches desired state.

Use this migration sequence:

1. Export or screenshot all current GoDaddy records.
2. Verify the classic credential with one disposable record outside ExternalDNS.
3. Deploy ExternalDNS with one opt-in canary, `dryRun: true`, `policy: create-only`, and TXT registry enabled.
4. Inspect the plan and confirm that no unrelated `rafaeltab.com` records appear.
5. Disable dry-run for the canary only.
6. Confirm against GoDaddy's authoritative nameserver that the canary A record and expected ownership TXT record exist.
7. Add one existing hostname at a time.
8. If a matching pre-existing record is not given ownership metadata, either:
   - remove/recreate that one record under controlled conditions so ExternalDNS creates it with ownership; or
   - seed ownership TXT only from output generated and verified against the deployed version. Never hand-guess TXT ownership content.
9. Move from `create-only` to `upsert-only` after all managed records have verified ownership.
10. Keep `sync` disabled unless automatic deletion is explicitly desired and a separate dry-run proves the deletion set is safe.

`upsert-only` is a good steady-state policy for this homelab: applications can update records, but deleting an Ingress cannot unexpectedly remove DNS during an outage or Flux mistake.

## Preferred long-term design: Cloudflare authoritative DNS

GoDaddy can remain the registrar while the domain's authoritative nameservers point to Cloudflare. That would produce a cleaner Kubernetes design:

```text
Flux + SOPS
   ├── cert-manager built-in Cloudflare DNS01 solver
   └── ExternalDNS Cloudflare provider
             ↓
      Cloudflare authoritative DNS
```

Advantages over the current/direct-GoDaddy path:

- Scoped API tokens rather than deprecated classic account keys
- First-party ExternalDNS provider support
- Built-in cert-manager Cloudflare solver
- Removal of the third-party `snowdrop/godaddy-webhook` deployment, Helm repository, webhook certificates, and custom solver
- One scoped DNS token can be managed through the existing SOPS workflow, or separate least-privilege tokens can be used for cert-manager and ExternalDNS

This is a separate migration and must preserve every existing DNS record before changing nameservers. Keep records unproxied (`DNS only`) for private RFC1918 targets; Cloudflare's HTTP proxy cannot route to `192.168.2.46`.

## Implementation checkpoints

If the short-term GoDaddy experiment is implemented, verification should include:

```bash
kubectl kustomize cluster
helm template external-dns external-dns/external-dns \
  --version <pinned-chart-version> \
  --namespace external-dns \
  -f <rendered-values>
kubectl -n external-dns get deploy,pods
kubectl -n external-dns logs deploy/external-dns
dig @ns71.domaincontrol.com A <canary-hostname>
dig @ns71.domaincontrol.com TXT <generated-ownership-name>
```

Success means:

- Flux reports the ExternalDNS Kustomization and HelmRelease ready.
- The rendered Deployment contains Secret references, not literal credentials.
- Dry-run lists only explicitly intended records.
- The canary A record resolves directly from GoDaddy's authoritative server.
- The corresponding ownership TXT record contains the expected owner ID.
- Existing application records and certificates remain unchanged.

## Primary sources

### Kubernetes and ExternalDNS

- Kubernetes DNS for Services and Pods: https://kubernetes.io/docs/concepts/services-networking/dns-pod-service/
- ExternalDNS providers: https://kubernetes-sigs.github.io/external-dns/latest/docs/providers/
- ExternalDNS GoDaddy tutorial: https://kubernetes-sigs.github.io/external-dns/latest/docs/tutorials/godaddy/
- ExternalDNS Ingress source: https://kubernetes-sigs.github.io/external-dns/latest/docs/sources/ingress/
- ExternalDNS CRD source: https://kubernetes-sigs.github.io/external-dns/latest/docs/sources/crd/
- ExternalDNS TXT registry: https://kubernetes-sigs.github.io/external-dns/latest/docs/registry/txt/
- ExternalDNS v0.22.0 release notes: https://github.com/kubernetes-sigs/external-dns/releases/tag/v0.22.0
- ExternalDNS GoDaddy client implementation: https://github.com/kubernetes-sigs/external-dns/blob/v0.22.0/provider/godaddy/client.go
- ExternalDNS chart values: https://github.com/kubernetes-sigs/external-dns/blob/v0.22.0/charts/external-dns/values.yaml

### GoDaddy

- GoDaddy authentication and credential status: https://developer.godaddy.com/en/docs/api-users/auth
- GoDaddy credential setup: https://developer.godaddy.com/en/docs/api-users/auth/how-to
- GoDaddy DNS troubleshooting: https://developer.godaddy.com/en/docs/api-users/troubleshoot/dns

### Cloudflare alternative

- ExternalDNS Cloudflare tutorial: https://kubernetes-sigs.github.io/external-dns/latest/docs/tutorials/cloudflare/
- cert-manager Cloudflare DNS01 solver: https://cert-manager.io/docs/configuration/acme/dns01/cloudflare/
