import json
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deploy import native_firewall as firewall
from deploy import fleet


class NativeFirewallTests(unittest.TestCase):
    def ruleset(self):
        rows=[{'table':{'family':'inet','name':'filter'}}]
        for name,hook in [('input','input'),('forward','forward'),('other_forward','forward')]:
            rows.append({'chain':dict(family='inet',table='filter',name=name,type='filter',hook=hook,policy='drop',prio=0)})
        for family,table in [('ip','filter'),('inet','docker-bridges'),('inet','tailscale')]:
            rows.append({'chain':dict(family=family,table=table,name='FORWARD',type='filter',hook='forward',policy='drop',prio=0)})
        return {'nftables':rows}

    def test_repairs_native_drop_and_uses_scoped_nat_without_global_policy_edits(self):
        commands=firewall.plan(self.ruleset(),'eth0','wlan0');text='\n'.join(commands)
        self.assertIn('insert rule inet filter forward jump car_fleet_forward',text)
        self.assertIn('insert rule inet filter other_forward jump car_fleet_forward',text)
        self.assertEqual(sum(c.startswith('add chain inet filter car_fleet_forward') for c in commands),1)
        self.assertIn('iifname "eth0" oifname "wlan0" ip saddr 172.16.42.0/24',text)
        self.assertIn('ct state established,related counter accept',text)
        self.assertIn('th dport 53',text);self.assertIn('masquerade',text)
        self.assertNotIn('flush ruleset',text);self.assertNotIn('policy drop',text)
        self.assertNotIn('docker',text);self.assertNotIn('tailscale',text);self.assertNotIn('ip "filter"',text)
        self.assertTrue(all(not c.startswith('flush chain') for c in commands))

    def test_disable_keeps_controls_only_and_does_not_create_outbound_access(self):
        text='\n'.join(firewall.plan(self.ruleset(),'eth0',None))
        self.assertIn('8796, 8798, 8799',text)
        self.assertNotIn('masquerade',text);self.assertNotIn('th dport 53',text)
        self.assertNotIn('ip saddr 172.16.42.0/24 counter accept',text)
        self.assertNotIn('8796', '\n'.join(firewall.plan(self.ruleset(),'eth0',None,False)))

    def installed(self,lan='eth0',wan='wlan0'):
        data=self.ruleset();commands=firewall.plan(data,lan,wan)
        # Reproduce the nft JSON shape for each command we generated, including
        # rule handles, without modifying the machine's firewall.
        import shlex
        for handle,command in enumerate(commands,1):
            parts=shlex.split(command)
            if parts[:2]==['add','table']:data['nftables'].append({'table':dict(family=parts[2],name=parts[3])})
            if parts[:2]==['add','chain']:data['nftables'].append({'chain':dict(family=parts[2],table=parts[3],name=parts[4])})
            if parts[0] in ('add','insert') and parts[1]=='rule':
                data['nftables'].append({'rule':dict(family=parts[2],table=parts[3],chain=parts[4],comment=parts[-1],handle=handle)})
        return data

    def test_repeated_reconciliation_is_read_only_but_missing_rules_are_restored(self):
        data=self.installed();self.assertEqual(firewall.plan(data,'eth0','wlan0'),[])
        # A reload loses only the forward jump; repair it without replacing NAT.
        data['nftables']=[item for item in data['nftables'] if item.get('rule',{}).get('chain')!='forward']
        commands=firewall.plan(data,'eth0','wlan0')
        self.assertEqual(len(commands),1);self.assertTrue(commands[0].startswith('insert rule inet filter forward'))

    def test_uplink_switch_is_atomic_and_only_own_chains_are_flushed(self):
        commands=firewall.plan(self.installed(),'eth0','wlan1')
        self.assertTrue(any('oifname "wlan1"' in c for c in commands))
        self.assertFalse(any('oifname "wlan0"' in c for c in commands))
        for c in commands:
            if c.startswith('flush chain'):self.assertIn('car_fleet_',c)
        with patch.object(firewall.subprocess,'run',side_effect=[subprocess.CompletedProcess([],0,json.dumps(self.installed()).encode(),b''),subprocess.CompletedProcess([],0,b'',b'')]) as run:
            firewall.apply('eth0','wlan1');self.assertEqual(run.call_count,2)
            self.assertEqual(run.call_args.args[0],['nft','-f','-'])

    def test_interface_command_injection_is_rejected(self):
        with self.assertRaises(ValueError):firewall.plan(self.ruleset(),'eth0; flush ruleset','wlan0')

    def test_running_daemon_keeps_its_immutable_helper_when_current_selector_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=root/'old';old.mkdir();new=root/'new';new.mkdir()
            (old/'helper.py').write_text('VERSION=1\n');(new/'helper.py').write_text('VERSION=2\n')
            current=root/'current';current.symlink_to(new,target_is_directory=True)
            with patch.object(fleet,'SCRIPT_DIR',old),patch.object(fleet,'__file__',str(current/'fleet.py')):
                self.assertEqual(fleet.role_module('helper').VERSION,1)

    def test_drop_inserted_ahead_of_owned_jump_is_repaired_without_removing_drop(self):
        data=self.installed()
        data['nftables'].insert(0,{'rule':dict(family='inet',table='filter',chain='forward',handle=999,expr=[{'drop':None}])})
        commands=firewall.plan(data,'eth0','wlan0')
        self.assertEqual(len(commands),2)
        self.assertNotIn('handle 999','\n'.join(commands));self.assertTrue(commands[-1].startswith('insert rule inet filter forward'))

    def test_nft_path_does_not_ask_iptables_to_decode_incompatible_nat(self):
        module=type('Module',(),{'apply':lambda *args:None})()
        with patch.object(fleet.shutil,'which',return_value='/usr/sbin/nft'),patch.object(fleet,'role_module',return_value=module),patch.object(fleet,'run',return_value=subprocess.CompletedProcess([],0)) as run,patch.object(fleet.Path,'write_text'):
            fleet.Sharing().firewall('eth0','wlan0')
        self.assertFalse(any('nat' in call.args[0] for call in run.call_args_list))

    def test_periodic_firewall_repair_keeps_healthy_dns_process_running(self):
        sharing=fleet.Sharing();sharing.gateway_state=('eth0','wlan0',('1.1.1.1',))
        from unittest.mock import MagicMock
        sharing.dns=MagicMock();sharing.dns.poll.return_value=None
        with patch.object(fleet.time,'monotonic',return_value=20),patch.object(sharing,'firewall') as repair,patch.object(sharing,'route'):
            sharing.apply('eth0','wlan0',['1.1.1.1'],'duet','duet',True)
            repair.assert_called_once_with('eth0','wlan0');sharing.dns.terminate.assert_not_called()


if __name__=='__main__':unittest.main()
