# Obsidian Self-hosted LiveSync implementation design and threat model

_Last verified: 2026-08-22 UTC_

## Decision summary

Deploy **Apache CouchDB behind the existing Traefik HTTPS ingress** as the only server-side component required by Obsidian Self-hosted LiveSync. Do not deploy LiveSync CLI or LiveSync Bridge in the initial architecture: neither is the synchronization server.

Use these immutable pins:

| Component | Supported pin | Immutable source/image | Compatibility basis |
| --- | --- | --- | --- |
| Obsidian Self-hosted LiveSync plugin | `1.0.16` | commit [`693cd77576b7c30df36b5c0c8f7a564fe62e455a`](https://github.com/vrtmrz/obsidian-livesync/commit/693cd77576b7c30df36b5c0c8f7a564fe62e455a) | Release manifest requires Obsidian `1.7.2`; plugin and CLI are co-tagged from the same commit and use LiveSync Commonlib `0.1.17`. |
| CouchDB | upstream `3.5.2`, official-image packaging `3.5.2.1` | `docker.io/library/couchdb:3.5.2.1@sha256:b80216f643e99d31df318c740dbc556ac08b56444030ed1d5e6d7b0d4e625213` | Multi-platform OCI index; the current node is `linux/amd64`, whose child manifest is `sha256:b7a129a4ce4da47aa56ed2b67c8c16eafc58252e83c1688b5aa069f03e60cf80`. |
| LiveSync CLI, only if a headless filesystem mirror is later required | `1.0.16-cli` | commit `693cd77576b7c30df36b5c0c8f7a564fe62e455a`; published image index `ghcr.io/vrtmrz/livesync-cli:1.0.16-cli@sha256:4d9ee24269277523ce7b05e964e1968bb1dacba0156b9034f3d227776a1259c6` | Same commit/common library as plugin `1.0.16`, plus upstream CLI-to-Obsidian E2E coverage. |
| LiveSync Bridge, not selected | no release tag | current reviewed commit [`3a32278899f325ee1fad0ac3ba9768e8d24a9f74`](https://github.com/vrtmrz/livesync-bridge/commit/3a32278899f325ee1fad0ac3ba9768e8d24a9f74) | Uses Commonlib `0.1.17`, but is a specialized multi-peer replicator and has no immutable upstream release. |

Expose the existing Traefik address through Tailscale with a **narrow subnet route for `192.168.2.46/32`**. Preserve the same URL on LAN and tailnet:

```text
https://livesync.homelab1.local.rafaeltab.com
```

Do not install the Tailscale Kubernetes Operator for this service. Do not enable Funnel.

**No-public-exposure assertion:** the design permits access only from the private LAN and explicitly authorized tailnet identities. It creates no public `LoadBalancer`, `NodePort`, WAN port-forward, public reverse proxy/CDN path, or Tailscale Funnel. Public DNS may disclose the private RFC1918 address, as this homelab already does, but it does not provide Internet routing to the service.

## Reconciliation: CLI versus Bridge

The earlier disagreement came from treating two filesystem-facing clients as candidates for the CouchDB server role.

- The plugin's documented remote is CouchDB (or the separately supported object-storage path). CouchDB implements the HTTP replication endpoint.
- The in-repository **LiveSync CLI** is a headless client. Its daemon synchronizes remote CouchDB to a local PouchDB and mirrors that local database to a filesystem. Its README lists an HTTP `serve` operation only as planned; the CLI does not replace CouchDB.
- **LiveSync Bridge** is a custom multidirectional replicator between CouchDB peers and/or filesystem storage peers, with peer groups, path remapping, and independent encryption/obfuscation settings. CouchDB endpoints are inputs to Bridge; Bridge is not CouchDB.

Therefore:

1. Initial deployment: plugin `1.0.16` plus CouchDB only.
2. Later headless single-vault mirror: prefer CLI `1.0.16-cli`, because it is released from the plugin's exact commit and covered by upstream CLI-to-Obsidian tests.
3. Use Bridge only for an explicit multi-vault, multi-storage, or path-remapping requirement. If approved, pin its exact commit rather than mutable `main`.

Primary evidence:

- [Self-hosted LiveSync `1.0.16` release](https://github.com/vrtmrz/obsidian-livesync/releases/tag/1.0.16)
- [Plugin manifest at the release commit](https://github.com/vrtmrz/obsidian-livesync/blob/693cd77576b7c30df36b5c0c8f7a564fe62e455a/manifest.json)
- [CLI README at the release commit](https://github.com/vrtmrz/obsidian-livesync/blob/693cd77576b7c30df36b5c0c8f7a564fe62e455a/src/apps/cli/README.md)
- [CLI-to-Obsidian E2E scenario](https://github.com/vrtmrz/obsidian-livesync/blob/693cd77576b7c30df36b5c0c8f7a564fe62e455a/test/e2e-obsidian/scripts/cli-to-obsidian-sync.ts)
- [CLI image build and E2E workflow](https://github.com/vrtmrz/obsidian-livesync/blob/693cd77576b7c30df36b5c0c8f7a564fe62e455a/.github/workflows/cli-docker.yml)
- [Bridge README at the reviewed commit](https://github.com/vrtmrz/livesync-bridge/blob/3a32278899f325ee1fad0ac3ba9768e8d24a9f74/readme.md)
- [Bridge Commonlib pin](https://github.com/vrtmrz/livesync-bridge/blob/3a32278899f325ee1fad0ac3ba9768e8d24a9f74/deno.jsonc)

## Repository and rollout precondition

The local homelab checkout is one commit ahead of `origin/main`: local `b04c99cc36a6c755d0f865351b3f8db67c68f2a1` versus remote `791beab5e2071ce820665cf140ed4d90127563eb`. Flux tracks `origin/main`, so local state is not live desired state.

Before implementation:

1. Review and push the existing local commit independently.
2. Fetch and require `git merge-base --is-ancestor origin/main HEAD`.
3. Build LiveSync as a separate reviewed commit on top of the reconciled branch.
4. Render and validate locally before pushing.
5. Confirm Flux applies the exact reviewed commit; do not infer success from the unpushed working tree.

## Target Kubernetes layout

Use a dedicated `livesync` namespace and follow this repository's Flux ordering:

```text
cluster/
├── namespaces/livesync.yaml
├── secrets/livesync_couchdb.yaml               # SOPS encrypted
├── workloads/livesync/
│   ├── couchdb.yaml                             # ConfigMap, PVC, StatefulSet, Service
│   ├── ingress.yaml                             # Traefik HTTPS only
│   ├── bootstrap-controller.yaml                # idempotent credential-aware provisioning
│   ├── network-policies.yaml
│   └── kustomization.yaml
├── workloads/certificates/livesync_homelab1_local_rafaeltab_com.yaml
└── flux/kustomizations/00N_livesync.yaml
```

The Flux Kustomization depends on namespaces, secrets, OpenEBS/storage, and certificates. Set `prune: true`, health checks for the StatefulSet and bootstrap controller Deployment, and a bounded reconciliation timeout.

### Workload

- One-replica `StatefulSet`, because this is a single-node CouchDB deployment with one persistent identity.
- Use `updateStrategy: RollingUpdate`; the image remains pinned by tag and digest, so declarative pod-template fixes roll out automatically without introducing tag drift.
- Mount the administrator Secret as a projected volume in addition to the entrypoint environment. A small PID 1 wrapper terminates CouchDB when that projection changes, causing Kubernetes to restart it with refreshed environment values.
- `podManagementPolicy: OrderedReady`.
- Container image by tag **and** OCI index digest shown above; `imagePullPolicy: IfNotPresent` is safe because the digest is immutable.
- Run with the official image's non-root `couchdb` identity. Set `runAsNonRoot`, drop all capabilities, disable privilege escalation, use the RuntimeDefault seccomp profile, and make the root filesystem read-only only after verifying the image's required writable paths are separately mounted.
- Persistent mount at `/opt/couchdb/data`.
- Writable `emptyDir` mounts for runtime paths that the image needs outside the data PVC.
- `Recreate` semantics are implicit for a one-replica StatefulSet on `ReadWriteOnce` storage.
- Requests start at `100m` CPU and `256Mi` memory; limits start at `1` CPU and `1Gi` memory. Adjust from observed compaction and replication behavior rather than removing limits pre-emptively.
- Authenticated readiness/liveness checks against `/_up`. The probe reads the admin credentials from the mounted Kubernetes Secret and does not log them. Do not enable anonymous `require_valid_user_except_for_up` unless an authenticated probe proves impossible.

### Persistent storage

- `20Gi` `ReadWriteOnce` PVC using `zfs-local-persistent`.
- PVC retention is deliberate: deleting or rolling back the workload must not delete the claim.
- Alert at 70% and 85% usage; stop writes and investigate before exhaustion.
- Schedule CouchDB compaction based on observed database and view growth. Compaction is maintenance, not backup.
- Do not introduce NFS or a multi-writer filesystem beneath CouchDB.

## Data flow and trust boundaries

```text
Obsidian vault (plaintext)
  │  plugin 1.0.16: encrypt content + properties on device
  │  HTTPS, authenticated as dedicated database member
  ▼
LAN client ───────────────┐
                          ├─> Traefik :443 ─> ClusterIP :5984 ─> CouchDB PVC
Tailnet client             │       TLS boundary       namespace boundary
  └─ Tailscale tunnel ─> /32 subnet router ┘
                                                │
                                                ├─ local ZFS snapshots
                                                └─ encrypted off-node backup

Flux Git ─> SOPS decryptor ─> Kubernetes Secret ─> CouchDB/bootstrap only
Password manager ─> E2EE passphrase ─> user devices only (never the server)
```

Trust boundaries:

1. **Device boundary:** vault plaintext, local PouchDB, CouchDB member credential, and E2EE passphrase exist on each approved device. A compromised device can read and alter the vault.
2. **Tailnet/LAN boundary:** Tailscale and private routing restrict reachability but do not replace CouchDB authentication or TLS. LAN peers are not implicitly trusted.
3. **Traefik boundary:** Traefik terminates public-CA TLS and forwards HTTP only inside the cluster. It must not log `Authorization`, Setup URIs, or request bodies.
4. **Namespace boundary:** NetworkPolicies permit only Traefik and the dedicated bootstrap controller to reach CouchDB. The Service is `ClusterIP` only.
5. **Cluster-admin boundary:** Kubernetes/Flux/SOPS administrators can obtain CouchDB credentials and manipulate the workload. They cannot decrypt E2EE note contents without the separately held passphrase, but metadata leakage depends on the selected property obfuscation.
6. **Storage/backup boundary:** the PVC contains encrypted LiveSync document content when E2EE is enabled, plus CouchDB operational metadata and credentials/configuration. Backups retain all server-side material and must be encrypted off-node.
7. **Tailscale control-plane boundary:** tailnet administrators can change route approval and grants. Default subnet-router SNAT means Traefik normally sees the router's LAN identity, not the originating user; authorization remains at Tailscale policy plus CouchDB credentials.

## Identities and authorization

| Identity | Scope | Stored in | Prohibited use |
| --- | --- | --- | --- |
| `couchdb-admin` | Server admin, bootstrap, upgrade, restore | SOPS-encrypted namespaced Secret; mounted only into CouchDB and the bootstrap controller | Never in plugin clients, Setup URIs, screenshots, logs, or routine synchronization |
| `livesync` | Named member of exactly one vault database | SOPS Secret for provisioning; distributed to approved clients through a short-lived Setup URI | No `_admin` role, cluster config, database creation/deletion, `_security` changes, or access to other databases |
| Traefik service account | Read routing resources and proxy TCP/HTTP | Existing cluster-managed identity | No CouchDB credential |
| Tailnet user/device | Reach `192.168.2.46:443` only when allowed by policy | Tailscale control plane/device keys | No route to `5984`, SSH, port 80, adjacent LAN hosts, or the full `/24` |
| Flux controllers | Reconcile Git and decrypt SOPS resources | Existing Flux/GPG setup | No E2EE passphrase |
| Device user | Decrypt and edit vault | Device plus password manager | No CouchDB admin credential |

After single-node initialization, create the ordinary `_users` document for `livesync` and set the vault database `_security` object to:

```json
{
  "admins": {"names": [], "roles": []},
  "members": {"names": ["livesync"], "roles": []}
}
```

A normal database member can read/write ordinary documents but cannot change design documents or the database security object. Server admins retain recovery control. See CouchDB's [authentication database](https://docs.couchdb.org/en/stable/intro/security.html#authentication-database) and [`_security` API](https://docs.couchdb.org/en/stable/api/database/security.html).

## CouchDB bootstrap and configuration

Run one idempotent bootstrap controller replica using the pinned plugin repository's provisioning behavior as evidence; do not pipe mutable `main` into a shell. The controller becomes Ready only after a successful reconciliation, periodically repairs database and authorization drift, and watches projected administrator and member credentials. A projection change restarts it with freshly injected environment values, so script/spec updates roll through the Deployment and credential rotations do not depend on a manually versioned Job name.

Required state:

```ini
[couchdb]
single_node = true
max_document_size = 50000000

[chttpd]
require_valid_user = true
enable_cors = true
max_http_request_size = 4294967296

[cors]
credentials = true
origins = app://obsidian.md,capacitor://localhost,http://localhost
methods = GET,PUT,POST,HEAD,DELETE
headers = accept,authorization,content-type,origin,referer
max_age = 3600
```

The exact CORS allowlist supports Obsidian desktop (`app://obsidian.md`), mobile (`capacitor://localhost`), and the upstream localhost client flow (`http://localhost`). Remove a listed origin only after proving the corresponding client class is not required. Never add `*`, the public service hostname, or speculative origins.

CORS is a browser response policy, not a network security control. Configure it once in CouchDB and let Traefik pass `OPTIONS`; do not duplicate CORS headers in Traefik middleware.

Bootstrap sequence:

1. Start CouchDB with the SOPS-managed admin credential.
2. Enable single-node mode, creating system databases.
3. Apply request/document limits, authenticated-only HTTP, and CORS.
4. Create the vault database with the agreed lowercase identifier.
5. Initialize the LiveSync database-version document using reviewed `1.0.16` provisioning logic.
6. Create/update the non-admin `livesync` user.
7. Apply the member-only `_security` object.
8. Run positive and negative authorization tests.
9. Mark the controller Ready, then repeat reconciliation periodically and immediately after projected credentials change, without embedding credentials in logs or annotations.

Primary sources:

- [CouchDB single-node setup](https://docs.couchdb.org/en/stable/setup/single-node.html)
- [`chttpd.require_valid_user`](https://docs.couchdb.org/en/stable/config/auth.html#chttpd-require-valid-user)
- [CouchDB CORS configuration](https://docs.couchdb.org/en/stable/config/http.html#config-cors)
- [LiveSync `1.0.16` server setup guide](https://github.com/vrtmrz/obsidian-livesync/blob/693cd77576b7c30df36b5c0c8f7a564fe62e455a/docs/setup_own_server.md)
- [LiveSync `1.0.16` CouchDB provisioner](https://github.com/vrtmrz/obsidian-livesync/blob/693cd77576b7c30df36b5c0c8f7a564fe62e455a/utils/couchdb/provision.ts)
- [CouchDB 3.5.2 release notes](https://docs.couchdb.org/en/stable/whatsnew/3.5.html#version-3-5-2)
- [Docker Official Image record at reviewed revision](https://github.com/docker-library/official-images/blob/6d7c63e73b123532833c888a41ec79b051a7e1c8/library/couchdb)
- [Official CouchDB image Dockerfile](https://github.com/apache/couchdb-docker/blob/a76020b130d5d2e2a0e54f9437d90d35652b8707/3.5.2.1/Dockerfile)

## TLS, ingress, DNS, and Tailscale

### TLS and ingress

- Create a cert-manager `Certificate` for `livesync.homelab1.local.rafaeltab.com` through the existing DNS01 `letsencrypt-prod` issuer.
- Create only a Traefik `websecure` Ingress, with TLS required and no port-80 router for this hostname.
- Use a `ClusterIP` CouchDB Service on port `5984`; never use `NodePort` or `LoadBalancer` for CouchDB.
- Preserve the URL, Host header, and SNI through the Tailscale path, so the same trusted certificate works on LAN and tailnet.
- Configure Traefik access logs to redact `Authorization`, cookies, Setup URI material, and bodies. Set conservative request/idle timeouts that still pass LiveSync attachment and long-poll `_changes` tests.

### Selected Tailscale method

Install Tailscale on an always-on LAN node and advertise only:

```text
192.168.2.46/32
```

Prefer the k3s host only after proving it can route tailnet traffic to its own LAN address. Otherwise use another always-on LAN node. Do not advertise `192.168.2.0/24`, a default route, or exit-node routes.

Authorize the subnet router under a dedicated tag such as `tag:homelab-subnet-router`. Route approval and data-plane grants are separate controls. Tailnet policy must grant the intended user/device group only TCP `443` to `192.168.2.46`, with policy tests for both allow and deny cases. Tailscale subnet routers use SNAT by default; keep it enabled unless the LAN has an explicit return route and there is a tested need for original source addresses.

If the existing authoritative DNS record resolves the FQDN to `192.168.2.46`, tailnet clients can use it unchanged. Otherwise configure restricted split DNS for `homelab1.local.rafaeltab.com`. MagicDNS does not synthesize this custom application name.

The Kubernetes Operator is not selected because it adds CRDs, a controller, OAuth credentials, per-service proxy resources, a second ingress path, and a `.ts.net` identity without removing the existing Traefik path. Revisit it only as a separate architecture decision.

Primary sources:

- [Tailscale subnet routers](https://tailscale.com/docs/features/subnet-routers)
- [Subnet-router setup](https://tailscale.com/docs/features/subnet-routers/how-to/setup)
- [Tailnet policy syntax, including `autoApprovers`](https://tailscale.com/docs/reference/syntax/policy-file)
- [Tailscale grants](https://tailscale.com/docs/features/access-control/grants)
- [DNS in Tailscale](https://tailscale.com/docs/reference/dns-in-tailscale)
- [Kubernetes Operator ingress, rejected alternative](https://tailscale.com/docs/kubernetes-operator/ingress)
- [Tailscale Funnel is Internet-public and excluded](https://tailscale.com/docs/features/tailscale-funnel)

## NetworkPolicies

Apply a namespace-wide default deny for both ingress and egress, then add only:

1. Ingress to CouchDB TCP `5984` from the actual Traefik pods in `kube-system`.
2. Ingress to CouchDB TCP `5984` from the labeled bootstrap controller.
3. DNS egress TCP/UDP `53` to the actual kube-dns/CoreDNS pods for pods that resolve the CouchDB Service.
4. Bootstrap egress to CouchDB TCP `5984`.

The CouchDB pod itself requires no general Internet egress. Backups should run in a separately labeled job with a destination-specific policy; do not open namespace-wide HTTPS egress.

Do not guess k3s labels. Before committing selectors, inspect the live labels for Traefik and CoreDNS and render policies using those exact stable labels. Acceptance includes an enforcement test from both allowed and unselected pods; the mere presence of `NetworkPolicy` objects is insufficient.

## Client security and first-sync invariants

Before the first real vault connects:

- Install plugin `1.0.16` on Obsidian `>=1.7.2` on every client.
- Enable LiveSync end-to-end encryption with one high-entropy passphrase held in the password manager and a separate recovery copy.
- Enable **Path Obfuscation / Obfuscate Properties** before first sync. Upstream documents that E2EE alone encrypts content but not all metadata; obfuscation conceals paths and property metadata at a small performance cost.
- Use identical E2EE, obfuscation, filename-case, chunk, and remote settings on every device.
- Do not toggle encryption, path obfuscation, or filename-case handling on an existing database. Changing these is a migration/rebuild requiring a verified backup and an authoritative source vault.
- Generate a Setup URI using only the non-admin `livesync` credential. Transfer it directly to each approved device, expire the transfer, clear clipboard/history, and never place it in Git, tickets, screenshots, shell history, or logs.
- Keep Hidden File Sync disabled initially. Add it only after a separate two-device test because it can synchronize plugin configuration and secrets from `.obsidian`.
- Keep automatic newer-file conflict resolution disabled; upstream classifies it as beta because it can overwrite one side.

Sources:

- [Quick setup and property-obfuscation choice](https://github.com/vrtmrz/obsidian-livesync/blob/693cd77576b7c30df36b5c0c8f7a564fe62e455a/docs/quick_setup.md)
- [Path Obfuscation setting](https://github.com/vrtmrz/obsidian-livesync/blob/693cd77576b7c30df36b5c0c8f7a564fe62e455a/docs/settings.md#path-obfuscation)
- [Why E2EE alone does not conceal paths](https://github.com/vrtmrz/obsidian-livesync/blob/693cd77576b7c30df36b5c0c8f7a564fe62e455a/docs/design_docs/intention_of_chunks.md#why-obfuscate-the-path)
- [Feature maturity: automatic newer-file resolution is beta](https://github.com/vrtmrz/obsidian-livesync/blob/693cd77576b7c30df36b5c0c8f7a564fe62e455a/docs/adr/2026_07_feature_maturity_for_1_0.md)

## Secret lifecycle and rotation

### CouchDB administrator

- Generate at least 32 random bytes; store only in the SOPS-encrypted `livesync` Secret.
- Mount or reference it only in CouchDB, the bootstrap controller, and restore jobs.
- Rotate every 180 days and immediately after suspected exposure.
- Rotation order: commit the SOPS-encrypted replacement, let the projected-volume watcher restart CouchDB and the bootstrap controller, verify authenticated health/bootstrap operations, and verify the old credential fails. Revert the encrypted Git change if reconciliation fails; never leave two permanent admins for convenience.

### LiveSync database member

- Generate independently from the admin credential; distribute only to approved clients.
- Rotate every 90 days, on device loss, or on Setup URI exposure.
- Prefer overlap without weakening authorization: create `livesync-next`, add it to `_security`, update and test all devices, remove `livesync` from `_security`, delete/disable the old user, then rename only during a later planned rotation if a stable username is required.
- Test that the old credential returns `401/403` before closing the incident/change.

### E2EE/obfuscation passphrase

- Never store it in Kubernetes, SOPS, CouchDB configuration, or the repository.
- Store it in the user's password manager plus a separate recovery copy.
- Rotate only through an upstream-supported, rehearsed database rebuild/migration from an authoritative vault. Do not flip the passphrase in place on active clients.
- A lost passphrase makes encrypted backup data unrecoverable; backup drills must include retrieval from the recovery copy.

### TLS and Tailscale

- cert-manager owns TLS private-key creation and renewal in a namespaced Secret. Alert before expiry and verify automatic renewal.
- Use a tagged Tailscale node auth key only for initial router enrollment; make it reusable only if operationally necessary, preauthorized only when policy allows, and ephemeral only if the node is genuinely disposable.
- Expire/revoke the enrollment key after use. Device/node keys then follow Tailscale rotation. Remove route approval and grants before decommissioning the router.

## Threat model

| Threat | Impact | Controls | Residual risk / detection |
| --- | --- | --- | --- |
| Public Internet exposure | Credential attacks and vault tampering | Private RFC1918 address; `/32` subnet route; no Funnel, public LB, NodePort, or WAN forward; authenticated CouchDB | Public DNS leaks hostname/private IP. Continuously inventory Services, Ingresses, router forwards, and Tailscale policy. |
| Stolen CouchDB member credential | Read/write encrypted documents; deletion or conflict injection | One-database member; E2EE; path/property obfuscation; 90-day rotation; device revocation | Attacker can destroy or corrupt remote state. Backups, CouchDB logs, and changes monitoring are required. |
| Stolen CouchDB admin credential | Full database/configuration takeover | SOPS, least mounting, no client distribution, 180-day rotation, audit tests | Cluster admins can still retrieve it. Rotate after any cluster/SOPS compromise. |
| Compromised Obsidian device | Plaintext vault disclosure and valid writes | Device encryption/lock, plugin pin, tailnet/device revocation, credential rotation | E2EE cannot protect plaintext on an authorized endpoint. |
| Malicious LAN peer | Probe/replay/credential attack | TLS, CouchDB auth, exact CORS, no direct `5984`, Traefik-only NetworkPolicy | CORS does not block non-browser clients. Detect authentication failures and rate anomalies. |
| Compromised pod in cluster | Direct CouchDB access or secret theft | Namespace isolation, default-deny policies, Secret least exposure, hardened pod security | Cluster-admin or node compromise bypasses these controls. |
| Misconfigured Tailscale route/grant | Broader LAN reachability | Advertise `/32`; grant only TCP 443; policy tests; no SNAT disablement | Tailnet admin can broaden policy. Audit policy/device changes. |
| Unencrypted metadata leakage | Sensitive note titles/path disclosure in DB/backups | Enable E2EE and path/property obfuscation before first sync | Sizes, timing, account names, and operational metadata can remain visible. |
| Concurrent edits/conflicts | Silent overwrite or duplicated/deleted content | Keep beta newer-file auto-resolution off; use LiveSync inspection; preserve revisions; test conflict cases | Automatic simple merges can still be wrong semantically. Human review remains necessary. |
| Disk/node loss or corruption | Vault outage or permanent loss | ZFS snapshots, encrypted off-node copies, quarterly isolated restore | Single-node service has downtime until restore; target RTO is 4 hours. |
| Supply-chain/tag drift | Unreviewed code/image | Commit and digest pins; Flux review; verify runtime `imageID` | Upstream security fixes require deliberate repinning and retesting. |
| Traefik/log leakage | Credentials or Setup URI captured | Header/body redaction; no query-string credentials; log review | Operators with node access may inspect traffic after TLS termination. |

## Conflict handling and recovery

Operational policy:

1. Do not edit the same note concurrently where avoidable.
2. Let LiveSync automatically merge only its supported simple, non-overlapping text conflicts.
3. Keep automatic "newer file wins" bulk resolution disabled.
4. For a visible conflict, stop editing the affected file on all devices, preserve both revisions, and use **Hatch → Inspect conflicts and file/database differences** to compare the database winner and each conflict revision.
5. Choose or manually merge content from an authoritative device; synchronize; verify the conflict disappears on every device before resuming edits.
6. If many files conflict or local state is corrupt, stop per-file repair. Take backups, choose one authoritative vault, and follow upstream reset/rebuild recovery rather than bulk-discarding revisions.
7. Do not run garbage collection while conflicts or a recovery investigation remain active.

Source: [Self-hosted LiveSync recovery guide at the pinned commit](https://github.com/vrtmrz/obsidian-livesync/blob/693cd77576b7c30df36b5c0c8f7a564fe62e455a/docs/recovery.md).

## Backup and restore

Target **RPO: 24 hours** and **RTO: 4 hours** for a single-node homelab service.

Backup layers:

1. Daily ZFS snapshot of the complete CouchDB PVC dataset, retained for 30 days.
2. Weekly encrypted ZFS send (or equivalent complete snapshot copy) to a physically off-node target, retaining 12 weekly copies.
3. Monthly copy retained for 12 months.
4. Git retains declarative configuration and SOPS ciphertext. The E2EE passphrase recovery copy remains outside Git and outside CouchDB backups.
5. Optionally add continuous replication to a separate off-node CouchDB as a second logical recovery path; do not treat it as the only backup because destructive writes can replicate.

A snapshot on `homelab1` is fast rollback protection, not disaster recovery. Monitor snapshot and off-node job completion, age of newest successful copy, and restore-drill results.

Quarterly restore drill:

1. Select an off-node backup and record its age.
2. Restore into an isolated namespace/host with no production ingress and no production clients.
3. Deploy the exact CouchDB image digest used by the backup.
4. With CouchDB stopped, restore the complete dataset and required configuration onto an empty volume; never overlay a partial backup onto a running database.
5. Start CouchDB and verify `/_up`, admin auth, the member-only `_security` object, document count, update sequence, attachments, and `_changes`.
6. Retrieve the E2EE passphrase from its recovery copy.
7. Connect two disposable plugin `1.0.16` clients, verify note and binary round trips, and confirm expected plaintext.
8. Destroy the isolated restore and credentials after recording achieved RPO/RTO.

CouchDB explicitly supports replication, file copy of append-only database files, and storage snapshots including ZFS. See the [official backup documentation](https://docs.couchdb.org/en/stable/maintenance/backups.html).

## Rollout and rollback

### Rollout

1. Reconcile local Git with `origin/main` and review the pre-existing local commit.
2. Create namespace, encrypted secrets, certificate, ConfigMap, PVC, StatefulSet, ClusterIP Service, NetworkPolicies, bootstrap controller, and HTTPS Ingress.
3. Render with `kubectl kustomize cluster`; run schema validation and policy checks.
4. Push one reviewed commit and wait for Flux readiness.
5. Run infrastructure/security acceptance tests before any real vault connects.
6. Connect two disposable vaults and run the full compatibility matrix.
7. Take the first known-good off-node backup and restore it in isolation.
8. Only then connect the production vault.
9. Enroll/approve the `/32` Tailscale subnet route and run remote-access tests last.

### Rollback

- **Pre-client infrastructure failure:** revert the Flux commit, retain the PVC, certificate, and SOPS Secret, and remove route approval. Do not delete data automatically.
- **Bad configuration:** restore the prior ConfigMap/Secret revision, restart the same pinned image, and rerun auth/CORS tests.
- **Bad image upgrade:** stop clients, stop CouchDB, restore the pre-upgrade ZFS snapshot, and run the previous image digest. Do not run an older CouchDB binary against a data directory already migrated by a newer release unless CouchDB release notes explicitly permit it.
- **Bad plugin upgrade:** stop synchronization, preserve every device vault and server backup, restore the previously verified plugin on disposable clients first, and follow upstream database compatibility guidance. Never mass-downgrade all clients against production without the test.
- **Credential exposure:** remove Tailscale access if needed, rotate member/admin credentials in the sequence above, verify old credentials fail, then restore access.
- **Corruption/conflict storm:** create `redflag.md` as documented upstream to suspend ordinary LiveSync work, select an authoritative vault, snapshot everything, and use the recovery/rebuild workflow.

## Exact acceptance test matrix

All tests are release gates. Replace placeholders without printing secrets into shell history.

| ID | Test | Expected result |
| --- | --- | --- |
| GIT-1 | `git status --short --branch`; compare `HEAD`, `origin/main`, and Flux source revision | Implementation commit is pushed, reviewed, and exactly reconciled by Flux. |
| IMG-1 | `kubectl -n livesync get pod -l app=couchdb -o jsonpath='{.items[0].status.containerStatuses[0].imageID}'` | Runtime image ID resolves to the pinned index or amd64 child digest, never a mutable-only tag. |
| K8S-1 | `kubectl -n livesync get svc,ingress,pvc,statefulset,deployment,networkpolicy` | One ClusterIP service, one TLS Ingress, retained RWO PVC, ready StatefulSet/bootstrap controller, and expected policies. No NodePort/LoadBalancer. |
| K8S-2 | Restart/delete the CouchDB pod, then read a test note and attachment | Pod returns Ready and persisted data is unchanged. |
| NET-1 | From a Traefik pod, connect to `couchdb.livesync.svc:5984`; from an unselected pod in `livesync`, repeat | Traefik succeeds; unselected pod times out/fails. This proves policy enforcement. |
| NET-2 | From CouchDB pod, attempt arbitrary Internet TCP/443 | Fails; CouchDB has no general egress. |
| AUTH-1 | Unauthenticated `GET /`, vault DB, and `/_changes` through HTTPS | Every request returns `401`; no anonymous database data. |
| AUTH-2 | As `livesync`, create/read/update/delete a disposable document and attachment and consume `_changes` | Succeeds. |
| AUTH-3 | As `livesync`, attempt database create/delete, `/_node/.../_config`, and `PUT <db>/_security` | Returns `401/403`; no state changes. |
| AUTH-4 | Create a second ordinary user and read the vault DB | Returns `401/403`. Delete the test user afterward. |
| AUTH-5 | Search rendered manifests, pod specs, logs, and Traefik logs for known credential canaries | No literal admin/member password, Setup URI, or Authorization header appears. SOPS ciphertext is the only Git representation. |
| CORS-1 | Send preflight from `app://obsidian.md` requesting `PUT` and `authorization,content-type` | Matching `Access-Control-Allow-Origin`, credentials `true`, and required methods/headers. |
| CORS-2 | Repeat for `capacitor://localhost` and `http://localhost` | Each explicitly allowed origin succeeds. |
| CORS-3 | Preflight from `https://evil.example` | No `Access-Control-Allow-Origin`; no response contains wildcard ACAO. |
| TLS-1 | `openssl s_client -connect 192.168.2.46:443 -servername livesync.homelab1.local.rafaeltab.com` | Trusted chain, correct SAN, currently valid certificate, TLS 1.2+; no hostname mismatch. |
| TLS-2 | Plain HTTP request for the LiveSync hostname | No application route on port 80; connection fails or a global redirect exposes no data. HTTPS remains mandatory. |
| PLUG-1 | On two Obsidian `>=1.7.2` clients with plugin `1.0.16`, create, update, rename, and delete a Markdown note | Both vaults converge after each operation. |
| PLUG-2 | Synchronize a binary attachment larger than one chunk | Byte-for-byte hashes match on both clients. |
| PLUG-3 | Inspect CouchDB documents after syncing a note with a sensitive filename/property | Plaintext content, path, filename, and selected properties are absent; clients decrypt correctly. |
| CONFLICT-1 | Disconnect both clients, make non-overlapping edits to one Markdown note, reconnect | Supported simple merge converges and preserves both edits. |
| CONFLICT-2 | Repeat with overlapping edits and a delete-versus-edit case | Conflict remains visible for manual review; no silent newer-file overwrite. Resolve manually and verify convergence. |
| CLI-1 | Only if CLI is introduced: run the pinned `1.0.16-cli` image against a disposable DB and vault | CLI and plugin exchange create/update/rename/delete operations and encrypted, obfuscated content. |
| BKP-1 | Verify newest local snapshot and encrypted off-node copy age | Both meet the 24-hour RPO; off-node copy is on a different physical host. |
| BKP-2 | Perform the isolated restore drill described above | Auth, security object, counts, attachments, changes feed, decryption, and two-client sync pass within 4 hours. |
| TS-1 | Inspect tailnet routes | Exactly `192.168.2.46/32` is advertised/approved by the intended tagged router; no `/24`, default, or exit route. |
| TS-2 | From an authorized off-LAN tailnet client, resolve and access the FQDN on TCP 443 | DNS returns `192.168.2.46`; TLS and CouchDB auth pass. |
| TS-3 | From the authorized client, probe TCP 80, 5984, SSH, another LAN host, and adjacent addresses | All fail. Only `192.168.2.46:443` is reachable under the grant. |
| TS-4 | From an unauthorized tailnet identity/device, probe TCP 443 | Fails; policy tests also assert the deny. |
| TS-5 | Stop the subnet router | Off-LAN access fails closed; LAN access through the same FQDN and certificate remains healthy. Reboot restores only the `/32` route. |
| PUB-1 | Inspect Kubernetes Services/Ingresses, Tailscale policy/devices, edge-router forwarding, and external connectivity | No public LB, NodePort, WAN forward, CDN/proxy, Operator proxy, or Funnel. A non-tailnet Internet client cannot connect. |

## Implementation gate

Implementation may start only when the operator confirms:

- the existing local-only Git commit has been reviewed and pushed or otherwise reconciled;
- the exact production vault database name;
- the off-node backup destination exists and has enough retained capacity;
- the Tailscale subnet-router host and intended tailnet user/device identities are known;
- the tailnet policy change has explicit deny tests;
- E2EE and Path Obfuscation are accepted as first-sync invariants.

No production vault data should be uploaded until infrastructure, authorization, CORS, TLS, two-client compatibility, conflict, and isolated restore tests all pass.
