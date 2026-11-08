"""Persistent short messages; existing authenticated events and encrypted relay.

No commands execute, GPS is not read, and broker/modem TX never means delivered.
The normal role installer starts the LAN worker; CLI and browser share its queue.
"""
from contextlib import closing
import argparse
import json
from pathlib import Path
import socket
import time
import uuid

ROLES=('home','tiny','duet','pi','laptop')


def validate(doc):
    if not isinstance(doc,dict) or set(doc)-{'action'}!={'type','source','destination','text','created'} or doc['type']!='message':
        raise ValueError('Invalid message fields')
    if doc.get('action','text') not in ('text','status-request','status-response'):raise ValueError('Unsupported message action')
    if doc['source'] not in ROLES or doc['destination'] not in ROLES or doc['source']==doc['destination']:
        raise ValueError('Invalid message source/destination')
    if doc['source']!='home' and doc['destination']!='home':
        raise ValueError('Car radio messages must target home; local car-to-car chat is not configured')
    text=doc['text']
    if not isinstance(text,str) or not text.strip() or len(text.encode('utf-8'))>240 or any(ord(c)<32 and c not in '\n\t' for c in text):
        raise ValueError('Message must contain 1–240 UTF-8 bytes without control characters')
    if type(doc['created']) not in (int,float) or not 0<=doc['created']<1e12:
        raise ValueError('Invalid message time')


def enqueue(events,source,destination,text,action='text',request_id=None):
    if action=='status-request':text='Request device health status'
    doc={'type':'message','source':source,'destination':destination,'text':text,'created':time.time()}
    if action!='text':doc['action']=action
    validate(doc)
    import re
    if request_id is not None and (not isinstance(request_id,str) or not re.fullmatch('[0-9a-f]{32}',request_id)):
        raise ValueError('Invalid client message identity')
    ident='message:'+source+':'+(request_id or uuid.uuid4().hex)
    with closing(events.db()) as db:old=db.execute('SELECT doc,done FROM jobs WHERE id=?',(ident,)).fetchone()
    if old:
        previous=json.loads(old[0])
        if {k:v for k,v in previous.items() if k!='created'}!={k:v for k,v in doc.items() if k!='created'}:
            raise ValueError('Client identity already belongs to another message')
        return dict(previous,id=ident,phase={0:'queued',1:'delivered',2:'cancelled'}[old[1]])
    events.enqueue(ident,doc)
    return dict(doc,id=ident,phase='queued')


def cancel(events,ident):
    if not isinstance(ident,str):raise ValueError('Message ID required')
    with closing(events.db()) as db,db:
        row=db.execute("SELECT done FROM jobs WHERE id=? AND json_extract(doc,'$.type')='message'",(ident,)).fetchone()
        if not row:raise ValueError('Outgoing message not found')
        if row[0]==1:raise ValueError('Delivered messages cannot be recalled')
        db.execute('UPDATE jobs SET done=2 WHERE id=?',(ident,))
    return {'id':ident,'phase':'cancelled','message':'Local retries stopped. Data already transmitted or queued at the gateway cannot be recalled.'}


def status_reply(events,doc,role,home):
    """One fixed, read-only action. No shell, files, GPS or vehicle controls."""
    if doc.get('action')!='status-request' or doc.get('destination')!=role:return
    import hashlib
    ident='status-reply:'+hashlib.sha256(json.dumps(doc,sort_keys=True).encode()).hexdigest()[:32]
    with closing(events.db()) as db:
        if db.execute('SELECT 1 FROM jobs WHERE id=?',(ident,)).fetchone():return
    import shutil
    uptime=int(float(Path('/proc/uptime').read_text().split()[0]))
    free=shutil.disk_usage(home).free//1024**2
    summary=json.dumps({'role':role,'uptimeSeconds':uptime,'freeMiB':free,
                        'pendingMessages':sum(row['phase']=='queued' for row in listing(events)['items'])},separators=(',',':'))
    reply={'type':'message','source':role,'destination':doc['source'],'text':summary,
           'created':time.time(),'action':'status-response'}
    validate(reply)
    events.enqueue(ident,reply)


