#!/bin/bash
# Run as root on zerotwo; the services themselves run as rafaeltab.
set -euo pipefail
cd -- "$(dirname -- "$0")"
[ "$(hostname)" = zerotwo ] || { echo 'This receiver configuration is for zerotwo.' >&2; exit 1; }
[ "$(id -u)" = 0 ] || { echo 'Run with sudo.' >&2; exit 1; }
install -d -m 0755 /opt/homelab-diagnostics /var/log/homelab1
install -d -o rafaeltab -g adm -m 0750 /var/log/homelab1/{probes,journal,incidents}
install -d -o syslog -g adm -m 0750 /var/log/homelab1/netconsole
install -d -o rafaeltab -g rafaeltab -m 0750 /var/lib/homelab-diagnostics
install -m 0755 observer.py /opt/homelab-diagnostics/observer.py
cat > /etc/homelab-diagnostics.json <<'JSON'
{
  "ip": "192.168.2.46",
  "ssh_host": "homelab1",
  "grafana_url": "https://grafana.homelab1.local.rafaeltab.com/api/health",
  "kubectl": "/home/rafaeltab/.local/bin/kubectl",
  "kubeconfig": "/home/rafaeltab/.kube/config",
  "state_dir": "/var/lib/homelab-diagnostics",
  "log_dir": "/var/log/homelab1"
}
JSON
chmod 0644 /etc/homelab-diagnostics.json
cat > /etc/systemd/system/homelab1-probe.service <<'UNIT'
[Unit]
Description=Independent homelab1 availability probes and incident capture
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
User=rafaeltab
Group=rafaeltab
SupplementaryGroups=adm
Environment=HOME=/home/rafaeltab
UMask=0027
ExecStart=/usr/bin/python3 /opt/homelab-diagnostics/observer.py probe
TimeoutStartSec=50
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/var/log/homelab1 /var/lib/homelab-diagnostics /home/rafaeltab/.kube/cache
PrivateTmp=yes
UNIT
# kubectl may write its discovery cache, but it never needs to modify credentials.
install -d -o rafaeltab -g rafaeltab /home/rafaeltab/.kube/cache
cat > /etc/systemd/system/homelab1-probe.timer <<'UNIT'
[Unit]
Description=Check homelab1 every 30 seconds
[Timer]
OnBootSec=15s
OnUnitActiveSec=30s
AccuracySec=1s
Unit=homelab1-probe.service
[Install]
WantedBy=timers.target
UNIT
cat > /etc/systemd/system/homelab1-journal.service <<'UNIT'
[Unit]
Description=Archive homelab1 system journal on zerotwo
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0
[Service]
User=rafaeltab
Group=rafaeltab
SupplementaryGroups=adm
Environment=HOME=/home/rafaeltab
UMask=0027
ExecStart=/usr/bin/python3 /opt/homelab-diagnostics/observer.py journal
Restart=always
RestartSec=5s
KillMode=control-group
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/var/log/homelab1 /var/lib/homelab-diagnostics
PrivateTmp=yes
[Install]
WantedBy=multi-user.target
UNIT
# A dedicated input ruleset writes directly to files. Received kernel messages
# are not forwarded to zerotwo's journal, the OTel collector, or a Hermes agent.
cat > /etc/rsyslog.d/15-homelab1-netconsole.conf <<'RSYSLOG'
module(load="imudp")
template(name="Homelab1Kernel" type="string" string="%timegenerated:::date-rfc3339% from=%fromhost-ip% %rawmsg%\n")
ruleset(name="homelab1_netconsole") {
  if $fromhost-ip == "192.168.2.46" then {
    action(type="omfile" file="/var/log/homelab1/netconsole/kernel.log"
           template="Homelab1Kernel" fileOwner="syslog" fileGroup="adm" fileCreateMode="0640")
  }
  stop
}
input(type="imudp" address="192.168.2.98" port="6666" ruleset="homelab1_netconsole")
RSYSLOG
cat > /etc/logrotate.d/homelab1-netconsole <<'ROTATE'
/var/log/homelab1/netconsole/kernel.log {
    daily
    maxsize 20M
    rotate 7
    compress
    missingok
    notifempty
    create 0640 syslog adm
    sharedscripts
    postrotate
        /usr/bin/systemctl kill -s HUP rsyslog.service >/dev/null 2>&1 || true
    endscript
}
ROTATE
rsyslogd -N1
systemd-analyze verify /etc/systemd/system/homelab1-{probe.service,probe.timer,journal.service}
systemctl daemon-reload
systemctl restart rsyslog
systemctl enable --now homelab1-journal.service homelab1-probe.timer
systemctl start homelab1-probe.service
