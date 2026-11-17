#!/usr/bin/env python3
import concurrent.futures, datetime, json, os, re, secrets, shlex, shutil, socket, subprocess, sys, threading, time, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
def configured_url():
    url=os.environ.get('COOLIFY_URL','')
    if not url:
        try: url=json.loads((ROOT/'settings.json').read_text()).get('coolify_url','')
        except (OSError,ValueError): pass
    url=url.rstrip('/')
    parsed=urllib.parse.urlsplit(url)
    if not parsed.hostname or parsed.scheme!='https' or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Configurez une adresse HTTPS Coolify dans settings.json ou COOLIFY_URL.')
    return url

PORT = 8765
KEY = secrets.token_urlsafe(24)
try: KEYCHAIN_SERVICE=json.loads((ROOT/'settings.json').read_text()).get('keychain_service','buildscope.coolify')
except (OSError,ValueError): KEYCHAIN_SERVICE='buildscope.coolify'
KEYCHAIN_ACCOUNT = 'lecture-coolify'

def saved_token():
    result=subprocess.run(['/usr/bin/security','find-generic-password','-s',KEYCHAIN_SERVICE,'-a',KEYCHAIN_ACCOUNT,'-w'],capture_output=True,text=True,timeout=20)
    return result.stdout.strip() if result.returncode == 0 else ''

def save_token(token):
    # Supply the token on standard input, not as a process argument.
    args=['add-generic-password','-U','-s',KEYCHAIN_SERVICE,'-a',KEYCHAIN_ACCOUNT,'-w',token]
    result=subprocess.run(['/usr/bin/security','-i'],input=' '.join(shlex.quote(a) for a in args)+'\n',capture_output=True,text=True,timeout=30)
    if result.returncode != 0 or saved_token()!=token: raise RuntimeError('Impossible d’enregistrer le jeton dans le trousseau macOS.')

def get(path, token):
    req = urllib.request.Request(configured_url()+'/api/v1' + path, headers={'Authorization': 'Bearer '+token, 'Accept':'application/json', 'User-Agent':'BuildScope/1.0'})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)

def rows(x):
    if isinstance(x, list): return x
    if isinstance(x, dict):
        for k in ('data','deployments','items'):
            if isinstance(x.get(k), list): return x[k]
    return []

def text_log(v):
    if isinstance(v, str):
        try: v=json.loads(v)
        except (ValueError,TypeError): return v
    if isinstance(v,list):
        return '\n'.join((str(x.get('timestamp', x.get('created_at',''))) + ' ' + str(x.get('output', x.get('message','')))) if isinstance(x,dict) else str(x) for x in v)
    return ''

def metric(log):
    # Progress output reports per-layer cumulative downloaded bytes; repeated lines are not additive.
    sizes={}; ts=[]
    pattern=re.compile(r'(?P<id>[a-f0-9]{8,64}):?\s+Downloading\s+\[[^]]*\]\s+(?P<n>[\d.]+)\s*(?P<u>kB|MB|GB|B)\s*/',re.I)
    for line in re.split(r'[\r\n]+',log):
        m=pattern.search(line)
        if m:
            size=float(m['n'])*{'b':1,'kb':1000,'mb':1000000,'gb':1000000000}[m['u'].lower()]
            sizes[m['id']]=max(size,sizes.get(m['id'],0))
            tm=re.search(r'(\d{4}-[A-Za-z]{3}-\d{2} \d\d:\d\d:\d\d|\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d)',line)
            if tm:
                try:
                    value=tm.group(1)
                    ts.append(datetime.datetime.fromisoformat(value) if value[5:7].isdigit() else datetime.datetime.strptime(value,'%Y-%b-%d %H:%M:%S'))
                except ValueError: pass
    return {'download_bytes_estimate':round(sum(sizes.values())) if sizes else None, 'download_seconds_observed':int((max(ts)-min(ts)).total_seconds()) if len(ts)>1 else None,
            'builder': 'Mac' if 'Found a suitable build server (mac-colima-build)' in log else None}

