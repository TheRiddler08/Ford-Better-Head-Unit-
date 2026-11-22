"""Duet USB sharing with PTY stand-ins; loopback only, no host network changes."""
import json
import os
from pathlib import Path
import pty
import select
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from deploy import usb_share
from control import rak3172


class FakeSerial:
    """A PTY whose far end behaves like a GPS (streams NMEA) or a RAK3172 (answers AT)."""
    def __init__(self,kind):
        self.master,self.slave=pty.openpty();self.path=os.ttyname(self.slave)  # keep slave open so writes never EIO
        self.kind=kind;self.stop=threading.Event();self.received=b''
        threading.Thread(target=self.run,daemon=True).start()
    def run(self):
        while not self.stop.is_set():
            if self.kind=='gps':
                try:os.write(self.master,b'$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A\r\n')
                except OSError:return
            if select.select([self.master],[],[],.2)[0]:
                try:data=os.read(self.master,4096)
                except OSError:return
                self.received+=data
                if self.kind=='rak' and b'AT+VER=?' in data:os.write(self.master,b'AT+VER=RUI_4.2.4_RAK3172-E\r\nOK\r\n')
                if self.kind=='rak' and b'AT+NJS=?' in data:os.write(self.master,b'AT+NJS=1\r\nOK\r\n')
    def close(self):self.stop.set();time.sleep(.3);os.close(self.master);os.close(self.slave)