def listing(events):
    with closing(events.db()) as db:
        rows=db.execute("SELECT id,doc,done,cursor FROM jobs WHERE json_extract(doc,'$.type')='message' ORDER BY rowid DESC LIMIT 100").fetchall()
        counts=db.execute("SELECT done,COUNT(*) FROM jobs WHERE json_extract(doc,'$.type')='message' GROUP BY done").fetchall()
    outgoing=[]
    from control.lorawan_protocol import split_message
    for ident,raw,done,cursor in rows:
        doc=json.loads(raw);count=len(split_message('status',1,{'catalog':doc,'id':ident},events.key))
        outgoing.append(dict(doc,id=ident,direction='sent',phase={0:'queued',1:'delivered',2:'cancelled'}[done],
                             progress={'attemptedFragments':min(cursor,count),'totalFragments':count,'attemptCycles':cursor//count,
                                       'state':'awaiting-receipt' if cursor>=count else 'sending' if cursor else 'waiting'}))
    incoming=[dict(row,phase='received',direction='received') for row in events.catalog('message')]
    return {'items':sorted(outgoing+incoming,key=lambda row:row['created'],reverse=True)[:200],
            'counts':{'queued':dict(counts).get(0,0),'delivered':dict(counts).get(1,0),'cancelled':dict(counts).get(2,0),'received':len(incoming)},
            'deliveryMeaning':'Delivered means the receiving application durably saved and acknowledged the message; it does not mean read.'}


def local_events(config=None):
    from control.event_transfer import Events
    if config is None:config=json.loads(Path('/etc/car-dashboard/fleet.json').read_text())
    home=Path(config['home'])
    key=bytes.fromhex((home/'.config/car-dashboard/update-link.key').read_text().strip())
    return Events(home/'.local/state/drive-console/software/messages.sqlite3',key,destination=config['role'],types=('message',))


def worker():
    from control.update_agent import LAN,PORT
    config=json.loads(Path('/etc/car-dashboard/fleet.json').read_text());role=config['role']
    while not (Path(config['home'])/'.config/car-dashboard/update-link.key').exists():time.sleep(5)
    events=local_events(config)
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as receiver,socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sender:
        receiver.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);receiver.bind(('',PORT));receiver.settimeout(1)
        sender.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1);sender.bind((LAN[role],0))
        next_send=0
        while True:
            try:
                data,peer=receiver.recvfrom(128)
                if peer[0] in LAN.values():
                    if data[:3]==b'D\x01\x06':
                        result=events.receive(data[1:])
                        if result:status_reply(events,result,role,config['home'])
                        if result and 'received' in result:
                            for frame in events.completed_frames(result['received']):sender.sendto(b'F'+frame,('172.16.42.255',PORT))
                    elif data[:3]==b'R\x01\x06':
                        events.sent(data[1:],ack=True);events.sent(data[1:])
                        for frame in events.completed_frames(raw=data[1:]):sender.sendto(b'F'+frame,('172.16.42.255',PORT))
            except socket.timeout:pass
            except ValueError:pass
            if time.monotonic()>=next_send:
                with closing(events.db()) as db:cancelled=db.execute('SELECT id FROM jobs WHERE done=2 ORDER BY rowid DESC LIMIT 100').fetchall()
                for row in cancelled:
                    for frame in events.completed_frames(row[0]):sender.sendto(b'F'+frame,('172.16.42.255',PORT))
                raw=events.outgoing(ack=True) or events.outgoing(kinds=['message'])
                if raw:sender.sendto(b'U'+raw,('172.16.42.255',PORT))
                next_send=time.monotonic()+5


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['send','status','list','cancel','run'])
    parser.add_argument('text',nargs='?')
    parser.add_argument('--to',default='home',choices=ROLES)
    args=parser.parse_args()
    if args.action=='run':worker();return
    config=json.loads(Path('/etc/car-dashboard/fleet.json').read_text());events=local_events(config)
    result=cancel(events,args.text) if args.action=='cancel' else enqueue(events,config['role'],args.to,args.text,'status-request' if args.action=='status' else 'text') if args.action in ('send','status') else listing(events)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:main()
    except FileNotFoundError:raise SystemExit('Messages pending: complete application pairing on this car device first.')
    except ValueError as error:raise SystemExit('Messages: '+str(error))