def local_observations():
    lima=shutil.which('limactl')
    if not lima: return {},{}
    env={**os.environ,'LIMA_HOME':str(Path.home()/'.colima/_lima')}
    def call(args):
        try:
            return subprocess.run([lima,'shell','colima','--',*args],env=env,cwd='/private/tmp',capture_output=True,text=True,timeout=12,check=True).stdout
        except (OSError,subprocess.SubprocessError): return ''
    images={}
    for line in call(['docker','image','ls','--format','{{json .}}']).splitlines():
        try:
            image=json.loads(line); match=re.fullmatch(r'([0-9.]+)\s*(kB|MB|GB|B)',image.get('Size',''),re.I)
            if match: images[(image.get('Repository'),image.get('Tag'))]=round(float(match[1])*{'b':1,'kb':1000,'mb':1000000,'gb':1000000000}[match[2].lower()])
        except (ValueError,TypeError): pass
    transfers={}
    for line in call(['sudo','cat','/var/lib/coolify-build-report/transfers.jsonl']).splitlines():
        try:
            data=json.loads(line)
            if data.get('deployment_id'): transfers[data['deployment_id']]=data
        except (ValueError,TypeError): pass
    return images,transfers

def build_machine_metrics():
    lima=shutil.which('limactl')
    if not lima: raise RuntimeError('Outil Colima indisponible')
    program='''import json,time,shutil,subprocess
def cpu():
 a=[int(x) for x in open('/proc/stat').readline().split()[1:9]]
 return sum(a),a[3]+a[4]
a,b=cpu();time.sleep(0.3);c,d=cpu()
m={}
for line in open('/proc/meminfo'):
 p=line.split()
 if len(p)>1 and p[0] in ('MemTotal:','MemAvailable:'):m[p[0][:-1]]=int(p[1])*1024
total=m['MemTotal'];used=total-m['MemAvailable']
disk=shutil.disk_usage('/var/lib/docker')
info={'cpu_percent':round(100*(1-(d-b)/(c-a)),1) if c>a else None,'memory_used_bytes':used,'memory_total_bytes':total,'memory_percent':round(100*used/total,1),'disk_used_bytes':disk.used,'disk_total_bytes':disk.total,'disk_free_bytes':disk.free}
try:
 p=subprocess.run(['docker','system','df','--format','{{json .}}'],capture_output=True,text=True,timeout=7,check=True)
 for line in p.stdout.splitlines():
  x=json.loads(line)
  if x.get('Type')=='Build Cache':info['cache_size_display']=x.get('Size');info['cache_reclaimable_display']=x.get('Reclaimable')
  if x.get('Type')=='Images':info['images_size_display']=x.get('Size')
 p=subprocess.run(['docker','ps','-q'],capture_output=True,text=True,timeout=3,check=True)
 info['busy']=bool(p.stdout.strip())
except (OSError,ValueError,subprocess.SubprocessError):pass
print(json.dumps(info))'''
    env={**os.environ,'LIMA_HOME':str(Path.home()/'.colima/_lima')}
    result=subprocess.run([lima,'shell','colima','--','python3','-c',program],env=env,cwd='/private/tmp',capture_output=True,text=True,timeout=15,check=True)
    values=json.loads(result.stdout)
    if not isinstance(values,dict) or not 0<=values['memory_percent']<=100: raise ValueError('Mesure mémoire invalide')
    return values

