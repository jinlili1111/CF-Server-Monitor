"""Manage one isolated preferred-edge entry; never replace the primary hostname.

DNS credentials and rollback IDs are read/written outside the repository.
This is an operational experiment, validated using the actual application.
"""
import argparse
import json
import os
from pathlib import Path
import tomllib
import requests

HOST = 'monitor-fast.jinli90.com'
ZONE = '98ed26def96974a54381f972fcc22c01'
PRIVATE = Path('/root/.config/cf-game-monitor')
state_file = PRIVATE/'edge-entry-state.json'

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['prepare','prefer','revert','status'])
    parser.add_argument('--ip',default='104.18.0.1')
    parser.add_argument('--backup-ip',default='104.16.10.1')
    args=parser.parse_args()
    auth=json.loads((PRIVATE/'dns-auth.json').read_text())
    oauth=tomllib.loads(Path('/root/.config/.wrangler/config/default.toml').read_text())
    session=requests.Session();session.trust_env=False
    session.proxies={'https':os.environ.get('CFSM_PROXY','http://192.168.9.11:2080')}
    def api(method,path,data=None,dns=False):
        response=session.request(method,'https://api.cloudflare.com/client/v4/zones/'+ZONE+path,
          headers={'Authorization':'Bearer '+(auth['token'] if dns else oauth['oauth_token'])},json=data,timeout=(10,40))
        result=response.json()
        if not result.get('success'):raise RuntimeError(f'{method} {path}: HTTP {response.status_code}')
        return result.get('result')
    if args.action=='prepare':
        if state_file.exists():raise RuntimeError('Entry state already exists; inspect status before changes')
        records=api('GET','/dns_records?name='+HOST,dns=True)
        routes=api('GET','/workers/routes')
        if records or any(r['pattern']==HOST+'/*' for r in routes):raise RuntimeError('Hostname already configured; refusing to overwrite')
        record=api('POST','/dns_records',{'type':'A','name':HOST,'content':'192.0.2.1','proxied':True,'ttl':1},dns=True)
        state={'hostname':HOST,'record_id':record['id'],'route_id':None,'created_by':'cf-game-monitor'}
        state_file.write_text(json.dumps(state,indent=2));state_file.chmod(0o600)
        route=api('POST','/workers/routes',{'pattern':HOST+'/*','script':'cf-server-monitor'})
        state['route_id']=route['id'];state_file.write_text(json.dumps(state,indent=2))
        print('Isolated entry prepared:',HOST)
    else:
        state=json.loads(state_file.read_text())
        if state['hostname']!=HOST:raise RuntimeError('Unexpected entry ownership')
        current=api('GET','/dns_records/'+state['record_id'],dns=True)
        if current['name']!=HOST:raise RuntimeError('DNS ownership mismatch')
        if args.action=='prefer':
            # Only pretested Cloudflare VIPs, never arbitrary caller supplied origins.
            approved=['104.16.0.1','104.16.10.1','104.18.0.1','104.21.42.117','172.67.161.215']
            if args.ip not in approved or args.backup_ip not in approved or args.ip==args.backup_ip:raise RuntimeError('IPs not in approved test set')
            current=api('PATCH','/dns_records/'+state['record_id'],{'type':'A','name':HOST,'content':args.ip,'proxied':False,'ttl':120},dns=True)
            extras=state.get('extra_record_ids',[])
            if extras:
                extra=api('GET','/dns_records/'+extras[0],dns=True)
                if extra['name']!=HOST:raise RuntimeError('Backup DNS ownership mismatch')
                api('PATCH','/dns_records/'+extras[0],{'type':'A','name':HOST,'content':args.backup_ip,'proxied':False,'ttl':120},dns=True)
            else:
                extra=api('POST','/dns_records',{'type':'A','name':HOST,'content':args.backup_ip,'proxied':False,'ttl':120},dns=True)
                state['extra_record_ids']=[extra['id']]
                state_file.write_text(json.dumps(state,indent=2))
        elif args.action=='revert':
            current=api('PATCH','/dns_records/'+state['record_id'],{'type':'A','name':HOST,'content':'192.0.2.1','proxied':True,'ttl':1},dns=True)
            for record_id in state.get('extra_record_ids',[]):
                extra=api('GET','/dns_records/'+record_id,dns=True)
                if extra['name']!=HOST:raise RuntimeError('Backup DNS ownership mismatch')
                api('DELETE','/dns_records/'+record_id,dns=True)
            state['extra_record_ids']=[]
            state_file.write_text(json.dumps(state,indent=2))
        records=api('GET','/dns_records?name='+HOST,dns=True)
        print(json.dumps([{k:r.get(k) for k in ['name','type','content','proxied','ttl']} for r in records]))

if __name__=='__main__':main()
