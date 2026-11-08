import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app.system_status import snapshot,observation
from recorder.gps_recorder import StatusTracker
from deploy import fleet


class SystemStatusTests(unittest.TestCase):
    def test_gps_no_fix_returns_without_waiting_for_additional_samples(self):
        from app import server
        from unittest.mock import MagicMock
        stream=MagicMock();stream.__enter__.return_value=stream;stream.readline.side_effect=[b'{"class":"TPV","mode":1}\n',AssertionError('No more reads needed')]
        sock=MagicMock();sock.__enter__.return_value=sock;sock.makefile.return_value=stream
        with patch.object(server.socket,'create_connection',return_value=sock):data=server.query_gpsd()
        self.assertFalse(data['fix']);self.assertTrue(data['connected']);self.assertEqual(stream.readline.call_count,1)

    def test_radio_restart_has_a_fresh_challenge_and_never_forges_passed(self):
        from control.radio_diagnostic import Diagnostic
        with tempfile.TemporaryDirectory() as directory:
            tracker=Diagnostic(Path(directory)/'radio.json',bytes(range(32)))
            with patch('control.radio_diagnostic.time.time',return_value=1000):tracker.start()
            old=tracker.read()['nonce']
            with patch('control.radio_diagnostic.time.time',return_value=20000):tracker.restart()
            self.assertEqual(tracker.read()['phase'],'running');self.assertNotEqual(tracker.read()['nonce'],old)

    def test_status_distinguishes_stale_runtime_from_configuration_without_secrets_or_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);home=root/'home';home.mkdir();settings=root/'etc';settings.mkdir();runtime=root/'run';runtime.mkdir()
            state=root/'state';state.mkdir();now=1000
            (runtime/'fleet-status.json').write_text(json.dumps({'phase':'ready','updated':1,'gateway':'duet','wifiPassword':'private'}))
            (settings/'update-link.json').write_text(json.dumps({'radio_model':'RAK3172','radio_device':'/dev/serial/by-id/fixture','token':'private token'}))
            gps=home/'gps.sqlite3';gps.with_suffix('.status.json').write_text(json.dumps({'phase':'recording','updated':999,'gpsMode':3,'lat':39,'lon':-98}))
            with patch('control.car_data.mounted',return_value=False):result=snapshot(home,'pi',settings,runtime,state,gps,now)
            self.assertEqual(result['network']['phase'],'stale');self.assertEqual(result['gps']['phase'],'recording')
            self.assertTrue(result['storage']['clientOnly']);self.assertFalse(result['storage']['poolMounted'])
            self.assertTrue(result['radio']['configured']);self.assertFalse(result['radio']['homeKeyPinned'])
            raw=json.dumps(result);self.assertNotIn('private token',raw);self.assertNotIn('wifiPassword',raw);self.assertNotIn('"lat"',raw);self.assertNotIn('"lon"',raw)

    def test_gps_status_uses_normal_messages_and_never_retains_position(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'gps.status.json';tracker=StatusTracker(path)
            tracker.observe({'class':'TPV','mode':1},100);tracker.publish(True,force=True,now=100)
            self.assertEqual(json.loads(path.read_text())['phase'],'waiting-fix')
            tracker.observe({'class':'SKY','nSat':8,'uSat':5},101)
            tracker.observe({'class':'TPV','mode':3,'lat':39,'lon':-98},102);tracker.publish(True,force=True,now=103)
            value=json.loads(path.read_text());self.assertEqual(value['phase'],'recording');self.assertEqual(value['satellitesUsed'],5)
            self.assertNotIn('lat',value);self.assertNotIn('lon',value)
            tracker.publish(True,force=True,now=120);self.assertEqual(json.loads(path.read_text())['phase'],'waiting-fix')
            tracker.publish(False,force=True,now=121);self.assertEqual(json.loads(path.read_text())['phase'],'disconnected')

    def test_beacon_health_is_small_allowlisted_and_not_used_to_choose_gateway(self):
        message={'schema':1,'role':'laptop','mac':'02:11:22:33:44:55','internet':False,
                 'health':{'power':'ready-deep-sleep','token':'private','radio':'value with spaces and secret'}}
        peer=fleet.eligible_peer(message,'172.16.42.4',100)
        self.assertEqual(peer['health'],{'power':'ready-deep-sleep'});self.assertIsNone(fleet.elect('tiny',False,{'laptop':peer},101))
