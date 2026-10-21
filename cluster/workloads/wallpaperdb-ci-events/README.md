# WallpaperDB CI completion events

Metadata-only public GitHub Actions collector for rafaeltab/wallpaperdb. Tracking: https://github.com/rafaeltab/homelab/issues/2.

Rafael approved this bounded collector and a separately reviewed dashboard in the originating thread (approval recorded on Kanban t_c030f001). Merge only after repository review. The dashboard is not included here and must not be published as an empty placeholder.

The single nonroot Deployment polls every ten minutes. SQLite on a retained 1Gi PVC stores API request debits, pagination, attempt tasks and immutable outbox entries. No credentials, GitHub CLI, service-account token, logs, artifacts, source contents or step names are used. The image version follows the existing PR collector. The new workload does not change that collector, Alloy discovery, backend limits, Hermes or existing dashboards.

Events use completed_at as the Loki nanosecond timestamp. Duration is completed_at minus started_at; missing/negative durations and skipped jobs do not become successful zero-duration observations. Missing completion time is counted as an omission, never replaced with polling time. JSON includes full workflow/job/matrix/branch metadata; indexed labels are only service, repository, event type, controlled job category, bounded conclusion and initial/live phase.

The collector enforces 30 public GitHub requests per rolling hour across restarts, debiting before network IO, honoring rate reset and Retry-After. No hidden token fallback. At most four attempt tasks are processed per cycle after discovery. Unresolved attempts refresh their status independently of discovery; 404/410 attempts are counted and retired, transient failures deferred so they do not starve other tasks. Failures do not prevent delivery of already-persisted outbox entries.

History is best effort, not exhaustive. Discovery explicitly paginates runs CREATED within the preceding six days, including noncompleted runs, and visits all their attempts. A newly rerun workflow originally created more than six days ago is outside discovery unless already tracked; discovering arbitrary old-created reruns needs a separate reviewed source strategy. Pagination is not a transactional GitHub snapshot. Rate backlog can reduce history. The first complete traversal gates chronological initial delivery, not completion of ongoing runs. Late events older than the per-stream high-water mark minus 30 minutes are counted as out_of_order omissions. Events older than 167 hours at delivery are counted as expired. No limits are enlarged and no events retimestamped.

Loki effective read-only preflight: unordered_writes true, max_chunk_age 2h, reject_old_samples true, maximum age one week, retention five years; runtime overrides empty. Initial and live streams are isolated. Retry uses identical labels, timestamp and body after ambiguous network outcomes; backend deduplication and source reconciliation MUST be verified after deployment. This implementation does not claim exactly-once ingestion. The local database is retained and grows with observed jobs; observe PVC usage during final acceptance rather than silently changing retention.

collector_status events at polling time contain delivery/discovery timestamps, sent/pending events, pending attempts, oldest/newest retained event, omitted-event counters, API failures and bounded error categories. No new alerts or notifications. Query status separately from completion-time job events. Pod readiness checks database access, not upstream freshness; dashboard status must expose lag and missing data.

Run isolated fixtures with:

    PYTHONDONTWRITEBYTECODE=1 python cluster/workloads/wallpaperdb-ci-events/test_collector.py

Render without deployment:

    kubectl kustomize cluster/workloads/wallpaperdb-ci-events
    kubectl kustomize cluster/flux/kustomizations

After reviewed merge, allow normal Flux reconciliation. Before delivering the dashboard, reconcile real GitHub job IDs and timestamps with Loki, test retry/deduplicated counts, validate every saved query, read back UID wallpaperdb-ci-duration, and inspect a fresh native 2560x1440 full render. Preserve the existing PR dashboard. Main success chart must retain actual observations; failures/cancellations and skips/invalids remain separate. Smoothing must be labeled and not replace actual observations.

Rollback is a reviewed revert of the new workload/Flux wiring, with explicit operator approval for removals. The PVC has Flux prune disabled to protect cursor/outbox state. Do not delete it or the Loki data, change backend admission/retention, or restart unrelated services.
