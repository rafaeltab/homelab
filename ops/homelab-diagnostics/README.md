# homelab1 incident capture

These services run on zerotwo independently of Hermes and homelab1's cluster.
Received logs stay in local files for on-demand inspection. The receiver does
not write received messages into zerotwo's journal or an agent conversation.

## Services and evidence

- `homelab1-probe.timer`: runs every 30 seconds.
- `homelab1-probe.service`: checks ping, an actual SSH command, Kubernetes
  `/readyz`, Grafana `/api/health`, ready telemetry endpoints, and metric freshness
  for both homelab1 and zerotwo. A sample older than 180 seconds fails freshness.
- `homelab1-journal.service`: archives the remote journal over SSH, reconnecting
  after failure and resuming from its saved cursor.
- `rsyslog.service`: receives kernel netconsole on zerotwo's LAN IP, UDP 6666,
  and accepts packets from homelab1's address only.

Files on zerotwo:

- `/var/log/homelab1/probes/checks.jsonl`: results of every check.
- `/var/log/homelab1/incidents/incident-*.json`: failure snapshots.
- `/var/log/homelab1/journal/system.jsonl`: original journal metadata and the
  zerotwo receipt timestamp.
- `/var/log/homelab1/netconsole/kernel.log`: kernel messages and receipt times.
- `/var/lib/homelab-diagnostics/health.json`: latest probe states. The journal
  cursor is stored in the same state directory.

Failures trigger a bounded SSH snapshot of memory, pressure metrics, processes,
recent logs, temperatures and ZFS status, plus Kubernetes pod summaries/events.
Every command times out; failed attempts are recorded. Capture repeats at most
once per five minutes during an unchanged failure. A completely frozen node
cannot execute a snapshot. Full object YAML, environment variables and Secrets
are not archived by the snapshot.

Probe/journal archives rotate at 20 MiB and keep seven older files; the newest
100 incident snapshots are retained. Netconsole has daily/20 MiB logrotate
configuration with seven compressed archives. Hermes can read local files on
demand. Journal delivery is at least once: a restart can repeat entries since
the last cursor checkpoint, which is written every two seconds. Original cursors
identify duplicates. Source journal retention limits recovery after long outages.

## Installation and verification

These scripts contain Rafael's current host names, addresses and service user.
Zerotwo needs Python 3, rsyslog, existing noninteractive SSH access to homelab1,
and the existing working Kubernetes configuration.

```sh
sudo ops/homelab-diagnostics/install-zerotwo.sh
systemctl status homelab1-journal.service homelab1-probe.timer
cat /var/lib/homelab-diagnostics/health.json
python3 /opt/homelab-diagnostics/observer.py capture
```

Observers run as rafaeltab. Netconsole files are owned by syslog:adm. Receiver
address: 192.168.2.98, MAC 38:05:25:39:33:56. Sender: 192.168.2.46/enp2s0.
Update both configurations if addresses change.

On homelab1 run as **host root, outside a resource-limited monitoring container**:

```sh
apt-get install --no-install-recommends kdump-tools makedumpfile
bash install-homelab1.sh
```

During this incident the package installation and final configuration ran as
transient host systemd services. The installer emits a harmless, explicitly
labelled netconsole delivery-test message; verify it in zerotwo's `kernel.log`.
Journal delivery was also checked using a labelled logger message and receiver
restart. No deliberate kernel crash was used for validation.

## Crash capture activation

The installer prepares `crashkernel=512M` in GRUB, enables kdump-tools, directs
compressed dumps to `/var/crash` on ext4, and stages panic-on-soft-lockup,
panic-on-hard-lockup and panic-on-oops. These panic settings are not applied live
until a capture kernel is loaded. They take effect at the next ordinary boot,
when crash memory can be reserved.

After a planned reboot, verify:

```sh
cat /proc/cmdline                    # crashkernel=512M
cat /sys/kernel/kexec_crash_size     # 536870912
cat /sys/kernel/kexec_crash_loaded   # 1
sudo kdump-config show              # ready to kdump
sysctl kernel.nmi_watchdog kernel.softlockup_panic kernel.hardlockup_panic
```

A boot parameter alone does not prove capture is armed. If the loaded flag is
zero, inspect kdump-tools logs. The reservation reduces ordinary workload memory
by 512 MiB. Detected lockups will trigger capture instead of waiting indefinitely;
complete hardware failure or power loss can still prevent a dump.

`configure-zerotwo-collector.py` filters the OTel collector's own journal messages
out of its export pipeline, avoiding export-error feedback during an outage.
Its errors remain in the local journal. The script preserves existing settings
and credentials, validates before installing, and leaves restart as a separate
step because restarting can discard an in-memory exporter backlog.

## Recovery evidence: 2026-10-21

Around 21:00 UTC the gateway process leader was a zombie while two surviving
threads spun in ZFS write paths. Kubernetes could not stop it. The telemetry
ingress returned 503, so zerotwo/agent dashboards lacked fresh data while node
metrics and Kubernetes scrapes continued. This is a concrete storage/kernel
symptom; the cause of the earlier whole-host failures remains unproven.

The gateway now uses a new local-path PVC on ext4, two OTLP queue consumers,
bounded batches and `GOMEMLIMIT=256MiB` within its 512 MiB container limit. The
original ZFS queue is retained for evidence/backlog recovery. The stuck container
had to be removed from the Kubernetes API; its kernel threads may survive until
reboot. Do not attach its queue to another writer while those threads exist.

Current sample timestamps for both hosts were verified in Mimir. Some old
replayed metrics were rejected as too old; missing samples cannot all be
reconstructed. Zerotwo's collector was restarted and its self-log export loop
was filtered to restore current data.

An initial package-install attempt inherited the metrics container's 32 MiB
memory limit and was killed. Those approximately 21:03–21:05 UTC container OOMs
were caused by setup, not new host memory pressure. The host-systemd retry
succeeded. Backups and original thread stacks are under
`/home/rafaeltab/backups/homelab1-incident-20261021/`. Do not commit collector
configuration backups: they contain the existing ingest credentials.

References: [netconsole](https://docs.kernel.org/networking/netconsole.html),
[lockup detectors](https://docs.kernel.org/admin-guide/lockup-watchdogs.html),
[Ubuntu crash dumps](https://ubuntu.com/server/docs/how-to/software/kernel-crash-dump/).
