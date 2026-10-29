"""Rootless, cross-architecture downloads for the Duet's offline Tiny courier.

Ubuntu signatures are retained and checked by native APT on the Tiny. Registry
manifests, compressed layers and unpacked diff IDs are checked while downloading.
No Docker daemon, amd64 execution or package manager is needed on the ARM Duet.
"""
import gzip
import ctypes
import ctypes.util
import hashlib
import io
import json
import lzma
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

ROOT_PACKAGES=('docker.io','ca-certificates','gpgv','ubuntu-keyring')
KEEP={'Package','Version','Architecture','Filename','SHA256','Size','Depends','Pre-Depends','Provides'}
DIGEST=re.compile(r'^sha256:[0-9a-f]{64}$')
MAX_DOWNLOAD=6*1024**3


def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()


def safe_path(name):
    path=PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or not path.parts or '\\' in name:
        raise ValueError('Unsafe artifact path')
    return path


class Redirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if urlsplit(newurl).scheme!='https':raise ValueError('Artifact redirect must retain HTTPS')
        redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
        if urlsplit(newurl).netloc!=urlsplit(req.full_url).netloc:
            redirected.remove_header('Authorization')
        return redirected


class Downloads:
    def __init__(self,cache):
        self.cache=Path(cache);self.cache.mkdir(parents=True,exist_ok=True)
        self.client=build_opener(Redirect())
        self.token=None
    def fetch(self,url,expected=None,headers=None,limit=MAX_DOWNLOAD):
        if urlsplit(url).scheme!='https':raise ValueError('Artifacts require HTTPS')
        if expected and not re.fullmatch('[0-9a-f]{64}',expected):raise ValueError('Invalid expected digest')
        target=self.cache/expected if expected else None
        if target and target.exists() and sha(target)==expected:return target
        temporary=None
        try:
            with self.client.open(Request(url,headers=headers or {}),timeout=45) as response, tempfile.NamedTemporaryFile(dir=self.cache,delete=False) as f:
                temporary=Path(f.name);size=0
                for chunk in iter(lambda:response.read(1024*1024),b''):
                    size+=len(chunk)
                    if size>limit:raise ValueError('Artifact exceeds size limit')
                    f.write(chunk)
            actual=sha(temporary)
            if expected and actual!=expected:raise ValueError('Artifact checksum mismatch')
            target=self.cache/actual;temporary.replace(target);return target
        except HTTPError as exc:
            raise RuntimeError('Artifact download failed: '+urlsplit(url).hostname+' HTTP '+str(exc.code)) from None
        finally:
            if temporary and temporary.exists():temporary.unlink()
    def registry(self,path,expected=None):
        url='https://ghcr.io/v2/home-assistant/home-assistant/'+path
        headers={'Accept':'application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'}
        if self.token:headers['Authorization']='Bearer '+self.token
        try:
            return self.fetch(url,expected,headers)
        except RuntimeError as exc:
            if 'HTTP 401' not in str(exc):raise
        # Anonymous, public pull token. Never saved or logged.
        with self.client.open('https://ghcr.io/token?service=ghcr.io&scope=repository:home-assistant/home-assistant:pull',timeout=30) as response:
            self.token=json.loads(response.read(1024*1024))['token']
        headers['Authorization']='Bearer '+self.token
        return self.fetch(url,expected,headers)


