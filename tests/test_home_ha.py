import importlib.util
import json
from pathlib import Path
import tempfile
import sqlite3
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock,patch

from control.telemetry import encode,decode,store,snapshot,collect,METRICS
from control.home_ha import discovery,HomeHA,update_states,journal_states,overview,report_state
from control.lorawan_protocol import authenticate_application_frame

KEY=bytes(range(32))


class TelemetryTests(unittest.TestCase):
    def test_compact_round_trip_and_no_gps_metric(self):
        values={'rpm':1540,'voltage':13.82,'coolant':85.25,'speed':31.45,'fuel':51.2,'load':12.3,'uptime':5000,'free_space':2000}
        frames=encode('tiny',values,KEY,1000);self.assertEqual(len(frames),2)
        received={}
        for raw in frames:
            self.assertLessEqual(len(raw),46);authenticate_application_frame(raw,KEY)
            sample=decode(raw,KEY);self.assertEqual(sample['source'],'tiny');received.update(sample['values'])
        self.assertEqual(received,values)
        for values in ({'lat':39},{'lon':-80},{'rpm':float('nan')},{'rpm':True}):
            with self.assertRaises(ValueError):encode('tiny',values,KEY,1000)
        with self.assertRaises(ValueError):decode(frames[0][:-1]+bytes([frames[0][-1]^1]),KEY)
        with self.assertRaises(ValueError):decode(frames[0],bytes(reversed(KEY)))

    def test_latest_observation_wins_and_stale_values_are_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'states.db'
            store(path,{'source':'tiny','observedAt':1000,'values':{'rpm':1500}},now=1001)
            store(path,{'source':'tiny','observedAt':999,'values':{'rpm':0}},now=1001)
            self.assertEqual(snapshot(path,now=1100)['tiny']['values']['rpm'],1500)
            self.assertIsNone(snapshot(path,now=3000)['tiny']['values']['rpm'])
            self.assertIsNone(snapshot(path,now=1100)['tiny']['values']['fuel'])
            self.assertFalse(store(path,{'source':'tiny','observedAt':5000,'values':{'rpm':900}},now=1100))

    def test_old_sensor_database_migrates_and_delayed_radio_does_not_change_last_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'states.db'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE sensors(role TEXT,name TEXT,value REAL,observed REAL,received REAL,PRIMARY KEY(role,name))')
                db.execute('INSERT INTO sensors VALUES(?,?,?,?,?)',('tiny','rpm',900,1000,1001))
            self.assertEqual(snapshot(path,now=1002)['tiny']['transport'],'unknown')
            store(path,{'source':'tiny','observedAt':1100,'values':{'rpm':1200}},now=1101,transport='ip')
            store(path,{'source':'tiny','observedAt':1090,'values':{'rpm':800}},now=1102,transport='lorawan')
            state=snapshot(path,now=1103)['tiny']
            self.assertEqual(state['transport'],'ip');self.assertEqual(state['receivedAt'],1101)
            self.assertEqual(state['values']['rpm'],1200)

    def test_discovery_targets_external_home_ha_and_has_no_gps(self):
        configs=discovery('car-1','tiny')
        self.assertTrue(all(topic.startswith('homeassistant/') for topic in configs))
        raw=json.dumps(configs)
        self.assertNotIn('device_tracker',raw);self.assertNotIn('latitude',raw);self.assertNotIn('longitude',raw)
        rpm=next(value for key,value in configs.items() if key.endswith('_rpm/config'))
        self.assertIn('Last reported',rpm['name']);self.assertEqual(rpm['expire_after'],1800)
        self.assertEqual(rpm['availability_mode'],'all')

    def test_update_states_do_not_call_received_chunks_applied(self):
        rows=[{'role':'tiny','phase':'queued','acknowledged':2,'total':8,'lastTransport':'lorawan'}]
        result=update_states(rows)['tiny']
        self.assertEqual(result['phase'],'receiving');self.assertEqual(result['pending'],1)
        self.assertNotEqual(result['phase'],'applied')
        rows[0]['acknowledged']=8
        self.assertEqual(update_states(rows)['tiny']['phase'],'awaiting-application')

    def test_pending_update_is_not_hidden_behind_more_than_32_finished_jobs(self):
        from control.update_transfer import Journal
        with tempfile.TemporaryDirectory() as directory:
            journal=Journal(Path(directory)/'updates.db')
            with journal.db() as db:
                for index in range(40):
                    ident=f'{index:016x}'+'0'*48
                    db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?)',(ident,ident[:16],json.dumps({'role':'tiny'}),'out','queued' if index==0 else 'applied',index,None))
            state=journal_states(journal)['tiny']
            self.assertEqual(state['pending'],1);self.assertEqual(state['phase'],'queued')

    def test_stale_bridge_heartbeat_is_not_reported_as_working_in_operator_view(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'home-ha-status.json').write_text(json.dumps({'phase':'mqtt-connected','time':time.time()-100}))
            state=overview(root);self.assertEqual(state['bridge']['phase'],'status-stale')
            self.assertIsNone(state['roles']['tiny']['report']['age'])
            self.assertEqual(state['roles']['tiny']['sync']['phase'],'not-configured')

    def test_fresh_receipt_does_not_make_old_observations_fresh(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'states.db'
            store(path,{'source':'tiny','observedAt':1000,'values':{'rpm':1500}},now=3000,transport='lorawan')
            report=report_state(snapshot(path,now=3000)['tiny'],now=3000)
            self.assertEqual(report['receivedAt'],3000);self.assertEqual(report['age'],2000)
            self.assertEqual(report['transport'],'lorawan');self.assertFalse(report['fresh'])


@unittest.skipUnless(importlib.util.find_spec('cryptography'),'Home installer provides cryptography')
class HomeHATests(unittest.TestCase):
    def test_discovery_states_availability_and_retained_command_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            client=MagicMock();client.publish.return_value.rc=0;root=Path(directory)
            service=HomeHA(client,root)
            service.on_connect(client,None,None,0)
            self.assertFalse(client.publish.called) # MQTT callback does no database work.
            service.tick()
            calls=client.publish.call_args_list
            self.assertEqual(calls[0].args[1],'offline')
            self.assertEqual(calls[-1].args[1],'online')
            self.assertTrue(all(call.kwargs['retain'] for call in calls))
            service.on_message(None,None,SimpleNamespace(topic=service.base+'/command/update_delivery',payload=b'OFF',retain=True))
            self.assertFalse((root/'radio/update-delivery-paused').exists())
            service.on_message(None,None,SimpleNamespace(topic=service.base+'/command/update_delivery',payload=b'OFF',retain=False))
            service.process_commands()
            self.assertTrue((root/'radio/update-delivery-paused').exists())
            service.on_message(None,None,SimpleNamespace(topic=service.base+'/command/update_delivery',payload=b'ON',retain=False))
            service.process_commands()
            self.assertFalse((root/'radio/update-delivery-paused').exists())

    def test_health_command_uses_existing_encrypted_queue_and_unknown_commands_do_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);service=HomeHA(MagicMock(),root)
            with patch('control.home_server.radio_key',return_value=KEY):
                service.on_message(None,None,SimpleNamespace(topic=service.base+'/command/tiny/status',payload=b'PRESS',retain=False))
                service.process_commands()
            from control.event_transfer import Events
            events=Events(root/'radio/catalog.sqlite3',KEY)
            with events.db() as db:row=json.loads(db.execute('SELECT doc FROM jobs').fetchone()[0])
            self.assertEqual(row['action'],'status-request');self.assertEqual(row['destination'],'tiny')
            service.on_message(None,None,SimpleNamespace(topic=service.base+'/command/tiny/shell',payload=b'rm -rf /',retain=False))
            service.process_commands()
            with events.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],1)

    def test_ha_restart_refreshes_discovery_and_states_with_existing_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            client=MagicMock();client.publish.return_value.rc=0;service=HomeHA(client,Path(directory))
            service.on_connect(client,None,None,0);service.tick();client.publish.reset_mock()
            service.on_message(None,None,SimpleNamespace(topic='homeassistant/status',payload=b'online',retain=True))
            self.assertFalse(client.publish.called)
            service.tick();calls=client.publish.call_args_list
            self.assertEqual(calls[0].args[1],'offline');self.assertEqual(calls[-1].args[1],'online')
            self.assertTrue(any(call.args[0].endswith('_rpm/config') for call in calls))
            report=next(json.loads(call.args[1]) for call in calls if call.args[0].endswith('/tiny/report'))
            self.assertFalse(report['fresh']);self.assertIsNone(report['age'])

    def test_broker_publish_failure_retries_discovery_and_reports_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            client=MagicMock();client.publish.return_value.rc=4;root=Path(directory);service=HomeHA(client,root)
            service.on_connect(client,None,None,0);service.step()
            self.assertTrue(service.rediscover)
            self.assertEqual(json.loads(service.status.read_text())['phase'],'mqtt-retrying')
            client.publish.return_value.rc=0;client.publish.reset_mock();service.step()
            self.assertEqual(client.publish.call_args_list[-1].args[1],'online')
            self.assertEqual(json.loads(service.status.read_text())['phase'],'mqtt-connected')
            service.on_disconnect();service.step()
            self.assertEqual(json.loads(service.status.read_text())['phase'],'mqtt-disconnected-retrying')

    def test_health_button_duplicates_are_limited_across_bridge_restarts(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);message=SimpleNamespace(topic='car/car_1/command/tiny/status',payload=b'PRESS',retain=False)
            service=HomeHA(MagicMock(),root);self.assertEqual(service.command(message)['phase'],'queued')
            restarted=HomeHA(MagicMock(),root);self.assertEqual(restarted.command(message)['phase'],'rate-limited')
            with sqlite3.connect(root/'radio/catalog.sqlite3') as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],1)

    def test_command_queue_is_bounded_and_oversized_payloads_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            service=HomeHA(MagicMock(),Path(directory))
            for _ in range(100):service.on_message(None,None,SimpleNamespace(topic=service.base+'/command/update_delivery',payload=b'OFF',retain=False))
            self.assertEqual(service.commands.qsize(),32);self.assertEqual(service.last_command['phase'],'queue-full')
            service.process_commands();self.assertTrue((service.root/'radio/update-delivery-paused').exists())
            service.on_message(None,None,SimpleNamespace(topic=service.base+'/command/update_delivery',payload=b'OFF'*100,retain=False))
            self.assertTrue(service.commands.empty())

    def test_encrypted_telemetry_reaches_home_state_before_home_ha_publish(self):
        try:from control.radio_crypto import RadioBox,identity,pin
        except ModuleNotFoundError as exc:
            if exc.name!='nacl':raise
            self.skipTest('PyNaCl required for encrypted path')
        from control import chirpstack_bridge as bridge
        import base64,time
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);home=root/'home';private=identity(home/'radio/private.key');car_key=identity(root/'car.key')
            pin(home/'radio/car.pub',bytes(car_key.public_key).hex());pin(root/'home.pub',bytes(private.public_key).hex())
            car=RadioBox(root/'car.key',root/'home.pub',root/'car.db',b'U',46)
            raw=encode('tiny',{'rpm':1425,'voltage':13.9},KEY)[0]
            client=MagicMock()
            with patch.object(bridge,'ROOT',home),patch.object(bridge,'software',return_value=MagicMock()),patch.object(bridge,'radio_key',return_value=KEY):
                receiver=bridge.Bridge(client)
                with patch.object(receiver,'send_one'):
                    for wire in car.frames(raw)[1]:receiver.on_message(None,None,SimpleNamespace(payload=json.dumps({'data':base64.b64encode(wire).decode()}).encode()))
            self.assertEqual(snapshot(home/'radio/sensors.sqlite3')['tiny']['values']['rpm'],1425)
            ha=MagicMock();ha.publish.return_value.rc=0;integration=HomeHA(ha,home);integration.connected=True;integration.tick()
            state=next(json.loads(call.args[1]) for call in ha.publish.call_args_list if call.args[0].endswith('/tiny/state'))
            self.assertEqual(state['values']['voltage'],13.9)
            self.assertNotIn('gps',state);self.assertIsNone(state['values']['fuel'])
