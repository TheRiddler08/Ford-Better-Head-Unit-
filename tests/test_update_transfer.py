import json
import importlib.util
from pathlib import Path
import tempfile
import unittest
from control.software import Software
from control.update_transfer import Journal,APPLIED,encode,decode,canonical,digest

KEY=bytes(range(32))


@unittest.skipUnless(importlib.util.find_spec('cryptography'),'native cryptography dependency not installed yet')
class TransferTests(unittest.TestCase):
    def prepare(self,root):
        home=Software(root/'home')
        source='let speed = 10;\n'
        home.inventory('car-1','tiny',[{'path':'app/web/cockpit.js','source':source,'sha256':digest(source.encode())}])
        job=home.publish('car-1','tiny','app/web/cockpit.js',digest(source.encode()),'let speed = 20;\n')
        return home,job['id']
    def test_wifi_then_radio_resume_after_both_processes_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);home,ident=self.prepare(root);car=Journal(root/'car.db')
            for _ in range(7):
                frame=home.journal.next(ident,KEY,0)
                home.journal.mark_sent(frame,KEY)
                ack=car.receive(frame,KEY,home.public,'car-1','tiny')
                if ack:home.journal.receive(ack,KEY,home.public,'car-1','tiny')
            saved=car.status()[0]['received']
            home=Software(root/'home');car=Journal(root/'car.db')
            self.assertEqual(car.status()[0]['received'],saved)
            for _ in range(1000):
                if car.ready():break
                frame=home.journal.next(ident,KEY,0)
                home.journal.mark_sent(frame,KEY)
                self.assertIsNotNone(frame)
                self.assertLessEqual(len(frame),46)
                ack=car.receive(frame,KEY,home.public,'car-1','tiny')
                if ack:home.journal.receive(ack,KEY,home.public,'car-1','tiny')
            else:self.fail('Transfer did not finish')
            self.assertEqual(home.journal.status()[0]['phase'],'queued')
            short,doc,payload=car.ready()[0]
            self.assertEqual(digest(payload),doc['sha256'])
            car.applied(short)
            home.journal.receive(encode(APPLIED,short,0,1,b'applied',KEY),KEY,home.public,'car-1','tiny')
            self.assertEqual(home.journal.status()[0]['phase'],'applied')
    def test_duplicate_and_tampered_fragment(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);home,ident=self.prepare(root);car=Journal(root/'car.db')
            frame=home.journal.next(ident,KEY,0)
            ack=car.receive(frame,KEY,home.public,'car-1','tiny')
            self.assertEqual(car.receive(frame,KEY,home.public,'car-1','tiny'),ack)
            self.assertEqual(car.status()[0]['received'],1)
            with self.assertRaises(ValueError):car.receive(frame[:-1]+bytes([frame[-1]^1]),KEY,home.public,'car-1','tiny')
    def test_source_conflict_and_no_double_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);home,_=self.prepare(root)
            with self.assertRaises(ValueError):home.publish('car-1','tiny','app/web/cockpit.js','0'*64,'bad')
            base=home.source('car-1','tiny','app/web/cockpit.js')['base']
            with self.assertRaises(ValueError):home.publish('car-1','tiny','app/web/cockpit.js',base,'let speed=30;')
    def test_radio_budget_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'queue.db';journal=Journal(path)
            self.assertTrue(journal.budget('downlinks',0,1))
            self.assertFalse(Journal(path).budget('downlinks',0,1))

    def test_other_role_never_acknowledges_manifest_or_exhausts_receiver_slots(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);home,ident=self.prepare(root);other=Journal(root/'other.db')
            with home.journal.db() as db:
                rows=db.execute('SELECT idx,count,data FROM parts WHERE id=? AND kind=0 ORDER BY idx',(ident,)).fetchall()
            for index,count,data in rows:
                self.assertIsNone(other.receive(encode(0,ident,index,count,data,KEY),KEY,home.public,'car-1','duet'))
            self.assertEqual(other.status()[0]['phase'],'ignored')
            with other.db() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs WHERE phase NOT IN ('applied','failed','ignored')").fetchone()[0],0)

    def test_out_of_order_payload_only_becomes_ready_after_missing_chunk(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);home,ident=self.prepare(root);car=Journal(root/'car.db')
            with home.journal.db() as db:
                manifest=db.execute('SELECT idx,count,data FROM parts WHERE id=? AND kind=0 ORDER BY idx DESC',(ident,)).fetchall()
                data=db.execute('SELECT idx,count,data FROM parts WHERE id=? AND kind=1 ORDER BY idx DESC',(ident,)).fetchall()
            for index,count,payload in manifest:car.receive(encode(0,ident,index,count,payload,KEY),KEY,home.public,'car-1','tiny')
            missing=data.pop(len(data)//2)
            for index,count,payload in data:car.receive(encode(1,ident,index,count,payload,KEY),KEY,home.public,'car-1','tiny')
            self.assertFalse(car.ready())
            car=Journal(root/'car.db');index,count,payload=missing
            car.receive(encode(1,ident,index,count,payload,KEY),KEY,home.public,'car-1','tiny')
            self.assertEqual(len(car.ready()),1)

    def test_unchanged_copy_does_not_queue_or_transmit_an_update(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Software(Path(temp));source='let speed = 10;\n';base=digest(source.encode())
            home.inventory('car-1','tiny',[{'path':'app/web/cockpit.js','source':source,'sha256':base}])
            result=home.publish('car-1','tiny','app/web/cockpit.js',base,source)
            self.assertEqual(result['phase'],'already-current')
            self.assertEqual(home.journal.status(),[])
