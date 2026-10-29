#!/usr/bin/env python3
"""Collect existing read-only game probes and push authenticated snapshots to CF.

Runtime config, SSH keys and state live outside the repository. Never run a shell
with untrusted commands: probe commands are operator-owned configuration.
"""
import argparse
import concurrent.futures
import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request


def parse_kv(text):
    out = {}
    for line in text.splitlines():
        if '=' not in line:
            continue
        key, value = line.strip().split('=', 1)
        try:
            out[key] = int(value)
        except ValueError:
            try:
                out[key] = float(value)
            except ValueError:
                out[key] = value
    return out


def probe_game(game, full):
    mode = 'full' if full else 'fast'
    command = (game['command'] + ' ' + mode).strip()
    try:
        result = subprocess.run([
            'ssh', '-n', '-i', game['key'], '-o', 'BatchMode=yes',
            '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=10',
            '-o', 'ServerAliveCountMax=2', '-o', 'LogLevel=ERROR',
            game['user']+'@'+game['host'], command,
        ], capture_output=True, timeout=55, check=True, text=True)
        data = parse_kv(result.stdout)
        if 'alive' not in data or 'online' not in data:
            raise ValueError('Missing probe fields')
        if 'err' in data or data.get('online', -1) < 0:
            raise ValueError('Probe reported unavailable metrics')
        data['status'] = {1: 'ok', 2: 'warn', 0: 'down'}.get(data['alive'], 'unknown')
        data['online_source'] = game['source']
        data.pop('err', None)
        return data
    except (OSError, subprocess.SubprocessError, ValueError):
        # Do not transmit SSH stderr, commands or production configuration.
        return {'online': -1, 'alive': -1, 'status': 'unknown', 'collection_error': True}


def merge_sample(previous, fresh, full, now):
    current = {**previous, **fresh}
    current['collection_error'] = fresh.get('collection_error', False)
    current.pop('err', None)
    if current['collection_error']:
        current.update(online=-1, status='unknown', alive=-1)
        return current
    current['last_success_at'] = now
    if full:
        current['ts_full'] = now
    day = datetime.datetime.fromtimestamp(now, datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d')
    online = current['online']
    if previous.get('peak_source') != current['online_source']:
        current.update(peak_today=online, peak_all=online)
    else:
        current['peak_today'] = max(online, previous.get('peak_today', 0) if previous.get('peak_day') == day else 0)
        current['peak_all'] = max(online, previous.get('peak_all', 0))
    current.update(peak_day=day, peak_source=current['online_source'])
    return current


def collect(config, state, full):
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(probe_game, g, full): g for g in config['games']}
        for f in concurrent.futures.as_completed(futures):
            g = futures[f]
            now = int(time.time())
            state[g['id']] = {'sampled_at': now, 'metrics': merge_sample(state.get(g['id'], {}).get('metrics', {}), f.result(), full, now)}
    return [{'id': g['id'], **state[g['id']]} for g in config['games']]


def push(config, games):
    body = json.dumps({'games': games}, ensure_ascii=False, separators=(',', ':')).encode()
    timestamp = str(int(time.time()))
    signature = hmac.new(config['report_secret'].encode(), timestamp.encode()+b'\n'+body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(config['url'].rstrip('/')+'/games/update', data=body, headers={
        'Content-Type': 'application/json', 'X-Game-Timestamp': timestamp,
        'X-Game-Signature': signature,
    })
    # Direct egress: runtime never depends on the former 9.11 proxy.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=25) as response:
        if not json.load(response).get('success'):
            raise ValueError('Report rejected')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='/etc/cf-game-monitor/config.json')
    parser.add_argument('--state', default='/var/lib/cf-game-monitor/state.json')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    state_path = Path(args.state)
    try:
        state = json.loads(state_path.read_text())
    except (FileNotFoundError, ValueError):
        state = {}
    last_full = 0
    while True:
        started = time.monotonic()
        full = started-last_full >= 300
        games = collect(config, state, full)
        if full:
            last_full = time.monotonic()
        if args.dry_run:
            for g in games:
                print(g['id'], g['metrics']['status'], 'online='+str(g['metrics']['online']))
        else:
            try:
                push(config, games)
                print('Reported',len(games),'games;',sum(g['metrics']['status']=='ok' for g in games),'healthy',flush=True)
            except Exception as exc:
                print('Report failed:',type(exc).__name__,flush=True)
                if args.once:
                    raise SystemExit(1)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = state_path.with_suffix('.tmp')
        fd = os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as file:
            json.dump(state, file, ensure_ascii=False)
        temporary.replace(state_path)
        if args.once:
            break
        time.sleep(max(1, 30-(time.monotonic()-started)))


if __name__ == '__main__':
    main()
