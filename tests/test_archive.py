import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from app import reporting
from recorder import gps_recorder as gps
from recorder import obd_full_recorder as obd


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.db=obd.open_db(self.root/'vehicle.sqlite3');self.addCleanup(self.db.close)
        self.gps=gps.open_db(self.root/'gps.sqlite3');self.addCleanup(self.gps.close)

    def test_every_query_outcome_and_legacy_data(self):
        command=SimpleNamespace(name='RPM',command=b'010C')
        class Reply:
            value=1350
            messages=[SimpleNamespace(raw=lambda:b'41 0C 15 18')]
            def is_null(self):return False
        class Connection:
            def query(self,c):return Reply()
        self.assertTrue(obd.collect_query(self.db,Connection(),command))
        class Empty:
            def query(self,c):return None
        self.assertFalse(obd.collect_query(self.db,Empty(),command))
        class Error:
            def query(self,c):raise OSError('adapter disconnected')
        self.assertFalse(obd.collect_query(self.db,Error(),command))
        rejected=SimpleNamespace(name='CLEAR_DTC',command=b'04')
        self.assertFalse(obd.collect_query(self.db,Connection(),rejected))
        statuses=[row[0] for row in self.db.execute('SELECT status FROM command_attempts')]
        self.assertEqual(statuses,['ok','empty','error','rejected'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM readings').fetchone()[0],1)
        self.assertIn('adapter disconnected',self.db.execute("SELECT error FROM command_attempts WHERE status='error'").fetchone()[0])
        self.assertTrue(self.db.execute('SELECT raw FROM command_attempts LIMIT 1').fetchone()[0])
        # Reopening adds tables without deleting the user's existing readings.
        self.db.close();self.db=obd.open_db(self.root/'vehicle.sqlite3')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM readings').fetchone()[0],1)
        self.db.close()

    def test_lossless_cursor_and_full_export(self):
        self.db.executemany('INSERT INTO readings VALUES(?,?,?,?,?)',[(i,'RPM',str(i),i,'rpm') for i in range(1203)])
        self.db.commit()
        reader=reporting.connect(self.root/'vehicle.sqlite3');self.addCleanup(reader.close)
        first=reporting.page(reader,'values',{'limit':['500']})
        self.assertEqual(len(first['items']),500);self.assertTrue(first['hasMore'])
        second=reporting.page(reader,'values',{'after':[str(first['next'])],'limit':['500']})
        self.assertEqual(second['items'][0]['number'],500)
        exported=b''.join(reporting.csv_batches(reader,'values',{})).decode()
        self.assertEqual(len(list(csv.reader(io.StringIO(exported)))),1204)
        filtered=list(csv.reader(io.StringIO(b''.join(reporting.csv_batches(reader,'values',{'from':['1100'],'to':['1200']})).decode())))
        self.assertEqual(len(filtered),102)
        with self.assertRaises(Exception):reader.execute('DELETE FROM readings')
        for bad in [{'limit':['nan']},{'after':['-1']},{'limit':['9999']}]:
            with self.assertRaises(ValueError):reporting.page(reader,'values',bad)

    def fix(self,ts,lat=40,lon=-74):
        return {'class':'TPV','mode':3,'time':f'2026-10-27T12:{ts//60:02}:{ts%60:02}Z','lat':lat,'lon':lon,'speed':1.0}

    def test_gps_every_fix_gaps_stops_and_restart(self):
        for t in (0,60,120,180,240):gps.record_message(self.gps,self.fix(t),received=1000+t)
        self.assertEqual(self.gps.execute('SELECT COUNT(*) FROM fixes').fetchone()[0],5)
        self.assertEqual(self.gps.execute('SELECT COUNT(*) FROM stops').fetchone()[0],1)
        # Reopen and retain the movement segment and stop candidate.
        self.gps.close();self.gps=gps.open_db(self.root/'gps.sqlite3')
        gps.record_message(self.gps,self.fix(250),received=1250)
        gps.record_message(self.gps,self.fix(500,41,-73),received=1500)
        self.assertEqual(self.gps.execute('SELECT break_reason FROM fixes ORDER BY id DESC LIMIT 1').fetchone()[0],'gap')
        invalid={'class':'TPV','mode':1,'lat':40,'lon':-74}
        gps.record_message(self.gps,invalid,received=1501)
        self.assertEqual(self.gps.execute('SELECT COUNT(*) FROM gps_messages').fetchone()[0],8)
        self.assertEqual(self.gps.execute('SELECT COUNT(*) FROM fixes').fetchone()[0],7)
        self.gps.close()

    def test_bad_fix_never_creates_route(self):
        for message in [{'class':'TPV','mode':3,'lat':999,'lon':1},
                        {'class':'TPV','mode':True,'lat':0,'lon':0},
                        {'class':'SKY','mode':3,'lat':0,'lon':0}]:
            self.assertIsNone(gps.record_message(self.gps,message,1000))
        self.assertEqual(self.gps.execute('SELECT COUNT(*) FROM fixes').fetchone()[0],0)
        a=gps.record_message(self.gps,{'class':'TPV','mode':2,'lat':0,'lon':0},1000)
        b=gps.record_message(self.gps,{'class':'TPV','mode':2,'lat':20,'lon':0},1001)
        self.assertEqual(a['time_source'],'receipt');self.assertEqual(b['break_reason'],'position_jump')
