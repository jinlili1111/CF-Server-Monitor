"""Create additive game tables and bind approved games to existing server IDs.

Reads deployment credentials and collector inventory from private external files.
Never guesses server identity from the public IP of a PVE guest.
"""
import json
from pathlib import Path
import tomllib
import requests

root=Path(__file__).resolve().parents[1]
c=json.loads(Path('/root/.config/cf-server-monitor/credentials.json').read_text())
a=tomllib.loads(Path('/root/.config/.wrangler/config/default.toml').read_text())
config=json.loads(Path('/root/.config/cf-game-monitor/config.json').read_text())
nodes=json.loads(Path('/root/.config/cf-server-monitor/enrollment.json').read_text())['nodes']
ids={n['ip']:n['id'] for n in nodes}
s=requests.Session();s.trust_env=False;s.proxies={'https':'http://192.168.9.11:2080'}
url=f"https://api.cloudflare.com/client/v4/accounts/{c['account_id']}/d1/database/{c['database_id']}/query"
def query(sql,params=None):
 r=s.post(url,headers={'Authorization':'Bearer '+a['oauth_token']},json={'sql':sql,'params':params or []},timeout=(15,90));r.raise_for_status();result=r.json()
 if not result.get('success'):raise RuntimeError('D1 query failed')
 return result['result']
query((root/'migrations/0001_games.sql').read_text())
for g in config['games']:
 server_id=ids.get(g['host'])
 if not server_id and g['host'].startswith('192.168.9.'):server_id=ids['192.168.9.2']
 if not server_id:raise RuntimeError('Unmapped game '+g['id'])
 query('INSERT INTO game_services(id,name,server_id,online_source) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,server_id=excluded.server_id,online_source=excluded.online_source',[g['id'],g['name'],server_id,g['source']])
 print('Registered:',g['id'],flush=True)
