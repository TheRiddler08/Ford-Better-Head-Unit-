"""Optional integration check against an explicitly supplied disposable Docker daemon.

Use --socket /path/to/test/docker.sock. Refuses the production default socket.
Only a tiny generated image and a stopped container are created; no code runs.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import uuid
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deploy import offline_payload as payload


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--socket',required=True);args=parser.parse_args()
    sock=str(Path(args.socket).resolve())
    if sock==str(Path('/var/run/docker.sock').resolve()):raise SystemExit('Use an isolated disposable daemon socket')
    env=dict(os.environ)
    for key in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):env.pop(key,None)
    def docker(*argv,timeout=600):
        result=subprocess.run(['docker','--host=unix://'+sock,*map(str,argv)],env=env,capture_output=True,timeout=timeout)
        if result.returncode:raise RuntimeError(result.stderr.decode())
        return result.stdout
    unique=uuid.uuid4().hex;tag='ghcr.io/home-assistant/home-assistant:2099.1.'+str(int(unique[:12],16));name='car-identity-'+unique
    with tempfile.TemporaryDirectory() as temp:
        archive=Path(temp)/'image.tar';layer=io.BytesIO()
        with tarfile.open(fileobj=layer,mode='w') as tar:
            data=b'identity fixture';entry=tarfile.TarInfo('fixture');entry.size=len(data);tar.addfile(entry,io.BytesIO(data))
        raw=layer.getvalue();diff='sha256:'+hashlib.sha256(raw).hexdigest()
        config=json.dumps({'architecture':'amd64','os':'linux','config':{'Cmd':['/fixture'],'Labels':{'fixture':unique}},
                           'rootfs':{'type':'layers','diff_ids':[diff]},'history':[{'created_by':'regression'}]}).encode()
        expected='sha256:'+hashlib.sha256(config).hexdigest();config_name=expected[7:]+'.json'
        manifest=[{'Config':config_name,'RepoTags':[tag],'Layers':['layer.tar']}]
        with tarfile.open(archive,'w') as tar:
            for filename,data in [(config_name,config),('layer.tar',raw),('manifest.json',json.dumps(manifest).encode())]:
                entry=tarfile.TarInfo(filename);entry.size=len(data);tar.addfile(entry,io.BytesIO(data))
        try:
            with patch.object(payload,'docker',side_effect=docker):
                actual=payload.ensure_offline_image({'imageId':expected,'reference':tag},archive,proof_cache=None,socket_path=sock)
            docker('create','--name',name,'--pull=never','--network','none',actual)
            container=json.loads(docker('container','inspect',name))[0]
            assert container['Image']==actual
            print('PASS: Docker '+docker('version','--format','{{.Server.Version}}').decode().strip()+
                  ' verified config '+expected+' and created stopped container from '+actual)
        finally:
            try:docker('container','rm','--force',name)
            except RuntimeError:pass
            try:docker('image','rm',tag)
            except RuntimeError:pass


if __name__=='__main__':main()
