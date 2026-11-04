import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,MagicMock
from duet import proxy


class HATunnelRecoveryTests(unittest.TestCase):
    def test_only_owned_display_client_is_reconnected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);home=root/'home';proc=root/'proc';proc.mkdir()
            key=str(home/'.ssh/drive-console-duet')
            commands={101:['ssh','-N','-i',key,'-L','127.0.0.1:8788:127.0.0.1:8787','car@172.16.42.2'],
                      102:['sshd: cayden@pts/0'],103:['tailscaled'],
                      104:['ssh','-N','-i',str(home/'.ssh/admin'),'-L','127.0.0.1:8788:127.0.0.1:8787'],
                      105:['ssh','-N','-i',key,'-L','127.0.0.1:9999:127.0.0.1:8787']}
            for pid,argv in commands.items():
                folder=proc/str(pid);folder.mkdir();(folder/'cmdline').write_bytes(b'\0'.join(os.fsencode(arg) for arg in argv)+b'\0')
            with patch.object(proxy.Path,'home',return_value=home),patch.object(proxy.os,'pidfd_open',return_value=123),patch.object(proxy.signal,'pidfd_send_signal') as signal,patch.object(proxy.os,'close'),patch.object(proxy.os,'kill') as kill:
                count=proxy.refresh_ha_tunnel(force=True,proc=proc)
                self.assertEqual(count,1);signal.assert_called_once_with(123,proxy.signal.SIGTERM);kill.assert_not_called()
    def test_ready_backend_broken_forward_refreshes_without_desktop_restart(self):
        handler=object.__new__(proxy.ProxyHandler)
        data=json.dumps({'installed':True,'enabled':True,'ready':True}).encode()
        with patch.object(proxy,'fetch_upstream',return_value=(200,{},data)),patch.object(proxy.http.client,'HTTPConnection',side_effect=OSError('forward failed')),patch.object(proxy,'refresh_ha_tunnel',return_value=1) as refresh:
            status=handler.start_status()
        self.assertIsNone(status['target']);self.assertIn('Refreshing',status['message']);refresh.assert_called_once_with(8788)
    def test_starting_backend_does_not_refresh_tunnel(self):
        handler=object.__new__(proxy.ProxyHandler)
        data=json.dumps({'installed':True,'enabled':True,'ready':False}).encode()
        with patch.object(proxy,'fetch_upstream',return_value=(200,{},data)),patch.object(proxy.http.client,'HTTPConnection',side_effect=OSError('starting')),patch.object(proxy,'refresh_ha_tunnel') as refresh:
            self.assertIsNone(handler.start_status()['target'])
        refresh.assert_not_called()
    def test_native_dashboard_opens_while_reporting_backend_is_offline(self):
        handler=object.__new__(proxy.ProxyHandler)
        response=MagicMock();response.status=200
        response.read.return_value=b'{"name":"Car Assistant","short_name":"Car Assistant"}'
        connection=MagicMock();connection.getresponse.return_value=response
        with patch.object(proxy,'fetch_upstream',side_effect=TimeoutError('reporter still starting')) as fetch,patch.object(proxy.http.client,'HTTPConnection',return_value=connection):
            status=handler.start_status()
        self.assertEqual(status['target'],'http://127.0.0.1:8878/drive-console')
        self.assertEqual(fetch.call_args.kwargs['timeout'],.25)
        connection.close.assert_called_once()

    def test_explicit_disabled_choice_wins_over_running_native_frontend(self):
        handler=object.__new__(proxy.ProxyHandler)
        settings=json.dumps({'installed':True,'enabled':False}).encode()
        with patch.object(proxy,'fetch_upstream',return_value=(200,{},settings)),patch.object(proxy.http.client,'HTTPConnection') as connection:
            self.assertEqual(handler.start_status()['target'],'/')
        connection.assert_not_called()

    def test_refresh_is_rate_limited(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(proxy,'_HA_REFRESH_AT',100),patch.object(proxy.time,'monotonic',return_value=110):
                self.assertEqual(proxy.refresh_ha_tunnel(proc=Path(temp)),0)

    def test_older_kernel_without_pidfd_uses_rechecked_owned_client(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);home=root/'home';proc=root/'proc';process=proc/'100000001';process.mkdir(parents=True)
            args=['ssh','-N','-i',str(home/'.ssh/drive-console-duet'),'-L','127.0.0.1:8788:127.0.0.1:8787']
            (process/'cmdline').write_bytes(b'\0'.join(os.fsencode(arg) for arg in args)+b'\0')
            with patch.object(proxy.Path,'home',return_value=home),patch.object(proxy.os,'pidfd_open',side_effect=OSError('unsupported kernel')),patch.object(proxy.os,'kill') as kill:
                self.assertEqual(proxy.refresh_ha_tunnel(force=True,proc=proc),1)
            kill.assert_called_once_with(100000001,proxy.signal.SIGTERM)
