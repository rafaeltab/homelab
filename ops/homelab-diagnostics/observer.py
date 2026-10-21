#!/usr/bin/env python3
"""Independent homelab1 probes and a resumable journal archive on zerotwo."""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.parse

CONFIG = Path('/etc/homelab-diagnostics.json')
SSH_OPTIONS = ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=3',
               '-o', 'ConnectionAttempts=1', '-o', 'ServerAliveInterval=5',
               '-o', 'ServerAliveCountMax=2']
SNAPSHOT = r'''
date -Is
uptime
free -h
cat /proc/pressure/cpu /proc/pressure/memory /proc/pressure/io
df -h /
ps -eo pid,ppid,stat,pcpu,pmem,wchan:30,comm --sort=-pcpu | head -70
journalctl --since '-10 minutes' -n 400 --no-pager -o short-iso-precise
for h in /sys/class/hwmon/hwmon*; do
  cat "$h/name" 2>/dev/null
  for t in "$h"/temp*_input; do [ ! -f "$t" ] || { printf '%s ' "$t"; cat "$t"; }; done
done
zpool status
'''


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='milliseconds')


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    with temp.open('w') as stream:
        json.dump(value, stream)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def append(path: Path, text: str, limit: int = 20 * 1024 * 1024, backups: int = 7) -> None:
    """Size-bounded archive, also bounded if an incident floods the journal."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size + len(text.encode()) > limit:
        path.with_name(path.name + f'.{backups}').unlink(missing_ok=True)
        for n in range(backups - 1, 0, -1):
            old = path.with_name(path.name + f'.{n}')
            if old.exists():
                old.replace(path.with_name(path.name + f'.{n + 1}'))
        path.replace(path.with_name(path.name + '.1'))
    with path.open('a') as stream:
        stream.write(text)
        stream.flush()


def run(argv: list[str], timeout: int = 8, data: str | None = None) -> dict:
    started = time.monotonic()
    try:
        p = subprocess.run(argv, input=data, capture_output=True, text=True,
                           timeout=timeout, check=False)
        return {'ok': p.returncode == 0, 'returncode': p.returncode,
                'duration_ms': round((time.monotonic() - started) * 1000),
                'stdout': p.stdout[-65536:], 'stderr': p.stderr[-4000:]}
    except subprocess.TimeoutExpired as exc:
        def partial(value):
            return value.decode(errors='replace') if isinstance(value, bytes) else (value or '')
        return {'ok': False, 'duration_ms': round((time.monotonic() - started) * 1000),
                'error': 'TimeoutExpired', 'stdout': partial(exc.stdout)[-65536:],
                'stderr': partial(exc.stderr)[-4000:]}
    except OSError as exc:
        return {'ok': False, 'duration_ms': round((time.monotonic() - started) * 1000),
                'error': type(exc).__name__}


def commands(config: dict) -> dict[str, list[str]]:
    query = 'max by(host_name)(timestamp(system_cpu_load_average_1m{host_name=~"zerotwo|homelab1"}))'
    metrics_path = ('/api/v1/namespaces/observability/services/mimir-nginx:80/proxy/'
                    'prometheus/api/v1/query?' + urllib.parse.urlencode({'query': query}))
    return {
        'metrics_freshness': [config['kubectl'], '--kubeconfig=' + config['kubeconfig'],
                              '--request-timeout=4s', 'get', '--raw=' + metrics_path],
        'ping': ['/usr/bin/ping', '-n', '-c', '1', '-W', '2', config['ip']],
        'ssh': ['/usr/bin/ssh', *SSH_OPTIONS, config['ssh_host'], 'true'],
        'kubernetes': [config['kubectl'], '--kubeconfig=' + config['kubeconfig'],
                       '--request-timeout=4s', 'get', '--raw=/readyz'],
        'grafana': ['/usr/bin/curl', '--silent', '--show-error', '--fail',
                    '--connect-timeout', '3', '--max-time', '5', config['grafana_url']],
        'telemetry': [config['kubectl'], '--kubeconfig=' + config['kubeconfig'],
                      '--request-timeout=4s', '-n', 'observability', 'get',
                      'endpointslices', '-l', 'kubernetes.io/service-name=host-telemetry-gateway',
                      '-o=jsonpath={.items[*].endpoints[*].conditions.ready}'],
    }


def capture(config: dict, reason: dict) -> dict:
    result = {'captured_at': now(), 'reason': reason}
    kube = [config['kubectl'], '--kubeconfig=' + config['kubeconfig'], '--request-timeout=4s']
    stacks = r'''date -Is
ps -eLo pid,tid,stat,pcpu,wchan:35,comm --sort=-pcpu | head -60
for pid in $(pgrep -x otelcol-contrib); do
  for task in /proc/$pid/task/*; do
    state=$(awk '{print $3}' "$task/stat" 2>/dev/null)
    case "$state" in
      R|D|Z) printf '\n%s state=%s\n' "$task" "$state"; timeout 2 cat "$task/stack" ;;
    esac
  done
done'''
    jobs = {
        'host': (['/usr/bin/ssh', *SSH_OPTIONS, config['ssh_host'],
                  'timeout 18s sh -s'], 23, SNAPSHOT),
        'pods': (kube + ['get', 'pods', '-A', '-o', 'wide'], 8, None),
        'events': (kube + ['get', 'events', '-A', '--sort-by=.metadata.creationTimestamp'], 8, None),
        # The existing privileged exporter has a read-only host mount. Entering
        # the host namespaces here only runs this fixed, read-only stack probe.
        'kernel_stacks': (kube + ['-n', 'observability', 'exec',
            'daemonset/host-telemetry-agent', '-c', 'zpool-metrics', '--',
            'chroot', '/hostfs', '/usr/bin/nsenter', '-t', '1', '-m', '-u', '-i',
            '-n', '-p', '-r', '-w', '--', '/usr/bin/timeout', '12',
            '/bin/bash', '-c', stacks], 16, None),
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {name: pool.submit(run, argv, timeout, data)
                   for name, (argv, timeout, data) in jobs.items()}
        result.update({name: future.result() for name, future in futures.items()})
    # Do not archive full object YAML: pod environment/config may contain credentials.
    path = Path(config['log_dir']) / 'incidents' / ('incident-' + now().replace(':', '-') + '.json')
    atomic_json(path, result)
    for old in sorted(path.parent.glob('incident-*.json'))[:-100]:
        old.unlink()
    return {'path': str(path), 'ssh_snapshot_ok': result['host']['ok']}


def probe(config: dict, force_capture: bool = False) -> dict:
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {name: pool.submit(run, argv) for name, argv in commands(config).items()}
        results = {name: future.result() for name, future in futures.items()}
    # curl --fail also accepts redirects. The health endpoint must return its expected body.
    if results['grafana']['ok']:
        try:
            results['grafana']['ok'] = json.loads(results['grafana']['stdout']).get('database') == 'ok'
        except (ValueError, AttributeError):
            results['grafana']['ok'] = False
    if results['kubernetes']['ok']:
        results['kubernetes']['ok'] = results['kubernetes']['stdout'].strip() == 'ok'
    if results['telemetry']['ok']:
        results['telemetry']['ok'] = 'true' in results['telemetry']['stdout'].split()
    if results['metrics_freshness']['ok']:
        try:
            data = json.loads(results['metrics_freshness']['stdout'])
            samples = {row['metric']['host_name']: float(row['value'][1])
                       for row in data['data']['result']}
            results['metrics_freshness']['sample_ages_seconds'] = {
                host: round(time.time() - stamp, 1) for host, stamp in samples.items()}
            results['metrics_freshness']['ok'] = all(
                host in samples and -30 <= time.time() - samples[host] <= 180
                for host in ('homelab1', 'zerotwo'))
        except (ValueError, KeyError, TypeError):
            results['metrics_freshness']['ok'] = False
    record = {'checked_at': now(), 'probes': results}
    record['healthy'] = all(r['ok'] for r in results.values())
    state_path = Path(config['state_dir']) / 'health.json'
    try:
        previous = json.loads(state_path.read_text())
    except (OSError, ValueError):
        previous = {}
    changed = {name: r['ok'] for name, r in results.items()} != previous.get('status')
    last_capture = previous.get('last_capture', 0)
    if force_capture or (not record['healthy'] and (changed or time.time() - last_capture >= 300)):
        record['snapshot'] = capture(config, record)
        last_capture = time.time()
    append(Path(config['log_dir']) / 'probes' / 'checks.jsonl', json.dumps(record) + '\n')
    atomic_json(state_path, {'checked_at': record['checked_at'], 'healthy': record['healthy'],
                            'status': {name: r['ok'] for name, r in results.items()},
                            'last_capture': last_capture})
    if changed:
        print(json.dumps({'checked_at': record['checked_at'], 'healthy': record['healthy'],
                          'status': {name: r['ok'] for name, r in results.items()}}), flush=True)
    return record


def journal(config: dict) -> None:
    cursor_path = Path(config['state_dir']) / 'journal-cursor.json'
    try:
        cursor = json.loads(cursor_path.read_text()).get('cursor')
    except (OSError, ValueError):
        cursor = None
    # A fixed timestamp argument prevents interpolation of commands or shell syntax.
    remote = ['journalctl', '--follow', '--output=json', '--no-pager']
    if cursor:
        remote += ['--after-cursor=' + cursor]
    else:
        remote += ['--since=-5minutes', '--no-tail']
    import shlex
    argv = ['/usr/bin/ssh', *SSH_OPTIONS, config['ssh_host'], shlex.join(remote)]
    # Do not echo received messages to our journal: these archives stay local to zerotwo.
    with subprocess.Popen(argv, stdout=subprocess.PIPE, text=True) as proc:
        assert proc.stdout
        last_save = time.monotonic()
        try:
            for line in proc.stdout:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                entry['zerotwo_received_at'] = now()
                append(Path(config['log_dir']) / 'journal' / 'system.jsonl', json.dumps(entry) + '\n')
                cursor = entry.get('__CURSOR', cursor)
                if cursor and time.monotonic() - last_save >= 2:
                    atomic_json(cursor_path, {'cursor': cursor})
                    last_save = time.monotonic()
        finally:
            if cursor:
                atomic_json(cursor_path, {'cursor': cursor})
        code = proc.wait()
        if code:
            raise SystemExit(code)
    # Even a clean remote exit should reconnect under systemd Restart=always.


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['probe', 'capture', 'journal'])
    parser.add_argument('--config', type=Path, default=CONFIG)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if args.mode == 'journal':
        journal(config)
    else:
        probe(config, force_capture=args.mode == 'capture')


if __name__ == '__main__':
    main()
