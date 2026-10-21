# WallpaperDB CI completion events

Metadata-only public GitHub Actions collector for rafaeltab/wallpaperdb. Tracking: https://github.com/rafaeltab/homelab/issues/2.

Rafael approved this bounded collector and a separately reviewed dashboard in the originating thread, recorded on Kanban t_c030f001. Merge only after repository review, including both independent review axes on the updated exact head. The prior a60d5e3 candidate is on hold. No deployment has occurred. The dashboard is not included here and must not be published as an empty placeholder.

## Collection and discovery

The single nonroot Deployment polls every ten minutes. SQLite on a retained 1Gi PVC stores API request debits, pagination, attempt tasks and immutable outbox entries. No credentials, GitHub CLI, service-account token, logs, artifacts, source contents or step names are used. The image version follows the existing PR collector. The new workload does not change that collector, Alloy discovery, backend limits, Hermes or existing dashboards.

Events use completed_at as the Loki nanosecond timestamp. Duration is completed_at minus started_at. Missing/negative durations and skipped jobs do not become successful zero-duration observations. Missing completion time is counted as an omission, never replaced with polling time. JSON includes full workflow/job/matrix/branch metadata. Indexed labels are service, repository, event type, controlled job category, bounded conclusion, initial/live phase and a bounded completion_slot described below. No run, job, commit, exact branch or matrix identifier is an indexed label.

The collector enforces 30 public GitHub requests per rolling hour across restarts, debiting before network IO and honoring rate reset and Retry-After. HTTP redirects are rejected by the real urllib handler, not followed behind a single debit. No hidden token fallback. Recent discovery paginates runs created in the preceding six days. In addition, a daily unfiltered traversal of ALL retained runs discovers reruns of older-created workflows. Each cycle advances at most one page of each scan, then up to four attempt tasks. The daily traversal keeps its page and fixed six-day updated_at cutoff across rate waits/restarts, and skips only runs with an authoritative updated_at older than that cutoff. Completed attempt markers do not suppress a newer attempt. No created/event/status search filter is used on the daily traversal, avoiding GitHub's documented filtered-search 1,000-result limit.

Unresolved attempts refresh their status independently of discovery. 404/410 attempts are counted and retired, transient failures deferred so they do not starve other tasks. Malformed HTTP-200 job responses and event-processing errors are isolated to the offending attempt; the current page transaction rolls back before the attempt is deferred, including any partial inserts and counters. GitHub failures do not prevent persisted outbox delivery. The two scans share the same 30/hour budget; no rate expansion is included. Daily scan progress, last completed daily scan and request usage are reported. Older-created rerun detection can lag a day plus traversal/backlog time. Pagination is not a transactional GitHub snapshot, and daily overlap repairs page shifts only while activity remains within the fixed update horizon. API retention/deletions, prolonged failures and insufficient budget can still cause omissions. They are not an intentional exclusion of older-created workflows.

Initial history is best effort, at most six days before bootstrap. Older events encountered through the unfiltered scan are deduplicated and marked outside_backfill without ingestion. Events older than 167 hours at delivery are expired. No limits are enlarged and no events retimestamped.

## Safe late delivery without a global barrier

Loki effective read-only preflight found unordered_writes true, max_chunk_age 2h, reject_old_samples true, maximum age one week, retention five years and empty runtime overrides. These are deployment prerequisites. Do not deploy if the reorder window is less than 30 minutes.

The prior global initial-history barrier and per-stream 30-minute omission rule are removed. A fifth task finishing after four newer completions, or a persistently failing unrelated attempt, must not cause loss or hold all initial history.

Instead, completion_slot = floor(completed_seconds / 1800) modulo 336. This is a fixed 336-value half-hour ring, never an unbounded date/run label. Within one slot generation, any two completion timestamps differ by less than 30 minutes, inside the verified one-hour Loki reorder allowance. Slot generations are 168 hours apart, while delivery admits only events within 167 hours of now. An older generation therefore cannot be newly delivered after a later generation advances its stream. Retries retain the original slot, stream labels, event timestamp and body. Initial/live phase remains stable per persisted event.

This adds at most 336 slot values and multiplies possible job-stream combinations by that bound, only creating slots with real events. Four job categories, ten conclusions including other, and two phases give a theoretical 26,880 job-stream combinations, not that many observed active streams. Review this bounded indexing tradeoff before deployment. Dashboard aggregation must exclude slot and phase when grouping jobs. No backend retention or admission change is involved.

Flush assigns at most 1,000 previously unbatched pending rows in timestamp/identity order into persisted immutable request bodies of at most 100 events per stream. It attempts at most ten batches per cycle in persisted least-recently-attempted order, including new batches at the current delivery turn. A rejected batch cannot monopolize the bounded selection, and continuous arrivals cannot jump ahead of older retries. Failures retain exact body/labels/timestamps across restart and new arrivals, increment delivery_failures, and do not prevent independent batches from progressing; aggregate failure is raised only after the selected batches are attempted. Indexes support pending-row and fair-batch selection. Partially expired frozen batches are not sent or rewritten; they wait less than one half-hour slot until all their members expire, then are counted and retired together. No unrelated attempt participates in a delivery barrier. initial_ready is diagnostic first-traversal progress only. The legacy out_of_order counter remains zero for status compatibility; the collector no longer intentionally discards valid events based on another task's highwater. Actual Loki rejection retains pending rows and reports delivery failure.

Backend deduplication and source reconciliation MUST be verified after deployment. The modeled reorder-window and ambiguous-ack tests are not proof of live exactly-once ingestion. The local database is retained and grows with observed jobs; observe PVC usage during final acceptance rather than silently changing retention.

collector_status events at polling time contain delivery/discovery timestamps, daily scan progress, rolling API usage, sent/pending events, pending attempts, oldest/newest retained event, omission counters, API failures and bounded error categories. Sanitized local health prints before remote status export, so a failed push does not hide local diagnostics. No new alerts or notifications. Query status separately from completion-time job events. Pod readiness checks database access, not upstream freshness; dashboard status must expose lag and missing data.

## Verification and delivery

Run isolated fixtures with:

    PYTHONDONTWRITEBYTECODE=1 python cluster/workloads/wallpaperdb-ci-events/test_collector.py

Fixtures cover a day-seven rerun of a completed older-created run, daily pagination/restart, a fifth older completion across the four-task cycle, prolonged HTTP-500 plus successful unrelated history and later recovery, same-slot reverse arrival, weekly slot reuse, multi-hour ambiguous acknowledgement, bounded outbox reads and six-day backfill scope. They use fake transports, not live GitHub or Loki writes.

Render without deployment:

    kubectl kustomize cluster/workloads/wallpaperdb-ci-events
    kubectl kustomize cluster/flux/kustomizations

After reviewed merge, allow normal Flux reconciliation. Before delivering the dashboard, reconcile real GitHub job IDs and timestamps with Loki, test retry/deduplicated counts, validate every saved query, read back UID wallpaperdb-ci-duration, and inspect a fresh native 2560x1440 full render. Preserve the existing PR dashboard. Main success chart must retain actual observations; failures/cancellations and skips/invalids remain separate. Smoothing must be labeled and not replace actual observations.

Rollback is a reviewed revert of the new workload/Flux wiring, with explicit operator approval for removals. The PVC has Flux prune disabled to protect cursor/outbox state. Do not delete it or the Loki data, change backend admission/retention, or restart unrelated services.
