import json
from pathlib import Path
import tempfile
import unittest
from control.event_transfer import Events
from control.file_sync import Files,digest,filename

KEY=bytes(range(32))


class FileSyncTests(unittest.TestCase):
    def pair(self,root):
        car=Files(root/'car',root/'car-state.db',Events(root/'car-events.db',KEY,destination='tiny',types=('file','file-receipt')),'tiny','home',['notes.txt'])
        home=Files(root/'home',root/'home-state.db',Events(root/'home-events.db',KEY,destination='home'),'home','tiny',['notes.txt'])
        car.root.mkdir();home.root.mkdir();return car,home

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
