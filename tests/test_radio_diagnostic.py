import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from control.radio_diagnostic import Diagnostic, Responder, packet, decode, record_report
from control.lorawan_protocol import authenticate_application_frame

KEY = bytes(range(32))


class DiagnosticTests(unittest.TestCase):
    def test_authentication_tamper_and_no_wrong_nonce_or_request_success(self):
        with tempfile.TemporaryDirectory() as directory:
            test=Diagnostic(Path(directory)/'result.json',KEY)
            raw=test.start()
            self.assertEqual(len(raw),34)
            authenticate_application_frame(raw,KEY)
            self.assertFalse(test.receive(raw))
            self.assertFalse(test.receive(packet(2,b'x'*16,KEY)))
            self.assertEqual(test.read()['phase'],'running')
            for bad in (raw[:-1],raw[:-1]+bytes([raw[-1]^1]),b'plaintext'):
                with self.assertRaises(ValueError):decode(bad,KEY)
            with self.assertRaises(ValueError):decode(raw,bytes(reversed(KEY)))
            self.assertTrue(test.receive(packet(2,raw[2:18],KEY)))
            self.assertIsNone(test.outgoing())
            self.assertFalse(test.receive(packet(2,raw[2:18],KEY)))

    def test_restart_does_not_repeat_completed_test(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'result.json'
            test=Diagnostic(path,KEY);challenge=test.start()
            test=Diagnostic(path,KEY)
            self.assertEqual(test.start(),challenge)
            test.receive(packet(2,challenge[2:18],KEY))
            self.assertIsNone(Diagnostic(path,KEY).start())
            self.assertEqual(path.stat().st_mode & 0o777,0o600)

    def test_expiry_is_failure_not_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            test=Diagnostic(Path(directory)/'result.json',KEY)
            with patch('control.radio_diagnostic.time.time',return_value=1000):raw=test.start()
            with patch('control.radio_diagnostic.time.time',return_value=13000):
                self.assertFalse(test.receive(packet(2,raw[2:18],KEY)))
                self.assertIsNone(test.outgoing())
            self.assertEqual(test.read()['phase'],'timed-out')

    def test_home_receipt_or_ip_report_cannot_complete_car_test(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);test=Diagnostic(root/'car.json',KEY)
            responder=Responder(root/'home/diagnostic.json',KEY)
            responder.receive(test.start())
            report=test.read()
            record_report(root/'home/diagnostic-result.json',report)
            self.assertIsNotNone(responder.outgoing())
            self.assertEqual(test.read()['phase'],'running')
            self.assertTrue(test.receive(responder.outgoing()))
            record_report(root/'home/diagnostic-result.json',test.read())
            self.assertIsNone(responder.outgoing())

    def test_untrusted_report_fields_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);test=Diagnostic(root/'car.json',KEY);test.start()
            for extra in ({'phase':'passed-via-broker'}, {'nonce':'../bad'},
                          {'elapsedSeconds':float('nan')}, {'command':'exec'}, {'message':'x'*400}):
                with self.subTest(extra=extra),self.assertRaises(ValueError):
                    record_report(root/'result.json',dict(test.read(),**extra))

    def test_encrypted_serial_and_mqtt_round_trip_without_gps_or_ip_echo(self):
        try:
            from control.radio_crypto import RadioBox,identity,pin
        except ModuleNotFoundError as exc:
            if exc.name!='nacl':raise
            self.skipTest('PyNaCl required for sealed round-trip test')
        from control import chirpstack_bridge as bridge
        from tests.test_rak3172 import Modem
        modem=Modem();self.addCleanup(modem.close)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);home=root/'home';home.mkdir()
            private=identity(home/'radio/private.key');car_key=identity(root/'car.key')
            pin(root/'home.pub',bytes(private.public_key).hex())
            pin(home/'radio/car.pub',bytes(car_key.public_key).hex())
            car=RadioBox(root/'car.key',root/'home.pub',root/'car.db',b'U',46)
            diagnostic=Diagnostic(root/'result.json',KEY);raw=diagnostic.start()
            service=MagicMock();service.journal.budget.return_value=True;service.outgoing.return_value=[]
            client=MagicMock();client.publish.return_value.rc=0
            with patch.object(bridge,'ROOT',home),patch.object(bridge,'software',return_value=service),patch.object(bridge,'radio_key',return_value=KEY),patch.object(bridge,'MODE','A'),patch.object(bridge,'export_next',return_value=None):
                worker=bridge.Bridge(client)
                published=0
                for attempt in range(20):
                    wire=car.next(raw)
                    modem.radio.request({'id':str(attempt),'uplink':wire.hex()},46)
                    actual=bytes.fromhex(modem.commands[-1].split(':',1)[1])
                    car.sent(raw)
                    worker.last=-1e9
                    event=MagicMock(payload=json.dumps({'data':base64.b64encode(actual).decode()}).encode())
                    worker.on_message(None,None,event)
                    while published<len(client.publish.call_args_list):
                        outgoing=json.loads(client.publish.call_args_list[published].args[1]);published+=1
                        downlink=base64.b64decode(outgoing['data'])
                        self.assertEqual(downlink[0],3)
                        modem.radio.event('+EVT:RX_1:-48:8:UNICAST:10:'+downlink.hex())
                        response=car.accept(bytes.fromhex(modem.output[-1]['downlink']))
                        if response is not None:diagnostic.receive(response)
                    if diagnostic.read()['phase']=='passed':break
                else:self.fail('Encrypted radio echo never completed')
            self.assertGreater(published,1)
            self.assertEqual(diagnostic.read()['phase'],'passed')
            self.assertNotIn('gps',json.dumps({'raw':raw.hex()}).lower())
            service.journal.mark_sent.assert_not_called()
