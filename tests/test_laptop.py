"""Portable laptop policy tests: never touch the host's actual power state."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,MagicMock
from power import standby
from deploy import fleet
from control.update_agent import LAN,Agent
from control.car_data import ROLES


class LaptopTests(unittest.TestCase):
    def test_car_off_requires_confirmed_session_power_loss_and_closed_lid(self):
        session=standby.LaptopSession()
        self.assertEqual(session.observe(0,False,False,True),'waiting-confirmed-car-session')
        self.assertEqual(session.observe(10,True,True,True),'car-active')
        self.assertEqual(session.observe(20,False,True,True),'waiting-car-power-loss')
        self.assertEqual(session.observe(30,False,False,True),'car-off-grace-period')
        self.assertEqual(session.observe(119,False,False,True),'car-off-grace-period')
        self.assertEqual(session.observe(120,False,False,True),'ready-deep-sleep')
        session.resumed()
        self.assertEqual(session.observe(999,False,False,True),'waiting-confirmed-car-session')
    def test_laptop_in_use_after_removal_is_not_suspended(self):
        session=standby.LaptopSession();session.observe(0,True,True,False)
        session.observe(1,False,False,False)
        self.assertEqual(session.observe(100,False,False,False),'portable-use')
        self.assertEqual(session.observe(200,False,False,True),'waiting-confirmed-car-session')
    def test_unknown_sensors_and_pi_reboot_never_become_car_off(self):
        session=standby.LaptopSession();session.observe(0,True,True,True)
        self.assertEqual(session.observe(100,False,None,True),'waiting-car-power-loss')
        session.observe(101,False,False,None)
        self.assertEqual(session.observe(200,False,False,None),'pending-lid-sensor')
        self.assertEqual(session.observe(201,True,True,True),'car-active')
    def test_power_supply_and_lid_sensors_are_read_without_guessing(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.assertIsNone(standby.ac_connected(root));self.assertIsNone(standby.lid_closed(root))
            ac=root/'AC';ac.mkdir();(ac/'type').write_text('Mains');(ac/'online').write_text('1')
            self.assertIs(standby.ac_connected(root),True)
            (ac/'online').write_text('0');self.assertIs(standby.ac_connected(root),False)
            lid=root/'LID0';lid.mkdir();(lid/'state').write_text('state: closed')
            self.assertIs(standby.lid_closed(root),True)
            (lid/'state').write_text('state: open');self.assertIs(standby.lid_closed(root),False)
    def test_role_is_consistent_and_has_no_tiny_source_inventory(self):
        self.assertEqual(fleet.ADDRESSES['laptop'],LAN['laptop']);self.assertIn('laptop',ROLES)
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(Agent({'role':'laptop','home':temp,'repo':temp}).inventory(),[])
        config={'schema':1,'role':'laptop','interface':'eth0','sleep_mode':'deep','pi_address':'172.16.42.3','home':'/home/driver'}
        standby.validate(config)
        self.assertEqual(standby.lock_paths(config),[Path('/run/lock/car-dashboard-update.lock')])
    def test_pi_wake_list_includes_portable_laptop(self):
        writes={}
        peers={'laptop':{'mac':'02:11:22:33:44:55'}}
        with patch.object(fleet,'save',side_effect=lambda path,data:writes.update({str(path):data})):
            fleet.write_wake({'role':'pi'},'eth0',peers)
        self.assertEqual(writes['/etc/car-dashboard/pi-wake.json']['targets'][0]['name'],'laptop')
        with patch.object(fleet,'run') as run:
            self.assertEqual(fleet.write_wake({'role':'laptop'},'eth0',peers),'car-off-monitor');run.assert_not_called()
    def test_laptop_power_unit_uses_immutable_source_and_respects_optout(self):
        units={}
        with patch.object(fleet.Path,'exists',return_value=True),patch.object(fleet,'unit',side_effect=lambda name,text:units.update({name:text})),patch.object(fleet,'run') as run:
            fleet.install_laptop_power({'laptop_sleep':False})
        self.assertIn('/fleet-current/standby.py laptop',units['car-laptop-power.service'])
        run.assert_any_call(['systemctl','disable','--now','car-laptop-power.service'])
    def test_other_roles_do_not_start_nonexistent_laptop_power_unit(self):
        with patch.object(fleet,'unit'),patch.object(fleet,'load',return_value={}),patch.object(fleet,'run') as run:
            fleet.systemd_units('pi')
        for call in run.call_args_list:
            self.assertNotIn('car-laptop-power.service',call.args[0])
    def test_laptop_does_not_enter_boot_standby(self):
        with patch.object(fleet.subprocess,'run') as run:fleet.boot({'role':'laptop'})
        run.assert_not_called()

    def test_deep_sleep_preserves_inhibitors_and_restores_previous_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);nic=root/'nic';(nic/'device/power').mkdir(parents=True)
            (nic/'device/power/wakeup').write_text('disabled')
            modes=MagicMock();modes.read_text.return_value='[s2idle] deep'
            real_path=Path
            def paths(value):
                if str(value)=='/sys/power/mem_sleep':return modes
                if str(value)=='/var/lib/car-dashboard-fleet/refresh.lock':return root/'refresh.lock'
                return real_path(value)
            with patch.object(standby,'Path',side_effect=paths),patch.object(standby,'probe',return_value=nic),patch.object(standby,'acquire') as locks,patch.object(standby,'run',return_value='Wake-on: g'),patch.object(standby.os,'sync'),patch.object(standby,'ac_connected',return_value=False),patch.object(standby,'lid_closed',return_value=True),patch.object(standby,'pi_registered',return_value=False),patch.object(standby,'request_sleep',side_effect=RuntimeError('application inhibitor')) as request:
                with self.assertRaises(RuntimeError):standby.laptop_sleep({'role':'laptop','interface':'eth0','home':str(root)})
            locks.assert_called_once();request.assert_called_once()
            self.assertEqual([call.args[0] for call in modes.write_text.call_args_list],['deep\n','s2idle\n'])
            self.assertEqual((nic/'device/power/wakeup').read_text(),'enabled\n')

    def test_laptop_shares_wifi_when_preferred_gateways_are_unavailable(self):
        laptop={'laptop':{'internet':True,'seen':100}}
        self.assertEqual(fleet.elect('tiny',False,laptop,105),'laptop')
        self.assertEqual(fleet.elect('laptop',True,{},105),'laptop')
        self.assertIsNone(fleet.elect('tiny',False,laptop,116))
        peers=dict(laptop,duet={'internet':True,'seen':100},pi={'internet':True,'seen':100})
        self.assertEqual(fleet.elect('laptop',True,peers,105),'pi')
        peers['pi']['internet']=False
        self.assertEqual(fleet.elect('laptop',True,peers,105),'duet')
        peers['duet']['internet']=False
        self.assertEqual(fleet.elect('tiny',False,peers,105),'laptop')

    def test_wifi_laptop_does_not_install_route_back_to_peer(self):
        sharing=fleet.Sharing()
        with patch.object(sharing,'firewall') as firewall,patch.object(sharing,'route') as route:
            sharing.apply('eth0','wlan0',['1.1.1.1'],'laptop','pi',True)
            firewall.assert_called_once_with('eth0',None)
            route.assert_called_once_with('eth0',None)
        sharing=fleet.Sharing()
        with patch.object(sharing,'firewall'),patch.object(sharing,'route') as route,patch.object(fleet.subprocess,'Popen') as dns:
            dns.return_value.poll.return_value=None
            sharing.apply('eth0','wlan0',['1.1.1.1'],'laptop','laptop',True)
            self.assertIn('--listen-address=172.16.42.4',dns.call_args.args[0])
            route.assert_called_once_with('eth0',None)
        sharing=fleet.Sharing()
        with patch.object(sharing,'firewall'),patch.object(sharing,'route') as route:
            sharing.apply('eth0',None,[],'tiny','laptop',False)
            route.assert_called_once_with('eth0','172.16.42.4')

    def test_prefer_deep_but_use_s2idle_when_it_is_the_only_supported_mode(self):
        self.assertEqual(standby.laptop_memory_mode('[s2idle] deep'),'deep')
        self.assertEqual(standby.laptop_memory_mode('[s2idle]'),'s2idle')
        with self.assertRaises(RuntimeError):standby.laptop_memory_mode('')
