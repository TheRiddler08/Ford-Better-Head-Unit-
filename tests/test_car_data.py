import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from contextlib import contextmanager
from unittest.mock import patch,MagicMock
from control.car_data import Store,copy_verified,clip_path,clips
from control.event_transfer import Events

KEY=bytes(range(32))


def restore(store,name):
    """Rebuild tables from archived increments: the last copy of each rowid wins."""
    import gzip,sqlite3
    tables={}
    with sqlite3.connect(store.state/'library.sqlite3') as db:
        idents=[r[0] for r in db.execute('SELECT id FROM objects WHERE kind=? ORDER BY created,rowid',(name+'-increment',))]
    for ident in idents:
        with gzip.open(store.objects/ident,'rt') as stream:
            header=json.loads(stream.readline());assert header['source']==name
            for line in stream:
                section=json.loads(line)
                for row in section['rows']:tables.setdefault(section['table'],{})[row[0]]=tuple(row[1:])
    return {table:[rows[k] for k in sorted(rows)] for table,rows in tables.items()},len(idents)


class CarDataTests(unittest.TestCase):
    def setUp(self):
        # Functional fixtures are tiny; do not require gigabytes of free test disk.
        reserve=patch('control.car_data.RESERVE',0);reserve.start();self.addCleanup(reserve.stop)

    def test_backups_store_each_row_once_and_restore_all_history(self):
        import sqlite3
        from recorder import gps_recorder as gps
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);store=Store(root/'spool','tiny');live=root/'gps.sqlite3'
            db=gps.open_db(live);gps.start_session(db,0)
            def fix(t,lat):return {'class':'TPV','mode':3,'lat':lat,'lon':-75.0,'speed':10,'time':'2026-11-01T01:%02d:%02dZ'%(t//60,t%60)}
            for t in range(60):gps.record_message(db,fix(t,40+t*0.001),100+t)
            self.assertEqual(store.increment(live,'gps',now=1000),1)
            # Unchanged history is not copied again, even after the interval.
            self.assertEqual(store.increment(live,'gps',now=1100),0)
            self.assertEqual(store.increment(live,'gps',now=5000),0)
            for t in range(60,90):gps.record_message(db,fix(t,40+t*0.001),100+t)
            gps.end_session(db,1,'stopped',500)  # in-place update of an archived row
            self.assertEqual(store.increment(live,'gps',now=9000),1)
            restored,count=restore(store,'gps');self.assertEqual(count,2)
            for table in ('fixes','gps_sessions','stops','gps_state'):
                self.assertEqual(restored.get(table,[]),[tuple(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY rowid')],table)
            # The second backup holds the new rows plus one repeated row per table, not a full copy.
            with sqlite3.connect(store.state/'library.sqlite3') as lib:
                second=json.loads(lib.execute("SELECT metadata FROM objects WHERE kind='gps-increment' ORDER BY rowid DESC").fetchone()[0])
            self.assertLessEqual(second['tables']['fixes'][2],31)
            db.close()
            # A replaced database starts over instead of silently skipping rows.
            live.unlink();db=gps.open_db(live);gps.record_message(db,fix(0,41),100);db.close()
            self.assertEqual(store.increment(live,'gps',now=20000),1)

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
            @contextmanager
            def pool(*_):
                target=root/'pool/duet/objects';target.mkdir(parents=True,exist_ok=True);yield target
            with patch('control.car_data.mounted',return_value=True),patch('control.car_data.shared_objects',pool),patch('control.car_data.shared_goal'):
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
