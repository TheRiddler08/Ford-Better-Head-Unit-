import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,MagicMock
from control import chirpstack_bridge as bridge
from control.update_transfer import encode,DATA

KEY=bytes(range(32))


class BridgeTests(unittest.TestCase):
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
