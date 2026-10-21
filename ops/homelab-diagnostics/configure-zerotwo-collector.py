#!/usr/bin/env python3
"""Exclude the collector's own journal messages from its export pipeline.

Keep its errors in the local journal, avoiding an export failure -> journal ->
export failure feedback loop during a backend outage. Never print credentials.
"""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import yaml

path = Path('/etc/otelcol-contrib/config.yaml')
backup = path.with_name('config.before-homelab-diagnostics.yaml')
if not backup.exists():
    shutil.copy2(path, backup)
    backup.chmod(0o600)
config = yaml.safe_load(path.read_text())
receiver = config['receivers'].get('journald') or {}
operators = receiver.setdefault('operators', [])
if not any(op.get('id') == 'drop_collector_self_logs' for op in operators):
    operators.insert(0, {'type': 'filter', 'id': 'drop_collector_self_logs',
                         'expr': 'body._SYSTEMD_UNIT == "otelcol-contrib.service"'})
config['receivers']['journald'] = receiver
staged = path.with_name('config.homelab-diagnostics-staged.yaml')
staged.write_text(yaml.safe_dump(config, sort_keys=False))
staged.chmod(0o640)
shutil.chown(staged, user='root', group='otelcol-contrib')
env = os.environ.copy()
for line in Path('/etc/otelcol-contrib/otelcol-contrib.conf').read_text().splitlines():
    if line.strip() and not line.lstrip().startswith('#') and '=' in line:
        key, value = line.split('=', 1)
        env[key.strip()] = ' '.join(shlex.split(value))
subprocess.run(['/usr/bin/otelcol-contrib', 'validate', '--config=' + str(staged)],
               env=env, check=True)
staged.replace(path)
# Explicitly restart separately after reviewing the change. This can discard an
# in-memory exporter backlog, so it should not run on every receiver install.
print('Collector configuration validated and installed; restart separately to load it.')