def report(token):
    apps=rows(get('/applications',token))
    images,transfers=local_observations()
    try: builders=[s for s in rows(get('/servers',token)) if (s.get('settings') or {}).get('is_build_server')]
    except Exception: builders=[]
    def one(app):
        uid=app.get('uuid','')
        if not re.fullmatch(r'[A-Za-z0-9-]+',str(uid)): return [], 'Application ignorée : identifiant invalide'
        try:
            data=get('/deployments/applications/'+uid+'?take=15',token)
            items=rows(data)
            clean=[]
            for d in items:
                if not isinstance(d,dict): continue
                # Avoid returning secrets, configuration or raw logs to the browser.
                ident=d.get('deployment_uuid') or d.get('uuid')
                if not ident: continue
                m=metric(text_log(d.get('logs')))
                build_server_id=d.get('build_server_id')
                if build_server_id and len(builders)==1: m['builder']=builders[0].get('name')
                elif build_server_id: m['builder']='Serveur distant'
                elif 'build_server_id' in d and d.get('status') not in ('queued','in_progress','pending'): m['builder']=d.get('server_name')
                tag=d.get('docker_registry_image_tag') or d.get('commit')
                transfer=transfers.get(str(ident),{})
                clean.append({'id':str(ident),'application':app.get('name') or app.get('git_repository') or uid,
                    'status':d.get('status','unknown'),'created_at':d.get('created_at'),'updated_at':d.get('updated_at'),
                    'server':d.get('server_name'), 'commit':str(d.get('commit') or '')[:8],
                    'image_size_bytes':transfer.get('image_size_bytes') or (images.get((app.get('docker_registry_image_name'),tag)) if d.get('status') in ('finished','success') else None),
                    'upload_seconds':transfer.get('duration_seconds'), 'upload_tx_estimate':transfer.get('network_tx_bytes_estimate'),
                    'url':d.get('deployment_url') if str(d.get('deployment_url','')).startswith(configured_url()+'/') else None, **m})
            return clean,None
        except Exception as e: return [], str(e)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        results=list(pool.map(one,apps))
    history=[d for group,_ in results for d in group]
    history.sort(key=lambda d:str(d.get('created_at') or ''),reverse=True)
    def details(d):
        try:
            if not re.fullmatch(r'[A-Za-z0-9-]+',d['id']): return
            detail=get('/deployments/'+d['id'],token)
            if isinstance(detail,dict) and detail.get('logs'):
                measured=metric(text_log(detail['logs']))
                for key,value in measured.items():
                    if value is not None: d[key]=value
        except Exception: pass
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(details,history[:15]))
    return {'applications':len(apps),'deployments':history,'errors':sum(1 for _,err in results if err),'updated_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass
    def send(self,code,content,ctype='application/json; charset=utf-8'):
        if not isinstance(content,bytes): content=json.dumps(content,ensure_ascii=False).encode()
        self.send_response(code); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(content))); self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"); self.end_headers(); self.wfile.write(content)
    def do_GET(self):
        if self.path != '/': return self.send(404,{'error':'Introuvable'})
        html=(ROOT/'index.html').read_text().replace('__SESSION_KEY__',KEY).encode(); self.send(200,html,'text/html; charset=utf-8')
    def do_POST(self):
        if self.path not in ('/report','/configure','/metrics','/cleanup','/deployment-logs') or self.headers.get('X-Local-Key')!=KEY or self.headers.get('Origin')!=f'http://127.0.0.1:{PORT}': return self.send(403,{'error':'Requête refusée'})
        try:
            n=int(self.headers.get('Content-Length','0'))
            if n<=0 or n>4096: raise ValueError('requête invalide')
            payload=json.loads(self.rfile.read(n))
            token=payload.get('token','')
            if self.path=='/metrics':
                try: return self.send(200,{'available':True,**build_machine_metrics()})
                except (OSError,ValueError,KeyError,subprocess.SubprocessError): return self.send(200,{'available':False})
            if self.path=='/cleanup':
                lima=shutil.which('limactl')
                if not lima: raise RuntimeError('Machine de compilation indisponible')
                env={**os.environ,'LIMA_HOME':str(Path.home()/'.colima/_lima')}
                cmd=['sh','-c','if [ -n "$(docker ps -q)" ]; then echo BUSY; else docker builder prune --all --force --keep-storage 8GB; fi']
                result=subprocess.run([lima,'shell','colima','--',*cmd],env=env,cwd='/private/tmp',capture_output=True,text=True,timeout=90,check=True)
                return self.send(200,{'busy':result.stdout.strip()=='BUSY','message':'Nettoyage terminé ou reporté si une compilation a démarré.'})
            if self.path=='/configure':
                if not isinstance(token,str) or not 10<=len(token)<=1024 or '\n' in token: raise ValueError('jeton invalide')
                get('/applications',token)
                save_token(token)
                return self.send(200,{'saved':True})
            token=saved_token()
            if not token: return self.send(401,{'error':'Connectez Coolify pour commencer.'})
            if self.path=='/deployment-logs':
                ident=payload.get('id')
                if not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9-]{1,100}',ident): raise ValueError('Identifiant de déploiement invalide.')
                detail=get('/deployments/'+ident,token)
                if not isinstance(detail,dict): raise ValueError('Réponse Coolify invalide.')
                return self.send(200,{'logs':text_log(detail.get('logs'))})
            return self.send(200,report(token))
        except urllib.error.HTTPError as e:
            return self.send(502,{'error':f'Coolify a refusé la requête (HTTP {e.code}). Vérifiez le jeton et les permissions API.'})
        except Exception as e:
            return self.send(502,{'error':'Impossible de joindre Coolify ou de lire ses données : '+str(e)[:200]})

if __name__=='__main__':
    server=ThreadingHTTPServer(('127.0.0.1',PORT),Handler)
    print(f'Tableau de bord : http://127.0.0.1:{PORT}/',flush=True)
    server.serve_forever()
