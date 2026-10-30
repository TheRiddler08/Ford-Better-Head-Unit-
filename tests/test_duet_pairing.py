"""Exercise reconnect pairing without SSH, hardware, passwords or init changes."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[1]/'duet/prepare-update.sh'
RUNNER=compile(SCRIPT.read_text().split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0],str(SCRIPT),'exec')


class DuetPairingTests(unittest.TestCase):
    def exercise(self,root,code):
        marker=root/'pending.desktop';marker.write_text('pending')
        args=['pairing',str(root),'car@172.16.42.2',str(root),str(marker)]
        with patch.object(sys,'argv',args),patch('socket.create_connection') as connect,patch('subprocess.run',return_value=subprocess.CompletedProcess([],code)) as run,redirect_stdout(io.StringIO()):
            connect.side_effect=[OSError('not connected'),unittest.mock.MagicMock()]
            with patch('time.sleep') as sleep,patch('builtins.open') as terminal:
                if code:
                    with self.assertRaises(SystemExit) as raised:exec(RUNNER,{'__name__':'__main__'})
                    self.assertEqual(raised.exception.code,code)
                else:exec(RUNNER,{'__name__':'__main__'})
                sleep.assert_called_once_with(5)
            run.assert_called_once_with(['bash',str(root/'duet/setup-offline-updates.sh'),'car@172.16.42.2'],stdin=terminal.return_value.__enter__.return_value)
        return marker

    def test_offline_then_reconnected_runs_real_pairing_and_clears_reminder(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertFalse(self.exercise(Path(temp),0).exists())

    def test_rejected_pairing_keeps_reminder_and_reports_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertTrue(self.exercise(Path(temp),1).exists())
