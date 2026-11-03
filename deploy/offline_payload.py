"""Verified offline Home Assistant dependencies, installed only by Tiny receiver."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

try:
    from .runtime_assets import release_hashes,sha,safe_path,ROOT_PACKAGES,records
except ImportError:
    # Standalone copies are installed together in a root-owned directory.
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    from runtime_assets import release_hashes,sha,safe_path,ROOT_PACKAGES,records


def run(args,env=None,timeout=600,pass_fds=()):
    p=subprocess.run([str(a) for a in args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,timeout=timeout,pass_fds=pass_fds)
    if p.returncode:raise RuntimeError('Offline dependency operation failed: '+p.stderr.decode(errors='replace')[-2500:])
    return p.stdout


def extract(archive,target):
    target=Path(target);target.mkdir(parents=True,exist_ok=True)
    seen=set();size=0
    with tarfile.open(archive,'r|gz') as tar:
        for entry in tar:
            relative=safe_path(entry.name)
            if entry.name in seen or not (entry.isfile() or entry.isdir()):raise ValueError('Invalid offline repository entry')
            seen.add(entry.name);size+=entry.size
            if len(seen)>20000 or size>12*1024**3:raise ValueError('Offline repository exceeds limits')
            path=target/str(relative)
            if not path.resolve().is_relative_to(target.resolve()):raise ValueError('Archive escapes staging directory')
            if entry.isdir():path.mkdir(parents=True,exist_ok=True);continue
            path.parent.mkdir(parents=True,exist_ok=True)
            with tar.extractfile(entry) as source,path.open('wb') as destination:shutil.copyfileobj(source,destination,1024*1024)
            os.chmod(path,0o644)
    os.chmod(target,0o755)
    for directory in target.rglob('*'):
        if directory.is_dir():os.chmod(directory,0o755)


def verify_repository(repository,keyring='/usr/share/keyrings/ubuntu-archive-keyring.gpg'):
    repository=Path(repository);details=json.loads((repository/'payload.json').read_text())
    version=details.get('version');codename=details.get('codename')
    known={'22.04':'jammy','24.04':'noble','26.04':'resolute'}
    if known.get(version)!=codename or details.get('architecture')!='amd64':raise ValueError('Unexpected Ubuntu package target')
    expected=[codename,codename+'-updates',codename+'-security']
    if details.get('suites')!=expected:raise ValueError('Unexpected repository suites')
    for suite in expected:
        release=repository/'dists'/suite/'InRelease'
        run(['gpgv','--keyring',keyring,release])
        text=release.read_text()
        fields=dict(re.findall(r'^(Origin|Suite|Codename|Version):\s*(.+)$',text,re.M))
        if fields.get('Origin')!='Ubuntu' or fields.get('Suite')!=suite or fields.get('Codename') not in (codename,suite) or not fields.get('Version','').startswith(version):raise ValueError('Ubuntu signed release does not match the Tiny OS')
        signed=release_hashes(text)
        for component in ('main','universe'):
            name=component+'/binary-amd64/Packages'
            if name not in signed or sha(repository/'dists'/suite/name)!=signed[name]:raise ValueError('Ubuntu signed index mismatch')
    # Native APT verifies its own signed Release->Packages->deb chain as well.
    # Pre-check archive bytes before invoking the privileged package manager.
    for record in details['packages']:
        name=str(safe_path(record['Filename']))
        if not name.startswith('pool/') or sha(repository/name)!=record['SHA256']:raise ValueError('Ubuntu package checksum mismatch')
    wanted={(r['Package'],r['Version'],r['Architecture'],r['Filename'],r['SHA256']) for r in details['packages']}
    for suite in expected:
        for component in ('main','universe'):
            with (repository/'dists'/suite/component/'binary-amd64/Packages').open('rb') as stream:
                for record in records(stream):
                    wanted.discard(tuple(record.get(key) for key in ('Package','Version','Architecture','Filename','SHA256')))
    if wanted:raise ValueError('Carried package is not authenticated by Ubuntu signed metadata')
    return details


def install_packages(repository):
    repository=Path(repository).resolve();details=verify_repository(repository)
    with tempfile.TemporaryDirectory(prefix='car-offline-apt-') as temp:
        temp=Path(temp);os.chmod(temp,0o755)
        cache=temp/'archives';cache.mkdir();(cache/'partial').mkdir()
        for record in details['packages']:
            name=record['Package']+'_'+record['Version'].replace(':','%3a')+'_'+record['Architecture']+'.deb'
            if '/' in name or '\\' in name:raise ValueError('Invalid package identity')
            source=repository/record['Filename'];target=cache/name
            try:os.link(source,target)
            except OSError:shutil.copyfile(source,target)
        sources=temp/'sources.list'
        sources.write_text(''.join('deb [arch=amd64 by-hash=no signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] file:'+str(repository)+' '+suite+' main universe\n' for suite in details['suites']))
        lists=temp/'lists';lists.mkdir();(lists/'partial').mkdir()
        empty=temp/'empty';empty.mkdir()
        config=temp/'apt.conf'
        config.write_text('''Dir::Etc::Parts "'''+str(empty)+'''";
Dir::Etc::main "/dev/null";
Dir::Etc::sourcelist "'''+str(sources)+'''";
Dir::Etc::sourceparts "'''+str(empty)+'''";
Dir::State::lists "'''+str(lists)+'''";
Dir::Cache::archives "'''+str(cache)+'''";
APT::Architecture "amd64";
APT::Install-Recommends "false";
Acquire::Languages "none";
Acquire::Check-Valid-Until "true";
''')
        environment=dict(os.environ,APT_CONFIG=str(config),DEBIAN_FRONTEND='noninteractive')
        # All sources are local files. No direct or proxy Internet route is used.
        run(['apt-get','update'],environment)
        # APT must run its local file acquisition to resolve archive filenames.
        # --no-download can hand dpkg a bare basename when an archive is not
        # already in the cache. Only the verified file: repository is enabled.
        # Avoid upgrading existing desktop packages and their version-locked
        # reverse dependencies merely to bootstrap the car services.
        run(['apt-get','--no-upgrade','-y','install',*ROOT_PACKAGES],environment,timeout=1200)


def os_version(path='/etc/os-release'):
    values={}
    for line in Path(path).read_text().splitlines():
        if '=' in line:
            key,value=line.split('=',1);values[key]=value.strip().strip('"')
    if values.get('ID')!='ubuntu' or values.get('VERSION_ID') not in ('22.04','24.04','26.04'):
        raise ValueError('Offline Home Assistant bootstrap supports Ubuntu 22.04/24.04/26.04 amd64 Tiny')
    return values['VERSION_ID']


def docker(*args,timeout=600):
    environment=dict(os.environ)
    for name in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):environment.pop(name,None)
    return run(['docker','--host=unix:///var/run/docker.sock',*args],environment,timeout)


class DockerSocketConnection(http.client.HTTPConnection):
    """Read only the local engine, regardless of ambient Docker endpoint settings."""
    def __init__(self,path='/var/run/docker.sock'):
        super().__init__('localhost',timeout=1200);self.path=path
    def connect(self):
        import socket
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout);self.sock.connect(self.path)


def verify_saved_config(stream,info,expected):
    """Bind Docker's selected image to the exact signed OCI config digest.

    Docker 29/containerd reports a manifest ID, not the config ID. Its standard
    save stream contains both content-addressed blobs. Stop once the cryptographic
    binding is established; do not buffer/extract video-sized image layers.
    """
    config_names={expected[7:]+'.json','blobs/sha256/'+expected[7:]}
    manifest_name='blobs/sha256/'+info['Id'][7:]
    config=None;bound=False;count=0
    with tarfile.open(fileobj=stream,mode='r|') as archive:
        for entry in archive:
            count+=1
            if count>8192:raise ValueError('Docker export has too many entries')
            name=entry.name.removeprefix('./')
            if name not in config_names|{manifest_name}:continue
            if not entry.isfile() or entry.size>4*1024*1024:raise ValueError('Invalid Docker image metadata')
            with archive.extractfile(entry) as source:raw=source.read()
            if name in config_names:
                if 'sha256:'+hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Saved image configuration digest mismatch')
                config=json.loads(raw)
                if not isinstance(config,dict):raise ValueError('Invalid saved image configuration')
            elif name==manifest_name:
                if 'sha256:'+hashlib.sha256(raw).hexdigest()!=info['Id']:raise ValueError('Saved image manifest digest mismatch')
                manifest=json.loads(raw)
                if not isinstance(manifest,dict) or manifest.get('config',{}).get('digest')!=expected:
                    raise ValueError('Selected Docker manifest does not reference expected config')
                bound=True
            if config is not None and bound:
                if config.get('architecture')!='amd64' or config.get('os')!='linux':raise ValueError('Saved image platform mismatch')
                if config.get('rootfs',{}).get('diff_ids')!=info.get('RootFS',{}).get('Layers',[]):raise ValueError('Saved image layers do not match selected Docker image')
                return
    raise ValueError('Docker export does not prove the expected image configuration')


def verify_engine_config(info,expected,socket_path='/var/run/docker.sock'):
    connection=DockerSocketConnection(socket_path);response=None
    try:
        connection.request('GET','/images/'+info['Id']+'/get')
        response=connection.getresponse()
        if response.status!=200:raise RuntimeError('Docker image verification export failed: '+response.read(4096).decode(errors='replace'))
        verify_saved_config(response,info,expected)
    finally:
        if response is not None:response.close()
        connection.close()


def image_proofs(path):
    if path is None:return {}
    path=Path(path)
    if not path.exists():return {}
    stat=path.stat()
    if path.is_symlink() or stat.st_uid!=os.geteuid() or stat.st_mode&0o022:raise ValueError('Unsafe image proof cache')
    try:
        value=json.loads(path.read_text())
        return value if isinstance(value,dict) else {}
    except ValueError:return {}


def record_image_proof(path,proofs):
    if path is None:return
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    parent=path.parent.stat()
    if path.parent.is_symlink() or parent.st_uid!=os.geteuid() or parent.st_mode&0o022:raise ValueError('Unsafe image proof directory')
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as stream:
        json.dump(proofs,stream);stream.flush();os.fsync(stream.fileno());pending=Path(stream.name)
    pending.chmod(0o600);os.replace(pending,path)


def ensure_offline_image(image,archive,proof_cache=Path('/var/lib/car-dashboard/verified-image-configs.json'),socket_path='/var/run/docker.sock'):
    expected=image['imageId'];reference=image['reference']
    if not re.fullmatch('sha256:[0-9a-f]{64}',expected):raise ValueError('Invalid Home Assistant config identity')
    if not re.fullmatch(r'ghcr.io/home-assistant/home-assistant:20[0-9]{2}\.[0-9]{1,2}\.[0-9]+',reference):raise ValueError('Only pinned official Home Assistant tags are supported')
    proofs=image_proofs(proof_cache)
    def inspect(candidate):
        info=json.loads(docker('image','inspect',candidate))[0]
        if info.get('Architecture')!='amd64' or info.get('Os')!='linux':raise ValueError('Loaded image platform mismatch')
        actual=info.get('Id','')
        if not re.fullmatch('sha256:[0-9a-f]{64}',actual):raise ValueError('Invalid Docker image identity')
        if actual!=expected and proofs.get(actual)!=expected:
            verify_engine_config(info,expected,socket_path)
            proofs[actual]=expected;record_image_proof(proof_cache,proofs)
        return actual
    for candidate in (expected,reference):
        try:return inspect(candidate)
        except (RuntimeError,ValueError,KeyError,IndexError):pass
    docker('load','--input',archive,timeout=1200)
    # The reference locates the just-loaded image. Export hashing, not the tag,
    # establishes its identity; the manager receives an immutable engine ID.
    return inspect(reference)


def configure_power_safety(directory='/etc/systemd/system'):
    """Best-effort flushes, never a claim that it is safe to remove power."""
    units={
        'car-disk-flush.service': '''[Unit]
Description=Reduce dirty disk data before unexpected vehicle power loss
[Service]
Type=oneshot
DynamicUser=yes
ExecStart=/usr/bin/sync
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
TimeoutStartSec=120
''',
        'car-disk-flush.timer': '''[Unit]
Description=Periodically flush car computer disks (not a UPS replacement)
[Timer]
OnBootSec=15
OnUnitInactiveSec=15
AccuracySec=1
Unit=car-disk-flush.service
[Install]
WantedBy=timers.target
'''}
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    changed=False
    for name,content in units.items():
        path=directory/name
        if path.is_symlink():raise ValueError('Refusing a symlink power-safety unit')
        if path.exists() and path.read_text()==content:continue
        with tempfile.NamedTemporaryFile(mode='w',dir=directory,delete=False) as file:
            file.write(content);file.flush();os.fsync(file.fileno());pending=Path(file.name)
        os.chmod(pending,0o644);os.replace(pending,path);changed=True
    if changed:run(['systemctl','daemon-reload'])
    run(['systemctl','enable','--now','car-disk-flush.timer'])


def install(stage,runtime,repo,user,home_assistant_manager,update_lock_fd=None):
    configure_power_safety()
    settings_path=Path('/etc/car-dashboard/home-assistant-settings.json')
    if settings_path.exists():
        settings=json.loads(settings_path.read_text())
        if not settings.get('enabled',True):return {'homeAssistant':'disabled','internetRequiredOnTiny':False}
        status_path=Path('/etc/car-dashboard/home-assistant.json')
        completed=json.loads(status_path.read_text()).get('installed',False) if status_path.exists() else False
        if completed and subprocess.run(['systemctl','is-active','--quiet','car-home-assistant.service']).returncode!=0:
            return {'homeAssistant':'stopped','internetRequiredOnTiny':False}
    if os.uname().machine!='x86_64':raise ValueError('Runtime packages target the amd64 Tiny, not the Duet')
    version=os_version()
    if version not in runtime['ubuntu']:raise ValueError('Package does not include this Tiny Ubuntu release')
    stage=Path(stage)
    with tempfile.TemporaryDirectory(prefix='car-runtime-') as temp:
        temp=Path(temp);os.chmod(temp,0o755)
        extract(stage/('ubuntu-'+version+'.tar.gz'),temp/'repository')
        details=verify_repository(temp/'repository')
        if details['version']!=version:raise ValueError('Refusing packages for another Ubuntu version')
        install_packages(temp/'repository')
    run(['systemctl','enable','--now','docker.service'])
    image=runtime['image']
    engine_id=ensure_offline_image(image,stage/'ha-image.tar.gz')
    timezone=Path('/etc/timezone').read_text().strip() if Path('/etc/timezone').exists() else 'UTC'
    args=['/usr/bin/python3','-I',home_assistant_manager,'install','--repo',repo,'--user',user,'--timezone',timezone,
          '--offline-image',engine_id]
    if update_lock_fd is not None:args+=['--update-lock-fd',str(update_lock_fd)]
    run(args,timeout=1800,pass_fds=() if update_lock_fd is None else (update_lock_fd,))
    return {'homeAssistant':'ready','imageId':image['imageId'],'dockerImageId':engine_id,'ubuntu':version,'internetRequiredOnTiny':False}


if __name__=='__main__':
    if sys.argv[1:]!=['--install-power-safety'] or os.geteuid()!=0:
        raise SystemExit('Use deploy/install-power-safety.sh as your ordinary Tiny user')
    configure_power_safety()
