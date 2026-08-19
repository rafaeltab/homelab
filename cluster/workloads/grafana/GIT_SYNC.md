# Grafana dashboard Git Sync

Grafana 13 synchronizes the selected homelab dashboards from the private repository
`zero-two-rafaeltab/homelab-grafana-dashboards`, branch `main`, path `grafana/`.

The Git Sync repository resource is named `homelab-dashboards` in Grafana's
`default` namespace. It uses the `folderless` target so top-level dashboards stay in
General while the explicit `hermes` folder resource preserves the Hermes folder UID.
The direct-write workflow is enabled, so dashboard saves from Grafana create commits
on `main`. Grafana polls every 60 seconds; webhooks are not used because this instance
is private.

The repository credential is stored as a write-only Grafana secure value in the
persistent Grafana database. It is not stored in this GitOps repository. If the
Grafana database is restored from before the migration, recreate the repository
connection through **Administration > General > Provisioning** using the same URL,
branch, path, target, and direct-write workflow.

Classic ConfigMap provisioning remains enabled only for
`Kubernetes / Views / Pods`. The archived JSON files for the migrated dashboards are
retained under `dashboards/` as rollback artifacts but are no longer mounted into
Grafana.

Before this migration, a verified SQLite backup was captured outside the cluster at
`/home/rafaeltab/backups/grafana/20260819T191308Z/grafana.db` on the Hermes host.

Rollback:

1. Disable the `homelab-dashboards` Git Sync repository.
2. Restore the `general` and `hermes` providers, ConfigMap mappings, and generators
   from Git history.
3. Reconcile Flux and verify the three dashboards report classic provisioning.
