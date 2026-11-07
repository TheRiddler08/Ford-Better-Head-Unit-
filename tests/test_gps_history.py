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
            self.assertEqual(b''.join(base64.b64decode(row[0]) for row in db.execute('SELECT raw_b64 FROM gps_stream ORDER BY id')),b''.join(chunks))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM fixes').fetchone()[0],1)
            gps.record_message(db,{'class':'TPV','mode':1},102)
            gps.record_message(db,fix,103)
            self.assertEqual(db.execute('SELECT break_reason FROM fixes ORDER BY id DESC LIMIT 1').fetchone()[0],'fix_lost')
            gps.start_session(db,104);gps.record_message(db,fix,105)
            self.assertEqual(db.execute('SELECT break_reason FROM fixes ORDER BY id DESC LIMIT 1').fetchone()[0],'connection_start')
            self.assertEqual(db.execute('SELECT reason FROM gps_sessions WHERE id=?',(session,)).fetchone()[0],'interrupted')
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
            self.assertEqual([call.args[2] for call in source.call_args_list],['recorder/virtual_gps.py','recorder/gps_recorder.py'])
            self.assertIn('UMask=0077',units['car-gps-recorder.service'])
            source.reset_mock();fleet.install_gps(dict(config,gps_recording=False));source.assert_not_called()