def zstd_chunks(path):
    """Stream OCI zstd layers using the system library, also on ARM/musl."""
    try:lib=ctypes.CDLL(ctypes.util.find_library('zstd') or 'libzstd.so.1')
    except OSError as exc:raise ValueError('Install libzstd/zstd-libs on the Duet once to unpack official OCI images') from exc
    class Buffer(ctypes.Structure):
        _fields_=[('data',ctypes.c_void_p),('size',ctypes.c_size_t),('pos',ctypes.c_size_t)]
    lib.ZSTD_createDStream.restype=ctypes.c_void_p
    lib.ZSTD_freeDStream.argtypes=[ctypes.c_void_p]
    lib.ZSTD_initDStream.argtypes=[ctypes.c_void_p];lib.ZSTD_initDStream.restype=ctypes.c_size_t
    lib.ZSTD_decompressStream.argtypes=[ctypes.c_void_p,ctypes.POINTER(Buffer),ctypes.POINTER(Buffer)];lib.ZSTD_decompressStream.restype=ctypes.c_size_t
    lib.ZSTD_isError.argtypes=[ctypes.c_size_t];lib.ZSTD_isError.restype=ctypes.c_uint
    context=lib.ZSTD_createDStream()
    if not context:raise ValueError('Cannot allocate zstd decoder')
    remaining=lib.ZSTD_initDStream(context)
    try:
        with Path(path).open('rb') as stream:
            for chunk in iter(lambda:stream.read(256*1024),b''):
                source=ctypes.create_string_buffer(chunk);incoming=Buffer(ctypes.cast(source,ctypes.c_void_p),len(chunk),0)
                while incoming.pos<incoming.size:
                    target=ctypes.create_string_buffer(256*1024);outgoing=Buffer(ctypes.cast(target,ctypes.c_void_p),len(target),0)
                    previous=incoming.pos
                    remaining=lib.ZSTD_decompressStream(context,ctypes.byref(outgoing),ctypes.byref(incoming))
                    if lib.ZSTD_isError(remaining):raise ValueError('Invalid zstd image layer')
                    if outgoing.pos:yield target.raw[:outgoing.pos]
                    if previous==incoming.pos and outgoing.pos==0:raise ValueError('Stalled zstd image decoder')
        if remaining:raise ValueError('Truncated zstd image layer')
    finally:lib.ZSTD_freeDStream(context)


def layer_chunks(path,kind):
    if kind.endswith('+zstd'):
        yield from zstd_chunks(path);return
    if not (kind.endswith(('+gzip','.gzip')) or kind.endswith('.tar')):raise ValueError('Unsupported OCI layer compression')
    source=gzip.open(path,'rb') if kind.endswith(('+gzip','.gzip')) else Path(path).open('rb')
    with source:
        yield from iter(lambda:source.read(1024*1024),b'')


def image_archive(downloads,reference,output):
    prefix='ghcr.io/home-assistant/home-assistant:'
    if not reference.startswith(prefix) or not re.fullmatch(r'(?:stable|20[0-9]{2}\.[0-9]{1,2}\.[0-9]+)',reference[len(prefix):]):
        raise ValueError('Only pinned official Home Assistant releases are supported')
    descriptor=downloads.registry('manifests/'+reference[len(prefix):])
    manifest=json.loads(descriptor.read_text())
    if 'manifests' in manifest:
        matches=[m for m in manifest['manifests'] if m.get('platform',{}).get('os')=='linux' and m.get('platform',{}).get('architecture')=='amd64']
        if len(matches)!=1:raise ValueError('No unambiguous Linux amd64 image')
        digest=matches[0]['digest']
        if not DIGEST.fullmatch(digest):raise ValueError('Invalid platform manifest digest')
        descriptor=downloads.registry('manifests/'+digest,digest[7:]);manifest=json.loads(descriptor.read_text())
    config_digest=manifest['config']['digest']
    if not DIGEST.fullmatch(config_digest):raise ValueError('Invalid image config digest')
    config_file=downloads.registry('blobs/'+config_digest,config_digest[7:])
    config=json.loads(config_file.read_text())
    if config.get('architecture')!='amd64' or config.get('os')!='linux':raise ValueError('Image is not for the Tiny')
    if reference.endswith(':stable'):
        labels=config.get('config',{}).get('Labels',{})
        version=labels.get('io.hass.version') or labels.get('org.opencontainers.image.version','')
        if not re.fullmatch(r'20[0-9]{2}\.[0-9]{1,2}\.[0-9]+',version):raise ValueError('Stable image has no release version label')
        reference=prefix+version
    diff_ids=config['rootfs']['diff_ids'];layers=manifest['layers']
    if len(diff_ids)!=len(layers):raise ValueError('Image layer count mismatch')
    identity=sha(descriptor)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    archive=output/(identity+'.tar.gz');metadata=output/(identity+'.json')
    if archive.exists() and metadata.exists():
        info=json.loads(metadata.read_text())
        if sha(archive)==info['sha256']:return archive,info
    with tempfile.TemporaryDirectory(dir=output) as temp:
        temp=Path(temp);docker_manifest=[{'Config':config_digest[7:]+'.json','RepoTags':[reference],'Layers':[]}]
        with tarfile.open(temp/'image.tar.gz','w:gz') as tar:
            tar.add(config_file,arcname=config_digest[7:]+'.json')
            for index,layer in enumerate(layers):
                digest=layer['digest']
                if not DIGEST.fullmatch(digest) or not DIGEST.fullmatch(diff_ids[index]):raise ValueError('Invalid layer digest')
                blob=downloads.registry('blobs/'+digest,digest[7:])
                target=temp/'layer.tar';kind=layer.get('mediaType','')
                size=0
                with target.open('wb') as dst:
                    for chunk in layer_chunks(blob,kind):
                        size+=len(chunk)
                        if size>MAX_DOWNLOAD:raise ValueError('Image layer expands beyond limit')
                        dst.write(chunk)
                if 'sha256:'+sha(target)!=diff_ids[index]:raise ValueError('Unpacked image layer checksum mismatch')
                name=digest[7:]+'/layer.tar';tar.add(target,arcname=name);target.unlink()
                docker_manifest[0]['Layers'].append(name)
            data=json.dumps(docker_manifest).encode();entry=tarfile.TarInfo('manifest.json');entry.size=len(data);tar.addfile(entry,io.BytesIO(data))
        info={'reference':reference,'imageId':config_digest,'manifestSha256':identity,'sha256':sha(temp/'image.tar.gz')}
        (temp/'image.tar.gz').replace(archive);metadata.write_text(json.dumps(info))
    return archive,info


