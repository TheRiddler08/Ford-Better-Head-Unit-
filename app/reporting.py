"""Lossless cursor-based access to recorded history, not chart downsampling."""
import csv
import io
import math
import sqlite3
from pathlib import Path

TABLES = {
 'values':('readings','rowid','ts',['ts','name','value','number','unit']),
 'commands':('command_attempts','id','ts',['ts','completed','name','command','status','duration_ms','value','number','unit','error','raw','snapshot_id']),
 'snapshots':('snapshots','id','started',['stage','started','total','successes']),
 'snapshot-values':('recorder_snapshot_values','id','ts',['snapshot_id','ts','name','value','number','unit']),
 'events':('events','rowid','ts',['ts','name','detail']),
 'gps':('fixes','id','ts',['received','ts','time_source','lat','lon','altitude','speed','heading','mode','epx','epy','segment','break_reason']),
 'gps-messages':('gps_messages','id','received',['received','class','raw']),
 'stops':('stops','id','started',['started','ended','lat','lon','samples','segment']),
 'gps-events':('gps_events','id','ts',['ts','name','detail'])}


def connect(path):
    if not Path(path).is_file():
        return None
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def number(query, key, default=None):
    value=query.get(key,[default])[0]
    if value in (None,''):return default
    value=float(value)
    if not math.isfinite(value):raise ValueError('Invalid '+key)
    return value


def selection(kind,query,upper=None):
    if kind not in TABLES:raise ValueError('Unknown report')
    table,ident,stamp,fields=TABLES[kind]
    after=number(query,'after',0)
    if after<0 or int(after)!=after:raise ValueError('Invalid cursor')
    clauses=[ident+'>?'];args=[int(after)]
    if upper is not None:clauses.append(ident+'<=?');args.append(upper)
    for key,op in [('from','>='),('to','<=')]:
        value=number(query,key)
        if value is not None:clauses.append(stamp+op+'?');args.append(value)
    if 'name' in query and query['name'][0] and 'name' in fields:
        clauses.append('name=?');args.append(query['name'][0])
    if 'snapshot' in query and 'snapshot_id' in fields:
        clauses.append('snapshot_id=?');args.append(number(query,'snapshot'))
    if 'status' in query and query['status'][0] and 'status' in fields:
        clauses.append('status=?');args.append(query['status'][0])
    return table,ident,fields,' AND '.join(clauses),args


def exists(db,table):
    return db is not None and db.execute("SELECT 1 FROM sqlite_master WHERE name=? AND type='table'",(table,)).fetchone() is not None


def page(db,kind,query):
    table,ident,fields,where,args=selection(kind,query)
    limit=number(query,'limit',500)
    if int(limit)!=limit or not 1<=limit<=2000:raise ValueError('Limit must be 1–2000')
    if not exists(db,table):
        return {'items':[],'next':int(number(query,'after',0)),'hasMore':False,'available':False,'columns':['id']+fields}
    result=[dict(row) for row in db.execute('SELECT '+ident+' AS id,'+','.join(fields)+' FROM '+table+' WHERE '+where+' ORDER BY '+ident+' LIMIT ?',args+[int(limit)+1])]
    more=len(result)>limit
    result=result[:int(limit)]
    return {'items':result,'next':result[-1]['id'] if result else int(number(query,'after',0)),
            'hasMore':more,'available':True,'columns':['id']+fields}


def csv_batches(db,kind,query):
    table,ident,fields,_,_=selection(kind,query)
    stream=io.StringIO();writer=csv.writer(stream)
    writer.writerow(['id']+fields)
    yield stream.getvalue().encode();stream.seek(0);stream.truncate(0)
    if not exists(db,table):return
    # High-water mark and short individual SELECTs avoid a long reader lock
    # blocking the live recorder. New readings are included in the next export.
    upper=db.execute('SELECT COALESCE(MAX('+ident+'),0) FROM '+table).fetchone()[0]
    cursor=int(number(query,'after',0))
    while True:
        q=dict(query,after=[str(cursor)])
        table,ident,fields,where,args=selection(kind,q,upper)
        batch=db.execute('SELECT '+ident+' AS id,'+','.join(fields)+' FROM '+table+' WHERE '+where+' ORDER BY '+ident+' LIMIT 500',args).fetchall()
        if not batch:break
        for row in batch:
            # Preserve raw values. This is a data export, not a spreadsheet file:
            # do not open untrusted text as executable spreadsheet formulas.
            writer.writerow(list(row))
        cursor=batch[-1]['id']
        yield stream.getvalue().encode();stream.seek(0);stream.truncate(0)


def summary(db,gps):
    result={}
    for kind in ('values','commands','snapshots','gps','stops','gps-messages'):
        target=gps if kind in ('gps','stops','gps-messages') else db
        table,_,stamp,_=TABLES[kind]
        if exists(target,table):
            row=target.execute('SELECT COUNT(*) AS count,MIN('+stamp+') AS first,MAX('+stamp+') AS last FROM '+table).fetchone()
            result[kind]=dict(row)
        else:result[kind]={'count':0,'first':None,'last':None,'available':False}
    if exists(db,'command_attempts'):
        result['outcomes']={row['status']:row['n'] for row in db.execute('SELECT status,COUNT(*) n FROM command_attempts GROUP BY status')}
    else:result['outcomes']={}
    return result
