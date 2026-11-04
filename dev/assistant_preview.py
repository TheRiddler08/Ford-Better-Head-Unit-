#!/usr/bin/env python3
"""Isolated Car Assistant preview for WSL2/Docker Desktop and Linux."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from deploy import home_assistant as ha

NAME='car-assistant-preview'
VOLUME='car-assistant-preview-config'
LABEL='org.drive-console.preview'
REVISION_LABEL=LABEL+'.revision'
BASE='ghcr.io/home-assistant/home-assistant@sha256:23f4dfbb69ce76315dbc4de7915504f1c2dcecd7ca2648113a493a385972e133'
URL='http://127.0.0.1:18123'

# Run only in the isolated preview container, without host mounts or networking.
# Update unchanged managed files; never replace saved accounts/dashboard stores.
INITIALIZE=r'''
import hashlib,json,os,sys,tempfile
from pathlib import Path
root=Path(os.getenv('CAR_PREVIEW_CONFIG','/config'))
payload=json.load(sys.stdin)
registry=root/'.car-preview-managed.json'
if registry.is_symlink():raise ValueError('Unsafe preview registry path')
previous=json.loads(registry.read_text()) if registry.exists() else {}
for name,content in payload['files'].items():
    path=root/name
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Unsafe preview config path')
    raw=content.encode()
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()!=previous.get(name):
        continue
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as pending:
        pending.write(raw);temp=Path(pending.name)
    temp.chmod(0o600);os.replace(temp,path)
    previous[name]=hashlib.sha256(raw).hexdigest()
registry.write_text(json.dumps(previous));registry.chmod(0o600)
exec(payload['migration'])
'''


def docker(*args,data=None):
    result=subprocess.run(['docker',*map(str,args)],input=data,stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)
    if result.returncode:raise RuntimeError(result.stderr.decode(errors='replace').strip())
    return result.stdout


def existing():
    try:result=docker('container','inspect',NAME)
    except RuntimeError as exc:
        if 'no such' in str(exc).lower():return None
        raise
    info=json.loads(result)[0]
    if (info.get('Config',{}).get('Labels') or {}).get(LABEL)!='true':
        raise ValueError('That container name belongs to another application; leaving it alone')
    return info


def launch():
    client=build_opener(ProxyHandler({}))
    started=time.monotonic()
    docker('start',NAME)
    deadline=started+120
    while time.monotonic()<deadline:
        try:
            with client.open(URL+'/manifest.json',timeout=1) as response:
                manifest=json.load(response)
                identity=' '.join(str(manifest.get(key,'')) for key in ('name','short_name')).lower().replace(' ','')
                if response.status==200 and ('homeassistant' in identity or 'carassistant' in identity):
                    elapsed=time.monotonic()-started
                    print(f'Frontend HTTP ready in {elapsed:.2f} seconds from container start.')
                    print('Open http://localhost:18123/drive-console in your Windows browser.')
                    print('First run: create a separate test owner account. GPS/OBD data stays unavailable.')
                    return elapsed
        except (OSError,URLError,ValueError):pass
        info=existing()
        if not info or not info['State']['Running']:
            raise RuntimeError('Preview stopped. Inspect: docker logs --tail 60 '+NAME)
        time.sleep(.2)
    raise RuntimeError('Frontend not ready within 120 seconds. Inspect: docker logs --tail 60 '+NAME)


def profile():
    files={name:raw.decode() for name,raw in ha.managed_files(ROOT,'America/New_York').items()}
    config=json.loads(files['configuration.yaml'])
    config['http']['server_host']='0.0.0.0'  # Container only; host publication is loopback.
    config['homeassistant']['name']='Car Assistant preview'
    files['configuration.yaml']=json.dumps(config,indent=2)+'\n'
    revision=hashlib.sha256(json.dumps({'runtime':ha.FORK_ARCHIVE_SHA,'files':files},sort_keys=True).encode()).hexdigest()
    return files,revision


def start():
    files,revision=profile()
    info=existing()
    if info and info['State']['Running'] and info['Config']['Labels'].get(REVISION_LABEL)==revision:
        print('Preview is already running: http://localhost:18123/drive-console')
        print('Use the measure command for a stop/start timing run.')
        return
    docker('info')
    print('Preparing the verified runtime. First download/build can take several minutes.',flush=True)
    try:docker('image','inspect',BASE)
    except RuntimeError as exc:
        if 'no such' not in str(exc).lower():raise
        # Keep normal Docker Desktop credentials, proxy and certificate settings.
        subprocess.run(['docker','pull','--platform','linux/amd64',BASE],check=True)
    image=ha.car_assistant_image(ROOT,BASE)
    try:
        volume=json.loads(docker('volume','inspect',VOLUME))[0]
        if (volume.get('Labels') or {}).get(LABEL)!='true':
            raise ValueError('That volume belongs to another application; leaving it alone')
    except RuntimeError as exc:
        if 'no such' not in str(exc).lower():raise
        docker('volume','create','--label',LABEL+'=true',VOLUME)
    if info:
        if info['State']['Running']:docker('stop','--time','30',NAME)
        docker('rm',NAME)  # Only this managed preview; its named volume is retained.
    docker('run','--rm','--interactive','--pull=never','--network=none','--volume',VOLUME+':/config',
        '--entrypoint','python3',image,'-c',INITIALIZE,
        data=json.dumps({'files':files,'migration':ha.DASHBOARD_MIGRATION}).encode())
    docker('create','--name',NAME,'--label',LABEL+'=true','--label',REVISION_LABEL+'='+revision,'--pull=never',
        '--publish','127.0.0.1:18123:8123','--volume',VOLUME+':/config',
        '--security-opt','no-new-privileges=true','--env','TZ=America/New_York',image)
    launch()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('start','measure','stop','status'),nargs='?',default='start')
    args=parser.parse_args()
    try:
        if args.action=='start':start()
        else:
            info=existing()
            if not info:raise ValueError('Preview not installed yet; run this script with start first')
            if args.action=='status':
                print(json.dumps({'running':info['State']['Running'],'image':info['Image'],
                    'url':URL+'/drive-console','configVolume':VOLUME},indent=2))
            else:
                if info['State']['Running']:docker('stop','--time','30',NAME)
                if args.action=='measure':launch()
                else:print('Preview stopped. Saved test dashboard and account are retained.')
    except (OSError,ValueError,RuntimeError,subprocess.CalledProcessError) as exc:
        print('Preview: '+str(exc),file=sys.stderr)
        if isinstance(exc,FileNotFoundError) or 'docker daemon' in str(exc).lower():
            print('Start Docker Desktop and enable Settings → Resources → WSL integration for this distro.',file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':sys.exit(main())