def release_hashes(text):
    hashes={};inside=False
    for line in text.splitlines():
        if line.startswith('- '):line=line[2:]
        if line=='SHA256:':inside=True;continue
        if inside:
            if not line.startswith(' '):break
            parts=line.split()
            if len(parts)==3 and re.fullmatch('[0-9a-f]{64}',parts[0]):hashes[parts[2]]=parts[0]
    return hashes


def records(stream):
    record={};previous=None
    for raw in stream:
        line=raw.decode('utf-8',errors='replace').rstrip('\n')
        if not line:
            if record:yield record
            record={};previous=None;continue
        if line[0].isspace():
            if previous in KEEP:record[previous]+=' '+line.strip()
        elif ':' in line:
            key,value=line.split(':',1);previous=key
            if key in KEEP:record[key]=value.strip()
    if record:yield record


def names(value):
    return re.findall(r'(?:^|[,|])\s*([a-z0-9][a-z0-9+.-]*)',value or '')


def closure(index,roots=ROOT_PACKAGES):
    providers={}
    for package,variants in index.items():
        for variant in variants:
            for name in names(variant.get('Provides','')):providers.setdefault(name,set()).add(package)
    visited=set();pending=list(roots);selected={}
    while pending:
        name=pending.pop()
        if name in visited:continue
        visited.add(name)
        candidates=[name] if name in index else sorted(providers.get(name,()))
        if not candidates:raise ValueError('Dependency absent from official metadata: '+name)
        for candidate in candidates:
            if candidate!=name:pending.append(candidate);continue
            for record in index[candidate]:
                key=(record['Package'],record['Version'],record['Architecture'])
                selected[key]=record
                for field in ('Depends','Pre-Depends'):
                    for group in record.get(field,'').split(','):
                        if not group.strip():continue
                        available=[dep for dep in names(group) if dep in index or dep in providers]
                        if not available:raise ValueError('Dependency absent from official metadata: '+group)
                        pending.extend(available)
        if len(selected)>4096:raise ValueError('Unexpectedly large dependency graph')
    return list(selected.values())


