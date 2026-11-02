import base64
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
try:
    from control.radio_crypto import RadioBox,identity,pin
except ModuleNotFoundError as exc:
    if exc.name!='nacl':raise
    RadioBox=None
from control.lorawan_protocol import split_message,authenticate_application_frame
from control import chirpstack_bridge as bridge


@unittest.skipIf(RadioBox is None,'PyNaCl is installed by normal provisioning; allow old receiver bootstrap')
class EncryptionTests(unittest.TestCase):
    def boxes(self,root,budget=46):
        home=identity(root/'home.key');car=identity(root/'car.key')
        pin(root/'home.pub',bytes(home.public_key).hex());pin(root/'car.pub',bytes(car.public_key).hex())
        return (RadioBox(root/'home.key',root/'car.pub',root/'home.db',b'D',budget),
                RadioBox(root/'car.key',root/'home.pub',root/'car.db',b'U',budget))
    def test_existing_libsodium_boxes_restart_out_of_order_and_duplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);home,car=self.boxes(root)
            raw=split_message('status',1,{'GPS':'private location'},bytes(range(32)))[0]
            _,frames=home.frames(raw)
            self.assertGreater(len(frames),1)
            self.assertTrue(all(len(frame)<=46 for frame in frames))
            self.assertNotIn(b'private',b''.join(frames))
            self.assertIsNone(car.accept(frames[-1]));self.assertIsNone(car.accept(frames[-1]))
            car=RadioBox(root/'car.key',root/'home.pub',root/'car.db',b'U')
            received=None
            for frame in reversed(frames[:-1]):received=car.accept(frame)
            self.assertEqual(received,raw);authenticate_application_frame(received,bytes(range(32)))
            self.assertEqual(home.frames(raw)[1],frames)
            home.sent(raw);home=RadioBox(root/'home.key',root/'car.pub',root/'home.db',b'D')
            self.assertEqual(home.next(raw),frames[1])
    def test_no_plaintext_wrong_key_or_silent_rotation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);home,car=self.boxes(root)
            with self.assertRaises(ValueError):car.accept(b'plaintext application packet')
            _,frames=home.frames(b'private')
            wrong=RadioBox(root/'other.key',root/'home.pub',root/'other.db',b'U')
            with self.assertRaises(ValueError):
                for frame in frames:wrong.accept(frame)
            with self.assertRaises(ValueError):pin(root/'home.pub',bytes(identity(root/'other.key').public_key).hex())
            self.assertEqual((root/'home.key').stat().st_mode&0o777,0o600)
    def test_us915_small_payload_budget_is_honored(self):
        with tempfile.TemporaryDirectory() as temp:
            home,car=self.boxes(Path(temp),11)
            _,frames=home.frames(b'hello')
            self.assertTrue(all(len(frame)<=11 for frame in frames))
            for frame in frames:raw=car.accept(frame)
            self.assertEqual(raw,b'hello')
    def test_mqtt_never_sends_original_application_bytes(self):
        import json
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);home,car=self.boxes(root)
            raw=split_message('status',1,{'gps':'location'},bytes(range(32)))[0]
            client=MagicMock();client.publish.return_value.rc=0
            service=MagicMock();service.outgoing.return_value=[raw.hex()];service.journal.budget.return_value=True
            with patch.object(bridge,'software',return_value=service),patch.object(bridge,'export_next',return_value=None),patch.object(bridge,'radio_key',return_value=bytes(range(32))):
                worker=bridge.Bridge(client)
                with patch.object(worker,'cipher',return_value=home):
                    for _ in home.frames(raw)[1]:worker.last=-1e9;worker.send_one()
            wires=[base64.b64decode(json.loads(call.args[1])['data']) for call in client.publish.call_args_list]
            self.assertTrue(all(wire!=raw and wire[0]==3 for wire in wires))
            received=None
            for wire in wires:received=car.accept(wire)
            self.assertEqual(received,raw);service.journal.mark_sent.assert_called_once()
