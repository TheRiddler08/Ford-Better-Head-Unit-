import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from recorder import gps_recorder as gps
from control import coverage
from deploy import fleet


class HistoryTests(unittest.TestCase):
    def test_exact_bytes_restart_and_fix_loss(self):
        with tempfile.TemporaryDirectory() as temp:
            db=gps.open_db(Path(temp)/'gps.db')
            session=gps.start_session(db,100)
            fix={'class':'TPV','mode':3,'lat':40,'lon':-75}
            line=json.dumps(fix).encode()+b'\n'
            chunks=[b'bad\xff\n'+line[:12],line[12:]+b'{unfinished']
            pending=b''
            for chunk in chunks:pending=gps.ingest_chunk(db,session,pending,chunk,101)
            self.assertEqual(pending,b'{unfinished')
            # Only the unparseable line keeps its exact bytes; parsed fixes are not stored twice.
            self.assertEqual(b''.join(base64.b64decode(row[0]) for row in db.execute('SELECT raw_b64 FROM gps_stream ORDER BY id')),b'bad\xff\n')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM fixes').fetchone()[0],1)
            gps.record_message(db,{'class':'TPV','mode':1},102)
            gps.record_message(db,fix,103)
            self.assertEqual(db.execute('SELECT break_reason FROM fixes ORDER BY id DESC LIMIT 1').fetchone()[0],'fix_lost')
            gps.start_session(db,104);gps.record_message(db,fix,105)
            self.assertEqual(db.execute('SELECT break_reason FROM fixes ORDER BY id DESC LIMIT 1').fetchone()[0],'connection_start')
            self.assertEqual(db.execute('SELECT reason FROM gps_sessions WHERE id=?',(session,)).fetchone()[0],'interrupted')
            db.close()

    def test_parked_car_is_one_position_not_thirty(self):
        with tempfile.TemporaryDirectory() as temp:
            db=gps.open_db(Path(temp)/'gps.db');gps.start_session(db,0)
            def fix(t,lat=40.0,lon=-75.0,speed=0.0,heading=None):
                return {'class':'TPV','mode':3,'lat':lat,'lon':lon,'speed':speed,'track':heading,'time':'2026-11-01T00:%02d:%02dZ'%(t//60,t%60)}
            # 30 minutes parked with normal receiver jitter of a few metres.
            for t in range(0,1800,1):
                gps.record_message(db,fix(t,40.0+(t%7)*0.00002,-75.0+(t%5)*0.00002),1000+t,commit=False)
                if t%60==0:gps.record_message(db,{'class':'SKY','uSat':8,'nSat':12},1000+t,commit=False)
            db.commit()
            self.assertEqual(db.execute('SELECT COUNT(*) FROM fixes').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gps_messages').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM stops').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT ended FROM stops').fetchone()[0],db.execute("SELECT MAX(ts) FROM fixes").fetchone()[0]+1799)
            # Driving away: departure time from the parking spot, then real movement.
            for step in range(1,11):gps.record_message(db,fix(1800+step,40.0+step*0.0002,-75.0,speed=15,heading=0),2800+step)
            rows=db.execute('SELECT lat,ts FROM fixes ORDER BY id').fetchall()
            self.assertEqual(len(rows),12)
            self.assertAlmostEqual(rows[1][1]-rows[0][1],1799)
            # A turn with little distance is still captured; a straight stand-still is not.
            gps.record_message(db,fix(1812,40.0+10*0.0002+0.00002,-75.0,speed=3,heading=90),2812)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM fixes').fetchone()[0],13)
            # Another drive (new connection) starts at the same place: recorded once.
            gps.start_session(db,4000)
            gps.record_message(db,fix(3000,40.0+10*0.0002+0.00002,-75.0),4001)
            gps.record_message(db,fix(3001,40.0+10*0.0002+0.00002,-75.0),4002)
            self.assertEqual(db.execute('SELECT break_reason FROM fixes ORDER BY id DESC LIMIT 1').fetchone()[0],'connection_start')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM fixes').fetchone()[0],14)
            db.close()

    def test_only_fresh_client_fixed_gateway_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'coverage.db'
            registry={'fixed':{'role':'fixed'},'car':{'role':'mobile'}}
            sample={'lat':40,'lon':-75,'ts':1000,'gatewayMode':'client','gatewayModeAt':1000}
            event={'deduplicationId':'one','object':{'carGpsV1':sample},'rxInfo':[{'gatewayId':'fixed','rssi':-90,'snr':4},{'gatewayId':'car'},{'gatewayId':'unknown'}]}
            self.assertEqual(coverage.ingest(path,event,registry,1001),3)
            self.assertEqual(coverage.ingest(path,event,registry,1001),0)
            for mode in ('gateway','unknown'):
                sample['gatewayMode']=mode;event['deduplicationId']=mode
                coverage.ingest(path,event,registry,1001)
            sample['gatewayMode']='client';sample['gatewayModeAt']=900;event['deduplicationId']='stale'
            coverage.ingest(path,event,registry,1001)
            result=coverage.report(path)
            self.assertEqual(sum(row['eligible'] for row in result['items']),1)
            self.assertEqual(result['totals']['gateway_mode_stale'],1)
            self.assertEqual(result['totals']['mobile_gateway_active'],1)

    def test_auto_gps_uses_verified_source_and_respects_optout(self):
        config={'user':'driver','home':'/home/driver','repo':'/home/driver/car-dashboard','commit':'a'*40}
        units={}
        with patch.object(fleet.Path,'exists',return_value=False),patch.object(fleet.Path,'is_symlink',return_value=False),patch.object(fleet,'source_bytes',return_value=b'print("gps")\n') as source,patch.object(fleet,'save'),patch.object(fleet,'unit',side_effect=lambda name,value:units.update({name:value})),patch.object(fleet,'run'):
            fleet.install_gps(config)
            source.assert_called_once_with(config,'a'*40,'recorder/gps_recorder.py')
            self.assertIn('UMask=0077',units['car-gps-recorder.service'])
            source.reset_mock();fleet.install_gps(dict(config,gps_recording=False));source.assert_not_called()