def ubuntu_archive(downloads,version,codename,output):
    if version not in ('22.04','24.04','26.04') or not re.fullmatch('[a-z]+',codename):raise ValueError('Unsupported Ubuntu target')
    metadata={};index={};pockets=(codename,codename+'-updates',codename+'-security')
    for suite in pockets:
        base='https://security.ubuntu.com/ubuntu' if suite.endswith('-security') else 'https://archive.ubuntu.com/ubuntu'
        release=downloads.fetch(base+'/dists/'+suite+'/InRelease',limit=4*1024**2)
        metadata['dists/'+suite+'/InRelease']=release
        hashes=release_hashes(release.read_text())
        for component in ('main','universe'):
            relative=component+'/binary-amd64/Packages'
            compressed=relative+'.xz'
            if compressed not in hashes:raise ValueError('Missing signed package index')
            packed=downloads.fetch(base+'/dists/'+suite+'/'+compressed,hashes[compressed],limit=256*1024**2)
            raw=downloads.cache/hashes[relative]
            if not raw.exists() or sha(raw)!=hashes[relative]:
                with lzma.open(packed,'rb') as src,raw.open('wb') as dst:shutil.copyfileobj(src,dst,1024*1024)
                if sha(raw)!=hashes[relative]:raise ValueError('Package index hash mismatch')
            metadata['dists/'+suite+'/'+relative]=raw
            metadata['dists/'+suite+'/'+compressed]=packed
            for suffix in ('.gz','.bz2','.zst'):
                alternative=relative+suffix
                if alternative in hashes:metadata['dists/'+suite+'/'+alternative]=downloads.fetch(base+'/dists/'+suite+'/'+alternative,hashes[alternative],limit=256*1024**2)
            with raw.open('rb') as stream:
                for record in records(stream):
                    if record.get('Architecture') not in ('amd64','all'):continue
                    record['_base']=base
                    index.setdefault(record['Package'],[]).append(record)
    releases={name:sha(path) for name,path in metadata.items() if name.endswith('/InRelease')}
    identity=hashlib.sha256(json.dumps({'format':3,'releases':releases},sort_keys=True).encode()).hexdigest()
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    archive=output/(version+'-'+identity+'.tar.gz');info_file=archive.with_suffix('.json')
    if archive.exists() and info_file.exists():
        info=json.loads(info_file.read_text())
        if sha(archive)==info['sha256']:return archive,info
    selected=closure(index);del index
    for record in selected:
        filename=str(safe_path(record['Filename']))
        if not filename.startswith('pool/'):raise ValueError('Unexpected Ubuntu package filename')
        payload=downloads.fetch(record['_base']+'/'+filename,record['SHA256'])
        if filename in metadata and sha(metadata[filename])!=record['SHA256']:raise ValueError('Conflicting official package versions')
        metadata[filename]=payload
    details={'version':version,'codename':codename,'architecture':'amd64','suites':list(pockets),
             'releases':releases,'packages':[{k:r[k] for k in ('Package','Version','Architecture','Filename','SHA256')} for r in selected]}
    with tempfile.NamedTemporaryFile(dir=output,delete=False) as f:pending=Path(f.name)
    try:
        with tarfile.open(pending,'w:gz') as tar:
            for name,path in sorted(metadata.items()):tar.add(path,arcname=name)
            data=json.dumps(details).encode();entry=tarfile.TarInfo('payload.json');entry.size=len(data);tar.addfile(entry,io.BytesIO(data))
        pending.replace(archive)
    finally:
        if pending.exists():pending.unlink()
    info={'version':version,'releases':releases,'sha256':sha(archive),'packages':len(selected)}
    info_file.write_text(json.dumps(info));return archive,info


def build(cache,spec):
    if spec.get('schema')!=1:raise ValueError('Unknown offline runtime specification')
    cache=Path(cache);downloads=Downloads(cache/'objects');files={}
    image,info=image_archive(downloads,spec['image'],cache/'images');files['ha-image.tar.gz']=image
    result={'image':info,'ubuntu':{}}
    for version,codename in spec['ubuntu'].items():
        archive,info=ubuntu_archive(downloads,version,codename,cache/'ubuntu')
        name='ubuntu-'+version+'.tar.gz';files[name]=archive;result['ubuntu'][version]=info
    return files,result
