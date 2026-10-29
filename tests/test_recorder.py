import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path
FILE=Path(__file__).resolve().parents[1]/"recorder"/"obd_full_recorder.py"
spec=importlib.util.spec_from_file_location("recorder",FILE)
rec=importlib.util.module_from_spec(spec)
spec.loader.exec_module(rec)
class Cmd:
 def __init__(self,name,command): self.name,self.command=name,command
class RecorderTests(unittest.TestCase):
 def test_read_only_allowlist(self):
  self.assertTrue(rec.approved(Cmd("RPM",b"010C")))
  self.assertFalse(rec.approved(Cmd("CLEAR_DTC",b"04")))
  self.assertFalse(rec.approved(Cmd("VIN",b"0902")))
  self.assertFalse(rec.approved(Cmd("CONTROL",b"08")))
 def test_compatible_readings(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/"vehicle.sqlite3"
   d=rec.open_db(p)
   d.execute("INSERT INTO readings VALUES(?,?,?,?,?)",(100,"RPM","900",900,"rpm"))
   d.commit();d.close()
   d=rec.open_db(p)
   self.assertEqual(d.execute("SELECT COUNT(*) FROM readings").fetchone()[0],1)
   d.close()
 def test_committed_vehicle_and_gps_records_survive_killed_writer(self):
  import subprocess
  import sys
  root=FILE.parents[1]
  with tempfile.TemporaryDirectory() as tmp:
   for module,table,insert in [
    ('obd_full_recorder','readings',"INSERT INTO readings VALUES(100,'RPM','900',900,'rpm')"),
    ('gps_recorder','gps_events',"INSERT INTO gps_events(ts,name,detail) VALUES(100,'connected','gpsd')")]:
    path=Path(tmp)/(module+'.sqlite3')
    script="""import os,signal,sys
from recorder import MODULE as rec
db=rec.open_db(sys.argv[1])
assert db.execute('PRAGMA synchronous').fetchone()[0]==2
assert db.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
db.execute(sys.argv[2]);db.commit()
db.execute(sys.argv[2])
os.kill(os.getpid(),signal.SIGKILL)
""".replace('MODULE',module)
    result=subprocess.run([sys.executable,'-c',script,str(path),insert],cwd=root)
    self.assertEqual(result.returncode,-9)
    with sqlite3.connect(path) as db:
     self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
     self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],1)
 def test_protection_against_commands(self):
  class Conn:
   def query(self,c): raise AssertionError("not permitted")
  with self.assertRaises(ValueError): rec.safe_query(Conn(),Cmd("RESET",b"04"))
if __name__=="__main__": unittest.main()
