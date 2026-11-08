"""Sender controls preserve signed identity, durable ACKs and radio budgets."""
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from control.software import Software
from control.transfer_status import transfers,budget
from control.update_transfer import Journal,encode,decode,ACK,APPLIED,ERROR


@unittest.skipUnless(importlib.util.find_spec('cryptography'),'Normal home signing dependency required')
class TransferControlTests(unittest.TestCase):
    def prepare(self,root,path='app/web/cockpit.js'):
        from control.update_transfer import digest
        service=Software(root/'home');source='let speed = 10;\n'
        service.inventory('car-1','tiny',[{'path':path,'source':source,'sha256':digest(source.encode())}])
        ident=service.publish('car-1','tiny',path,digest(source.encode()),source+'// change\n')['id']
        return service,ident

    def test_pause_resume_and_policy_persist_without_replacing_signed_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);service,ident=self.prepare(root)
            with service.journal.db() as db:document=db.execute('SELECT doc FROM jobs WHERE id=?',(ident,)).fetchone()[0]
            result=service.delivery('car-1',ident,'pause',0)
            self.assertEqual(service.outgoing('car-1'),[]);self.assertIsNone(service.journal.next(ident,service.key()))
            service=Software(root/'home');self.assertEqual(transfers(service.journal,'car-1')['items'][0]['displayPhase'],'paused')
            service.delivery('car-1',ident,'resume',result['revision'])
            result=service.delivery('car-1',ident,'policy',2,'ip-only')
            self.assertEqual(service.outgoing('car-1',transport='lorawan'),[])
            self.assertTrue(service.outgoing('car-1',mark=False,transport='ip'))
            with service.journal.db() as db:self.assertEqual(db.execute('SELECT doc FROM jobs WHERE id=?',(ident,)).fetchone()[0],document)
            with self.assertRaises(ValueError):service.delivery('car-1',ident,'pause',0)
            with self.assertRaises(ValueError):service.delivery('other-car',ident,'resume',result['revision'])

    def test_retry_keeps_receipts_and_does_not_reset_daily_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            service,ident=self.prepare(Path(directory));car=Journal(Path(directory)/'car.db')
            with service.journal.db() as db:manifest=db.execute('SELECT idx,count,data FROM parts WHERE id=? AND kind=0 ORDER BY idx',(ident,)).fetchall()
            for index,count,data in manifest:reply=car.receive(encode(0,ident,index,count,data,service.key()),service.key(),service.public,'car-1','tiny')
            service.receipts('car-1','tiny',[reply.hex()])
            self.assertTrue(service.journal.budget('mqtt-downlinks',0,1));before=budget(service.journal)
            with service.journal.db() as db:receipts=db.execute('SELECT * FROM receipts WHERE id=?',(ident,)).fetchall()
            service.delivery('car-1',ident,'retry',0)
            with service.journal.db() as db:self.assertEqual(db.execute('SELECT * FROM receipts WHERE id=?',(ident,)).fetchall(),receipts)
            self.assertEqual(budget(service.journal)['reserved'],before['reserved'])
            self.assertFalse(service.journal.budget('mqtt-downlinks',0,1))
            self.assertTrue(service.outgoing('car-1',mark=False))

    def test_late_applied_receipt_finishes_a_paused_transfer_and_terminal_jobs_cannot_be_reopened(self):
        with tempfile.TemporaryDirectory() as directory:
            service,ident=self.prepare(Path(directory));service.delivery('car-1',ident,'pause',0)
            service.receipts('car-1','tiny',[encode(APPLIED,ident,0,1,b'applied',service.key()).hex()])
            row=transfers(service.journal,'car-1',state='finished')['items'][0]
            self.assertEqual(row['displayPhase'],'applied');self.assertEqual(row['decision'],'finished')
            for action in ('resume','retry'):
                with self.assertRaises(ValueError):service.delivery('car-1',ident,action,1)

    def test_recent_ip_holds_radio_only_until_the_bounded_fallback_window(self):
        with tempfile.TemporaryDirectory() as directory:
            service,ident=self.prepare(Path(directory));now=time.time();service.journal.note_ip('car-1','tiny',now=now)
            self.assertTrue(service.outgoing('car-1',mark=False,transport='ip'))
            self.assertEqual(service.outgoing('car-1',mark=False,transport='lorawan'),[])
            service.journal.note_ip('car-1','tiny',now=now-46)
            self.assertTrue(service.outgoing('car-1',mark=False,transport='lorawan'))
            service.journal.note_ip('other-car','tiny',now=now)
            self.assertTrue(service.outgoing('car-1',mark=False,transport='lorawan'))

    def test_high_priority_is_scheduled_before_normal_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);service,first=self.prepare(root)
            from control.update_transfer import digest
            path='app/web/app.js';source='const value = 1;'
            service.inventory('car-1','tiny',[{'path':path,'source':source,'sha256':digest(source.encode())}])
            second=service.publish('car-1','tiny',path,digest(source.encode()),source+'\n// second')['id']
            service.delivery('car-1',second,'priority',0,'high')
            raw=bytes.fromhex(service.outgoing('car-1',limit=1,mark=False)[0]);self.assertEqual(decode(raw,service.key())[1],second[:16])

    def test_radio_estimate_counts_actual_sealed_envelopes_without_transmitting(self):
        try:from control.radio_crypto import RadioBox,identity,pin
        except ImportError:self.skipTest('Normal radio encryption dependency required')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);service,ident=self.prepare(root);peer=identity(root/'car.key');pin(root/'car.pub',bytes(peer.public_key).hex())
            box=RadioBox(root/'home.key',root/'car.pub',root/'wire.db',b'D',46)
            with service.journal.db() as db:parts=db.execute('SELECT kind,idx,count,data FROM parts WHERE id=?',(ident,)).fetchall()
            expected=sum(len(box.frames(encode(kind,ident,index,count,data,service.key()))[1]) for kind,index,count,data in parts)
            self.assertEqual(transfers(service.journal,'car-1')['items'][0]['radioFramesPerPass'],expected)
            with service.journal.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)

    def test_receipt_completion_stays_awaiting_application_and_retry_solicits_final_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            service,ident=self.prepare(Path(directory))
            with service.journal.db() as db:
                db.execute('INSERT INTO receipts SELECT id,kind,idx FROM parts WHERE id=?',(ident,));db.execute('UPDATE parts SET sent=?',(time.time(),))
            row=transfers(service.journal,'car-1')['items'][0]
            self.assertEqual(row['progress'],100);self.assertEqual(row['displayPhase'],'awaiting-application')
            service.delivery('car-1',ident,'retry',0)
            self.assertEqual(decode(bytes.fromhex(service.outgoing('car-1',limit=1)[0]),service.key())[0],1)

    def test_queue_pagination_keeps_old_active_jobs_accessible(self):
        with tempfile.TemporaryDirectory() as directory:
            service,ident=self.prepare(Path(directory))
            with service.journal.db() as db:
                doc=json.loads(db.execute('SELECT doc FROM jobs WHERE id=?',(ident,)).fetchone()[0])
                for index in range(40):
                    key=f'{index+1:016x}'+'1'*48;db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?)',(key,key[:16],json.dumps(doc),'out','queued',time.time(),None))
            first=transfers(service.journal,'car-1');second=transfers(service.journal,'car-1',before=first['nextCursor'])
            self.assertEqual(len(first['items']),25);self.assertEqual(len(second['items']),16)
            self.assertIn(ident,[row['id'] for row in second['items']]);self.assertIsNone(second['nextCursor'])

    def test_home_ha_distinguishes_paused_from_receiving(self):
        from control.home_ha import journal_states
        with tempfile.TemporaryDirectory() as directory:
            service,ident=self.prepare(Path(directory));service.delivery('car-1',ident,'pause',0)
            state=journal_states(service.journal)['tiny'];self.assertEqual(state['phase'],'paused');self.assertEqual(state['paused'],1)
