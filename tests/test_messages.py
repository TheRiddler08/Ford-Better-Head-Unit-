import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,MagicMock

from control.event_transfer import Events
from control.messages import enqueue,listing,validate,status_reply,cancel

KEY=bytes(range(32))


class MessageTests(unittest.TestCase):
    def test_retry_identity_reuses_original_and_rejects_different_content(self):
        with tempfile.TemporaryDirectory() as directory:
            events=Events(Path(directory)/'events.db',KEY)
            first=enqueue(events,'tiny','home','One message',request_id='a'*32)
            again=enqueue(events,'tiny','home','One message',request_id='a'*32)
            self.assertEqual(first,again);self.assertEqual(len(listing(events)['items']),1)
            with self.assertRaises(ValueError):enqueue(events,'tiny','home','Different',request_id='a'*32)
            with self.assertRaises(ValueError):enqueue(events,'tiny','home','Bad',request_id='../bad')

    def test_progress_is_transport_attempts_and_cancel_stays_cancelled_after_late_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);car=Events(root/'car.db',KEY,destination='tiny');home=Events(root/'home.db',KEY,destination='home')
            item=enqueue(car,'tiny','home','Queued for delivery')
            self.assertEqual(listing(car)['items'][0]['progress']['state'],'waiting')
            raw=car.outgoing();home.receive(raw);car.sent(raw)
            self.assertEqual(listing(car)['items'][0]['progress']['state'],'sending')
            self.drain(car,home)
            while listing(car)['items'][0]['progress']['state']!='awaiting-receipt':
                raw=car.outgoing();car.sent(raw)
            self.assertEqual(listing(car)['items'][0]['phase'],'queued')
            cancel(car,item['id']);self.assertIsNone(car.outgoing());self.assertTrue(car.completed_frames(item['id']))
            self.drain(home,car,ack=True)
            self.assertEqual(listing(car)['items'][0]['phase'],'cancelled')
            self.assertEqual(listing(car)['counts']['cancelled'],1)
            with self.assertRaises(ValueError):cancel(car,'unknown')

    def test_delivered_cannot_be_recalled_and_unicode_byte_limit_is_consistent(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);car=Events(root/'car.db',KEY);home=Events(root/'home.db',KEY,destination='home')
            item=enqueue(car,'tiny','home','🚗'*60)
            self.drain(car,home);self.drain(home,car,ack=True)
            self.assertEqual(listing(car)['items'][0]['phase'],'delivered')
            with self.assertRaisesRegex(ValueError,'cannot be recalled'):cancel(car,item['id'])
            with self.assertRaises(ValueError):enqueue(car,'tiny','home','🚗'*61)

    def test_only_fixed_read_only_status_action_and_idempotent_reply(self):
        with tempfile.TemporaryDirectory() as directory:
            events=Events(Path(directory)/'events.db',KEY,destination='tiny')
            request={'type':'message','source':'home','destination':'tiny','text':'health','created':1,'action':'status-request'}
            with patch('control.messages.Path.read_text',return_value='1000.5 50'),patch('shutil.disk_usage',return_value=MagicMock(free=5*1024**2)):
                status_reply(events,request,'tiny',directory);status_reply(events,request,'tiny',directory)
            result=listing(events)['items'];self.assertEqual(len(result),1)
            summary=json.loads(result[0]['text']);self.assertEqual(summary['uptimeSeconds'],1000)
            self.assertNotIn('gps',summary);self.assertNotIn('obd',summary)
            with self.assertRaises(ValueError):validate(dict(request,action='shell'))

    def drain(self,source,target,ack=False):
        for _ in range(100):
            raw=source.outgoing(ack=ack)
            if raw is None:return
            target.receive(raw);target.receive(raw);source.sent(raw,ack=ack)
            if not ack and target.catalog('message'):return
        self.fail('Message did not complete')

    def test_bidirectional_restart_dedup_and_only_durable_ack_delivers(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);car=Events(root/'car.db',KEY,destination='tiny');home=Events(root/'home.db',KEY,destination='home')
            item=enqueue(car,'tiny','home','Arriving soon — hello')
            first=car.outgoing();home.receive(first);car.sent(first)
            self.assertEqual(listing(car)['items'][0]['phase'],'queued')
            car=Events(root/'car.db',KEY,destination='tiny');home=Events(root/'home.db',KEY,destination='home')
            self.drain(car,home)
            self.assertEqual(len(listing(home)['items']),1)
            self.assertEqual(listing(home)['items'][0]['text'],item['text'])
            self.assertEqual(listing(car)['items'][0]['phase'],'queued')
            self.drain(home,car,ack=True)
            self.assertEqual(listing(car)['items'][0]['phase'],'delivered')
            self.assertTrue(car.completed_frames(item['id']))
            reply=enqueue(home,'home','tiny','Received, thanks')
            self.drain(home,car);self.drain(car,home,ack=True)
            self.assertTrue(any(row['id']==reply['id'] and row['phase']=='delivered' for row in listing(home)['items']))

    def test_other_role_never_stores_or_acknowledges_message(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);home=Events(root/'home.db',KEY);wrong=Events(root/'wrong.db',KEY,destination='duet')
            enqueue(home,'home','tiny','Only for Tiny')
            for _ in range(60):
                raw=home.outgoing();wrong.receive(raw);home.sent(raw)
            self.assertEqual(listing(wrong)['items'],[])
            self.assertIsNone(wrong.outgoing(ack=True))

    def test_encrypted_message_fragments_never_contain_original_text(self):
        try:from control.radio_crypto import RadioBox,identity,pin
        except ModuleNotFoundError as exc:
            if exc.name!='nacl':raise
            self.skipTest('PyNaCl required')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);a=identity(root/'a.key');b=identity(root/'b.key')
            pin(root/'a.pub',bytes(a.public_key).hex());pin(root/'b.pub',bytes(b.public_key).hex())
            sender=RadioBox(root/'a.key',root/'b.pub',root/'a.db',b'U',11)
            receiver=RadioBox(root/'b.key',root/'a.pub',root/'b.db',b'D',11)
            car=Events(root/'car.db',KEY);home=Events(root/'home.db',KEY,destination='home')
            enqueue(car,'tiny','home','Private radio message')
            for _ in range(64):
                raw=car.outgoing()
                for wire in sender.frames(raw)[1]:
                    self.assertLessEqual(len(wire),11)
                    self.assertNotIn(b'Private',wire)
                    plain=receiver.accept(wire)
                    if plain:home.receive(plain)
                car.sent(raw)
                if home.catalog('message'):break
            self.assertEqual(home.catalog('message')[0]['text'],'Private radio message')

    def test_invalid_content_and_full_queue_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            events=Events(Path(directory)/'events.db',KEY)
            for text in ('','  ','x'*241,'é'*121,'bad\x00data'):
                with self.subTest(text=text),self.assertRaises(ValueError):enqueue(events,'tiny','home',text)
            with self.assertRaises(ValueError):enqueue(events,'home','home','Invalid destination')
            for _ in range(100):enqueue(events,'tiny','home','short')
            with self.assertRaisesRegex(ValueError,'queue full'):enqueue(events,'tiny','home','one more')

    def test_conflicting_event_id_does_not_replace_saved_message(self):
        with tempfile.TemporaryDirectory() as directory:
            events=Events(Path(directory)/'events.db',KEY)
            doc={'type':'message','source':'tiny','destination':'home','text':'one','created':1}
            events.enqueue('message:fixed',doc)
            with self.assertRaises(ValueError):events.enqueue('message:fixed',dict(doc,text='two'))

    def test_gps_radio_default_is_deferred_even_with_existing_pending_sample(self):
        from control.car_data import Store,radio_catalog
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);creds=root/'.config/car-dashboard';creds.mkdir(parents=True);(creds/'update-link.key').write_text(KEY.hex())
            store=Store(root/'data','tiny')
            events=Events(store.state/'radio-catalog.sqlite3',KEY)
            events.enqueue('gps:tiny:old',{'type':'gps','source':'tiny','lat':0,'lon':0})
            with patch('control.car_data.recorder_paths',side_effect=AssertionError('GPS must not be read')),patch('control.car_data.socket.socket') as socket:
                status=radio_catalog(store,root,'tiny')
                socket.return_value.__enter__.return_value.sendto.assert_not_called()
            self.assertIn('gps-radio-deferred',status)

    def test_old_gps_relay_frames_are_retired_without_erasing_local_history(self):
        import hashlib,sqlite3
        from control.radio_gateway import suppress_deferred_gps
        from control.lorawan_protocol import split_message
        from control.update_transfer import Journal
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);events=Events(root/'.local/share/car-data/radio-catalog.sqlite3',KEY)
            doc={'type':'gps','lat':1,'lon':2};events.enqueue('gps:old',doc)
            gps=split_message('status',1,{'catalog':doc,'id':'gps:old'},KEY)[0]
            journal=Journal(root/'relay.db')
            with journal.db() as db:
                db.execute('CREATE TABLE uplinks(id TEXT PRIMARY KEY,frame TEXT,sent INTEGER DEFAULT 0)')
                db.execute('INSERT INTO uplinks VALUES(?,?,0)',(hashlib.sha256(gps).hexdigest(),gps.hex()))
                db.execute("INSERT INTO uplinks VALUES('unrelated','unchanged',0)")
            suppress_deferred_gps(journal,root,KEY)
            with journal.db() as db:
                self.assertEqual(db.execute('SELECT sent FROM uplinks WHERE id=?',(hashlib.sha256(gps).hexdigest(),)).fetchone()[0],2)
                self.assertEqual(db.execute("SELECT sent FROM uplinks WHERE id='unrelated'").fetchone()[0],0)
            with events.db() as db:self.assertEqual(json.loads(db.execute('SELECT doc FROM jobs').fetchone()[0]),doc)
