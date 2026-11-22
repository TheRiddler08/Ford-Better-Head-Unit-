"""Duet car-off power policy. Never touches the host's actual power state."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from power import standby


class DuetPowerTests(unittest.TestCase):
    def test_powers_off_after_grace_and_power_return_cancels(self):
        s=standby.CarPowerSession()
        self.assertEqual(s.observe(0,True,80),'car-power-on')
        self.assertEqual(s.observe(10,False,80),'car-off-grace-period')
        self.assertEqual(s.observe(150,False,80),'car-off-grace-period')
        self.assertEqual(s.observe(160,False,80),'poweroff-car-off')
        s=standby.CarPowerSession();s.observe(0,True,80);s.observe(10,False,80)
        self.assertEqual(s.observe(100,True,80),'car-power-on')  # crank dip / power back
        self.assertEqual(s.observe(200,False,80),'car-off-grace-period')

    def test_low_battery_powers_off_immediately_and_during_grace(self):
        s=standby.CarPowerSession();s.observe(0,True,9)
        self.assertEqual(s.observe(1,False,9),'poweroff-low-battery')
        s=standby.CarPowerSession();s.observe(0,True,10)
        self.assertEqual(s.observe(1,False,10),'poweroff-low-battery')
        s=standby.CarPowerSession();s.observe(0,True,30)
        self.assertEqual(s.observe(5,False,30),'car-off-grace-period')
        self.assertEqual(s.observe(60,False,10),'poweroff-low-battery')

    def test_manual_battery_use_and_unknown_sensor_never_power_off(self):
        s=standby.CarPowerSession()
        self.assertEqual(s.observe(0,False,5),'waiting-car-power')
        self.assertEqual(s.observe(999,False,5),'waiting-car-power')
        s.observe(1000,True,50)
        self.assertEqual(s.observe(1001,None,5),'pending-power-sensor')
        self.assertEqual(s.observe(9999,None,5),'pending-power-sensor')

    def test_battery_reading_ignores_peripheral_batteries(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name,kind,capacity,scope in (('sbs-battery','Battery','42',None),('hid-stylus','Battery','3','Device'),('cros-usbpd-charger','USB','0',None)):
                (root/name).mkdir();(root/name/'type').write_text(kind+'\n');(root/name/'capacity').write_text(capacity)
                if scope:(root/name/'scope').write_text(scope)
            (root/'cros-usbpd-charger'/'online').write_text('1')
            self.assertEqual(standby.battery_percent(root),42)
            self.assertTrue(standby.ac_connected(root))
            self.assertIsNone(standby.battery_percent(root/'none'))

    def test_monitor_rechecks_power_and_respects_opt_out(self):
        with tempfile.TemporaryDirectory() as temp:
            config=Path(temp)/'fleet.json';status=Path(temp)/'status.json'
            config.write_text(json.dumps({'role':'duet','duet_poweroff_delay':60}))
            readings=iter([True,False,False,False]);clock=iter([0,1,2,100,100,100])
            acted=[]
            with patch.object(standby,'write_power_status',lambda value,path=status:status.write_text(json.dumps(value))),patch.object(standby.subprocess,'run'):
                sensors=(lambda:next(readings),lambda:50)
                monitor=lambda:standby.duet_power_monitor(config,sensors=sensors,clock=lambda:next(clock),act=lambda:acted.append(1),once=True)
                # Use one session across calls: patch the class to reuse an instance.
                session=standby.CarPowerSession()
                with patch.object(standby,'CarPowerSession',return_value=session):
                    self.assertEqual(monitor()['phase'],'car-power-on')
                    self.assertEqual(monitor()['phase'],'car-off-grace-period')
                    self.assertEqual(monitor()['phase'],'poweroff-car-off');self.assertEqual(acted,[1])
            config.write_text(json.dumps({'role':'duet','duet_poweroff':False}))
            with patch.object(standby,'write_power_status',lambda value,path=status:None):
                self.assertEqual(standby.duet_power_monitor(config,sensors=(lambda:False,lambda:1),act=lambda:acted.append(2),once=True)['phase'],'disabled')
            self.assertEqual(acted,[1])

    def test_role_installer_enables_duet_service_by_default(self):
        from deploy import fleet
        with patch.object(fleet,'unit') as unit,patch.object(fleet,'run') as run,patch.object(fleet.Path,'exists',return_value=True):
            fleet.install_duet_power({'role':'duet'})
        self.assertIn('standby.py duet-power',unit.call_args[0][1])
        run.assert_any_call(['systemctl','enable','--now','car-duet-power.service'])
        with patch.object(fleet,'unit'),patch.object(fleet,'run') as run,patch.object(fleet.Path,'exists',return_value=True):
            fleet.install_duet_power({'role':'duet','duet_poweroff':False})
        run.assert_any_call(['systemctl','disable','--now','car-duet-power.service'])


if __name__=='__main__':unittest.main()
