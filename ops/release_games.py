"""Deploy the verified Worker bundle using Cloudflare's direct-upload API.

Credentials stay outside the source tree. Never print API or asset-upload tokens.
"""
import base64
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import tomllib

import requests

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = Path('/root/.config/cf-server-monitor/credentials.json')
credentials = json.loads(PRIVATE.read_text())
auth = tomllib.loads(Path('/root/.config/.wrangler/config/default.toml').read_text())
account = credentials['account_id']
worker = credentials['worker_name']
base = f'https://api.cloudflare.com/client/v4/accounts/{account}'
session = requests.Session()
session.trust_env = False
session.proxies = {'https': os.environ.get('CFSM_PROXY', 'http://192.168.9.11:2080')}
token = auth['oauth_token']


def api(method, path, bearer=None, **kwargs):
    response = session.request(
        method, base + path,
        headers={'Authorization': 'Bearer ' + (bearer or token)},
        timeout=(15, 180), **kwargs,
    )
    try:
        data = response.json()
    except ValueError:
        raise RuntimeError(f'{method} {path}: non-JSON HTTP {response.status_code}')
    if not data.get('success'):
        # Cloudflare errors contain validation details, never the request body.
        raise RuntimeError(f'{method} {path}: HTTP {response.status_code}: {data.get("errors")}')
    return data.get('result')


settings = api('GET', f'/workers/scripts/{worker}/settings')
# Keep a private rollback bundle and prior deployment identity before replacing code.
backup = Path('/root/.config/cf-game-monitor/rollback')
backup.mkdir(mode=0o700, exist_ok=True)
response = session.get(base + f'/workers/scripts/{worker}', headers={'Authorization': 'Bearer '+token}, timeout=(15,90))
response.raise_for_status()
(backup/'worker-before.multipart').write_bytes(response.content)
(backup/'content-type.txt').write_text(response.headers['Content-Type'])
(backup/'settings.json').write_text(json.dumps(settings))
for file in backup.iterdir(): file.chmod(0o600)
print('Previous Worker saved privately for rollback.', flush=True)

manifest = {}
files_by_hash = {}
for file in sorted((ROOT / 'dist').rglob('*')):
    if not file.is_file():
        continue
    content = file.read_bytes()
    digest = hashlib.sha256(content).hexdigest()[:32]
    manifest['/' + file.relative_to(ROOT / 'dist').as_posix()] = {
        'hash': digest, 'size': len(content),
    }
    files_by_hash[digest] = file

upload = api('POST', f'/workers/scripts/{worker}/assets-upload-session', json={'manifest': manifest})
upload_token = upload['jwt']
completion_token = upload_token if not upload['buckets'] else None
print(f'Asset manifest accepted: {len(manifest)} files, {len(upload["buckets"])} upload batches.', flush=True)
for index, bucket in enumerate(upload['buckets'], 1):
    parts = []
    for digest in bucket:
        file = files_by_hash[digest]
        mime = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
        parts.append((digest, (digest, base64.b64encode(file.read_bytes()), mime)))
    result = api('POST', '/workers/assets/upload?base64=true', bearer=upload_token, files=parts)
    if result and result.get('jwt'):
        completion_token = result['jwt']
    print(f'Asset batch {index}/{len(upload["buckets"])} uploaded.', flush=True)
if not completion_token:
    raise RuntimeError('Cloudflare did not return the asset completion token.')

metadata = {
    'main_module': 'index.js',
    'compatibility_date': '2024-12-01',
    'compatibility_flags': ['nodejs_compat'],
    'assets': {'jwt': completion_token},
    'bindings': [
        {'type': 'assets', 'name': 'ASSETS'},
        {'type': 'd1', 'name': 'DB', 'id': credentials['database_id']},
        {'type': 'durable_object_namespace', 'name': 'METRICS_BROADCASTER', 'class_name': 'MetricsBroadcaster', 'namespace_id': next(b['namespace_id'] for b in settings['bindings'] if b['name']=='METRICS_BROADCASTER')},
        {'type': 'secret_text', 'name': 'API_SECRET', 'text': credentials['api_secret']},
        {'type': 'secret_text', 'name': 'GAME_REPORT_SECRET', 'text': json.loads(Path('/root/.config/cf-game-monitor/config.json').read_text())['report_secret']},
    ],
    'keep_bindings': ['secret_text'],
}
result = api('PUT', f'/workers/scripts/{worker}', files=[
    ('metadata', (None, json.dumps(metadata), 'application/json')),
    ('index.js', ('index.js', Path('/tmp/cfsm-game-bundle/index.js').read_bytes(), 'application/javascript+module')),
])
print('Worker uploaded:', result.get('id'), 'etag:', result.get('etag'), flush=True)
print('Existing routes and schedules preserved.', flush=True)
