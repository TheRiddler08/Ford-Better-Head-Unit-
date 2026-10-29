import ctypes
import ctypes.util
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from deploy import runtime_assets as assets,offline_payload as payload,offline_updates as courier


class RuntimeTests(unittest.TestCase):
    def test_dependencies_include_versions_alternatives_and_virtuals(self):
        def record(name,version='1',deps='',provides=''):
            return {'Package':name,'Version':version,'Architecture':'amd64','Depends':deps,'Provides':provides}
        index={'root':[record('root',deps='real (>= 1) | absent, virtual')],
               'real':[record('real','1'),record('real','2')],
               'provider':[record('provider',provides='virtual',deps='root')]}
        result=assets.closure(index,['root'])
        self.assertEqual({(r['Package'],r['Version']) for r in result},{('root','1'),('real','1'),('real','2'),('provider','1')})
        with self.assertRaises(ValueError):assets.closure(index,['missing'])

    def test_package_control_continuations(self):
        data=b'Package: example\nVersion: 1:2.0\nArchitecture: all\nDepends: one,\n two | three:any\nDescription: ignored\n\n'
        parsed=list(assets.records(io.BytesIO(data)))
        self.assertEqual(parsed[0]['Depends'],'one, two | three:any')
        self.assertNotIn('Description',parsed[0])

    def test_zstd_decode_and_truncated_frames(self):
        lib=ctypes.CDLL(ctypes.util.find_library('zstd') or 'libzstd.so.1')
        lib.ZSTD_compressBound.argtypes=[ctypes.c_size_t];lib.ZSTD_compressBound.restype=ctypes.c_size_t
        lib.ZSTD_compress.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int];lib.ZSTD_compress.restype=ctypes.c_size_t
        data=b'layer data\n'*100000
        target=ctypes.create_string_buffer(lib.ZSTD_compressBound(len(data)));source=ctypes.create_string_buffer(data)
        size=lib.ZSTD_compress(target,len(target),source,len(data),3)
        with tempfile.TemporaryDirectory() as temp:
            file=Path(temp)/'layer';file.write_bytes(target.raw[:size])
            self.assertEqual(b''.join(assets.zstd_chunks(file)),data)
            file.write_bytes(target.raw[:size-3])
            with self.assertRaises(ValueError):b''.join(assets.zstd_chunks(file))

    def test_repository_extraction_rejects_symlinks_and_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for index,name in enumerate(('../outside','/absolute','link')):
                archive=root/(str(index)+'.tar.gz')
                with tarfile.open(archive,'w:gz') as tar:
                    entry=tarfile.TarInfo(name)
                    if name=='link':entry.type=tarfile.SYMTYPE;entry.linkname='/etc'
                    else:entry.size=3
                    tar.addfile(entry,None if name=='link' else io.BytesIO(b'bad'))
                with self.assertRaises(ValueError):payload.extract(archive,root/('stage'+str(index)))
            self.assertFalse((root/'outside').exists())

    def test_signed_runtime_checks_every_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);key=courier.ensure_key(root/'key')
            signers=root/'signers';signers.write_text('duet '+(root/'key.pub').read_text())
            files={'update.bundle':b'git bundle fixture','ha-image.tar.gz':b'image fixture','ubuntu-24.04.tar.gz':b'packages fixture'}
            runtime={'image':{'reference':'ghcr.io/home-assistant/home-assistant:2026.10.4','imageId':'sha256:'+'a'*64,'sha256':hashlib.sha256(files['ha-image.tar.gz']).hexdigest()},
                     'ubuntu':{'24.04':{'sha256':hashlib.sha256(files['ubuntu-24.04.tar.gz']).hexdigest()}}}
            commit='b'*40;identity=hashlib.sha256(json.dumps({'commit':commit,'runtime':runtime},sort_keys=True).encode()).hexdigest()
            manifest={'schema':2,'repository':courier.REPOSITORY,'branch':'main','commit':commit,'created':1,
                'bundleSha256':hashlib.sha256(files['update.bundle']).hexdigest(),'runtime':runtime,'payloadId':identity}
            (root/'manifest.json').write_text(json.dumps(manifest))
            courier.run(['ssh-keygen','-Y','sign','-f',key,'-n',courier.NAMESPACE,root/'manifest.json'])
            def archive(path,tamper=False):
                with zipfile.ZipFile(path,'w') as z:
                    z.write(root/'manifest.json','manifest.json');z.write(root/'manifest.json.sig','manifest.json.sig')
                    for name,value in files.items():z.writestr(name,value+(b'tamper' if tamper and name.startswith('ubuntu') else b''))
            good=root/'good.zip';archive(good);target=root/'verified';target.mkdir()
            self.assertEqual(courier.verify_package(good,signers,target)['payloadId'],identity)
            bad=root/'bad.zip';archive(bad,True);target=root/'bad-stage';target.mkdir()
            with self.assertRaises(ValueError):courier.verify_package(bad,signers,target)

    def test_same_code_new_runtime_is_delivered_again(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);commit='a'*40;target='car@172.16.42.2'
            (root/'delivered.json').write_text(json.dumps({'commit':commit,'payloadId':'old','target':target}))
            with zipfile.ZipFile(root/'ready.zip','w') as archive:archive.writestr('manifest.json',json.dumps({'commit':commit,'payloadId':'new'}))
            response=json.dumps({'ok':True,'commit':commit,'payloadId':'new'}).encode()
            with patch('deploy.offline_updates.subprocess.run',return_value=subprocess.CompletedProcess([],0,response,b'')) as run:
                result=courier.deliver(root,root/'key',target)
                self.assertEqual(result['payloadId'],'new');run.assert_called_once()
            with patch('deploy.offline_updates.subprocess.run') as run:
                self.assertTrue(courier.deliver(root,root/'key',target)['skipped']);run.assert_not_called()

    def test_atomic_bootstrap_publication_preserves_modified_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);commit='c'*40
            sources={name:('VERSION = '+repr(name)+'\n').encode() for name in courier.COMPONENTS}
            def git(*args):return sources[args[-1].split('/')[-1]]
            self.assertTrue(courier.publish_version(git,commit,root))
            current=root/'courier-current'
            self.assertEqual((current/'offline_updates.py').read_bytes(),sources['offline_updates.py'])
            self.assertFalse(courier.publish_version(git,commit,root))
            (current/'offline_updates.py').write_text('local edits')
            with self.assertRaises(ValueError):courier.publish_version(git,commit,root)
            self.assertEqual((current/'offline_updates.py').read_text(),'local edits')
