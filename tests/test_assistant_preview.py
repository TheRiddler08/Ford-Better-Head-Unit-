import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dev import assistant_preview as preview


class AssistantPreviewTests(unittest.TestCase):
    def test_preview_asset_refresh_preserves_owner_files_and_local_edits(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            auth=root/'.storage/auth';auth.parent.mkdir();auth.write_text('private fixture account')
            files={'configuration.yaml':'initial config','custom_components/car_console/sensor.py':'old sensor'}
            def initialize():
                payload=json.dumps({'files':files,'migration':'pass'})
                with patch.dict(os.environ,{'CAR_PREVIEW_CONFIG':temp}),patch('sys.stdin',io.StringIO(payload)):
                    exec(preview.INITIALIZE,{})
            initialize()
            (root/'configuration.yaml').write_text('owner edited config')
            files.update({'configuration.yaml':'new config','custom_components/car_console/sensor.py':'new sensor'})
            initialize()
            self.assertEqual((root/'configuration.yaml').read_text(),'owner edited config')
            self.assertEqual((root/'custom_components/car_console/sensor.py').read_text(),'new sensor')
            self.assertEqual(auth.read_text(),'private fixture account')

    def test_preview_cannot_stop_an_unmanaged_container(self):
        record=[{'Config':{'Labels':None},'State':{'Running':True}}]
        with patch.object(preview,'docker',return_value=json.dumps(record).encode()):
            with self.assertRaisesRegex(ValueError,'another application'):preview.existing()

    def test_preview_refreshes_generated_pointer_but_keeps_edited_legacy_module(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'www').mkdir();(root/'www/drive-console.js').write_text('owner legacy module')
            old='{"url":"/local/drive-console.js?v=old"}';(root/'drive-console-resource.json').write_text(old)
            payload=json.dumps({'files':{'www/drive-console.js':'new module','www/drive-console.'+'a'*20+'.js':'new module',
                'drive-console-resource.json':'{"url":"/local/drive-console.'+'a'*20+'.js"}'},'migration':'pass'})
            with patch.dict(os.environ,{'CAR_PREVIEW_CONFIG':temp}),patch('sys.stdin',io.StringIO(payload)):exec(preview.INITIALIZE,{})
            self.assertEqual((root/'www/drive-console.js').read_text(),'owner legacy module')
            self.assertEqual((root/'www'/('drive-console.'+'a'*20+'.js')).read_text(),'new module')
            self.assertIn('a'*20,(root/'drive-console-resource.json').read_text())
            backup=next((root/'.drive-console-backups').iterdir());self.assertEqual(backup.read_text(),old);self.assertEqual(backup.stat().st_mode&0o777,0o600)

    def test_current_running_preview_does_not_download_or_rebuild_for_start(self):
        _,revision=preview.profile()
        info={'Config':{'Labels':{preview.REVISION_LABEL:revision}},'State':{'Running':True},'HostConfig':{'RestartPolicy':{'Name':'unless-stopped'}}}
        with patch.object(preview,'existing',return_value=info),patch.object(preview,'docker') as docker:
            preview.start()
        docker.assert_not_called()

    def test_old_running_and_stopped_previews_get_recovery_without_rebuilding_or_resetting(self):
        _,revision=preview.profile()
        for running in (True,False):
            info={'Config':{'Labels':{preview.REVISION_LABEL:revision}},'State':{'Running':running},'HostConfig':{'RestartPolicy':{'Name':'no'}}}
            with self.subTest(running=running),patch.object(preview,'existing',return_value=info),patch.object(preview,'docker') as docker,patch.object(preview,'launch') as launch,patch.object(preview.ha,'car_assistant_image') as build:
                preview.start()
            docker.assert_called_once_with('update','--restart','unless-stopped',preview.NAME)
            self.assertEqual(launch.call_count,0 if running else 1)
            build.assert_not_called()

    def test_start_builds_isolated_preview_with_loopback_port_and_no_host_mounts(self):
        calls=[]
        def docker(*args,**kwargs):
            calls.append((args,kwargs))
            if args[:2]==('volume','inspect'):raise RuntimeError('get car-assistant-preview-config: no such volume')
            return b'{}'
        with patch.object(preview,'existing',return_value=None),patch.object(preview,'docker',side_effect=docker),patch.object(preview.ha,'car_assistant_image',return_value='sha256:fixture'),patch.object(preview,'launch') as launch:
            preview.start()
        create=next(args for args,_ in calls if args[0]=='create')
        self.assertIn('127.0.0.1:18123:8123',create)
        self.assertEqual(create[create.index('--restart')+1],'unless-stopped')
        self.assertNotIn('--network=host',create)
        self.assertEqual(create[create.index('--volume')+1],preview.VOLUME+':/config')
        initialize=next((args,kw) for args,kw in calls if args[0]=='run')
        self.assertIn('--network=none',initialize[0])
        # Docker must forward the JSON stdin used to initialize the volume.
        self.assertIn('--interactive',initialize[0])
        payload=json.loads(initialize[1]['data'])
        self.assertEqual(json.loads(payload['files']['configuration.yaml'])['http']['server_host'],'0.0.0.0')
        launch.assert_called_once_with()


if __name__=='__main__':unittest.main()
