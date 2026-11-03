"""Docker classic config IDs vs Docker 29/containerd manifest IDs."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from deploy import offline_payload as payload


def saved_image(wrong_config=False,wrong_binding=False,wrong_layers=False):
    config=json.dumps({'architecture':'amd64','os':'linux','rootfs':{'diff_ids':['sha256:'+'a'*64]}}).encode()
    expected='sha256:'+hashlib.sha256(config).hexdigest()
    manifest=json.dumps({'schemaVersion':2,'config':{'digest':'sha256:'+'b'*64 if wrong_binding else expected}}).encode()
    engine='sha256:'+hashlib.sha256(manifest).hexdigest()
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode='w') as tar:
        for digest,data in [(expected,config+b' ' if wrong_config else config),(engine,manifest)]:
            entry=tarfile.TarInfo('blobs/sha256/'+digest[7:]);entry.size=len(data);tar.addfile(entry,io.BytesIO(data))
        # Unused valid config in a compatibility manifest must not override a bad
        # content-addressed engine manifest binding.
        raw=json.dumps([{'Config':'blobs/sha256/'+expected[7:]}]).encode()
        entry=tarfile.TarInfo('manifest.json');entry.size=len(raw);tar.addfile(entry,io.BytesIO(raw))
    info={'Id':engine,'Architecture':'amd64','Os':'linux','RootFS':{'Layers':['sha256:'+('c' if wrong_layers else 'a')*64]}}
    output.seek(0)
    return output,info,expected


class DockerImageIdentityTests(unittest.TestCase):
    def test_containerd_manifest_id_proves_exact_config(self):
        stream,info,expected=saved_image()
        self.assertNotEqual(info['Id'],expected)
        payload.verify_saved_config(stream,info,expected)
    def test_wrong_config_binding_or_layers_are_rejected(self):
        for options in ({'wrong_config':True},{'wrong_binding':True},{'wrong_layers':True}):
            with self.subTest(options):
                stream,info,expected=saved_image(**options)
                with self.assertRaises(ValueError):payload.verify_saved_config(stream,info,expected)
    def test_matching_classic_id_does_not_need_export_or_load(self):
        _,info,expected=saved_image();info['Id']=expected
        image={'imageId':expected,'reference':'ghcr.io/home-assistant/home-assistant:2026.10.4'}
        with patch.object(payload,'docker',return_value=json.dumps([info]).encode()) as docker,patch.object(payload,'verify_engine_config') as export:
            self.assertEqual(payload.ensure_offline_image(image,'archive',proof_cache=None),expected)
        docker.assert_called_once_with('image','inspect',expected);export.assert_not_called()
    def test_docker29_tag_only_locates_image_and_never_substitutes_for_proof(self):
        _,info,expected=saved_image()
        image={'imageId':expected,'reference':'ghcr.io/home-assistant/home-assistant:2026.10.4'}
        def docker(*args,**kwargs):
            if args[:3]==('image','inspect',expected):raise RuntimeError('No such image')
            return json.dumps([info]).encode()
        with patch.object(payload,'docker',side_effect=docker) as commands,patch.object(payload,'verify_engine_config') as verify:
            self.assertEqual(payload.ensure_offline_image(image,'archive',proof_cache=None),info['Id'])
        verify.assert_called_once_with(info,expected,'/var/run/docker.sock')
        self.assertFalse(any(call.args[0]=='load' for call in commands.call_args_list))
    def test_unverified_wrong_tag_is_reloaded_and_still_requires_proof(self):
        _,info,expected=saved_image()
        image={'imageId':expected,'reference':'ghcr.io/home-assistant/home-assistant:2026.10.4'}
        def docker(*args,**kwargs):
            if args[:3]==('image','inspect',expected):raise RuntimeError('No such image')
            return json.dumps([info]).encode()
        with patch.object(payload,'docker',side_effect=docker) as commands,patch.object(payload,'verify_engine_config',side_effect=ValueError('wrong config')):
            with self.assertRaises(ValueError):payload.ensure_offline_image(image,'archive',proof_cache=None)
        commands.assert_any_call('load','--input','archive',timeout=1200)
    def test_proof_cache_avoids_reexporting_immutable_image(self):
        _,info,expected=saved_image()
        image={'imageId':expected,'reference':'ghcr.io/home-assistant/home-assistant:2026.10.4'}
        with tempfile.TemporaryDirectory() as temp:
            cache=Path(temp)/'proofs.json'
            def docker(*args,**kwargs):
                if args[:3]==('image','inspect',expected):raise RuntimeError('No such image')
                return json.dumps([info]).encode()
            with patch.object(payload,'docker',side_effect=docker),patch.object(payload,'verify_engine_config') as verify:
                payload.ensure_offline_image(image,'archive',proof_cache=cache)
                payload.ensure_offline_image(image,'archive',proof_cache=cache)
                verify.assert_called_once()
            self.assertEqual(cache.stat().st_mode&0o777,0o600)
