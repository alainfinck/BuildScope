#!/usr/bin/env python3
"""Collect local Docker push timing without saving command lines or credentials."""
import datetime,json,os,re,subprocess,time
from pathlib import Path
ROOT=Path('/var/lib/coolify-build-report')
LOG=ROOT/'transfers.jsonl'
INTERFACE='eth0'

def run(args):
    try:
        return subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=8,check=True).stdout.strip()
    except (OSError,subprocess.SubprocessError): return ''

def tx():
    try: return int(Path('/sys/class/net',INTERFACE,'statistics/tx_bytes').read_text())
    except (OSError,ValueError): return None

def helper():
    names=run(['docker','ps','--format','{{.Names}}']).splitlines()
    candidates=[x for x in names if re.fullmatch('[a-z0-9]{20,32}',x)]
    return candidates[0] if len(candidates)==1 else None

def pushes():
    found={}
    for p in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            args=p.read_bytes().decode('utf-8','replace').split('\0')
            if os.path.basename(args[0])!='docker': continue
            if 'push' not in args[1:5]: continue
            images=[x for x in args[1:] if re.fullmatch(r'(?:[A-Za-z0-9.-]+(?::[0-9]+)?/)[A-Za-z0-9_./:-]+',x)]
            if not images: continue
            found[int(p.parent.name)]=images[-1]
        except (OSError,ValueError): pass
    return found

def image_size(image):
    result=run(['docker','image','inspect','--format','{{.Size}}',image])
    return int(result) if result.isdigit() else None

def main():
    ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
    active={}
    while True:
        now=time.time(); current=pushes()
        for pid,image in current.items():
            if pid not in active:
                active[pid]={'deployment_id':helper(),'image':image,'started_at':datetime.datetime.fromtimestamp(now,datetime.timezone.utc).isoformat(),'start_epoch':now,'tx0':tx(),'image_size_bytes':image_size(image)}
        for pid in list(active):
            if pid not in current:
                item=active.pop(pid); last=tx(); first=item.pop('tx0'); start=item.pop('start_epoch')
                item.update({'duration_seconds':round(now-start,1),'network_tx_bytes_estimate':max(0,last-first) if first is not None and last is not None else None,'scope':'Trafic sortant total de la VM pendant la commande docker push','result':'non vérifié','completed_at':datetime.datetime.fromtimestamp(now,datetime.timezone.utc).isoformat()})
                with LOG.open('a',encoding='utf8') as f: f.write(json.dumps(item,ensure_ascii=False)+'\n')
                os.chmod(LOG,0o600)
        time.sleep(1)
if __name__=='__main__': main()
