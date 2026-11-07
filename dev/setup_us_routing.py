#!/usr/bin/env python3
"""Prepare USA regional OSRM data and run the standard local road-routing engine."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import time
from urllib.request import urlopen, build_opener, ProxyHandler

STATES=set('alabama alaska arizona arkansas california colorado connecticut delaware district-of-columbia florida georgia hawaii idaho illinois indiana iowa kansas kentucky louisiana maine maryland massachusetts michigan minnesota mississippi missouri montana nebraska nevada new-hampshire new-jersey new-mexico new-york north-carolina north-dakota ohio oklahoma oregon pennsylvania rhode-island south-carolina south-dakota tennessee texas utah vermont virginia washington west-virginia wisconsin wyoming us'.split())
IMAGE='ghcr.io/project-osrm/osrm-backend@sha256:729461bcc9ae9e6aafa92c0f93db9b060a32e85d5e72092c01ae4a4a9f1eb564'
NAME='car-local-routing'
LABEL='org.drive-console.routing'


def execute(args,**kwargs):
    result=subprocess.run([str(a) for a in args],capture_output=True,text=True,**kwargs)
    if result.returncode:raise RuntimeError(result.stderr[-2000:])
    return result.stdout.strip()


def checksum(path):
    h=hashlib.md5()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(2097152),b''):h.update(chunk)
    return h.hexdigest()


def dataset(region,root):
    base='https://download.geofabrik.de/north-america/'+('us' if region=='us' else 'us/'+region)+'-latest.osm.pbf'
    with urlopen(base+'.md5',timeout=30) as response:expected=response.read(1024).decode().split()[0].lower()
    if len(expected)!=32 or any(c not in '0123456789abcdef' for c in expected.lower()):raise ValueError('Invalid publisher checksum')
    path=root/(region+'.osm.pbf')
    if path.is_file() and checksum(path)==expected:return path
    pending=path.with_suffix('.pbf.pending')
    print('Downloading '+region+' from Geofabrik; this may take time.',flush=True)
    try:
        with urlopen(base,timeout=60) as response,pending.open('wb') as stream:
            while chunk:=response.read(2097152):stream.write(chunk)
        if checksum(pending)!=expected:raise ValueError('Dataset checksum failed; retry with a fresh publisher file')
        os.replace(pending,path)
    finally:pending.unlink(missing_ok=True)
    return path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region',nargs='+',choices=sorted(STATES),default=['us'])
    parser.add_argument('--directory',type=Path,default=Path.home()/'.local/share/drive-console/routing')
    parser.add_argument('--serve-only',action='store_true',help='Use a prepared pack without downloading or preprocessing it')
    parser.add_argument('--offline',action='store_true',help='Start only an already prepared pack and cached/carried image')
    parser.add_argument('--export-image',action='store_true',help='Carry the verified routing image alongside the data pack')
    args=parser.parse_args()
    if 'us' in args.region and len(args.region)>1:parser.error('Use either full USA or a list of states')
    root=args.directory.expanduser().resolve();root.mkdir(parents=True,exist_ok=True)
    # Never fill an 8 GB SD or accidentally attempt a whole-USA build on a small Tiny.
    reserve=150*1024**3 if args.region==['us'] else 10*1024**3
    memory=os.sysconf('SC_PAGE_SIZE')*os.sysconf('SC_PHYS_PAGES')
    if not args.serve_only and shutil.disk_usage(root).free<reserve:parser.error('Not enough free disk: reserve 150 GB for USA or 10 GB for regional builds')
    if not args.serve_only and args.region==['us'] and memory<32*1024**3:parser.error('Full-USA preprocessing needs a larger build host (at least 32 GB RAM); prepare regional data here instead')
    try:
        execute(['docker','info'])
        if args.serve_only:
            prepared=json.loads((root/'prepared.json').read_text())
            image=prepared['image']
            if not re.fullmatch(r'ghcr.io/project-osrm/osrm-backend@sha256:[0-9a-f]{64}',image):raise ValueError('Unexpected routing image identity')
            if prepared['regions']!=args.region:raise ValueError('Prepared pack regions differ from selected regions')
            if not all((root/('region.osrm.'+suffix)).is_file() for suffix in ('partition','cells','mldgr')):raise ValueError('Prepared routing pack is incomplete')
            if args.offline:
                image_id=prepared.get('imageId','')
                if not re.fullmatch(r'sha256:[0-9a-f]{64}',image_id):raise ValueError('Prepared pack has no verified image config identity')
                cached=subprocess.run(['docker','image','inspect',image_id],capture_output=True,text=True)
                if cached.returncode:
                    archive=root/'osrm-image.tar'
                    expected=prepared.get('runtimeSha256')
                    if not archive.is_file() or archive.is_symlink() or not expected:raise ValueError('Routing runtime is not cached or carried')
                    digest=hashlib.sha256()
                    with archive.open('rb') as stream:
                        for chunk in iter(lambda:stream.read(2097152),b''):digest.update(chunk)
                    if digest.hexdigest()!=expected:raise ValueError('Carried routing runtime checksum mismatch')
                    execute(['docker','load','-i',archive])
                execute(['docker','image','inspect',image_id]);image=image_id
            else:execute(['docker','pull',image])
        else:
            if len(args.region)>1 and not shutil.which('osmium'):raise RuntimeError('Install osmium-tool before preparing multiple states')
            files=[dataset(region,root) for region in dict.fromkeys(args.region)]
            pbf=root/'region.osm.pbf'
            if len(files)==1:
                if pbf.is_symlink() or pbf.exists():pbf.unlink()
                pbf.hardlink_to(files[0])
            else:execute(['osmium','merge',*files,'-o',pbf,'--overwrite'])
            execute(['docker','pull',IMAGE])
            info=json.loads(execute(['docker','image','inspect',IMAGE]))[0]
            image=next((value for value in info.get('RepoDigests',[]) if value.startswith('ghcr.io/project-osrm/osrm-backend@sha256:')),None)
            if not image:raise RuntimeError('Could not pin the official OSRM image digest')
            marker=root/'prepared.json'
            identity={'regions':args.region,'pbfMd5':checksum(pbf),'image':image,'imageId':info['Id']}
            prepared=json.loads(marker.read_text()) if marker.exists() else None
            common=['docker','run','--rm','--user',f'{root.stat().st_uid}:{root.stat().st_gid}','--network','none','--cap-drop','ALL','--security-opt','no-new-privileges','--volume',str(root)+':/data']
            if identity!=prepared or not (root/'region.osrm.partition').exists():
                for command in (['osrm-extract','-p','/opt/car.lua','/data/region.osm.pbf'],['osrm-partition','/data/region.osrm'],['osrm-customize','/data/region.osrm']):
                    print('Preparing routing: '+command[0],flush=True)
                    execute([*common,image,*command])
                marker.write_text(json.dumps(identity,indent=2))
        if args.export_image:
            archive=root/'osrm-image.tar';execute(['docker','save','-o',archive,image])
            metadata=json.loads((root/'prepared.json').read_text());digest=hashlib.sha256()
            with archive.open('rb') as stream:
                for chunk in iter(lambda:stream.read(2097152),b''):digest.update(chunk)
            metadata['runtimeSha256']=digest.hexdigest();(root/'prepared.json').write_text(json.dumps(metadata,indent=2))
        existing=subprocess.run(['docker','container','inspect',NAME],capture_output=True,text=True)
        if existing.returncode==0:
            container=json.loads(existing.stdout)[0]
            if container['Config'].get('Labels',{}).get(LABEL)!='true':raise RuntimeError('Routing container name belongs to another application')
            execute(['docker','rm','-f',NAME])
        execute(['docker','run','-d','--user',f'{root.stat().st_uid}:{root.stat().st_gid}','--name',NAME,'--label',LABEL+'=true','--restart','unless-stopped','--cap-drop','ALL','--security-opt','no-new-privileges','--publish','127.0.0.1:5000:5000','--volume',str(root)+':/data:ro',image,'osrm-routed','--algorithm','mld','--mmap=true','/data/region.osrm'])
        client=build_opener(ProxyHandler({}));deadline=time.monotonic()+60
        while True:
            try:
                with client.open('http://127.0.0.1:5000/nearest/v1/driving/-98.35,39.5?number=1',timeout=2) as response:
                    if response.status==200:break
            except OSError:pass
            if time.monotonic()>deadline:
                execute(['docker','stop',NAME])
                raise RuntimeError('Routing container did not become ready and was stopped; inspect docker logs car-local-routing')
            time.sleep(.5)
        print('Local road routing installed on 127.0.0.1:5000. Source: OpenStreetMap contributors / Geofabrik, ODbL. Map imagery is a separate regional raster tile pack.')
    except (OSError,ValueError,KeyError,TypeError,RuntimeError,subprocess.SubprocessError) as exc:parser.exit(1,str(exc)+'\n')


if __name__=='__main__':main()
