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
 def test_protection_against_commands(self):
  class Conn:
   def query(self,c): raise AssertionError("not permitted")
  with self.assertRaises(ValueError): rec.safe_query(Conn(),Cmd("RESET",b"04"))
if __name__=="__main__": unittest.main()
