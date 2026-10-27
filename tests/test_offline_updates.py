import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from deploy import offline_updates as courier


class CourierTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source';self.tiny=self.root/'tiny'
        courier.run(['git','init',self.source])
        self.source_git('symbolic-ref','HEAD','refs/heads/main')
        self.source_git('config','user.name','Courier Test');self.source_git('config','user.email','test@example.invalid')
        (self.source/'version.txt').write_text('old')
        self.source_git('add','.');self.source_git('commit','-m','old')
        self.old=self.source_git('rev-parse','HEAD').decode().strip()
        self.old_bundle=self.root/'old.bundle';self.source_git('bundle','create',str(self.old_bundle),'main')
        courier.run(['git','clone',self.source,self.tiny])
        self.git('remote','set-url','origin',courier.ORIGIN)
        (self.source/'version.txt').write_text('new')
        self.source_git('commit','-am','new')
        self.commit=self.source_git('rev-parse','HEAD').decode().strip()
        self.bundle=self.root/'update.bundle';self.source_git('bundle','create',str(self.bundle),'refs/heads/main')
        self.key=courier.ensure_key(self.root/'courier-key')
        self.signers=self.root/'allowed-signers'
        self.signers.write_text('duet '+self.key.with_suffix('.pub').read_text())

    def source_git(self,*args):return courier.run(['git','-C',self.source,*args])
    def git(self,*args):return courier.run(['git','-C',self.tiny,*args])

    def package(self,alter_manifest=False,alter_bundle=False):
        manifest={'schema':1,'repository':courier.REPOSITORY,'branch':'main','commit':self.commit,
                  'bundleSha256':courier.digest(self.bundle),'created':1}
        file=self.root/'manifest.json';file.write_text(json.dumps(manifest))
        courier.run(['ssh-keygen','-Y','sign','-f',self.key,'-n',courier.NAMESPACE,file])
        if alter_manifest:file.write_text(json.dumps(dict(manifest,commit=self.old)))
        package=self.root/'package.zip'
        with zipfile.ZipFile(package,'w') as archive:
            archive.write(file,'manifest.json');archive.write(self.root/'manifest.json.sig','manifest.json.sig')
            if alter_bundle:archive.writestr('update.bundle',b'not the signed bundle')
            else:archive.write(self.bundle,'update.bundle')
        return package

    def test_signature_checksum_and_real_offline_fast_forward(self):
        target=self.root/'verified';target.mkdir()
        manifest=courier.verify_package(self.package(),self.signers,target)
        checked=[]
        result=courier.apply_checkout(self.tiny,target/'update.bundle',manifest['commit'],self.git,lambda changed:checked.extend(changed))
        self.assertTrue(result['ok']);self.assertEqual((self.tiny/'version.txt').read_text(),'new')
        self.assertEqual(checked,['version.txt'])
        # Reconnection after a lost receipt is idempotent.
        again=courier.apply_checkout(self.tiny,target/'update.bundle',manifest['commit'],self.git,lambda changed:None)
        self.assertFalse(again['changed'])
        with self.assertRaises(RuntimeError):courier.apply_checkout(self.tiny,self.old_bundle,self.old,self.git,lambda changed:None)

    def test_reject_signature_or_checksum_tampering(self):
        for flags in [(True,False),(False,True)]:
            target=self.root/('verify'+str(flags));target.mkdir()
            signature=self.root/'manifest.json.sig'
            if signature.exists():signature.unlink()
            with self.assertRaises((ValueError,RuntimeError)):
                courier.verify_package(self.package(*flags),self.signers,target)
        self.assertEqual(self.git('rev-parse','HEAD').decode().strip(),self.old)

    def test_local_work_and_ignored_collision_are_preserved(self):
        (self.tiny/'version.txt').write_text('my changes')
        with self.assertRaises(ValueError):courier.apply_checkout(self.tiny,self.bundle,self.commit,self.git,lambda changed:None)
        self.assertEqual((self.tiny/'version.txt').read_text(),'my changes')
        self.git('restore','version.txt')
        (self.source/'new.local').write_text('from update')
        self.source_git('add','.');self.source_git('commit','-m','new file')
        commit=self.source_git('rev-parse','HEAD').decode().strip()
        bundle=self.root/'collision.bundle';self.source_git('bundle','create',str(bundle),'main')
        (self.tiny/'.git/info/exclude').write_text('new.local\n')
        (self.tiny/'new.local').write_text('local data')
        with self.assertRaises(ValueError):courier.apply_checkout(self.tiny,bundle,commit,self.git,lambda changed:None)
        self.assertEqual((self.tiny/'new.local').read_text(),'local data')
        (self.tiny/'new.local').unlink()
        (self.source/'local-cache').mkdir();(self.source/'local-cache/new.txt').write_text('new source')
        self.source_git('add','.');self.source_git('commit','-m','new directory')
        commit=self.source_git('rev-parse','HEAD').decode().strip()
        bundle=self.root/'parent-collision.bundle';self.source_git('bundle','create',str(bundle),'main')
        (self.tiny/'.git/info/exclude').write_text('new.local\nlocal-cache\n')
        (self.tiny/'local-cache').write_text('local parent data')
        with self.assertRaises(ValueError):courier.apply_checkout(self.tiny,bundle,commit,self.git,lambda changed:None)
        self.assertEqual((self.tiny/'local-cache').read_text(),'local parent data')

    def test_failed_validation_rolls_back_and_retains_notes(self):
        def fail(changed):
            (self.tiny/'notes.txt').write_text('user note')
            raise RuntimeError('health check failed')
        restored=[]
        with self.assertRaises(RuntimeError):
            courier.apply_checkout(self.tiny,self.bundle,self.commit,self.git,fail,lambda:restored.append(True))
        self.assertEqual(self.git('rev-parse','HEAD').decode().strip(),self.old)
        self.assertEqual((self.tiny/'version.txt').read_text(),'old')
        self.assertEqual((self.tiny/'notes.txt').read_text(),'user note')
        self.assertEqual(restored,[True])

    def test_duet_helpers_refresh_but_customizations_are_preserved(self):
        folder=self.source/'duet';folder.mkdir()
        (folder/'proxy.py').write_text('VERSION = 1\n')
        (folder/'launch.sh').write_text('#!/bin/bash\nexit 0\n')
        self.source_git('add','.');self.source_git('commit','-m','old helpers')
        runtime=self.root/'display';runtime.mkdir()
        (runtime/'proxy.py').write_text('VERSION = 1\n')
        (runtime/'launch.sh').write_text('#!/bin/bash\necho my_customization\n')
        (folder/'proxy.py').write_text('VERSION = 2\n')
        self.source_git('commit','-am','new helper')
        commit=self.source_git('rev-parse','HEAD').decode().strip()
        result=courier.refresh_duet_runtime(self.source/'.git',commit,runtime)
        self.assertEqual((runtime/'proxy.py').read_text(),'VERSION = 2\n')
        self.assertEqual(result['launch.sh'],'local customization preserved')
        self.assertIn('my_customization',(runtime/'launch.sh').read_text())

    def test_unexpected_archive_paths_and_ref_mismatch(self):
        package=self.root/'evil.zip'
        with zipfile.ZipFile(package,'w') as archive:archive.writestr('../outside','bad')
        target=self.root/'target';target.mkdir()
        with self.assertRaises(ValueError):courier.verify_package(package,self.signers,target)
        self.assertFalse((self.root/'outside').exists())
        with self.assertRaises(ValueError):courier.apply_checkout(self.tiny,self.bundle,self.old,self.git,lambda changed:None)
