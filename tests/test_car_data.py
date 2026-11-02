import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch,MagicMock
from control.car_data import Store,copy_verified,clip_path,clips
from control.event_transfer import Events

KEY=bytes(range(32))


class CarDataTests(unittest.TestCase):
    def setUp(self):
        # Functional fixtures are tiny; do not require gigabytes of free test disk.
        reserve=patch('control.car_data.RESERVE',0);reserve.start();self.addCleanup(reserve.stop)

    def test_production_reserve_rejects_new_copies_without_deleting_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'route.json';source.write_text('{}')
            store=Store(root/'spool','tiny')
            with patch('control.car_data.RESERVE',10**18):
                with self.assertRaises(ValueError):store.add(source,'route')
            self.assertTrue(source.exists())

    def test_unmounted_pool_never_gets_fallback_writes_and_keeps_original(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'routes.json';source.write_text('{"route":1}')
            store=Store(root/'spool','tiny',root/'not-mounted')
            ident=store.add(source,'route')
            with patch('control.car_data.mounted',return_value=False):self.assertEqual(store.archive(),'waiting-for-moosefs')
            self.assertFalse((root/'not-mounted').exists());self.assertTrue(source.exists())
            self.assertTrue((store.objects/ident).exists())
    def test_verified_idempotent_archive_and_conflicting_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'clip.mp4';source.write_bytes(b'recording')
            store=Store(root/'spool','duet',root/'pool');ident=store.add(source,'dashcam')
            with patch('control.car_data.mounted',return_value=True),patch('control.car_data.shutil.which',return_value='/usr/bin/mfssetgoal'),patch('control.car_data.subprocess.run'):
                self.assertEqual(store.archive(),'shared-archive-connected')
            self.assertEqual((root/'pool/duet/objects'/ident).read_bytes(),b'recording')
            self.assertTrue(source.exists());copy_verified(source,store.objects/ident)
            (store.objects/ident).write_bytes(b'bad')
            with self.assertRaises(ValueError):copy_verified(source,store.objects/ident)
    def test_camera_only_imports_stable_valid_video_and_never_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);inbox=root/'inbox';inbox.mkdir();clip=inbox/'camera.mp4';clip.write_bytes(b'camera recording');os.utime(clip,(1,1))
            (inbox/'link.mp4').symlink_to(clip)
            store=Store(root/'spool','tiny')
            reply=MagicMock(returncode=0,stdout=json.dumps({'streams':[{'codec_type':'video'}]}).encode())
            with patch('control.car_data.shutil.which',return_value='/bin/ffprobe'),patch('control.car_data.subprocess.run',return_value=reply) as probe:
                store.ingest_camera(inbox,100);probe.assert_not_called()
                store.ingest_camera(inbox,131);probe.assert_called_once()
                store.ingest_camera(inbox,200);probe.assert_called_once()
            with store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM objects').fetchone()[0],1)
            with store.db() as db:
                metadata=json.loads(db.execute('SELECT metadata FROM objects').fetchone()[0])
                self.assertEqual(metadata['camera'],'Botslab G980H')
                self.assertEqual(metadata['channel'],'unspecified')
            self.assertTrue(clip.exists())
    def test_g980h_four_channels_keep_separate_origin_labels(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);inbox=root/'inbox';inbox.mkdir()
            for channel in ('front','rear','left','right'):
                folder=inbox/channel;folder.mkdir();path=folder/'same-name.mp4'
                path.write_bytes(('recording '+channel).encode());os.utime(path,(1,1))
            store=Store(root/'spool','tiny')
            reply=MagicMock(returncode=0,stdout=json.dumps({'streams':[{'codec_type':'video'}],
                'format':{'tags':{'creation_time':'2026-11-02T12:00:00Z'}}}).encode())
            with patch('control.car_data.shutil.which',return_value='/bin/ffprobe'),patch('control.car_data.subprocess.run',return_value=reply):
                store.ingest_camera(inbox,100);store.ingest_camera(inbox,131)
            with store.db() as db:metadata=[json.loads(row[0]) for row in db.execute('SELECT metadata FROM objects')]
            self.assertEqual({row['channel'] for row in metadata},{'front','rear','left','right'})
            self.assertTrue(all(row['name']==row['channel']+'/same-name.mp4' for row in metadata))
            self.assertTrue(all(row['cameraTime']=='2026-11-02T12:00:00Z' for row in metadata))

    def test_clip_paths_cannot_read_arbitrary_files(self):
        with tempfile.TemporaryDirectory() as temp:
            for bad in ('tiny:../../etc/passwd','other:'+64*'a'+'.mp4','tiny:private.key'):
                with self.assertRaises((ValueError,FileNotFoundError)):clip_path(temp,'tiny',bad)
    def test_catalog_interruptions_duplicates_and_durable_ack(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);car=Events(root/'car.db',KEY);home=Events(root/'home.db',KEY)
            doc={'type':'dashcam','source':'tiny','name':'clip.mp4','size':123456789}
            car.enqueue('tiny:clip',doc)
            raw=car.outgoing();home.receive(raw);car.sent(raw)
            home=Events(root/'home.db',KEY) # Restart mid-message.
            for _ in range(64):
                raw=car.outgoing();home.receive(raw);home.receive(raw);car.sent(raw)
                if home.catalog():break
            self.assertEqual(home.catalog()[0]['name'],'clip.mp4')
            for _ in range(64):
                raw=home.outgoing(ack=True)
                if raw is None:break
                car.receive(raw);home.sent(raw,ack=True)
            car=Events(root/'car.db',KEY);self.assertIsNone(car.outgoing())
