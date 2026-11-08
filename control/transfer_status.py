"""Read-only delivery inspection. Budgets count local reservations, not receipt ACKs."""
import math
import os
import time
import json
from control.update_transfer import HEADER as APP_HEADER


def budget(journal,now=None):
    now=time.time() if now is None else now;day=int(now//86400)
    limit=min(200,max(1,int(os.getenv('CAR_DOWNLINKS_PER_DAY','60'))))
    spacing=max(30,int(os.getenv('CAR_DOWNLINK_SPACING','60')))
    payload=int(os.getenv('CAR_RADIO_MAX_APP_BYTES','46'))
    if not 10<=payload<=242:raise ValueError('Invalid encrypted downlink payload size')
    with journal.db() as db:row=db.execute("SELECT count,last FROM budgets WHERE name='mqtt-downlinks' AND day=?",(day,)).fetchone()
    used,last=row if row else (0,None)
    return {'limit':limit,'reserved':used,'remaining':max(0,limit-used),'spacingSeconds':spacing,
            'payloadBytes':payload,'resetsAt':(day+1)*86400,
            'nextEligibleAt':(day+1)*86400 if used>=limit else max(now,(last or 0)+spacing)}


def _view(db,row,radio_bytes,now):
    ident,raw,phase,updated,error,paused,policy,priority,revision=row;doc=json.loads(raw)
    groups=db.execute('''SELECT p.kind,COUNT(*),SUM(r.id IS NOT NULL) FROM parts p
        LEFT JOIN receipts r ON p.id=r.id AND p.kind=r.kind AND p.idx=r.idx WHERE p.id=? GROUP BY p.kind''',(ident,)).fetchall()
    counts={kind:{'total':total,'acknowledged':acked or 0} for kind,total,acked in groups}
    total=sum(item['total'] for item in counts.values());acked=sum(item['acknowledged'] for item in counts.values())
    seen=db.execute('SELECT ip_seen FROM links WHERE device=? AND role=?',(doc['device'],doc['role'])).fetchone()
    recent=bool(seen and 0<=now-seen[0]<=45)
    activity=db.execute("SELECT MAX(CASE WHEN type LIKE 'sent:%' THEN ts END),MAX(CASE WHEN type LIKE 'received:%' THEN ts END) FROM events WHERE id=?",(ident[:16],)).fetchone()
    transport=db.execute('SELECT transport FROM events WHERE id=? ORDER BY ts DESC LIMIT 1',(ident[:16],)).fetchone()
    terminal=phase in ('applied','failed')
    shown=phase if terminal else 'paused' if paused else 'awaiting-application' if total and acked>=total else 'receiving' if acked else phase
    minimum=None
    try:
        from control.radio_crypto import HEADER,MAGIC
        from nacl.bindings import crypto_box_SEALBYTES
        overhead=len(MAGIC)+1+crypto_box_SEALBYTES
        remaining=db.execute('''SELECT LENGTH(p.data),COUNT(*) FROM parts p LEFT JOIN receipts r
            ON p.id=r.id AND p.kind=r.kind AND p.idx=r.idx WHERE p.id=? AND r.id IS NULL GROUP BY LENGTH(p.data)''',(ident,)).fetchall()
        minimum=sum(count*math.ceil((APP_HEADER.size+8+size+overhead)/(radio_bytes-HEADER.size)) for size,count in remaining)
        if not minimum and not terminal:
            last=db.execute('SELECT LENGTH(data) FROM parts WHERE id=? AND kind=1 ORDER BY idx DESC LIMIT 1',(ident,)).fetchone()
            if last:minimum=math.ceil((APP_HEADER.size+8+last[0]+overhead)/(radio_bytes-HEADER.size))
        if terminal:minimum=0
    except ImportError:pass
    return {'id':ident,'device':doc['device'],'role':doc['role'],'path':doc['path'],'phase':phase,'displayPhase':shown,
      'updatedAt':updated,'error':error,'paused':bool(paused),'policy':policy or 'auto',
      'priority':'high' if priority else 'normal','revision':revision or 0,
      'total':total,'acknowledged':acked,'progress':round(100*acked/total,1) if total else 0,
      'manifest':counts.get(0,{'total':0,'acknowledged':0}),'data':counts.get(1,{'total':0,'acknowledged':0}),
      'lastSentAt':activity[0],'lastReceiptAt':activity[1],'lastTransport':transport[0] if transport else None,
      'ipSeenAt':seen[0] if seen else None,'ipRecent':recent,
      'decision':'finished' if terminal else 'paused' if paused else 'ip-only' if policy=='ip-only' else 'ip-preferred' if recent else 'radio-fallback',
      'radioFramesPerPass':minimum,'deltaBytes':doc['size'],'base':doc['base'],'target':doc['target']}


def transfers(journal,device,role=None,state='active',before=None,ident=None):
    from control.telemetry import ROLES
    if role is not None and role not in ROLES:raise ValueError('Invalid car role')
    if state not in ('active','finished','all'):raise ValueError('Invalid transfer filter')
    if before is not None and (type(before) is not int or not 0<before<2**63):raise ValueError('Invalid transfer cursor')
    limits=budget(journal);query="j.direction='out' AND json_extract(j.doc,'$.device')=?";args=[device]
    if role:query+=" AND json_extract(j.doc,'$.role')=?";args.append(role)
    if ident is not None:query+=' AND j.id=?';args.append(ident)
    elif state!='all':query+=" AND j.phase "+("IN" if state=='finished' else "NOT IN")+" ('applied','failed')"
    if before is not None:query+=' AND j.rowid<?';args.append(before)
    now=time.time()
    with journal.db() as db:
        rows=db.execute('SELECT j.id,j.doc,j.phase,j.updated,j.error,d.paused,d.policy,d.priority,d.revision,j.rowid FROM jobs j LEFT JOIN delivery d ON j.id=d.id WHERE '+query+' ORDER BY j.rowid DESC LIMIT 26',args).fetchall()
        if ident is not None and not rows:raise ValueError('Unknown outgoing transfer for this car')
        result=[_view(db,row[:-1],limits['payloadBytes'],now) for row in rows[:25]]
    return {'items':result,'nextCursor':rows[24][-1] if len(rows)>25 else None,'budget':limits}
