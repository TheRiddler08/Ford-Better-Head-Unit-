from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from control.car_data import Store, shared_objects, shared_goal
from control.storage_policy import bounded_db,guard_state_file
from deploy import fleet


class PiStorageTests(unittest.TestCase):
    def test_all_bulk_roles_keep_writes_on_pinned_pool_after_detach(self):
        from control.car_data import copy_verified
        for role in ('tiny','duet','laptop'):
            with self.subTest(role=role),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);mount=root/'pool';mount.mkdir();moved=root/'detached';source=root/'notes.txt';source.write_text('preserve original')
                store=Store(root/'state',role,mount)
                with patch('control.car_data.RESERVE',0):ident=store.add(source,'route')
                fd=os.open(mount,os.O_RDONLY|os.O_DIRECTORY)
                mount_id=next(line.split()[1] for line in Path('/proc/self/fdinfo/'+str(fd)).read_text().splitlines() if line.startswith('mnt_id:'));os.close(fd)
                original=Path.read_text
                def read(path,*args,**kwargs):
                    if str(path)=='/proc/self/mountinfo':return mount_id+' 0 0:1 / /fixture rw - fuse.mfs mfs rw\n'
                    return original(path,*args,**kwargs)
                def detach(_):mount.rename(moved);mount.mkdir()
                with patch.object(Path,'read_text',read),patch('control.car_data.mounted',return_value=True),patch('control.car_data.shared_goal',side_effect=detach):
                    self.assertEqual(store.archive(),'shared-archive-connected')
                self.assertEqual((moved/role/'objects'/ident).read_text(),'preserve original')
                self.assertEqual(list(mount.iterdir()),[]);self.assertEqual(source.read_text(),'preserve original')

    def test_pi_offline_never_spools_into_sd_or_unmounted_pool(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);pool=root/'pool';pool.mkdir()
            source=root/'route.json';source.write_text('{"route":1}')
            store=Store(root/'state','pi',pool)
            with self.assertRaisesRegex(ValueError,'will not use SD'):store.add(source,'route')
            self.assertFalse(store.objects.exists())
            self.assertEqual(list(pool.iterdir()),[])
            self.assertTrue(source.exists())
            with store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM objects').fetchone()[0],0)

    def test_pi_new_object_is_remote_and_already_archived(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);remote=root/'remote';remote.mkdir()
            source=root/'route.json';source.write_text('{"route":1}')
            store=Store(root/'state','pi',root/'pool')
            @contextmanager
            def pool(*_):yield remote
            with patch('control.car_data.shared_objects',pool),patch('control.car_data.RESERVE',0),patch('control.car_data.shutil.which',return_value='/usr/bin/mfssetgoal'),patch('control.car_data.subprocess.run'):
                ident=store.add(source,'route')
            self.assertEqual((remote/ident).read_bytes(),source.read_bytes())
            self.assertTrue((remote/(ident+'.json')).exists())
            self.assertFalse(store.objects.exists())
            with store.db() as db:self.assertEqual(db.execute('SELECT archived FROM objects').fetchone()[0],1)

    def test_open_pool_descriptor_stays_on_remote_tree_after_path_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);mount=root/'mount';mount.mkdir();moved=root/'detached'
            fd=os.open(mount,os.O_RDONLY|os.O_DIRECTORY)
            ident=next(line.split()[1] for line in Path('/proc/self/fdinfo/'+str(fd)).read_text().splitlines() if line.startswith('mnt_id:'))
            os.close(fd)
            original=Path.read_text
            def read(path,*args,**kwargs):
                if str(path)=='/proc/self/mountinfo':return ident+' 0 0:1 / /fixture rw - fuse.mfs mfs rw\n'
                return original(path,*args,**kwargs)
            with patch.object(Path,'read_text',read):
                with shared_objects(mount,'pi') as target:
                    mount.rename(moved);mount.mkdir()
                    (target/'proof.txt').write_text('pinned mount')
            self.assertEqual((moved/'pi/objects/proof.txt').read_text(),'pinned mount')
            self.assertEqual(list(mount.iterdir()),[])

    def test_small_durable_database_caps_growth_without_losing_committed_state(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'CAR_STATE_DB_LIMIT_BYTES':str(1024**2)}):
            db=bounded_db(sqlite3.connect(Path(temp)/'state.db'))
            try:
                db.execute('CREATE TABLE items(value BLOB)')
                db.execute('INSERT INTO items VALUES(?)',(b'keep committed receipt',));db.commit()
                with self.assertRaises(sqlite3.Error):db.execute('INSERT INTO items VALUES(zeroblob(2097152))')
                db.rollback()
                self.assertEqual(db.execute('SELECT value FROM items').fetchone()[0],b'keep committed receipt')
                self.assertLessEqual(db.execute('PRAGMA page_count').fetchone()[0]*db.execute('PRAGMA page_size').fetchone()[0],1024**2)
            finally:db.close()

    def test_remote_directory_symlink_cannot_redirect_bulk_to_sd(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);mount=root/'pool';mount.mkdir();outside=root/'sd';outside.mkdir()
            (mount/'pi').symlink_to(outside,target_is_directory=True)
            fd=os.open(mount,os.O_RDONLY|os.O_DIRECTORY)
            ident=next(line.split()[1] for line in Path('/proc/self/fdinfo/'+str(fd)).read_text().splitlines() if line.startswith('mnt_id:'))
            os.close(fd);original=Path.read_text
            def read(path,*args,**kwargs):
                if str(path)=='/proc/self/mountinfo':return ident+' 0 0:1 / /fixture rw - fuse.mfs mfs rw\n'
                return original(path,*args,**kwargs)
            with patch.object(Path,'read_text',read),self.assertRaises(OSError):
                with shared_objects(mount,'pi'):self.fail('Must reject symlink')
            self.assertEqual(list(outside.iterdir()),[])

    def test_replication_tool_receives_a_live_inherited_directory(self):
        original=subprocess.run
        def child(args,**kwargs):
            return original([sys.executable,'-c',
                'import os,sys; assert os.path.isdir(sys.argv[1]); assert os.path.isfile(sys.argv[1]+"/marker")',args[-1]],**kwargs)
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp)/'marker').write_text('shared directory')
            with patch('control.car_data.shutil.which',return_value='/usr/bin/mfssetgoal'),patch('control.car_data.subprocess.run',side_effect=child):
                shared_goal(Path(temp))

    def test_pi_workers_receive_scoped_database_and_file_limits(self):
        config={'role':'pi','home':'/home/pi','repo':'/home/pi/car-dashboard','user':'pi'}
        with patch.object(fleet,'load',return_value=config),patch.object(fleet,'save'),patch.object(fleet,'run'),patch.object(fleet,'unit') as unit:
            fleet.systemd_units('pi')
        units={call.args[0]:call.args[1] for call in unit.call_args_list}
        for name in ('car-update-link','car-radio-adapter','car-data'):
            self.assertIn('CAR_STATE_DB_LIMIT_BYTES=16777216',units[name+'.service'])
            self.assertNotIn('LimitFSIZE=',units[name+'.service'])
        self.assertNotIn('CAR_STATE_DB_LIMIT_BYTES',units['car-fleet.service'])

    def test_local_state_limit_does_not_restrict_remote_bulk_files(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'CAR_STATE_DB_LIMIT_BYTES':str(1024**2)}),patch.object(Path,'home',return_value=Path(temp)):
            with self.assertRaisesRegex(ValueError,'local state file limit'):
                guard_state_file(Path(temp)/'.local/state/drive-console/software/receipts.json',3*1024**2)
            guard_state_file(Path(temp)/'pool/pi/objects/video.mp4',1024**3)

    def test_ram_log_policy_is_pi_only_and_restarts_journal_only_on_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'systemd').mkdir();actual=Path
            def paths(value):
                return {'/run/systemd/system':root/'systemd',
                    '/etc/systemd/journald.conf.d/60-car-pi-storage.conf':root/'journal.conf'}.get(str(value),actual(value))
            with patch.object(fleet,'Path',side_effect=paths),patch.object(fleet.shutil,'which',return_value=None),patch.object(fleet,'run') as run:
                fleet.install_pi_storage_policy({'role':'tiny'})
                self.assertFalse((root/'journal.conf').exists())
                fleet.install_pi_storage_policy({'role':'pi'})
                fleet.install_pi_storage_policy({'role':'pi'})
                run.assert_called_once_with(['systemctl','restart','systemd-journald.service'],check=False)
            self.assertIn('Storage=volatile',(root/'journal.conf').read_text())
            self.assertIn('RuntimeMaxUse=32M',(root/'journal.conf').read_text())

    def test_pi_client_needs_no_chunk_account_or_eight_gib_reserve(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'systemd').mkdir();actual=Path
            def paths(value):
                return {'/car-data':root/'mount','/run/systemd/system':root/'systemd'}.get(str(value),actual(value))
            with patch.object(fleet,'Path',side_effect=paths),patch.object(fleet,'STATE',root/'state'),patch.object(fleet,'run') as run,patch.object(fleet,'unit') as unit,patch.object(fleet.shutil,'which',return_value='/usr/bin/mfsmount'),patch.object(fleet,'_has_user',side_effect=AssertionError('client needs no storage account')),patch.object(fleet.shutil,'disk_usage',side_effect=AssertionError('client needs no SD reserve')):
                self.assertEqual(fleet.install_storage({'role':'pi'}),[])
                commands=[call.args[0] for call in run.call_args_list]
                self.assertIn(['systemctl','disable','--now','car-storage-chunks.service'],commands)
                self.assertIn(['systemctl','disable','--now','car-storage-metadata.service'],commands)
                self.assertEqual(unit.call_args.args[0],'car-storage-mount.service')
            status=json.loads((root/'state/storage.json').read_text())
            self.assertEqual(status['contributionBytes'],0)
            self.assertEqual(status['mode'],'client-only')

    def test_pi_dependency_install_omits_video_and_storage_servers(self):
        with patch.object(fleet,'load',return_value={}),patch.object(fleet.shutil,'which',side_effect=lambda name:'/usr/bin/apt-get' if name=='apt-get' else None),patch.object(fleet.importlib.util,'find_spec',return_value=None),patch.object(fleet,'run'),patch.object(fleet.subprocess,'run') as install:
            fleet.native_dependencies(role='pi')
            packages=install.call_args.args[0]
            self.assertIn('moosefs-client',packages)
            for name in ('moosefs-master','moosefs-chunkserver','moosefs-metalogger','ffmpeg'):self.assertNotIn(name,packages)


if __name__=='__main__':unittest.main()
