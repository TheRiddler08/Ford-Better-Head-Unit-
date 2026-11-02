"""Network changes are mocked; no host network, power or init state is changed."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch,MagicMock
from deploy import fleet
from power import standby


class FleetTests(unittest.TestCase):
    def test_peer_identity_uses_role_ip_and_valid_ethernet_mac(self):
        msg={'schema':1,'role':'duet','mac':'02:11:22:33:44:55','internet':True}
        peer=fleet.eligible_peer(msg,'172.16.42.1',100)
        self.assertEqual(peer['mac'],msg['mac'])
        self.assertIsNone(fleet.eligible_peer(msg,'192.168.1.46',100))
        self.assertIsNone(fleet.eligible_peer(dict(msg,mac='ff:ff:ff:ff:ff:ff'),'172.16.42.1',100))
        self.assertIsNone(fleet.eligible_peer(dict(msg,role='unknown'),'172.16.42.1',100))

    def test_deterministic_gateway_and_expired_uplink_failover(self):
        peers={'pi':{'internet':True,'seen':100},'duet':{'internet':True,'seen':100}}
        self.assertEqual(fleet.elect('tiny',False,peers,105),'pi')
        self.assertEqual(fleet.elect('duet',True,peers,105),'pi')
        self.assertEqual(fleet.elect('tiny',False,peers,120),None)
        self.assertEqual(fleet.elect('duet',True,peers,120),'duet')
        self.assertIsNone(fleet.elect('tiny',True,{},100))
        peers['pi']['internet']=False
        self.assertEqual(fleet.elect('tiny',False,peers,105),'duet')

    def test_existing_addressed_link_gets_only_additive_car_identity(self):
        calls=[]
        def run(args,**kwargs):
            calls.append(args)
            if '-j' in args:return json.dumps([{'addr_info':[{'local':'192.168.1.46','scope':'global'}]}])
            return ''
        with patch.object(fleet,'run',side_effect=run):fleet.ensure_lan({'role':'duet'},'eth0')
        self.assertEqual(calls[-1],['ip','address','add','172.16.42.1/24','dev','eth0'])
        self.assertFalse(any('delete' in call or 'down' in call for call in calls))

    def test_existing_car_address_needs_no_network_change(self):
        data=json.dumps([{'addr_info':[{'local':'172.16.42.2','scope':'global'}]}])
        with patch.object(fleet,'run',return_value=data) as run:
            fleet.ensure_lan({'role':'tiny'},'eth0')
            run.assert_called_once()

    def test_firewall_is_scoped_and_own_wifi_does_not_route_back_to_peer(self):
        sharing=fleet.Sharing()
        with patch.object(sharing,'firewall') as firewall,patch.object(sharing,'route') as route:
            sharing.apply('eth0','wlan0',['1.1.1.1'],'duet','pi',True)
            firewall.assert_called_once_with('eth0',None)
            route.assert_called_once_with('eth0',None)
        with patch.object(fleet,'run',return_value=subprocess.CompletedProcess([],0)) as run:
            sharing.firewall('eth0',None)
            for call in run.call_args_list:
                args=call.args[0]
                if '-N' in args or '-F' in args:self.assertIn(args[-1],('CAR_FLEET_FWD','CAR_FLEET_NAT','CAR_FLEET_IN'))
                self.assertNotIn('-P',args)
                self.assertNotIn('flush',args)

    def test_owned_route_recovered_after_daemon_restart_without_replacing_wifi(self):
        with patch.object(fleet,'run',return_value=subprocess.CompletedProcess([],1)) as run,patch.object(fleet.shutil,'which',return_value='/usr/bin/resolvectl'):
            fleet.Sharing().route('eth0','172.16.42.3')
            commands=[call.args[0] for call in run.call_args_list]
            self.assertIn(['ip','-4','route','del','default','dev','eth0','proto','242','metric','50'],commands)
            self.assertIn(['ip','-4','route','replace','default','via','172.16.42.3','dev','eth0','proto','242','metric','50'],commands)
            self.assertFalse(any('wlan0' in command for command in commands))

    def test_published_installer_is_atomic_and_rejects_edited_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);sources={name:b'VERSION=1\n' for name in fleet.SOURCES}
            version=fleet.publish(sources,root)
            self.assertEqual((root/'fleet-current').resolve(),version)
            (version/'fleet.py').write_text('modified')
            with self.assertRaises(ValueError):fleet.publish(sources,root)

    def test_auto_standby_requires_pi_knowing_actual_mac(self):
        config={'pi_address':'172.16.42.3','require_registration':True,'interface':'eth0'}
        client=MagicMock();client.__enter__.return_value.recv.return_value=json.dumps({'schema':1,'uptime':20,'lastWake':150,'knownMacs':[]}).encode()
        with patch.object(standby.socket,'socket',return_value=client),patch.object(standby.Path,'read_text',return_value='02:11:22:33:44:55\n'):
            self.assertFalse(standby.wake_pending(config))
            client.__enter__.return_value.recv.return_value=json.dumps({'schema':1,'uptime':20,'lastWake':150,'knownMacs':['02:11:22:33:44:55']}).encode()
            self.assertTrue(standby.wake_pending(config))

    def test_full_bundle_old_receiver_receipt_gets_another_delivery(self):
        from deploy import offline_updates as courier
        import zipfile
        with tempfile.TemporaryDirectory() as temp:
            state=Path(temp);manifest={'schema':2,'commit':'a'*40,'payloadId':'b'*64}
            with zipfile.ZipFile(state/'ready.zip','w') as archive:archive.writestr('manifest.json',json.dumps(manifest))
            receipt=dict(manifest,ok=True,target='car@172.16.42.2')
            (state/'delivered.json').write_text(json.dumps(receipt))
            response=dict(receipt,provisioning=1,roleSetup={'missing':[]})
            result=subprocess.CompletedProcess([],0,json.dumps(response).encode(),b'')
            with patch.object(courier.subprocess,'run',return_value=result) as run:
                self.assertNotIn('skipped',courier.deliver(state,state/'key',receipt['target']))
                run.assert_called_once()
            pending=dict(response,roleSetup={'missing':['dnsmasq']})
            (state/'delivered.json').write_text(json.dumps(pending))
            with patch.object(courier.subprocess,'run',return_value=result) as run:
                self.assertNotIn('skipped',courier.deliver(state,state/'key',receipt['target']))
                run.assert_called_once()
            with patch.object(courier.subprocess,'run') as run:
                self.assertTrue(courier.deliver(state,state/'key',receipt['target'])['skipped'])
                run.assert_not_called()

    def test_gateway_nat_rules_and_dns_never_start_dhcp(self):
        calls=[]
        def run(args,**kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args,1 if '-C' in args else 0)
        sharing=fleet.Sharing()
        with patch.object(fleet,'run',side_effect=run),patch.object(fleet.Path,'write_text'),patch.object(fleet.subprocess,'Popen') as popen,patch.object(sharing,'route'):
            popen.return_value.poll.return_value=None
            sharing.apply('eth0','wlan0',['1.1.1.1'],'duet','duet',True)
            command=popen.call_args.args[0]
            self.assertIn('--listen-address=172.16.42.1',command)
            self.assertIn('--no-dhcp-interface=eth0',command)
            self.assertFalse(any('dhcp-range' in item for item in command))
            self.assertIn('--server=1.1.1.1@wlan0',command)
        self.assertTrue(any('MASQUERADE' in call and '172.16.42.0/24' in call for call in calls))
        self.assertFalse(any('-P' in call or 'DOCKER' in call for call in calls))

    def test_role_installer_enables_wake_and_target_standby_without_suspending_now(self):
        units={};commands=[]
        def run(args,**kwargs):commands.append(args);return ''
        with patch.object(fleet,'unit',side_effect=lambda name,content:units.update({name:content})),patch.object(fleet,'run',side_effect=run):
            fleet.systemd_units('pi')
            self.assertIn('OnBootSec=30',units['car-wake-first.timer'])
            self.assertIn('OnBootSec=150',units['car-wake-retry.timer'])
            fleet.systemd_units('duet')
            self.assertIn('OnBootSec=15',units['car-boot-standby.timer'])
        self.assertTrue(any('enable' in call and 'car-boot-standby.timer' in call for call in commands))
        self.assertFalse(any('--now' in call and 'car-boot-standby.timer' in call for call in commands))
        self.assertFalse(any('suspend' in call for call in commands))

    def test_root_reconciler_requires_real_courier_signature(self):
        import zipfile
        from deploy import offline_updates as courier
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);state=root/'.local/share/drive-console/courier';state.mkdir(parents=True)
            key=courier.ensure_key(root/'signing-key')
            (root/'allowed_signers').write_text('duet '+(root/'signing-key.pub').read_text())
            manifest={'commit':'a'*40,'branch':'main','repository':courier.REPOSITORY}
            path=root/'manifest.json';path.write_text(json.dumps(manifest))
            courier.run(['ssh-keygen','-Y','sign','-f',key,'-n',courier.NAMESPACE,path])
            def archive(data):
                with zipfile.ZipFile(state/'ready.zip','w') as zip:
                    zip.writestr('manifest.json',data);zip.write(root/'manifest.json.sig','manifest.json.sig')
            archive(path.read_bytes())
            with patch.object(fleet,'STATE',root):
                self.assertEqual(fleet.signed_prepared({'home':str(root)})[0],'a'*40)
                archive(json.dumps(dict(manifest,commit='b'*40)))
                with self.assertRaises(ValueError):fleet.signed_prepared({'home':str(root)})

    def test_tiny_online_updater_is_automatic_and_explicit_optout_persists(self):
        config={'repo':'/home/car/car-dashboard','user':'car','commit':'a'*40,'app_updates':True}
        writes={};units={}
        with patch.object(fleet,'source_bytes',return_value=b'#!/bin/bash\ntrue\n'),patch.object(fleet,'save',side_effect=lambda path,data:writes.update({str(path):data})),patch.object(fleet.os,'chmod'),patch.object(fleet,'unit',side_effect=lambda name,data:units.update({name:data})),patch.object(fleet.Path,'exists',return_value=False),patch.object(fleet,'run') as run:
            fleet.install_app_updates(config)
            run.assert_any_call(['systemctl','enable','--now','car-dashboard-update.timer'])
            self.assertIn('OnCalendar=',units['car-dashboard-update.timer'])
            self.assertIn(b'APP_USER=car',writes['/usr/local/sbin/car-dashboard-update'])
            with patch.object(fleet.Path,'exists',return_value=True):
                fleet.install_app_updates(config)
                run.assert_any_call(['systemctl','disable','--now','car-dashboard-update.timer'])

    def test_duet_automatic_checkout_preserves_edits_and_rolls_back_failed_tests(self):
        config={'repo':'/home/car/car-dashboard','home':'/home/car','user':'car'}
        old='a'*40;new='b'*40
        def git(config,*args,**kwargs):
            if args==('rev-parse','HEAD'):return old
            if args==('rev-parse','FETCH_HEAD'):return new
            if args==('symbolic-ref','--short','HEAD'):return 'main'
            if args==('remote','get-url','origin'):return fleet.ORIGIN
            return ''
        with patch.object(fleet,'git',side_effect=git) as calls,patch.object(fleet.subprocess,'run',return_value=subprocess.CompletedProcess([],1)):
            with self.assertRaises(ValueError):fleet.advance_duet(config,new)
            calls.assert_any_call(config,'reset','--keep',old)
        def edited(config,*args,**kwargs):
            return ' M local.py' if args==('status','--porcelain') else git(config,*args,**kwargs)
        with patch.object(fleet,'git',side_effect=edited) as calls:
            with self.assertRaises(ValueError):fleet.advance_duet(config,new)
            self.assertFalse(any(call.args[1:3]==('merge','--ff-only') for call in calls.call_args_list))

    def test_local_git_tip_alone_cannot_promote_new_root_role_code(self):
        config={'role':'tiny','repo':'/home/car/car-dashboard','home':'/home/car','user':'car','commit':'a'*40}
        with patch.object(fleet,'git',return_value='b'*40),patch.object(fleet,'approved_root_source',return_value=False),patch.object(fleet,'publish') as publish:
            with self.assertRaises(ValueError):fleet.refresh_locked(config)
            publish.assert_not_called()
