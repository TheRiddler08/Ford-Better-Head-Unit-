import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from deploy import home_assistant as ha


class CarAssistantRuntimeTests(unittest.TestCase):
    def archive(self, root, name='usr/src/homeassistant/homeassistant/const.py'):
        archive=root/'car_assistant/runtime.tar.gz'
        archive.parent.mkdir()
        with tarfile.open(archive,'w:gz') as output:
            entry=tarfile.TarInfo(name);raw=b'VERSION="test"\n';entry.size=len(raw)
            output.addfile(entry,io.BytesIO(raw))
        return hashlib.sha256(archive.read_bytes()).hexdigest()

    def test_changed_archive_is_rejected_before_docker(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.archive(root)
            with patch.object(ha,'FORK_ARCHIVE_SHA','0'*64),patch.object(ha,'run') as run:
                with self.assertRaisesRegex(ValueError,'trusted installer'):
                    ha.car_assistant_image(root,'sha256:'+'1'*64)
                run.assert_not_called()

    def test_verified_archive_still_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);digest=self.archive(root,'../../escape')
            def run(args,**kwargs):
                if args[0:3]==['docker','image','inspect']:
                    if args[-1].startswith('car-assistant:'):raise RuntimeError('No such image')
                    return json.dumps([{'Id':'sha256:'+'1'*64,'Architecture':'amd64',
                        'Config':{'Labels':{'io.hass.version':'2026.10.4'}}}]).encode()
                self.fail('Unsafe archive must not reach Docker build')
            with patch.object(ha,'FORK_ARCHIVE_SHA',digest),patch.object(ha,'run',side_effect=run):
                with self.assertRaisesRegex(ValueError,'Unsafe'):
                    ha.car_assistant_image(root,'sha256:'+'1'*64)

    def test_cached_build_requires_matching_base_and_source_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);digest=self.archive(root);base='sha256:'+'1'*64;image='sha256:'+'2'*64
            cached={'Id':image,'Config':{'Labels':{'org.drive-console.source-sha256':digest,
                                                 'org.drive-console.base':base}}}
            def run(args,**kwargs):
                return json.dumps([{'Id':base,'Architecture':'amd64',
                    'Config':{'Labels':{'io.hass.version':'2026.10.4'}}} if args[-1]==base else cached]).encode()
            with patch.object(ha,'FORK_ARCHIVE_SHA',digest),patch.object(ha,'run',side_effect=run):
                self.assertEqual(ha.car_assistant_image(root,base),image)
                cached['Config']['Labels']['org.drive-console.base']='wrong'
                with self.assertRaisesRegex(ValueError,'provenance'):ha.car_assistant_image(root,base)

    def test_failed_upgrade_does_not_claim_fork_or_change_base(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);digest=self.archive(root)
            settings={'repo':str(root),'image':'old-image'}
            before=dict(settings)
            with patch.object(ha,'FORK_ARCHIVE_SHA',digest),patch.object(ha,'car_assistant_image',return_value='new-image'),patch.object(ha,'upgrade_image',side_effect=RuntimeError('health failed')),patch.object(ha,'save') as save:
                with self.assertRaisesRegex(RuntimeError,'health failed'):ha.ensure_car_assistant(settings)
                self.assertEqual(settings,before)
                save.assert_not_called()

    def test_offline_build_uses_verified_local_tag_not_a_bare_from_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);digest=self.archive(root);base='sha256:'+'1'*64;image='sha256:'+'2'*64
            built=False;tagged=False
            def run(args,**kwargs):
                nonlocal built,tagged
                if args[:3]==['docker','image','inspect']:
                    if args[-1]==base:
                        return json.dumps([{'Id':base,'Architecture':'amd64',
                            'Config':{'Labels':{'io.hass.version':'2026.10.4'}}}]).encode()
                    if not built:raise RuntimeError('No such image')
                    return json.dumps([{'Id':image}]).encode()
                if args[:2]==['docker','tag']:
                    self.assertEqual(args[2:],[base,'car-assistant-dependency-base:'+'1'*64]);tagged=True
                elif args[:2]==['docker','build']:
                    self.assertTrue(tagged)
                    self.assertIn('--network=none',args);self.assertIn('--pull=false',args)
                    recipe=(Path(args[-1])/'Dockerfile').read_text()
                    self.assertTrue(recipe.startswith('FROM car-assistant-dependency-base:'))
                    self.assertNotIn('RUN ',recipe)
                    self.assertTrue((Path(args[-1])/'rootfs/usr/src/homeassistant/homeassistant/const.py').exists())
                    built=True
                else:self.fail(str(args))
                return b''
            with patch.object(ha,'FORK_ARCHIVE_SHA',digest),patch.object(ha,'run',side_effect=run):
                self.assertEqual(ha.car_assistant_image(root,base),image)


if __name__=='__main__':unittest.main()
