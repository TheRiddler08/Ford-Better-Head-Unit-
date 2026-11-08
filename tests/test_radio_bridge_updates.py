import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,MagicMock
from control import chirpstack_bridge as bridge
from control.update_transfer import encode,DATA

KEY=bytes(range(32))


class BridgeTests(unittest.TestCase):
    def test_pending_diagnostic_does_not_starve_signed_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            raw=encode(DATA,'a'*64,0,1,b'code',KEY);service=MagicMock();service.outgoing.return_value=[raw.hex()];service.journal.budget.return_value=True
            client=MagicMock();client.publish.return_value.rc=0
            with patch.object(bridge,'ROOT',Path(directory)),patch.object(bridge,'software',return_value=service),patch.object(bridge,'radio_key',return_value=KEY),patch.object(bridge,'export_next',return_value=None):
                worker=bridge.Bridge(client);cipher=MagicMock();cipher.peer.exists.return_value=True;cipher.sent.return_value=True;cipher.next.side_effect=lambda value:b'\x03'+value
                with patch.object(worker,'cipher',return_value=cipher),patch.object(worker,'diagnostic') as diagnostic:
                    diagnostic.return_value.outgoing.return_value=b'diagnostic'
                    for _ in range(4):worker.last=-1e9;worker.send_one()
            self.assertEqual(service.journal.mark_sent.call_count,2)
            self.assertTrue(any(call.args[0]==b'diagnostic' for call in cipher.next.call_args_list))

    def test_home_ha_radio_update_pause_preserves_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'radio').mkdir();(root/'radio/update-delivery-paused').write_text('paused')
            service=MagicMock();client=MagicMock()
            with patch.object(bridge,'ROOT',root),patch.object(bridge,'software',return_value=service),patch.object(bridge,'radio_key',return_value=KEY),patch.object(bridge,'export_next') as legacy:
                worker=bridge.Bridge(client)
                with patch.object(worker,'diagnostic') as diagnostic:
                    diagnostic.return_value.outgoing.return_value=None;worker.last=-1e9;worker.send_one()
            legacy.assert_not_called();service.outgoing.assert_not_called();client.publish.assert_not_called()

    def test_no_radio_budget_does_not_mark_fragment_sent_or_publish(self):
        service=MagicMock();frame=encode(DATA,'a'*64,0,1,b'code',KEY)
        service.outgoing.return_value=[frame.hex()];service.journal.budget.return_value=False
        client=MagicMock()
        with patch.object(bridge,'software',return_value=service),patch.object(bridge,'radio_key',return_value=KEY),patch.object(bridge,'export_next',return_value=None):
            worker=bridge.Bridge(client);worker.last=-1000000;worker.send_one()
        client.publish.assert_not_called();service.journal.mark_sent.assert_not_called()

    def test_class_a_poll_opens_downlink_even_without_update_ack_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            service=MagicMock()
            with patch.object(bridge,'software',return_value=service),patch.object(bridge,'radio_key',return_value=KEY),patch.object(bridge,'ROOT',Path(temp)),patch.object(bridge,'MODE','A'):
                worker=bridge.Bridge(MagicMock())
                with patch.object(worker,'send_one') as send:
                    # Valid network-server event with an ordinary modem heartbeat.
                    worker.on_message(None,None,MagicMock(payload=json.dumps({'data':'AA=='}).encode()))
                    send.assert_called_once()