class UsbShareTests(unittest.TestCase):
    def test_devices_are_identified_by_what_they_say(self):
        gps,rak,silent=FakeSerial('gps'),FakeSerial('rak'),FakeSerial('silent')
        try:
            self.assertEqual(usb_share.identify(gps.path)[0],'gps')
            self.assertEqual(usb_share.identify(rak.path),('lorawan',115200))
            self.assertIsNone(usb_share.identify(silent.path))
            # Only the read-only version query is ever sent; nothing to the GPS.
            self.assertEqual(rak.received.strip(),b'AT+VER=?')
            self.assertNotIn(b'AT+',gps.received.replace(b'AT+VER=?',b''))
        finally:
            for device in (gps,rak,silent):device.close()

    def test_streams_reach_only_tiny_and_modem_commands_round_trip(self):
        gps,rak=FakeSerial('gps'),FakeSerial('rak');stop=threading.Event()
        with tempfile.TemporaryDirectory() as temp,patch.object(usb_share,'STATUS',Path(temp)/'status.json'):
            thread=threading.Thread(target=usb_share.serve,kwargs={'address':'127.0.0.1','peer':'127.0.0.1','rescan':1,
                'find':lambda:{gps.path:Path(gps.path),rak.path:Path(rak.path)},'stop':stop},daemon=True)
            thread.start()
            try:
                deadline=time.monotonic()+20
                while usb_share.remote_devices('127.0.0.1')!={'gps','lorawan'}:
                    self.assertLess(time.monotonic(),deadline);time.sleep(.3)
                with socket.create_connection(('127.0.0.1',usb_share.GPS_PORT),timeout=5) as conn:
                    data=b''
                    while b'$GPRMC' not in data:data+=conn.recv(4096)
                with socket.create_connection(('127.0.0.1',usb_share.RADIO_PORT),timeout=5) as conn:
                    conn.sendall(b'AT+NJS=?\r\n');data=b''
                    while b'AT+NJS=1' not in data:data+=conn.recv(4096)
                self.assertEqual(json.loads((Path(temp)/'status.json').read_text())['phase'],'sharing')
            finally:
                stop.set();thread.join(5);gps.close();rak.close()

    def test_status_is_withheld_from_other_hosts(self):
        stop=threading.Event()
        with tempfile.TemporaryDirectory() as temp,patch.object(usb_share,'STATUS',Path(temp)/'s.json'):
            thread=threading.Thread(target=usb_share.serve,kwargs={'address':'127.0.0.1','peer':'172.16.42.2','find':dict,'stop':stop},daemon=True)
            thread.start()
            try:
                deadline=time.monotonic()+5
                while not (Path(temp)/'s.json').exists():self.assertLess(time.monotonic(),deadline);time.sleep(.1)
                self.assertEqual(usb_share.remote_devices('127.0.0.1'),set())
            finally:stop.set();thread.join(5)

    def test_tiny_uses_local_modem_first_then_the_duet_share(self):
        options={'home_url':'x'}
        self.assertEqual(rak3172.effective_options(options,'tiny',shared=lambda:True)['radio_device'],rak3172.SHARED_DEVICE)
        self.assertEqual(rak3172.effective_options(options,'tiny',shared=lambda:False),options)
        self.assertEqual(rak3172.effective_options(options,'duet',shared=lambda:True),options)
        self.assertEqual(rak3172.effective_options(dict(options,radio_share=False),'tiny',shared=lambda:True).get('radio_device'),None)
        with tempfile.TemporaryDirectory() as temp:
            local=Path(temp)/'modem';local.write_text('')
            # A present local by-id device wins (path existence check only).
            with patch('control.rak3172.os.path.exists',return_value=True):
                self.assertEqual(rak3172.effective_options({'radio_model':'RAK3172','radio_device':'/dev/serial/by-id/x'},'tiny',shared=lambda:True)['radio_device'],'/dev/serial/by-id/x')
        command=rak3172.adapter_command({'radio_model':'RAK3172','radio_device':rak3172.SHARED_DEVICE})
        self.assertIn(rak3172.SHARED_DEVICE,command)
        with self.assertRaises(ValueError):rak3172.adapter_command({'radio_model':'RAK3172','radio_device':'tcp://10.0.0.5:8801'})

    def test_tiny_attaches_duet_gps_to_gpsd(self):
        calls=[]
        class Result:returncode=0
        with patch.object(usb_share,'remote_devices',return_value={'gps'}),patch.object(usb_share.Path,'mkdir'),patch.object(usb_share.Path,'write_text'),patch.object(usb_share.os,'replace'):
            self.assertEqual(usb_share.attach_gps(run=lambda args,**_:calls.append(args) or Result(),once=True),'attached')
        self.assertEqual(calls,[['gpsdctl','add','tcp://172.16.42.1:8802']])
        with patch.object(usb_share,'remote_devices',return_value=set()),patch.object(usb_share.Path,'mkdir'),patch.object(usb_share.Path,'write_text'),patch.object(usb_share.os,'replace'):
            self.assertEqual(usb_share.attach_gps(run=lambda *a,**k:self.fail('no gpsd change without a Duet GPS'),once=True),'duet-gps-absent')

    def test_role_installer_enables_sharing_on_duet_and_gps_attach_on_tiny(self):
        from deploy import fleet
        with tempfile.TemporaryDirectory() as temp:
            units={};rules=Path(temp)/'rules'
            with patch.object(fleet,'unit',side_effect=lambda n,t:units.update({n:t})),patch.object(fleet,'run') as run,\
                 patch.object(fleet,'USB_SHARE_RULES',rules),patch.object(fleet.Path,'exists',lambda self:str(self)=='/run/systemd/system' or Path.is_file(self)):
                fleet.install_usb_share({'role':'duet'});fleet.install_usb_share({'role':'tiny'});fleet.install_usb_share({'role':'pi'})
            self.assertIn('usb_share.py serve',units['car-usb-share.service'])
            self.assertIn('usb_share.py attach-gps',units['car-remote-gps.service'])
            self.assertEqual(len(units),2)
            run.assert_any_call(['systemctl','enable','--now','car-usb-share.service'])
            run.assert_any_call(['systemctl','enable','--now','car-remote-gps.service'])

    def test_firewall_admits_shared_streams_from_tiny_only(self):
        from deploy import native_firewall
        ruleset={'nftables':[{'table':{'family':'inet','name':'filter'}},
            {'chain':{'family':'inet','table':'filter','name':'input','hook':'input','type':'filter','prio':0,'policy':'drop'}}]}
        commands=native_firewall.plan(ruleset,'eth0',None)
        self.assertTrue(any('ip saddr 172.16.42.2 tcp dport { 8800, 8801, 8802 }' in c for c in commands))
        self.assertFalse(any('172.16.42.0/24 tcp dport { 8800' in c for c in commands))


if __name__=='__main__':unittest.main()
