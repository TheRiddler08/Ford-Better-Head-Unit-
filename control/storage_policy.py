"""Bound small durable databases on the dedicated small-card Pi services."""
import os
from pathlib import Path


def state_limit():
    raw=os.getenv('CAR_STATE_DB_LIMIT_BYTES')
    if raw is None:return None
    limit=int(raw)
    if not 1024**2<=limit<=256*1024**2:raise ValueError('Invalid car state database limit')
    return limit


def guard_state_file(path,size):
    limit=state_limit()
    if limit is None or size<=2*limit:return
    path=Path(path).resolve();home=Path.home()
    if any(path.is_relative_to(home/part) for part in ('.local/state/drive-console/software','.local/share/car-data')):
        raise ValueError('Pi local state file limit reached; existing state retained')


def bounded_db(db):
    limit=state_limit()
    if limit is None:return db
    pages=max(1,limit//db.execute('PRAGMA page_size').fetchone()[0])
    db.execute('PRAGMA max_page_count='+str(pages))
    db.execute('PRAGMA journal_size_limit=1048576')
    db.execute('PRAGMA wal_autocheckpoint=256')
    return db
