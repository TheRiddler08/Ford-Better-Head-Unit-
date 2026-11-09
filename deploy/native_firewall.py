"""Repair scoped car rules in native nftables firewalls without replacing them.

An ACCEPT in iptables cannot override a DROP in another nftables base chain.
Install narrow jumps in each native input/forward filter chain, and use our own
native NAT table (which also works when iptables cannot decode the nat table).
"""
import hashlib
import json
import re
import subprocess

SUBNET='172.16.42.0/24'
TABLE='car_fleet_nat'
MARK='car-fleet-managed:'


def identifier(value):
    # nft command identifiers are bare tokens, not JSON-quoted string values.
    # Refuse exotic names rather than interpolating arbitrary firewall text.
    if not isinstance(value,str) or not re.fullmatch('[a-zA-Z_][a-zA-Z0-9_.-]{0,127}',value):raise ValueError('Unsupported native firewall identifier')
    return value


def native_chain(row):
    if row.get('family') not in ('ip','inet') or row.get('type')!='filter' or row.get('hook') not in ('input','forward'):return False
    table=row.get('table','')
    # iptables-nft owns these tables. Its filter rules are reconciled through
    # iptables itself; don't modify Docker/Tailscale-owned nftables tables.
    return row['family']=='inet' or table not in ('filter','nat','mangle','raw','security')


def plan(ruleset,lan,wan,control=True):
    for nic in (lan,wan):
        if nic is not None and not re.fullmatch('[a-zA-Z0-9_.:-]{1,15}',nic):raise ValueError('Unsupported interface name')
    data=ruleset.get('nftables',[])
    chains=[item['chain'] for item in data if 'chain' in item]
    targets=[c for c in chains if native_chain(c) and not c['table'].lower().startswith(('docker','tailscale','ts-'))]
    rules=[item['rule'] for item in data if 'rule' in item]
    tables={(item['table']['family'],item['table']['name']) for item in data if 'table' in item}
    signature=hashlib.sha256(json.dumps([lan,wan,control],sort_keys=True).encode()).hexdigest()[:16]
    comment=MARK+signature
    existing=lambda family,table,chain:[r for r in rules if (r.get('family'),r.get('table'),r.get('chain'))==(family,table,chain)]
    commands=[]
    q=json.dumps
    # Each managed chain has at least a counter marker. Check the rule count and
    # marker too, so an external firewall reload/partial deletion is repaired.
    for base in targets:
        family,table,hook=base['family'],base['table'],base['hook']
        name='car_fleet_'+hook;prefix=f'{family} {identifier(table)}';location=prefix+' '+name
        base_name=identifier(base['name'])
        expressions=[]
        if control and hook=='input':
            expressions=[f'iifname {q(lan)} ip saddr {SUBNET} udp dport {{ 8796, 8798, 8799 }} counter accept']
            if wan:expressions+=[f'iifname {q(lan)} ip saddr {SUBNET} meta l4proto {{ tcp, udp }} th dport 53 counter accept']
        if wan and hook=='forward':
            expressions=[f'iifname {q(lan)} oifname {q(wan)} ip saddr {SUBNET} counter accept',
                f'iifname {q(wan)} oifname {q(lan)} ip daddr {SUBNET} ct state established,related counter accept',
                f'iifname {q(wan)} oifname {q(lan)} ip daddr {SUBNET} counter drop']
        expressions+=['counter']
        owned=existing(family,table,name)
        base_rules=existing(family,table,base['name'])
        jumps=[r for r in base_rules if str(r.get('comment','')).startswith(MARK)]
        good=len(owned)==len(expressions) and all(r.get('comment')==comment for r in owned)
        if not good:
            if not any(c.get('family')==family and c.get('table')==table and c.get('name')==name for c in chains):commands+=['add chain '+location]
            else:commands+=['flush chain '+location]
            commands += ['add rule '+location+' '+expr+' comment '+q(comment) for expr in expressions]
        if len(jumps)!=1 or jumps[0].get('comment')!=comment or base_rules[0]!=jumps[0]:
            commands += [f'delete rule {prefix} {base_name} handle {int(r["handle"])}' for r in jumps]
            commands += [f'insert rule {prefix} {base_name} jump {name} comment {q(comment)}']
    # A table may have several base chains with the same hook; deduplicate its
    # regular-chain commands while retaining a jump in every base chain.
    commands=list(dict.fromkeys(commands))
    if existing('ip','nat','CAR_FLEET_NAT'):
        # Retire only our legacy chain; iptables can't decode this host's NAT
        # table when another manager has inserted native nft expressions.
        commands+=['flush chain ip nat CAR_FLEET_NAT']
    nat=existing('ip',TABLE,'postrouting')
    expressions=([f'ip saddr {SUBNET} oifname {q(wan)} counter masquerade'] if wan else [])+['counter']
    if len(nat)!=len(expressions) or any(r.get('comment')!=comment for r in nat):
        if ('ip',TABLE) not in tables:commands+=['add table ip '+TABLE]
        if not any(c.get('family')=='ip' and c.get('table')==TABLE and c.get('name')=='postrouting' for c in chains):
            commands+=['add chain ip '+TABLE+' postrouting { type nat hook postrouting priority srcnat; policy accept; }']
        else:commands+=['flush chain ip '+TABLE+' postrouting']
        commands+=['add rule ip '+TABLE+' postrouting '+expr+' comment '+q(comment) for expr in expressions]
    return commands


def apply(lan,wan,control=True):
    result=subprocess.run(['nft','-j','list','ruleset'],capture_output=True,timeout=10)
    if result.returncode:raise RuntimeError('Unable to inspect native car firewall')
    commands=plan(json.loads(result.stdout),lan,wan,control)
    if commands:
        # Atomic nft transaction: no interval of missing rules or half-switched
        # uplinks, no global flush, and existing user firewall policy survives.
        result=subprocess.run(['nft','-f','-'],input=('\n'.join(commands)+'\n').encode(),capture_output=True,timeout=10)
        if result.returncode:raise RuntimeError('Unable to apply scoped native car firewall rules')
