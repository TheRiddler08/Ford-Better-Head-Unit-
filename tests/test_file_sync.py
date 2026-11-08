import json
from pathlib import Path
import tempfile
import unittest
from control.event_transfer import Events
from control.file_sync import Files,digest,filename,home_selection,save_home_selection

KEY=bytes(range(32))


class FileSyncTests(unittest.TestCase):
    def pair(self,root):
        car=Files(root/'car',root/'car-state.db',Events(root/'car-events.db',KEY,destination='tiny',types=('file','file-receipt')),'tiny','home',['notes.txt'])
        home=Files(root/'home',root/'home-state.db',Events(root/'home-events.db',KEY,destination='home'),'home','tiny',['notes.txt'])
        car.root.mkdir();home.root.mkdir();return car,home

    def conflict_pair(self,root):
        car,home=self.pair(root)
        (car.root/'notes.txt').write_text('common');car.scan();item=car.pending()[0]
        reply=home.receive(item['doc'],item['id'],via_ip=True);car.receive(reply['doc'],reply['id'])
        (car.root/'notes.txt').write_text('car edit');(home.root/'notes.txt').write_text('home edit')
        car.scan();home.scan();item=car.pending()[0]
        reply=home.receive(item['doc'],item['id'],via_ip=True);car.receive(reply['doc'],reply['id'])
        return car,home,item

    def settle(self,car,home):
        for _ in range(6):
            for source,target in ((car,home),(home,car)):
                source.scan()
                for item in source.pending():
                    reply=target.receive(item['doc'],item['id'],via_ip=True)
                    if reply:source.receive(reply['doc'],reply['id'],via_ip=True)
                for item in source.receipts():target.receive(item['doc'],item['id'],via_ip=True)

    def test_home_selection_is_per_role_and_concurrent_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'sync-settings.json').write_text('{"files":["legacy.txt"]}')
            tiny=home_selection(root,'tiny');save_home_selection(root,'tiny',['notes.txt'],tiny['revision'])
            self.assertEqual(home_selection(root,'tiny')['files'],['notes.txt'])
            self.assertEqual(home_selection(root,'duet')['files'],['legacy.txt'])
            with self.assertRaises(ValueError):save_home_selection(root,'tiny',[],tiny['revision'])
            with self.assertRaises(ValueError):save_home_selection(root,'tiny',['../private.txt'],home_selection(root,'tiny')['revision'])

    def test_editor_uses_hash_preconditions_backups_and_preserves_crlf(self):
        with tempfile.TemporaryDirectory() as directory:
            car,home=self.pair(Path(directory));(home.root/'notes.txt').write_bytes(b'original\r\n')
            loaded=home.inspect('notes.txt');edited=home.edit('notes.txt',loaded['sha256'],'next\r\n')
            self.assertIsNotNone(edited['pending']);self.assertEqual((home.root/'notes.txt').read_bytes(),b'next\r\n')
            backups=list((home.state.parent/(home.state.stem+'-backups')).glob('*.bak'))
            self.assertEqual(backups[0].read_bytes(),b'original\r\n')
            with self.assertRaises(ValueError):home.edit('notes.txt',loaded['sha256'],'stale overwrite')
            self.assertEqual(home.read('notes.txt'),'next\r\n')

    def test_conflict_choices_converge_and_keep_originals_in_backups(self):
        for choice,expected in [('car','car edit'),('home','home edit'),('merge','merged 🚗\r\n')]:
            with self.subTest(choice=choice),tempfile.TemporaryDirectory() as directory:
                car,home,item=self.conflict_pair(Path(directory));loaded=home.inspect('notes.txt')
                self.assertEqual(loaded['conflicts'][0]['incoming'],'car edit')
                home.resolve('notes.txt',item['id'],loaded['sha256'],choice,'merged 🚗\r\n')
                self.settle(car,home)
                self.assertEqual(home.read('notes.txt'),expected);self.assertEqual(car.read('notes.txt'),expected)
                self.assertFalse(car.pending());self.assertFalse(home.pending())
                backups=list((home.state.parent/(home.state.stem+'-backups')).glob('*.bak'))
                self.assertIn('home edit',[path.read_bytes().decode() for path in backups])

    def test_resolution_refuses_stale_browser_and_missing_verified_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            car,home,item=self.conflict_pair(Path(directory));loaded=home.inspect('notes.txt')
            (home.root/'notes.txt').write_text('newer edit')
            with self.assertRaises(ValueError):home.resolve('notes.txt',item['id'],loaded['sha256'],'car')
            self.assertEqual(home.read('notes.txt'),'newer edit')
            with home.db() as db:db.execute('UPDATE conflicts SET incoming=NULL')
            with self.assertRaises(ValueError):home.resolve('notes.txt',item['id'],digest('newer edit'),'car')

    def test_resolution_intent_recovers_after_restart_without_losing_home_choice(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);car,home,item=self.conflict_pair(root)
            with patch.object(home,'edit',side_effect=RuntimeError('simulated process interruption')):
                with self.assertRaises(RuntimeError):home.resolve('notes.txt',item['id'],digest('home edit'),'home')
            self.assertEqual(home.read('notes.txt'),'car edit')
            restarted=Files(root/'home',root/'home-state.db',Events(root/'home-events.db',KEY,destination='home'),'home','tiny',['notes.txt'])
            restarted.scan();self.settle(car,restarted)
            self.assertEqual(car.read('notes.txt'),'home edit');self.assertEqual(restarted.read('notes.txt'),'home edit')

    def test_recovery_preserves_external_edit_and_allows_a_reviewed_new_choice(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);car,home,item=self.conflict_pair(root)
            with patch.object(home,'edit',side_effect=RuntimeError('simulated process interruption')):
                with self.assertRaises(RuntimeError):home.resolve('notes.txt',item['id'],digest('home edit'),'home')
            (home.root/'notes.txt').write_text('newer external edit');home.scan()
            view=home.inspect('notes.txt')
            self.assertEqual(view['phase'],'resolution-changed-locally');self.assertEqual(view['recovery']['saved'],'home edit')
            self.assertFalse(home.pending());self.assertEqual(view['source'],'newer external edit')
            home.resolve('notes.txt',item['id'],view['sha256'],'home');self.settle(car,home)
            self.assertEqual(car.read('notes.txt'),'newer external edit')

    def test_blocked_resolution_keeps_choice_and_other_files_continue(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            car,home,item=self.conflict_pair(Path(directory));home.selected.add('other.txt')
            (home.root/'other.txt').write_text('unrelated file')
            with patch.object(home,'edit',side_effect=PermissionError('temporary disk permission')):
                view=home.resolve('notes.txt',item['id'],digest('home edit'),'home');home.scan()
            self.assertEqual(view['phase'],'resolution-pending');self.assertEqual(view['recovery']['saved'],'home edit')
            self.assertEqual([item['doc']['name'] for item in home.pending()],['other.txt'])
            home.scan();self.assertEqual(home.read('notes.txt'),'home edit')
            self.assertEqual({item['doc']['name'] for item in home.pending()},{'notes.txt','other.txt'})

    def test_concurrent_editors_serialize_and_do_not_overwrite_each_other(self):
        import threading
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);car,home=self.pair(root);(home.root/'notes.txt').write_text('original')
            other=Files(home.root,home.state,home.events,'home','tiny',['notes.txt'])
            barrier=threading.Barrier(2);results=[]
            def edit(files,value):
                barrier.wait()
                try:files.edit('notes.txt',digest('original'),value);results.append('saved')
                except ValueError:results.append('stale')
            threads=[threading.Thread(target=edit,args=(files,value)) for files,value in [(home,'one'),(other,'two')]]
            for thread in threads:thread.start()
            for thread in threads:thread.join(timeout=5)
            self.assertEqual(sorted(results),['saved','stale']);self.assertIn(home.read('notes.txt'),('one','two'))

    def test_full_backup_folder_stops_editing_without_deleting_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            car,home=self.pair(Path(directory));(home.root/'notes.txt').write_text('original')
            folder=home.state.parent/(home.state.stem+'-backups');folder.mkdir()
            for index in range(32):(folder/str(index)).write_text('preserved backup')
            with self.assertRaises(ValueError):home.edit('notes.txt',digest('original'),'replacement')
            self.assertEqual(home.read('notes.txt'),'original');self.assertEqual(len(list(folder.iterdir())),32)

    def test_reordered_return_transfer_and_superseded_old_frames_do_not_undo_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            car,home,item=self.conflict_pair(Path(directory));superseded=home.pending()[0]
            home.resolve('notes.txt',item['id'],digest('home edit'),'merge','reviewed merge')
            # The return data can arrive before the acceptance receipt for the
            # original car transfer, especially over delayed radio downlinks.
            reverse=home.pending()[0];receipt=car.receive(reverse['doc'],reverse['id'],via_ip=True)
            home.receive(receipt['doc'],receipt['id'],via_ip=True)
            delayed=car.receive(superseded['doc'],superseded['id'],via_ip=True)
            self.assertEqual(delayed['doc']['status'],'conflict');self.assertEqual(car.read('notes.txt'),'reviewed merge')
            self.settle(car,home)
            self.assertEqual(car.read('notes.txt'),'reviewed merge');self.assertEqual(home.read('notes.txt'),'reviewed merge')
            self.assertFalse(car.pending());self.assertFalse(home.pending())

    def test_bidirectional_ip_keeps_same_ids_and_confirms_applied(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);car,home=self.pair(root)
            (car.root/'notes.txt').write_text('first version\n');car.scan();item=car.pending()[0]
            reply=home.receive(item['doc'],item['id'],via_ip=True)
            self.assertEqual((home.root/'notes.txt').read_text(),'first version\n')
            self.assertEqual(car.status()[0]['phase'],'queued')
            car.receive(reply['doc'],reply['id'],via_ip=True)
            self.assertEqual(car.pending(),[]);self.assertEqual(car.status()[0]['phase'],'applied')
            (home.root/'notes.txt').write_text('second version\n');home.scan();reverse=home.pending()[0]
            self.assertEqual(reverse['doc']['base'],digest('first version\n'))
            answer=car.receive(reverse['doc'],reverse['id'])
            home.receive(answer['doc'],answer['id'],via_ip=True)
            self.assertEqual((car.root/'notes.txt').read_text(),'second version\n');self.assertEqual(home.pending(),[])

    def test_conflict_preserves_both_user_edits_and_does_not_claim_applied(self):
        with tempfile.TemporaryDirectory() as directory:
            car,home=self.pair(Path(directory));(car.root/'notes.txt').write_text('car edit');(home.root/'notes.txt').write_text('home edit')
            car.scan();item=car.pending()[0];reply=home.receive(item['doc'],item['id'],via_ip=True)
            self.assertEqual(reply['doc']['status'],'conflict');car.receive(reply['doc'],reply['id'])
            self.assertEqual(car.status()[0]['phase'],'conflict')
            self.assertEqual((home.root/'notes.txt').read_text(),'home edit');self.assertEqual((car.root/'notes.txt').read_text(),'car edit')

    def test_radio_fragments_restart_and_ip_completion_share_transfer(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);car,home=self.pair(root);(car.root/'notes.txt').write_text('short content');car.scan();item=car.pending()[0]
            for _ in range(3):
                frame=car.events.outgoing();home.events.receive(frame);car.events.sent(frame)
            home=Files(root/'home',root/'home-state.db',Events(root/'home-events.db',KEY,destination='home'),'home','tiny',['notes.txt'])
            reply=home.receive(item['doc'],item['id'],via_ip=True);car.receive(reply['doc'],reply['id'],via_ip=True)
            self.assertEqual(car.pending(),[])
            self.assertEqual((home.root/'notes.txt').read_text(),'short content')
            # Replayed complete transfer is idempotent, even after the IP finish.
            again=home.receive(item['doc'],item['id'],via_ip=True);self.assertEqual(again['doc']['status'],'applied')

    def test_radio_only_applies_and_receipts_then_advances_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);car,home=self.pair(root);(car.root/'notes.txt').write_text('radio only');car.scan();item=car.pending()[0]
            result=None
            for _ in range(100):
                frame=car.events.outgoing();result=home.events.receive(frame);car.events.sent(frame)
                if result and result.get('type')=='file':break
            self.assertEqual(result['target'],digest('radio only'))
            receipt=home.receive(result,item['id']);self.assertEqual(receipt['doc']['status'],'applied')
            result=None
            for _ in range(200):
                frame=home.events.outgoing(ack=True)
                ack=frame is not None
                frame=frame or home.events.outgoing()
                if frame is None:break
                result=car.events.receive(frame);home.events.sent(frame,ack=ack)
                if result and result.get('type')=='file-receipt':break
            car.receive(result,receipt['id']);self.assertEqual(car.status()[0]['phase'],'applied')

    def test_large_data_waits_for_ip_and_unsafe_names_or_symlinks_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);car,home=self.pair(root);(car.root/'notes.txt').write_text('x'*3000);car.scan()
            self.assertEqual(car.status()[0]['phase'],'waiting-ip');self.assertIsNone(car.events.outgoing())
            item=car.pending()[0];reply=home.receive(item['doc'],item['id'],via_ip=True)
            self.assertEqual(reply['doc']['status'],'applied')
            for name in ('../notes.txt','/etc/passwd','file.py','folder/notes.txt'):
                with self.assertRaises(ValueError):filename(name)
            (home.root/'notes.txt').unlink();(home.root/'notes.txt').symlink_to(root/'outside')
            with self.assertRaises(ValueError):home.receive(item['doc'],item['id'],via_ip=True)

    def test_unselected_file_cannot_be_written_and_bad_hash_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);car,home=self.pair(root);(car.root/'notes.txt').write_text('safe');car.scan();item=car.pending()[0]
            home.selected=set();reply=home.receive(item['doc'],item['id'],via_ip=True)
            self.assertEqual(reply['doc']['status'],'not-selected');self.assertFalse((home.root/'notes.txt').exists())
            home.selected={'notes.txt'}
            with self.assertRaises(ValueError):home.receive(dict(item['doc'],target='0'*64),item['id'],via_ip=True)
            self.assertFalse((home.root/'notes.txt').exists())

    def test_old_completed_transfer_never_overwrites_a_later_local_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            car,home=self.pair(Path(directory));(car.root/'notes.txt').write_text('first version');car.scan();item=car.pending()[0]
            home.receive(item['doc'],item['id'],via_ip=True)
            (home.root/'notes.txt').write_text('newer local edit')
            replay=home.receive(item['doc'],item['id'],via_ip=True)
            self.assertEqual(replay['doc']['status'],'applied')
            self.assertEqual((home.root/'notes.txt').read_text(),'newer local edit')

    def test_deselection_pauses_queued_radio_and_ip_objects_without_deleting_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);car,home=self.pair(root);(car.root/'notes.txt').write_text('private selected data');car.scan()
            car=Files(root/'car',root/'car-state.db',Events(root/'car-events.db',KEY,destination='tiny',types=('file','file-receipt')),'tiny','home',[])
            self.assertEqual(car.pending(),[]);self.assertIsNone(car.events.outgoing())
            self.assertEqual(car.status()[0]['phase'],'paused-unselected')
            self.assertEqual((car.root/'notes.txt').read_text(),'private selected data')

    def test_utf8_and_windows_line_endings_are_preserved_exactly(self):
        with tempfile.TemporaryDirectory() as directory:
            car,home=self.pair(Path(directory));content='hello 🚗\r\nnext line\r\n'.encode()
            (car.root/'notes.txt').write_bytes(content);car.scan();item=car.pending()[0]
            home.receive(item['doc'],item['id'],via_ip=True)
            self.assertEqual((home.root/'notes.txt').read_bytes(),content)

    def test_identical_retried_content_keeps_the_new_event_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            car,home=self.pair(Path(directory));(car.root/'notes.txt').write_text('same change');car.scan();item=car.pending()[0]
            newer='sync:'+'a'*32;car.events.enqueue(newer,item['doc'])
            received=None
            for _ in range(200):
                raw=car.events.outgoing();received=home.events.receive(raw,with_id=True);car.events.sent(raw)
                if received and received['id']==newer:break
            self.assertEqual(received['id'],newer)

    def test_invalid_file_is_isolated_and_recovers_without_restarting_service(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);car,home=self.pair(root);car.selected={'notes.txt','other.txt'}
            (car.root/'notes.txt').write_bytes(b'\xff');(car.root/'other.txt').write_text('still works')
            car.scan();self.assertEqual([item['doc']['name'] for item in car.pending()],['other.txt'])
            blocked=next(row for row in car.status() if row['name']=='notes.txt')
            self.assertEqual(blocked['phase'],'blocked');self.assertIn('UTF-8',blocked['error'])
            (car.root/'notes.txt').write_text('repaired');car.scan()
            self.assertEqual({item['doc']['name'] for item in car.pending()},{'notes.txt','other.txt'})
            self.assertFalse(any(row['phase']=='blocked' for row in car.status()))

    def test_blocked_pending_transfer_pauses_then_resumes_with_same_id(self):
        with tempfile.TemporaryDirectory() as directory:
            car,home=self.pair(Path(directory));(car.root/'notes.txt').write_text('original');car.scan();item=car.pending()[0]
            (car.root/'notes.txt').write_text('x'*20000);car.scan()
            self.assertEqual(car.pending(),[]);self.assertIsNone(car.events.outgoing())
            (car.root/'notes.txt').write_text('original');car.scan()
            self.assertEqual(car.pending()[0]['id'],item['id']);self.assertIsNotNone(car.events.outgoing())

    def test_receive_defers_unwritable_target_without_applied_receipt(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            car,home=self.pair(Path(directory));(car.root/'notes.txt').write_text('retain source');car.scan();item=car.pending()[0]
            with patch('control.file_sync.atomic',side_effect=PermissionError('sensitive path')):
                self.assertIsNone(home.receive_available(item['doc'],item['id'],via_ip=True))
            self.assertEqual(home.status()[0]['phase'],'blocked')
            self.assertNotIn('sensitive',json.dumps(home.status()));self.assertEqual(car.pending()[0]['id'],item['id'])
            reply=home.receive_available(item['doc'],item['id'],via_ip=True)
            self.assertEqual(reply['doc']['status'],'applied');self.assertEqual(home.status()[0]['phase'],'applied')
