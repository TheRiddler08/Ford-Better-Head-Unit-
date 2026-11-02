from contextlib import ExitStack
import fcntl
import io
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch,MagicMock
from power import wake,standby


class WakeTests(unittest.TestCase):
    def test_magic_packet_identity_and_invalid_targets(self):
        self.assertEqual(wake.packet('02:11:22:33:44:55'),b'\xff'*6+bytes.fromhex('021122334455')*16)
        for mac in ('','ff:ff:ff:ff:ff:ff','00:00:00:00:00:00','01:11:22:33:44:55','$(id)'):
            with self.assertRaises(ValueError):wake.packet(mac)

    def test_failure_does_not_skip_other_device_and_interface_is_explicit(self):
        config={'schema':1,'targets':[
            {'name':'tiny','mac':'02:11:22:33:44:55','broadcast':'172.16.42.255','interface':'eth0'},
            {'name':'duet','mac':'02:11:22:33:44:66','broadcast':'172.16.42.255','interface':'eth0'}]}
        failed=MagicMock();good=MagicMock();failed.__enter__.return_value.sendto.side_effect=OSError('offline')
        with patch.object(wake.socket,'socket',side_effect=[failed,good]),patch.object(wake.time,'sleep'),patch('sys.stdout',new=io.StringIO()):
            with self.assertRaises(RuntimeError):wake.send(config)
        self.assertEqual(good.__enter__.return_value.sendto.call_count,3)
        good.__enter__.return_value.setsockopt.assert_any_call(socket.SOL_SOCKET,socket.SO_BINDTODEVICE,b'eth0\0')

    def test_pi_schedule_is_30_then_150_seconds_with_no_immediate_install_start(self):
        script=(Path(__file__).resolve().parents[1]/'power/install-pi-wake.sh').read_text()
        self.assertIn('SECONDS_AFTER_BOOT=30',script)
        self.assertIn('SECONDS_AFTER_BOOT=150',script)
        self.assertNotIn('enable --now car-wake-first.timer',script)


class StandbyTests(unittest.TestCase):
    def config(self,home):return dict(schema=1,role='duet',interface='eth0',home=str(home),sleep_mode='current',armed=True,pi_address='172.16.42.3')

    def test_unarmed_or_late_boot_never_probes_or_suspends(self):
        config=self.config('/home/car')
        with patch.object(standby,'probe') as probe,patch.object(standby,'uptime',return_value=150),patch('sys.stdout',new=io.StringIO()):
            standby.sleep_once(config)
            standby.sleep_once(dict(config,armed=False))
            probe.assert_not_called()

    def test_real_courier_lock_blocks_standby_and_missing_locks_are_not_created(self):
        with tempfile.TemporaryDirectory() as temp:
            config=self.config(temp)
            with ExitStack() as stack:
                with self.assertRaises(BlockingIOError):standby.acquire(stack,config)
            self.assertFalse((Path(temp)/'.local').exists())
            paths=standby.lock_paths(config)
            paths[0].parent.mkdir(parents=True)
            for path in paths:path.touch()
            with paths[0].open('a') as held:
                fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with ExitStack() as stack:
                    with self.assertRaises(BlockingIOError):standby.acquire(stack,config)
            with ExitStack() as stack:standby.acquire(stack,config)

    def test_resume_does_not_suspend_again(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);config=self.config(root)
            nic=root/'nic';nic.mkdir();(nic/'carrier').write_text('1')
            paths=standby.lock_paths(config);paths[0].parent.mkdir(parents=True)
            for path in paths:path.touch()
            with patch.object(standby,'uptime',return_value=20),patch.object(standby,'probe',return_value=nic),patch.object(standby,'run',return_value='Wake-on: g\n'),patch.object(standby,'suspend_command',return_value=['test-suspend']),patch.object(standby,'wake_pending',return_value=True),patch.object(standby,'request_sleep') as run,patch('sys.stdout',new=io.StringIO()):
                standby.sleep_once(config)
                run.assert_called_once_with()

    def test_deadline_during_update_wait_leaves_device_awake(self):
        config=self.config('/home/car')
        with patch.object(standby,'uptime',side_effect=[20,20,20,20,121]),patch.object(standby,'probe'),patch.object(standby,'acquire',side_effect=BlockingIOError),patch.object(standby.time,'sleep'),patch.object(standby.subprocess,'run') as run,patch('sys.stdout',new=io.StringIO()):
            standby.sleep_once(config)
            run.assert_not_called()

    def test_sleep_acknowledgement_waits_for_actual_resume(self):
        with patch.object(standby,'suspend_command',return_value=['test-suspend']),patch.object(standby.subprocess,'run') as run,patch.object(standby.time,'monotonic',return_value=10),patch.object(standby.time,'clock_gettime',side_effect=[10,10,70]),patch.object(standby.time,'sleep') as sleep:
            standby.request_sleep()
            run.assert_called_once_with(['test-suspend'],check=True,timeout=45)
            sleep.assert_called_once_with(.25)

    def test_late_pi_or_unreachable_pi_never_enters_standby(self):
        config=self.config('/home/car')
        client=MagicMock()
        for seconds,expected in [(20,True),(139,True),(140,False),(180,False)]:
            import json
            client.__enter__.return_value.recv.return_value=json.dumps({'schema':1,'uptime':seconds,'lastWake':150}).encode()
            with patch.object(standby.socket,'socket',return_value=client):
                self.assertEqual(standby.wake_pending(config),expected)
        with patch.object(standby.socket,'socket',side_effect=OSError('offline')):
            self.assertFalse(standby.wake_pending(config))
